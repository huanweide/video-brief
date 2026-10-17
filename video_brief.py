"""
video-brief · 视频总结器（第一步：备料）
BV号/av号/B站链接 → 拿字幕（复用 bili-subtitle-extractor）→ 清洗 → 生成「总结用输入包」

职责边界：本脚本不写总结内容。总结是 Agent 的活，本脚本只负责把原料备干净：
  1. transcript.txt   清洗后的纯文本字幕（给 Agent 读）
  2. meta.json        视频元信息（标题/UP主/时长/分区/简介）
  3. brief-input.md   填好占位符的完整提示词（system + user），Agent 直接拿着用

v1.1 起：长视频（清洗后超过 --chunk-chars 字，默认 15000）自动切段，
走两阶段提取（map-reduce）：每段一份 chunk-N.md 单独提炼成分段笔记，
再用 brief-merge.md 把 N 份笔记归并成终稿。长视频单 prompt 要么爆上下文，
要么模型 lost-in-the-middle 丢中段——这是 bili-subtitle 项目验证过的路子。

用法:
  python video_brief.py BV1xxxxxxx
  python video_brief.py BV1xxxxxxx --out ./myout
  python video_brief.py BV1xxxxxxx --subtitle ./已有字幕.srt      # .srt/.vtt 均可
  python video_brief.py BV1xxxxxxx --page 3
  python video_brief.py https://www.bilibili.com/video/BV1xx/?p=3 # v1.1：链接/av号也行
  python video_brief.py av170001                                 # v1.1：av号（自动转BV）
  python video_brief.py BV1xxxxxxx --chunk-chars 20000           # v1.1：自定义分段阈值
  python video_brief.py BV1xxxxxxx --chunk-chars 0               # v1.1：关闭分段，强制单包
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

# 字幕提取脚本（参照一）：优先同辈 skills 目录，其次环境变量覆盖
EXTRACTOR_CANDIDATES = [
    os.path.join(SKILL_DIR, "..", "bili-subtitle-extractor", "bili_subtitle_skill.py"),
    os.environ.get("BILI_SUBTITLE_EXTRACTOR_PY", ""),
]
EXTRACTOR_PY = next((p for p in EXTRACTOR_CANDIDATES if p and os.path.isfile(p)), None)

BV_RE = re.compile(r'^BV[a-zA-Z0-9]{10}$')
VIDEO_URL = "https://www.bilibili.com/video/{bvid}"

# v1.1 长视频分段：超过这个字数就切段做 map-reduce，不把整节课塞进一个 prompt。
# 为什么是 15000：一节 40-60 分钟的课清洗后约 1.5-2 万字，一次总结要么爆上下文，
# 要么模型「lost in the middle」丢掉中段。切段后每块单独提炼成分段笔记，
# 再用小得多的输入做归并——这是 bili-subtitle 项目验证过的两阶段提取。
DEFAULT_CHUNK_CHARS = 15000

# HTML 实体（VTT/SSA 字幕常见）：&amp; 必须最后解，否则 '&amp;lt;' 会被解成 '<'，
# 等于把转义内容还原成标签——这是 bili-subtitle-universal v9.0.1 的教训。
_ENTITIES = [
    ('&lt;', '<'), ('&gt;', '>'), ('&quot;', '"'),
    ('&#39;', "'"), ('&apos;', "'"), ('&nbsp;', ' '),
    ('&amp;', '&'),      # 放最后，防二次解码
]


def parse_video_ref(ref: str):
    """把用户输入解析成 (kind, id, page)。kind ∈ {'bv', 'av'}。

    接受：BV号 / av号 / 二者任意形式的 B站链接（可带 ?p=N）。
    纯函数不碰网络——av 号转 BV 要调接口，那件事在 run() 里做。

    大小写规则（实测结论，别改）：B站 API 对 BV 号大小写敏感——
    bvid=BV1a14y187qn 返回 code 0，整串大写成 BV1A14Y187QN 返回
    code -404。所以前缀「BV/bv」大小写不敏感地匹配并规范成大写，
    后缀 10 个字符一律原样保留，绝不做整串 upper()。
    """
    ref = (ref or '').strip()
    if not ref:
        raise ValueError("输入为空：请给 BV号、av号或 B站视频链接")
    page = None
    m = re.search(r'[?&]p=(\d+)', ref)
    if m:
        page = int(m.group(1))
    m = re.search(r'(BV[a-zA-Z0-9]{10})', ref, re.I)
    if m:
        bvid = m.group(1)
        if not bvid.startswith('BV'):       # bv/Bv/bV → BV，后缀原样
            bvid = 'BV' + bvid[2:]
        return ('bv', bvid, page)
    m = re.search(r'\bav(\d+)\b', ref, re.I)
    if m:
        return ('av', m.group(1), page)
    raise ValueError(f"无法从 '{ref}' 识别 BV号/av号/B站链接")


def _unescape_entities(s: str) -> str:
    """解码常见 HTML 实体（见 _ENTITIES 注释：&amp; 最后解）。"""
    for k, v in _ENTITIES:
        s = s.replace(k, v)
    return s


def split_transcript(text: str, max_chars: int) -> list:
    """按自然段边界把长字幕切块，单块尽量不超 max_chars。

    切点优先落在段尾（空行）；单段本身就超限时按句末标点切；
    单句仍超限才按长度硬切。绝不从半句话中间下刀——Agent 读到
    半句话会顺着猜，猜出来的内容会以「视频里说过」的身份混进总结。
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return [text] if text else []
    chunks, buf = [], ''
    for para in text.split('\n\n'):
        para = para.strip()
        if not para:
            continue
        if len(buf) + len(para) + 2 <= max_chars:
            buf = f"{buf}\n\n{para}" if buf else para
            continue
        if buf:
            chunks.append(buf)
            buf = ''
        if len(para) <= max_chars:
            buf = para
            continue
        # 单段超限：按句末标点切
        sents = re.findall(r'[^。！？!?…]+[。！？!?…]?|[^。！？!?…]+$', para)
        for s in sents:
            s = s.strip()
            if not s:
                continue
            if len(buf) + len(s) <= max_chars:
                buf += s
            else:
                if buf:
                    chunks.append(buf)
                # 单句仍超限：硬切（保底，不丢内容）
                while len(s) > max_chars:
                    chunks.append(s[:max_chars])
                    s = s[max_chars:]
                buf = s
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


# ============================================================
# 字幕清洗（本脚本的核心价值之一）
# ============================================================

_SRT_INDEX = re.compile(r'^\d+$')                                   # SRT 序号行："12"
_SRT_TIME = re.compile(                                             # SRT 时间戳行：
    r'^\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*'
    r'\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}')                              # "00:00:01,000 --> 00:00:03,000"
_HTML_TAG = re.compile(r'<[^>]+>')                                  # <font color=...> 等标签残留
# ASR 幻觉字符（v1.0.1 实跑教训）：SenseVoice 遇到纯音乐间奏/无人声片段时，
# 会吐出日文假名、韩文字节和音乐符号当「文本」——路演、MV、课程片头全是这种。
# 这些字符对总结零信息量，还会带偏 LLM。整行去完这些后若不含任何
# 汉字/拉丁字母/数字，判定为幻觉行直接丢。
_HALLUCINATION = re.compile(
    '[\u3040-\u30ff\u31f0-\u31ff'      # 日文平假名 / 片假名 / 假名扩展
    '\uac00-\ud7af'                    # 韩文字母音节（가-힣）
    '\U0001F3BC'                       # 🎼
    '\U0001F3B5-\U0001F3B9'            # 🎵🎶🎷🎸🎹
    '\U0001F941'                       # 🥁
    '\u2669-\u266c'                    # ♩♪♫♬
    '\U0001D15E-\U0001D167'            # 𝄞-𝄧 音乐符号块
    ']')
_HAS_CONTENT = re.compile(r'[\u4e00-\u9fffA-Za-z0-9]')             # 汉字 / 拉丁字母 / 数字
_SENT_END = '。！？!?…'                                             # 句末标点
_QUOTE_END = '」』”）)"\''                                          # 后引号（句末标点+后引号也算段尾）
_MIN_OVERLAP = 4          # 滚动重叠去重的最小长度：短于这个不切，防止误伤正常行文
_MIN_PARA = 60            # 自然段最小字数：不足则继续并入下一句，避免一句一段太碎
_MAX_PARA = 120           # 单段超过这个字数就强制断段（防止 ASR 一整段不换行）


def _overlap_len(prev: str, cur: str) -> int:
    """返回 cur 开头与 prev 结尾重复部分的最长长度；无足够长的重叠返回 0。

    为什么需要它：B站自动字幕（ai-zh）是滚动式上屏的——一句话说完后，
    下一句上屏时会把上一句的结尾几个字再显示一遍。逐条取 content 拼成的
    文本里，同一片段会出现两次。Agent 读到会把重复当成口播强调，
    写总结时跟着重复，读者还以为视频里真的强调了两遍。
    只切「足够长」的重叠（>= _MIN_OVERLAP），是宁可漏切也不错切：
    正常口播极少连续 4 个字与上一行结尾完全相同。
    """
    limit = min(len(prev), len(cur))
    for k in range(limit, _MIN_OVERLAP - 1, -1):
        if prev.endswith(cur[:k]):
            return k
    return 0


def _join_piece(a: str, b: str) -> str:
    """拼接两段文本。中英文/数字交界补一个空格，纯中文直接相连。"""
    if a and b and a[-1].isascii() and a[-1].isalnum() \
            and b[0].isascii() and b[0].isalnum():
        return ' ' + b
    return b


def _para_end(s: str) -> bool:
    """这一段是否可以收尾（以句末标点结尾，或句末标点+后引号）。"""
    if not s:
        return False
    if s[-1] in _SENT_END:
        return True
    if s[-1] in _QUOTE_END and len(s) >= 2 and s[-2] in _SENT_END:
        return True
    return False


def clean_transcript(raw: str) -> str:
    """把字幕原料清洗成适合 LLM 阅读的纯文本。

    处理五类问题：
    1. SRT 结构残留（序号行 + 时间戳行）——用户用 --subtitle 传入的可能是 SRT；
    2. ASR 幻觉字符——纯音乐间奏被 SenseVoice 转成日韩假名/音乐符号，见 _HALLUCINATION 注释；
    3. 相邻重复行——自动字幕滚动上屏的固有问题，见 _overlap_len 注释；
    4. 过碎短句——CC 字幕一条常只有几个字，直接喂给 LLM 语义会断裂，
       按「句末标点或长度上限」合并成自然段；
    5. 空白噪声——连续空格、全角空格、首尾空白、中文标点前的多余空格。
    """
    # --- 第一遍：逐行去结构、去标签、去 ASR 幻觉、归一化空白 ---
    # v1.1 起同时支持 SRT 与 VTT：B站/YouTube 下载的字幕文件大量是 VTT，
    # 不认 WEBVTT 头、NOTE 块和 cue 标识符的话，这些结构文本会以「正文」
    # 身份混进总结原料（WEBVTT 里有拉丁字母，去幻觉那关还拦不住它）。
    raw_lines = raw.splitlines()
    lines = []
    in_note_block = False
    for idx, ln in enumerate(raw_lines):
        ln = ln.strip()
        if not ln:
            in_note_block = False
            continue
        if in_note_block:                          # NOTE/STYLE/REGION 块内，跳过
            continue
        head = ln.upper()
        if head.startswith(('NOTE', 'STYLE', 'REGION')):
            in_note_block = True
            continue
        if head.startswith('WEBVTT'):              # 文件头（WEBVTT - 标题）
            continue
        if _SRT_INDEX.match(ln):        # SRT 序号行
            continue
        if _SRT_TIME.match(ln):         # SRT/VTT 时间戳行（VTT 的毫秒用点，正则已兼容）
            continue
        # VTT cue 标识符：时间戳行前面那一行独立文本（数字或名称）。
        # 判据是「下一行是时间戳」——格式良好的 VTT 块间有空行，内容行不会
        # 直接贴在同一条时间戳前面；宁可放过极少数畸形输入，不错杀正文行。
        nxt = raw_lines[idx + 1].strip() if idx + 1 < len(raw_lines) else ''
        if nxt and _SRT_TIME.match(nxt):
            continue
        ln = _HTML_TAG.sub('', ln)      # 去 <font>/<i>/<v Speaker> 等标签残留
        ln = _unescape_entities(ln)     # 去 &amp;/&lt; 等实体（VTT 常见）
        ln = _HALLUCINATION.sub('', ln) # 去日韩假名/音乐符号（ASR 对纯音乐段的幻觉）
        # 去完幻觉字符后，这一行若没有任何 汉字/字母/数字，就是纯噪音行，整行丢。
        # 宁保守勿激进：只要还剩一个汉字或字母就保留（歌词、中英混排都在保护范围）。
        if not _HAS_CONTENT.search(ln):
            continue
        ln = re.sub(r'\s+', ' ', ln)    # 行内连续空白（含全角空格）压成一个
        ln = ln.strip()
        if ln:
            lines.append(ln)

    # --- 第二遍：去相邻重复行 + 去滚动重叠 ---
    deduped = []
    for ln in lines:
        if deduped:
            prev = deduped[-1]
            if ln == prev:                      # 整行重复：跳过
                continue
            cut = _overlap_len(prev, ln)        # 开头是上一行结尾的重复：切掉
            if cut:
                ln = ln[cut:]
                if not ln:
                    continue
        deduped.append(ln)

    # --- 第三遍：合并过碎短句成自然段 ---
    # 句末标点且攒够 _MIN_PARA 字才断段；防止字幕一句一段读起来太碎
    paras = []
    buf = ''
    for ln in deduped:
        buf = buf + _join_piece(buf, ln) if buf else ln
        if (_para_end(buf) and len(buf) >= _MIN_PARA) or len(buf) >= _MAX_PARA:
            paras.append(buf)
            buf = ''
    if buf:
        paras.append(buf)

    # --- 第四遍：段落级归一化 ---
    out = []
    for p in paras:
        p = re.sub(r'\s+', ' ', p).strip()
        p = re.sub(r' +([，。！？；：、」』）)])', r'\1', p)   # 中文标点前的空格
        p = p.replace(' ,', ',').replace(' .', '.')
        if p:
            out.append(p)
    return ('\n\n'.join(out) + '\n') if out else ''


# ============================================================
# 复用 bili-subtitle-extractor（不重写字幕链路）
# ============================================================

def load_extractor():
    """importlib 动态加载参照一的脚本，复用它的字幕链路与 http 工具。"""
    if not EXTRACTOR_PY:
        raise RuntimeError(
            "找不到 bili-subtitle-extractor/bili_subtitle_skill.py，"
            "请设置环境变量 BILI_SUBTITLE_EXTRACTOR_PY 指向它")
    spec = importlib.util.spec_from_file_location("bili_subtitle_skill", EXTRACTOR_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch_extra_meta(mod, bvid: str) -> dict:
    """补充分区与简介（参照一的 get_video_info 不返回这两项）。

    复用它的 http_get，不重写请求逻辑。失败不致命，降级为空串。
    """
    try:
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        data = json.loads(mod.http_get(url))["data"]
        return {"tname": data.get("tname", ""), "desc": data.get("desc", "")}
    except Exception:
        return {"tname": "", "desc": ""}


def fmt_duration(seconds) -> str:
    """秒 → mm:ss 或 h:mm:ss。"""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "未知"
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _require_ffmpeg() -> None:
    """ASR 路径的前置依赖预检（v1.0.1 实跑教训）。

    无 CC 字幕时，参照一会走「下载音频 → ffmpeg/ffprobe 分块转码 → ASR」。
    这两个可执行文件不在 PATH 上时，下游抛的是 WinError 2 这类操作系统级
    错误，用户完全看不出缺什么、去哪装。在上游就检掉，给一条能照着做的
    安装指引——错误信息也是产品体验的一部分。
    """
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(
            f"该视频无 CC 字幕，转写需要 {' 和 '.join(missing)}，但未在 PATH 中找到。\n"
            "  安装方式（任选一，装完重开终端让 PATH 生效）：\n"
            "    Windows:  winget install Gyan.FFmpeg\n"
            "              （或到 ffmpeg.org 下载解压，把 bin 目录加入 PATH）\n"
            "    macOS:    brew install ffmpeg\n"
            "    Linux:    sudo apt install ffmpeg\n"
            "  也可以先用 --subtitle 提供已有字幕文件跳过这一步。")


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', s).strip()[:50]


def _display_path(p: str) -> str:
    """指引里显示路径：能相对当前目录就相对，跳出去就给绝对路径。"""
    try:
        rp = os.path.relpath(p, os.getcwd())
        return p if rp.startswith('..') else rp
    except ValueError:
        return p


# ============================================================
# 提示词组装
# ============================================================

def build_brief_input(system_text: str, user_tpl: str, fields: dict,
                      note: str = None) -> str:
    """system 全文 + 填好占位符的 user，合成 Agent 可直接使用的输入包。

    占位符用逐个 replace 而非 str.format：模板里可能出现其他花括号
    （ASCII 图、代码示例），format 会误伤。

    note：文件头部的用法说明注释。默认（None）是单包模式的三步指引；
    分段/归并文件由 run() 传各自的说明（见「长视频分段模式」）。
    """
    user_text = user_tpl
    for key, val in fields.items():
        user_text = user_text.replace("{" + key + "}", str(val))
    if note is None:
        note = ("用法：把「第一部分」整体作为 system prompt，「第二部分」整体作为 "
                "user prompt，交给任意 LLM Agent。Agent 产出的 markdown 保存为同目录 "
                "brief.md，再运行 render.py 生成 HTML。")
    return (
        "<!-- video-brief · 总结用输入包 -->\n"
        f"<!-- {note} -->\n\n"
        "# 第一部分 · 系统提示词（system prompt）\n\n"
        f"{system_text.strip()}\n\n"
        "---\n\n"
        "# 第二部分 · 用户提示词（user prompt）\n\n"
        f"{user_text.strip()}\n"
    )


# ============================================================
# 主流程
# ============================================================

def run(ref: str, out_root: str = None, subtitle_file: str = None,
        page_num: int = None, chunk_chars: int = None) -> str:
    # v1.1：输入从「必须是 BV号」放宽到 BV号/av号/任意 B站链接（可带 ?p=N）
    kind, vid, url_page = parse_video_ref(ref)
    if chunk_chars is None:
        chunk_chars = DEFAULT_CHUNK_CHARS
    if page_num is None:
        page_num = url_page        # 命令行没显式给分 P 时，URL 里的 ?p= 生效

    mod = load_extractor()
    print(f"输入 = {ref}" + (f"  P{page_num}" if page_num else ""))

    # av 号 → BV 号：B站查接口认 aid，但字幕链路按 BV 组织，先转再走
    if kind == 'av':
        try:
            data = json.loads(mod.http_get(f"https://api.bilibili.com/x/web-interface/view?aid={vid}"))["data"]
            bvid = (data or {}).get("bvid") or ""
        except Exception as e:
            raise RuntimeError(f"av{vid} 转 BV 号失败（{e}）。请直接提供 BV号或视频链接")
        if not BV_RE.match(bvid):
            raise RuntimeError(f"av{vid} 转出的 BV 号无效：{bvid!r}。请直接提供 BV号或视频链接")
        print(f"  av{vid} → {bvid}")
    else:
        bvid = vid

    # 1. 视频元信息（复用参照一；拿不到就降级，不阻断已有字幕的流程）
    print("[1/4] 获取视频元信息...")
    try:
        info = mod.get_video_info(bvid)
        extra = fetch_extra_meta(mod, bvid)
        title = info["title"]
        up_name = info["author"]
        duration_s = info["duration"]
    except Exception as e:
        print(f"  WARNING: 元信息获取失败（{e}），使用降级值")
        info = None
        extra = {"tname": "", "desc": ""}
        title = bvid
        up_name = "未知UP主"
        duration_s = 0

    # 2. 拿字幕：优先用户提供的文件，否则走参照一的链路
    print("[2/4] 获取字幕...")
    if subtitle_file:
        print(f"  使用已有字幕文件: {subtitle_file}")
        with open(subtitle_file, "r", encoding="utf-8") as f:
            raw_text = f.read()
        source = "user-provided"
    else:
        # 先探测有无 CC 字幕：既决定 source 标注，也决定要不要走 ASR
        try:
            probe = mod.get_subtitle_list(bvid, info["cid"])
        except Exception:
            probe = []
        if not probe and not os.environ.get("SILICONFLOW_API_KEY"):
            raise RuntimeError(
                "该视频没有 CC 字幕，需要走 ASR，但环境变量 "
                "SILICONFLOW_API_KEY 未设置。请设置后重试，"
                "或用 --subtitle 提供已有字幕文件")
        if not probe:
            # 无字幕 = 必然走 ASR = 必然要 ffmpeg 分块。先检掉，
            # 别让用户撞上 WinError 2 这种看不懂的系统错误。
            _require_ffmpeg()
        source = "cc" if probe else "asr"
        ext_dir = mod.extract(bvid, page_num)   # 有字幕直接下，无字幕 DASH+ASR
        with open(os.path.join(ext_dir, "subtitle.txt"), "r", encoding="utf-8") as f:
            raw_text = f.read()

    # 3. 清洗
    print("[3/4] 清洗字幕（去时间戳/去结构残留/去重复/合并碎句）...")
    transcript = clean_transcript(raw_text)
    if not transcript.strip():
        raise RuntimeError("清洗后字幕为空：请检查字幕文件是否有内容")
    print(f"  原始 {len(raw_text)} 字 → 清洗后 {len(transcript)} 字")

    # 4. 输出「总结用输入包」
    print("[4/4] 生成总结用输入包...")
    default_root = os.path.join(SKILL_DIR, "output")
    out_dir = os.path.join(out_root or default_root, f"{bvid}_{safe_name(title)}")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "transcript.txt"), "w", encoding="utf-8") as f:
        f.write(transcript)

    # v1.1 长视频分段：超过阈值就走 map-reduce 两阶段提取。
    # 切段只影响「提示词怎么组织」，transcript.txt 始终是全量留档。
    chunks = split_transcript(transcript.strip(), chunk_chars)
    chunked = len(chunks) > 1

    meta = {
        "bvid": bvid,
        "title": title,
        "up_name": up_name,
        "duration": fmt_duration(duration_s),
        "duration_seconds": duration_s,
        "tname": extra.get("tname", ""),
        "desc": extra.get("desc", ""),
        "url": VIDEO_URL.format(bvid=bvid),
        "subtitle_source": source,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "chunked": chunked,
        "chunk_count": len(chunks),
        "chunk_chars": chunk_chars if chunked else None,
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    def _read_prompt(name: str) -> str:
        with open(os.path.join(SKILL_DIR, "prompts", name), "r", encoding="utf-8") as f:
            return f.read()

    system_text = _read_prompt("brief-system.md")
    source_note = {
        "cc": "UP主提供的CC字幕（人工或官方生成，准确率较高，"
              "但可能有漏句和口误省略）",
        "asr": "AI语音识别自动生成（无CC字幕时走此路），专有名词、"
               "人名、数字、中英混说处出错概率较高",
        "user-provided": "使用者提供的外部字幕文件，来源与准确率未知",
    }[source]

    brief_md = os.path.join(out_dir, "brief.md")

    if not chunked:
        # ---- 单包模式：一份 brief-input.md 全包 ----
        user_tpl = _read_prompt("brief-user.md")
        brief_input = build_brief_input(system_text, user_tpl, {
            "title": title,
            "up_name": up_name,
            "duration": meta["duration"],
            "tname": extra.get("tname", "") or "未知",
            "url": meta["url"],
            "subtitle_note": source_note,
            "transcript": transcript.strip(),
        })
        with open(os.path.join(out_dir, "brief-input.md"), "w", encoding="utf-8") as f:
            f.write(brief_input)

        print(f"\n[DONE] 输入包已生成: {out_dir}")
        print(f"  transcript.txt   ← 清洗后的字幕（{len(transcript)} 字）")
        print(f"  meta.json        ← 视频元信息")
        print(f"  brief-input.md   ← 完整提示词（system + user）")
        print("\n[NEXT] 接下来：")
        print(f"  1. 把 brief-input.md 的第一部分作为 system prompt、第二部分作为 user prompt，")
        print(f"     交给任意 LLM Agent，得到教学式总结（markdown）")
        print(f"  2. 把总结保存为: {brief_md}")
        print(f"  3. 运行: python render.py {_display_path(brief_md)}")
        print(f"     （render.py 会自动读取同目录 meta.json 生成视频信息头）")
        return out_dir

    # ---- 分段模式（v1.1）：map 每段一份提示词，reduce 一份归并提示词 ----
    print(f"  字幕 {len(transcript)} 字超过分段阈值 {chunk_chars}，"
          f"切成 {len(chunks)} 段走两阶段提取")
    chunk_user_tpl = _read_prompt("brief-chunk-user.md")
    merge_user_tpl = _read_prompt("brief-merge-user.md")
    chunks_dir = os.path.join(out_dir, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)

    for i, ch in enumerate(chunks, 1):
        chunk_input = build_brief_input(system_text, chunk_user_tpl, {
            "title": title,
            "seg_no": i,
            "seg_total": len(chunks),
            "subtitle_note": source_note,
            "transcript": ch,
        }, note=(f"第 {i}/{len(chunks)} 段的分段笔记任务：把「第一部分」作为 system "
                 f"prompt、「第二部分」作为 user prompt 交给 LLM Agent，产出该段的"
                 f"分段笔记（markdown），保存为同目录 note-{i}.md。"))
        with open(os.path.join(chunks_dir, f"chunk-{i}.md"), "w", encoding="utf-8") as f:
            f.write(chunk_input)

    merge_input = build_brief_input(system_text, merge_user_tpl, {
        "title": title,
        "chunk_count": len(chunks),
        "chunk_notes": (
            f"（此处粘贴 {len(chunks)} 份分段笔记：按 note-1.md 到 "
            f"note-{len(chunks)}.md 的顺序，整份替换本段说明）"),
    }, note=("归并阶段：先把「第二部分」「分段笔记」一节的占位说明替换成按顺序贴齐的"
             "全部分段笔记，再把「第一部分」作为 system prompt、「第二部分」作为 "
             "user prompt 交给 LLM Agent，产出最终教学式总结（markdown），保存为同目录 "
             "brief.md，再运行 render.py 生成 HTML。"))
    with open(os.path.join(out_dir, "brief-merge.md"), "w", encoding="utf-8") as f:
        f.write(merge_input)

    # brief-input.md 在分段模式下降级为使用说明（提示词本体在 chunks/ 与 brief-merge.md）
    usage = f"""# video-brief · 长视频分段模式使用说明

本视频清洗后字幕 {len(transcript)} 字，超过分段阈值 {chunk_chars} 字，已切成
{len(chunks)} 段走两阶段提取（map-reduce）。单 prompt 塞进整段长视频，要么爆上下文，
要么模型 lost-in-the-middle 丢掉中段；分段提炼再归并，是 bili-subtitle 项目
验证过的路子。

## 产物清单

| 文件 | 是什么 |
| --- | --- |
| `transcript.txt` | 清洗后的全量字幕（留档，不直接进提示词） |
| `meta.json` | 视频元信息 |
| `chunks/chunk-1.md` … `chunks/chunk-{len(chunks)}.md` | 每段一份提示词（system + user） |
| `chunks/note-1.md` … `note-{len(chunks)}.md` | 分段笔记（你跑 Agent 后自己保存） |
| `brief-merge.md` | 归并阶段提示词（占位说明待替换） |

## 两步流程

1. **分段提炼（map）**：把 `chunks/chunk-1.md` 到 `chunks/chunk-{len(chunks)}.md`
   依次交给 LLM Agent（每份的第一部分作 system prompt、第二部分作 user prompt），
   每份产出该段的分段笔记（markdown），保存为 `chunks/note-1.md` 到
   `chunks/note-{len(chunks)}.md`。
2. **归并成稿（reduce）**：打开 `brief-merge.md`，把 {len(chunks)} 份笔记按顺序
   粘贴进「分段笔记」一节（替换占位说明），整体交给 LLM Agent，
   得到最终教学式总结（markdown）。
3. 把总结保存为 `brief.md`，运行 `python render.py brief.md` 生成 HTML
   （render.py 会自动读取同目录 meta.json 生成视频信息头）。

分段阈值可用 `--chunk-chars` 调整；`--chunk-chars 0` 关闭分段，回到单包模式。
"""
    with open(os.path.join(out_dir, "brief-input.md"), "w", encoding="utf-8") as f:
        f.write(usage)

    print(f"\n[DONE] 输入包已生成: {out_dir}")
    print(f"  transcript.txt      ← 清洗后的字幕（{len(transcript)} 字，全量留档）")
    print(f"  meta.json           ← 视频元信息（chunked=true，{len(chunks)} 段）")
    print(f"  chunks/chunk-*.md   ← {len(chunks)} 份分段提示词（分段提炼用）")
    print(f"  brief-merge.md      ← 归并提示词（笔记粘贴后出终稿）")
    print(f"  brief-input.md      ← 使用说明（两步流程）")
    print("\n[NEXT] 长视频分段模式：")
    print(f"  1. 依次把 chunks/chunk-1.md … chunk-{len(chunks)}.md 交给 LLM Agent，")
    print(f"     每份产出分段笔记，保存为 chunks/note-1.md … note-{len(chunks)}.md")
    print(f"  2. 把 {len(chunks)} 份笔记按顺序粘贴进 brief-merge.md 第二部分的")
    print(f"     「分段笔记」一节，整体交给 LLM Agent，得到最终教学式总结")
    print(f"  3. 把总结保存为: {brief_md}")
    print(f"  4. 运行: python render.py {_display_path(brief_md)}")
    return out_dir


def main():
    ap = argparse.ArgumentParser(
        prog="video_brief.py",
        description="video-brief 第一步：BV号/av号/链接 → 字幕 → 清洗 → 生成「总结用输入包」。"
                    "总结内容由 Agent 按教学方法论撰写，本脚本不写总结。",
        epilog="示例: python video_brief.py BV1a14y187qn  "
               "python video_brief.py BV1a14y187qn --subtitle ./已有字幕.vtt  "
               "python video_brief.py https://www.bilibili.com/video/BV1a14y187qn/?p=3",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref", nargs="?", help="B站视频BV号/av号/视频链接（链接可带 ?p=N）")
    ap.add_argument("--out", default=None,
                    help="输出根目录（默认 <skill目录>/output）")
    ap.add_argument("--subtitle", default=None,
                    help="已有字幕文件（.srt/.vtt/.txt），提供则跳过抓取")
    ap.add_argument("--page", type=int, default=None,
                    help="多P视频的分P号（默认第1P；链接里带 ?p=N 时以链接为准）")
    ap.add_argument("--chunk-chars", type=int, default=None, metavar="N",
                    help="长视频分段阈值：清洗后字幕超过 N 字就切段做两阶段提取"
                         "（默认 15000；0 = 关闭分段，强制单包）")
    args = ap.parse_args()

    if not args.ref:
        ap.print_help()
        sys.exit(2)

    try:
        run(args.ref, out_root=args.out, subtitle_file=args.subtitle,
            page_num=args.page, chunk_chars=args.chunk_chars)
    except Exception as e:
        print(f"\n[FAILED] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
