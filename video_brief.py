"""
video-brief · 视频总结器（第一步：备料）
BV号 → 拿字幕（复用 bili-subtitle-extractor）→ 清洗 → 生成「总结用输入包」

职责边界：本脚本不写总结内容。总结是 Agent 的活，本脚本只负责把原料备干净：
  1. transcript.txt   清洗后的纯文本字幕（给 Agent 读）
  2. meta.json        视频元信息（标题/UP主/时长/分区/简介）
  3. brief-input.md   填好占位符的完整提示词（system + user），Agent 直接拿着用

用法:
  python video_brief.py BV1xxxxxxx
  python video_brief.py BV1xxxxxxx --out ./myout
  python video_brief.py BV1xxxxxxx --subtitle ./已有字幕.srt
  python video_brief.py BV1xxxxxxx --page 3
"""
import argparse
import importlib.util
import json
import os
import re
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


# ============================================================
# 字幕清洗（本脚本的核心价值之一）
# ============================================================

_SRT_INDEX = re.compile(r'^\d+$')                                   # SRT 序号行："12"
_SRT_TIME = re.compile(                                             # SRT 时间戳行：
    r'^\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*'
    r'\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}')                              # "00:00:01,000 --> 00:00:03,000"
_HTML_TAG = re.compile(r'<[^>]+>')                                  # <font color=...> 等标签残留
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

    处理四类问题：
    1. SRT 结构残留（序号行 + 时间戳行）——用户用 --subtitle 传入的可能是 SRT；
    2. 相邻重复行——自动字幕滚动上屏的固有问题，见 _overlap_len 注释；
    3. 过碎短句——CC 字幕一条常只有几个字，直接喂给 LLM 语义会断裂，
       按「句末标点或长度上限」合并成自然段；
    4. 空白噪声——连续空格、全角空格、首尾空白、中文标点前的多余空格。
    """
    # --- 第一遍：逐行去结构、去标签、归一化空白 ---
    lines = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if _SRT_INDEX.match(ln):        # SRT 序号行
            continue
        if _SRT_TIME.match(ln):         # SRT 时间戳行
            continue
        ln = _HTML_TAG.sub('', ln)      # 去 <font>/<i> 等标签残留
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

def build_brief_input(system_text: str, user_tpl: str, fields: dict) -> str:
    """system 全文 + 填好占位符的 user，合成 Agent 可直接使用的输入包。

    占位符用逐个 replace 而非 str.format：模板里可能出现其他花括号
    （ASCII 图、代码示例），format 会误伤。
    """
    user_text = user_tpl
    for key, val in fields.items():
        user_text = user_text.replace("{" + key + "}", str(val))
    return (
        "<!-- video-brief · 总结用输入包 -->\n"
        "<!-- 用法：把「第一部分」整体作为 system prompt，「第二部分」整体作为 user prompt，"
        "交给任意 LLM Agent。Agent 产出的 markdown 保存为同目录 brief.md，"
        "再运行 render.py 生成 HTML。 -->\n\n"
        "# 第一部分 · 系统提示词（system prompt）\n\n"
        f"{system_text.strip()}\n\n"
        "---\n\n"
        "# 第二部分 · 用户提示词（user prompt）\n\n"
        f"{user_text.strip()}\n"
    )


# ============================================================
# 主流程
# ============================================================

def run(bvid: str, out_root: str = None, subtitle_file: str = None,
        page_num: int = None) -> str:
    if not BV_RE.match(bvid):
        raise ValueError(f"'{bvid}' 不是合法 BV号（应为 BV + 10 位字母数字）")

    mod = load_extractor()
    print(f"BV = {bvid}" + (f"  P{page_num}" if page_num else ""))

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
        source = "cc" if probe else "asr"
        ext_dir = mod.extract(bvid, page_num)   # 有字幕直接下，无字幕 DASH+ASR
        with open(os.path.join(ext_dir, "subtitle.txt"), "r", encoding="utf-8") as f:
            raw_text = f.read()

    # 3. 清洗
    print("[3/4] 清洗字幕（去时间戳/去重复/合并碎句）...")
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
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    with open(os.path.join(os.path.join(SKILL_DIR, "prompts", "brief-system.md")),
              "r", encoding="utf-8") as f:
        system_text = f.read()
    with open(os.path.join(os.path.join(SKILL_DIR, "prompts", "brief-user.md")),
              "r", encoding="utf-8") as f:
        user_tpl = f.read()

    source_note = {
        "cc": "UP主提供的CC字幕（人工或官方生成，准确率较高，"
              "但可能有漏句和口误省略）",
        "asr": "AI语音识别自动生成（无CC字幕时走此路），专有名词、"
               "人名、数字、中英混说处出错概率较高",
        "user-provided": "使用者提供的外部字幕文件，来源与准确率未知",
    }[source]

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

    # 下一步指引
    brief_md = os.path.join(out_dir, "brief.md")
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


def main():
    ap = argparse.ArgumentParser(
        prog="video_brief.py",
        description="video-brief 第一步：BV号 → 字幕 → 清洗 → 生成「总结用输入包」。"
                    "总结内容由 Agent 按教学方法论撰写，本脚本不写总结。",
        epilog="示例: python video_brief.py BV1a14y187qn  "
               "python video_brief.py BV1a14y187qn --subtitle ./已有字幕.srt",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bvid", nargs="?", help="B站视频BV号，如 BV1a14y187qn")
    ap.add_argument("--out", default=None,
                    help="输出根目录（默认 <skill目录>/output）")
    ap.add_argument("--subtitle", default=None,
                    help="已有字幕文件（.srt/.txt），提供则跳过抓取")
    ap.add_argument("--page", type=int, default=None,
                    help="多P视频的分P号（默认第1P）")
    args = ap.parse_args()

    if not args.bvid:
        ap.print_help()
        sys.exit(2)

    try:
        run(args.bvid, out_root=args.out, subtitle_file=args.subtitle,
            page_num=args.page)
    except Exception as e:
        print(f"\n[FAILED] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
