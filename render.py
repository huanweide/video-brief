"""
video-brief · 第二步：渲染
把 Agent 写好的 markdown 总结渲染成单文件 HTML（CSS 内联，拷走一个文件就能看）。

零第三方依赖：内置一个轻量 markdown 转换器，覆盖教学材料需要的元素——
标题层级、列表（含一级嵌套）、围栏代码块、表格、粗体/斜体、行内代码、
引用块、链接、水平线。不追求完整 CommonMark，追求「教学总结不丢元素」。

用法:
  python render.py output/BV1xx_标题/brief.md            # 产出同目录 brief.html
  python render.py brief.md -o out.html --title "标题"
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(SKILL_DIR, "assets", "style.css")
VIDEO_URL = "https://www.bilibili.com/video/{bvid}"

# ============================================================
# 轻量 markdown → HTML
# ============================================================

_HEADING = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
_LIST = re.compile(r'^(\s*)(?:([-*+])|(\d+)[.)、])\s+(.*)$')
_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$')
_HR = re.compile(r'^\s*(?:-{3,}|\*{3,}|_{3,})\s*$')
_CODE_INLINE = re.compile(r'`([^`]+)`')
_LINK = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')
_BOLD = re.compile(r'\*\*(.+?)\*\*|__(.+?)__')
_ITALIC = re.compile(r'\*(.+?)\*|_(.+?)_')


def _inline(text: str) -> str:
    """行内转换：先转义 HTML，再处理 code/链接/粗体/斜体。

    行内代码用占位符保护，防止其中的 * [ ] 被后续规则误伤。
    """
    text = html.escape(text, quote=False)
    # 1. 行内代码（占位保护）
    codes = []

    def _stash_code(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"
    text = _CODE_INLINE.sub(_stash_code, text)
    # 2. 链接
    def _link(m):
        label, url = m.group(1), m.group(2)
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'
    text = _LINK.sub(_link, text)
    # 3. 粗体 → 4. 斜体
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    # 5. 还原行内代码
    text = re.sub(r'\x00(\d+)\x00',
                  lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)
    return text


def _split_row(line: str):
    """拆表格行：去掉首尾竖线后按 | 切，空 cell 保留。"""
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return [c.strip() for c in s.split('|')]


def markdown_to_html(md: str) -> str:
    """块级转换。按行扫描，状态：普通 / 代码围栏 / 表格 / 引用 / 列表。"""
    lines = md.split('\n')
    out = []
    i = 0
    list_stack = []   # [(indent, 'ul'|'ol')]，记录开启中的列表

    def close_lists_to(indent):
        # 只闭合缩进严格大于 indent 的列表层（各自补 </li></ul>）；
        # 同层的 </li> 由调用方单独处理
        while list_stack and list_stack[-1][0] > indent:
            out.append('</li>')
            out.append(f"</{list_stack.pop()[1]}>")

    def close_all_lists():
        while list_stack:
            out.append('</li>')
            out.append(f"</{list_stack.pop()[1]}>")

    para_buf = []

    def end_para():
        nonlocal para_buf
        if para_buf:
            out.append('<p>' + _inline(' '.join(para_buf)) + '</p>')
            para_buf = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ---- 空行：收掉段落和列表 ----
        if not stripped:
            end_para()
            close_all_lists()
            i += 1
            continue

        # ---- 围栏代码块（ASCII 骨架图在这里，必须原样保留）----
        if stripped.startswith('```'):
            end_para()
            close_all_lists()
            lang = stripped[3:].strip()
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过收尾 ```
            cls = f' class="language-{html.escape(lang, quote=True)}"' if lang else ''
            body = html.escape('\n'.join(buf), quote=False)
            out.append(f"<pre><code{cls}>{body}</code></pre>")
            continue

        # ---- 表格（当前行含 | 且下一行是分隔行）----
        if '|' in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            end_para()
            close_all_lists()
            header = _split_row(line)
            i += 2
            rows = []
            while i < len(lines) and '|' in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            thead = '<tr>' + ''.join(
                f"<th>{_inline(c)}</th>" for c in header) + '</tr>'
            tbody = ''
            for r in rows:
                # 对齐列数：少则补空，多则截断
                r = (r + [''] * len(header))[:len(header)]
                tbody += '<tr>' + ''.join(
                    f"<td>{_inline(c)}</td>" for c in r) + '</tr>'
            out.append("<table><thead>" + thead +
                       "</thead><tbody>" + tbody + "</tbody></table>")
            continue

        # ---- 标题 ----
        m = _HEADING.match(line)
        if m:
            end_para()
            close_all_lists()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # ---- 水平线 ----
        if _HR.match(line):
            end_para()
            close_all_lists()
            out.append('<hr>')
            i += 1
            continue

        # ---- 引用块（块内递归转换，支持引用里再放粗体/列表）----
        if stripped.startswith('>'):
            end_para()
            close_all_lists()
            buf = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                buf.append(re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            inner = markdown_to_html('\n'.join(buf))
            out.append(f"<blockquote>{inner}</blockquote>")
            continue

        # ---- 列表（含一级嵌套）----
        m = _LIST.match(line)
        if m:
            end_para()
            indent = len(m.group(1).replace('\t', '    '))
            tag = 'ol' if m.group(3) else 'ul'
            close_lists_to(indent)
            if list_stack and list_stack[-1][0] == indent:
                out.append('</li>')          # 同层换项
            else:
                out.append(f"<{tag}>")       # 新开一层
                list_stack.append((indent, tag))
            out.append(f"<li>{_inline(m.group(4))}")
            i += 1
            continue

        # ---- 普通文本：并入段落（连续行合并为一段）----
        para_buf.append(stripped)
        i += 1

    end_para()
    close_all_lists()
    return ''.join(out)


# ============================================================
# 单文件 HTML 组装
# ============================================================

def _load_css() -> str:
    try:
        with open(CSS_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""

_HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
<article class="brief">
<header class="video-header">
<h1>{title}</h1>
<p class="meta">{meta_line}</p>
</header>
{body}
</article>
</body>
</html>
"""


def _build_meta_line(meta: dict, title: str) -> str:
    """视频信息头第二行：UP主 · BV号 · 原视频链接 · 生成时间。"""
    parts = []
    if meta.get("up_name"):
        parts.append(f"UP主：{html.escape(str(meta['up_name']))}")
    if meta.get("bvid"):
        bvid = str(meta["bvid"])
        url = meta.get("url") or VIDEO_URL.format(bvid=bvid)
        parts.append(f'BV号：{html.escape(bvid)}')
        parts.append(f'<a href="{html.escape(url, quote=True)}">原视频链接</a>')
    if meta.get("duration"):
        parts.append(f"时长：{html.escape(str(meta['duration']))}")
    if meta.get("tname"):
        parts.append(f"分区：{html.escape(str(meta['tname']))}")
    gen = meta.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(f"生成时间：{html.escape(str(gen))}")
    return ' · '.join(parts)


def _strip_leading_h1(md: str, title: str) -> str:
    """若 markdown 首个元素是与视频标题相同的 h1，去掉它（信息头已有标题）。"""
    lines = md.lstrip('\n').split('\n')
    if lines:
        m = _HEADING.match(lines[0])
        if m and len(m.group(1)) == 1:
            h1_text = m.group(2).strip()
            norm = lambda s: re.sub(r'\s+', '', s)
            if title and norm(h1_text) == norm(title):
                return '\n'.join(lines[1:]).lstrip('\n')
    return md


def render_to_html(md: str, meta: dict = None, title: str = None,
                   css: str = None) -> str:
    """markdown + 视频元信息 → 单文件 HTML。"""
    meta = dict(meta or {})
    title = meta.get("title") or title or "视频总结"
    md = _strip_leading_h1(md, title)
    body = markdown_to_html(md)
    css = css if css is not None else _load_css()
    return _HTML_TMPL.format(
        title=html.escape(title, quote=True),
        css=css,
        meta_line=_build_meta_line(meta, title),
        body=body,
    )


# ============================================================
# CLI
# ============================================================

def main():
    ap = argparse.ArgumentParser(
        prog="render.py",
        description="video-brief 第二步：把 brief.md 渲染成单文件 HTML"
                    "（CSS 内联，浅色清爽主题，可直接分享）。",
        epilog="示例: python render.py output/BV1xx_标题/brief.md  "
               "python render.py brief.md -o out.html --title \"标题\"",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("markdown", nargs="?", help="Agent 写好的总结 markdown 文件")
    ap.add_argument("-o", "--output", default=None,
                    help="输出 HTML 路径（默认与 markdown 同目录的 brief.html）")
    ap.add_argument("--title", default=None,
                    help="覆盖标题（默认读同目录 meta.json 的标题，其次文件名）")
    args = ap.parse_args()

    if not args.markdown:
        ap.print_help()
        sys.exit(2)

    md_path = args.markdown
    if not os.path.isfile(md_path):
        print(f"[FAILED] 文件不存在: {md_path}")
        sys.exit(1)

    with open(md_path, "r", encoding="utf-8") as f:
        md = f.read()

    # 同目录 meta.json 自动探测（video_brief.py 生成输入包时会带上）
    meta = {}
    meta_path = os.path.join(os.path.dirname(os.path.abspath(md_path)), "meta.json")
    if os.path.isfile(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    if args.title:
        meta["title"] = args.title

    out_path = args.output or os.path.join(
        os.path.dirname(os.path.abspath(md_path)), "brief.html")

    html_text = render_to_html(md, meta=meta, title=args.title)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(f"[DONE] {out_path}")
    print(f"  单文件 HTML（CSS 已内联，可直接分享）")


if __name__ == "__main__":
    main()
