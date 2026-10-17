"""render.py 的转换测试：纯标准库 unittest，不需要网络与第三方库。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import render  # noqa: E402


class TestMarkdownElements(unittest.TestCase):
    """markdown 基本元素都要能转。"""

    def setUp(self):
        self.meta = {"title": "测试视频", "up_name": "测试UP", "bvid": "BV1test0001",
                     "duration": "12:34", "generated_at": "2026-10-17 10:00"}

    def test_headings(self):
        md = "# 一级\n## 二级\n### 三级\n#### 四级"
        html = render.render_to_html(md, meta=self.meta)
        self.assertIn("<h2>二级</h2>", html)
        self.assertIn("<h3>三级</h3>", html)
        self.assertIn("<h4>四级</h4>", html)

    def test_lists(self):
        md = "- 甲\n- 乙\n  - 乙1\n  - 乙2\n- 丙\n\n1. 第一步\n2. 第二步"
        html = render.render_to_html(md, meta=self.meta)
        self.assertIn("<ul>", html)
        self.assertIn("<li>甲</li>", html)
        self.assertIn("<ol>", html)
        self.assertIn("<li>第一步</li>", html)
        # 嵌套：子列表嵌在父 li 内
        self.assertIn("<li>乙<ul>", html)
        self.assertIn("</ul></li>", html)

    def test_code_block(self):
        md = "```\n  [总问题]\n      │\n  [概念A]\n```"
        html = render.render_to_html(md, meta=self.meta)
        self.assertIn("<pre><code>", html)
        self.assertIn("[总问题]", html)
        self.assertIn("│", html)  # ASCII 骨架图原样保留

    def test_code_block_with_lang(self):
        md = "```python\nprint('hi')\n```"
        html = render.render_to_html(md, meta=self.meta)
        self.assertIn('<pre><code class="language-python">', html)

    def test_table(self):
        md = "| 内容 | 详略 |\n|---|---|\n| 核心定义 | 展开 |\n| 铺垫 | 一句带过 |"
        html = render.render_to_html(md, meta=self.meta)
        self.assertIn("<table>", html)
        self.assertIn("<thead>", html)
        self.assertIn("<tbody>", html)
        self.assertIn("<th>内容</th>", html)
        self.assertIn("<td>展开</td>", html)

    def test_blockquote(self):
        md = "> 上一块留下了什么问题\n> 这一块来解决它"
        html = render.render_to_html(md, meta=self.meta)
        self.assertIn("<blockquote>", html)
        self.assertIn("上一块留下了什么问题", html)

    def test_bold_italic_inline_code_link(self):
        md = "这是**粗体**和*斜体*，还有`行内代码`和[链接](https://example.com)。"
        html = render.render_to_html(md, meta=self.meta)
        self.assertIn("<strong>粗体</strong>", html)
        self.assertIn("<em>斜体</em>", html)
        self.assertIn("<code>行内代码</code>", html)
        self.assertIn('<a href="https://example.com">链接</a>', html)

    def test_paragraph_merge(self):
        md = "第一行\n第二行\n\n第二段"
        html = render.render_to_html(md, meta=self.meta)
        self.assertIn("<p>第一行 第二行</p>", html)
        self.assertIn("<p>第二段</p>", html)

    def test_html_escaped(self):
        md = "危险 <script>alert(1)</script> 内容"
        html = render.render_to_html(md, meta=self.meta)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class TestSingleFileHtml(unittest.TestCase):
    """产出必须是单文件：CSS 内联，无外链。"""

    def test_inline_css_no_external_link(self):
        md = "## 测试"
        html = render.render_to_html(md, meta={"title": "t"})
        self.assertIn("<style>", html)
        self.assertIn("</style>", html)
        self.assertNotIn('<link rel="stylesheet"', html)
        self.assertNotIn('href="http', html.replace("bilibili", ""))  # 无 meta 时也无外链 css

    def test_real_css_loaded(self):
        # 不传 css 参数时应读到 assets/style.css 的真实内容
        html = render.render_to_html("## 测试", meta={"title": "t"})
        self.assertIn("max-width: 720px", html)


class TestVideoHeader(unittest.TestCase):
    """视频信息头：标题、UP主、BV号、原视频链接、生成时间。"""

    def test_header_fields(self):
        md = "# 测试视频\n\n正文"
        meta = {"title": "测试视频", "up_name": "测试UP", "bvid": "BV1abc",
                "duration": "05:00", "generated_at": "2026-10-17 10:00"}
        html = render.render_to_html(md, meta=meta)
        self.assertIn('<header class="video-header">', html)
        self.assertIn("BV1abc", html)
        self.assertIn("https://www.bilibili.com/video/BV1abc", html)
        self.assertIn("UP主：测试UP", html)
        self.assertIn("生成时间：2026-10-17 10:00", html)

    def test_leading_h1_same_as_title_stripped(self):
        # markdown 首个 h1 与视频标题相同 → 去掉（信息头已有标题）
        html = render.render_to_html("# 同款标题\n\n正文",
                                     meta={"title": "同款标题", "bvid": "BV1x"})
        self.assertEqual(html.count("<h1>"), 1)  # 只剩信息头里的 h1

    def test_title_override(self):
        html = render.render_to_html("正文", meta={}, title="覆盖标题")
        self.assertIn("覆盖标题", html)


class TestEdgeCases(unittest.TestCase):
    """空输入 / 只有标题 / 超长文本不崩。"""

    def test_empty_input(self):
        html = render.render_to_html("", meta={})
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("</html>", html)

    def test_only_heading(self):
        html = render.render_to_html("## 只有一个标题", meta={})
        self.assertIn("<h2>只有一个标题</h2>", html)

    def test_very_long_text(self):
        md = "很长的一段话。" * 20000  # 约 12 万字
        html = render.render_to_html(md, meta={"title": "长文"})
        self.assertIn("<p>", html)
        self.assertGreater(len(html), 100000)

    def test_whitespace_only(self):
        html = render.render_to_html("\n\n   \n\n", meta={})
        self.assertIn("</html>", html)

    def test_meta_missing_all_fields(self):
        # meta 全空也能出头（只有生成时间）
        html = render.render_to_html("正文", meta={})
        self.assertIn("生成时间：", html)


class TestCleanTranscript(unittest.TestCase):
    """附带的字幕清洗逻辑测试（脚本核心价值之一）。"""

    def _clean(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import video_brief
        return video_brief.clean_transcript

    def test_srt_structure_removed(self):
        clean = self._clean()
        srt = ("1\n00:00:01,000 --> 00:00:03,000\n大家好。\n\n"
               "2\n00:00:03,000 --> 00:00:06,000\n今天讲缓存。\n")
        out = clean(srt)
        self.assertNotIn("-->", out)
        self.assertNotIn("00:00", out)
        self.assertIn("大家好。", out)
        self.assertIn("今天讲缓存。", out)

    def test_adjacent_duplicate_removed(self):
        clean = self._clean()
        out = clean("所以我们需要缓存\n所以我们需要缓存\n因为速度不够。")
        self.assertEqual(out.count("缓存"), 1)          # 重复行被去掉
        self.assertIn("缓存因为速度不够。", out)          # 剩余两行合并进同一自然段

    def test_scrolling_overlap_removed(self):
        # 自动字幕滚动上屏：下一句开头重复上一句结尾
        clean = self._clean()
        out = clean("今天我们一起学习Python\nPython是一门编程语言。")
        self.assertIn("今天我们一起学习Python是一门编程语言。", out)
        self.assertNotIn("PythonPython", out)

    def test_short_lines_merged(self):
        clean = self._clean()
        out = clean("缓存\n命中率\n很重要。")
        self.assertIn("缓存命中率很重要。", out)

    def test_empty(self):
        clean = self._clean()
        self.assertEqual(clean(""), "")
        self.assertEqual(clean("\n\n  \n"), "")

    def test_asr_music_hallucination_stripped(self):
        # v1.0.1 实跑教训：路演视频的纯音乐间奏被 SenseVoice 转成日韩假名+音乐符号
        clean = self._clean()
        out = clean("🎼以见我。아.🎼Inじ。Okay。六十。🎼你不能感觉。散就伞爱流过的美。")
        self.assertNotIn("아", out)          # 韩文幻觉去掉
        self.assertNotIn("じ", out)          # 日文假名幻觉去掉
        self.assertNotIn("🎼", out)          # 音乐符号去掉
        self.assertIn("以见我", out)         # 正文不误伤
        self.assertIn("你不能感觉", out)

    def test_pure_music_line_dropped(self):
        # 整行没有任何 汉字/字母/数字 → 纯噪音行，直接丢
        clean = self._clean()
        out = clean("🎼♪♫♩\n🎼🎵\n。，、！")
        self.assertEqual(out.strip(), "")

    def test_lyric_line_kept(self):
        # 中文歌词、中英混排是正常内容，不能误删
        clean = self._clean()
        out = clean("🎼 我不能感觉 散就伞爱流过的美 🎵 Love Me A Little")
        self.assertIn("我不能感觉", out)
        self.assertIn("Love Me A Little", out)


class TestRequireFfmpeg(unittest.TestCase):
    """ASR 前置依赖预检（v1.0.1 实跑教训：缺 ffmpeg 时下游报 WinError 2，用户看不懂）。"""

    def _require(self):
        import video_brief
        return video_brief._require_ffmpeg

    def test_missing_raises_with_install_hint(self):
        from unittest import mock
        import video_brief
        with mock.patch.object(video_brief.shutil, "which", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                self._require()()
        msg = str(ctx.exception)
        self.assertIn("ffmpeg", msg)
        self.assertIn("ffprobe", msg)
        # 报错必须给得出「照着做」的指引，而不是只报缺什么
        self.assertIn("winget install Gyan.FFmpeg", msg)
        self.assertIn("--subtitle", msg)

    def test_present_passes(self):
        from unittest import mock
        import video_brief
        with mock.patch.object(video_brief.shutil, "which",
                               return_value="C:/ffmpeg/bin/ffmpeg.exe"):
            self._require()()   # 两个都在，不抛

    def test_partial_missing_names_the_tool(self):
        from unittest import mock
        import video_brief
        def fake_which(tool):
            return "C:/ffmpeg/bin/ffmpeg.exe" if tool == "ffmpeg" else None
        with mock.patch.object(video_brief.shutil, "which", side_effect=fake_which):
            with self.assertRaises(RuntimeError) as ctx:
                self._require()()
        self.assertIn("ffprobe", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
