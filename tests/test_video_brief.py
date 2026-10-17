"""video_brief.py 的测试：纯函数 + 离线集成（mock 掉字幕提取器，不碰网络）。

覆盖 v1.1 新增面：
  - parse_video_ref：BV/av/链接（含 ?p=N）
  - _unescape_entities：&amp; 最后解，防二次解码
  - clean_transcript 的 VTT 兼容：WEBVTT 头 / NOTE 块 / cue 标识符 / 实体
  - split_transcript：段边界打包、句末切、硬切保底、不丢内容、顺序保持
  - run() 集成：单包模式与分段模式的产物结构、meta.json 字段
"""
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import video_brief as vb  # noqa: E402


class TestParseVideoRef(unittest.TestCase):
    """BV号 / av号 / B站链接 → (kind, id, page)。"""

    def test_bvid_plain(self):
        self.assertEqual(vb.parse_video_ref("BV1a14y187qn"),
                         ("bv", "BV1a14y187qn", None))

    def test_bvid_case_insensitive(self):
        # 前缀大小写不敏感：bv/Bv/bV 都认，规范成 BV
        self.assertEqual(vb.parse_video_ref("bv1a14y187qn"),
                         ("bv", "BV1a14y187qn", None))
        self.assertEqual(vb.parse_video_ref("Bv1a14y187qn"),
                         ("bv", "BV1a14y187qn", None))

    def test_suffix_case_preserved(self):
        # 回归防线：实测 bvid=BV1A14Y187QN → API code -404。
        # BV号大小写敏感，parse 层绝不整串 upper()「修正」
        self.assertEqual(vb.parse_video_ref("BV1A14Y187QN"),
                         ("bv", "BV1A14Y187QN", None))

    def test_url_with_page(self):
        ref = "https://www.bilibili.com/video/BV1a14y187qn/?p=3"
        self.assertEqual(vb.parse_video_ref(ref), ("bv", "BV1a14y187qn", 3))

    def test_url_with_extra_params_page(self):
        ref = "https://www.bilibili.com/video/BV1a14y187qn/?spm_id_from=333&p=2"
        self.assertEqual(vb.parse_video_ref(ref), ("bv", "BV1a14y187qn", 2))

    def test_url_without_page(self):
        ref = "https://www.bilibili.com/video/BV1a14y187qn/"
        self.assertEqual(vb.parse_video_ref(ref), ("bv", "BV1a14y187qn", None))

    def test_av_number(self):
        self.assertEqual(vb.parse_video_ref("av170001"), ("av", "170001", None))

    def test_av_url(self):
        ref = "https://www.bilibili.com/video/av170001"
        self.assertEqual(vb.parse_video_ref(ref), ("av", "170001", None))

    def test_av_url_with_page(self):
        ref = "https://www.bilibili.com/video/av170001/?p=5"
        self.assertEqual(vb.parse_video_ref(ref), ("av", "170001", 5))

    def test_bvid_wins_over_av_substring(self):
        # BV 号里可能含 "av" 字母组合，BV 规则先命中，不能被误判成 av号
        ref = "https://www.bilibili.com/video/BV1av4111c7d/"
        self.assertEqual(vb.parse_video_ref(ref), ("bv", "BV1av4111c7d", None))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            vb.parse_video_ref("")
        with self.assertRaises(ValueError):
            vb.parse_video_ref("   ")

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            vb.parse_video_ref("这不是一个视频链接")
        with self.assertRaises(ValueError):
            vb.parse_video_ref("https://www.bilibili.com/")


class TestUnescapeEntities(unittest.TestCase):
    """HTML 实体解码：&amp; 必须最后解（v9.0.1 二次解码教训的回归防线）。"""

    def test_amp_decodes_last(self):
        # '&amp;lt;' 只能解成 '&lt;'，再解一次才会变 '<'——那正是要防的
        self.assertEqual(vb._unescape_entities("&amp;lt;"), "&lt;")

    def test_basic_entities(self):
        self.assertEqual(vb._unescape_entities("&lt;b&gt;"), "<b>")
        self.assertEqual(vb._unescape_entities("&quot;x&quot;"), '"x"')
        self.assertEqual(vb._unescape_entities("it&#39;s"), "it's")
        self.assertEqual(vb._unescape_entities("a&apos;b"), "a'b")
        self.assertEqual(vb._unescape_entities("a&nbsp;b"), "a b")

    def test_amp_alone(self):
        self.assertEqual(vb._unescape_entities("Tom &amp; Jerry"), "Tom & Jerry")

    def test_plain_text_unchanged(self):
        s = "没有实体的普通字幕文本 & < > 「」"
        self.assertEqual(vb._unescape_entities(s), s)


class TestCleanTranscriptVTT(unittest.TestCase):
    """v1.1：clean_transcript 同时支持 SRT 与 VTT。"""

    VTT = (
        "WEBVTT - 测试字幕\n"
        "\n"
        "1\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "第一句话内容。\n"
        "\n"
        "NOTE 这是注释块\n"
        "注释第二行也要跳过\n"
        "\n"
        "00:00:03.000 --> 00:00:05.000\n"
        "第二句话内容。\n"
        "\n"
        "STYLE\n"
        "::cue { color: red }\n"
        "\n"
        "intro\n"
        "00:00:05.000 --> 00:00:07.000\n"
        "第三句话&amp;内容。\n"
    )

    def test_structural_lines_removed(self):
        out = vb.clean_transcript(self.VTT)
        for noise in ("WEBVTT", "NOTE", "注释", "STYLE", "::cue", "intro",
                      "00:00:01.000"):
            self.assertNotIn(noise, out, f"结构残留漏进了正文: {noise}")

    def test_content_lines_kept(self):
        out = vb.clean_transcript(self.VTT)
        self.assertIn("第一句话内容。", out)
        self.assertIn("第二句话内容。", out)
        self.assertIn("第三句话&内容。", out)

    def test_entity_decoded_not_double(self):
        out = vb.clean_transcript(self.VTT)
        self.assertNotIn("&amp;", out)
        self.assertIn("&", out)

    def test_srt_still_works(self):
        srt = (
            "1\n"
            "00:00:01,000 --> 00:00:03,000\n"
            "SRT第一句。\n"
            "\n"
            "2\n"
            "00:00:03,000 --> 00:00:05,000\n"
            "SRT第二句。\n"
        )
        out = vb.clean_transcript(srt)
        self.assertNotIn("00:00:01,000", out)
        self.assertIn("SRT第一句。", out)
        self.assertIn("SRT第二句。", out)

    def test_hallucination_line_dropped(self):
        raw = (
            "00:00:01.000 --> 00:00:03.000\n"
            "真正的内容在这里。\n"
            "\n"
            "00:00:03.000 --> 00:00:05.000\n"
            "♪♫🎵\n"
        )
        out = vb.clean_transcript(raw)
        self.assertIn("真正的内容在这里。", out)
        self.assertNotIn("♪", out)


class TestSplitTranscript(unittest.TestCase):
    """分段切块：段尾优先 → 句末 → 硬切；不丢内容、不乱序。"""

    def test_short_text_single_chunk(self):
        text = "短视频幕，一段就够。"
        self.assertEqual(vb.split_transcript(text, 15000), [text])

    def test_disabled_returns_whole(self):
        text = "任意长度的文本" * 5000
        self.assertEqual(vb.split_transcript(text, 0), [text])

    def test_chunks_respect_limit(self):
        paras = [f"第{i:04d}段：" + "内容" * 100 for i in range(40)]
        text = "\n\n".join(paras)
        chunks = vb.split_transcript(text, 2000)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 2000)

    def test_no_content_lost(self):
        paras = [f"第{i:04d}段：" + "内容" * 100 for i in range(40)]
        text = "\n\n".join(paras)
        joined = "".join(vb.split_transcript(text, 2000))
        self.assertEqual(re.sub(r"\s+", "", joined), re.sub(r"\s+", "", text))

    def test_order_preserved(self):
        paras = [f"第{i:04d}段：" + "内容" * 100 for i in range(40)]
        text = "\n\n".join(paras)
        chunks = vb.split_transcript(text, 2000)
        self.assertTrue(chunks[0].startswith(paras[0][:10]))
        self.assertTrue(chunks[-1].endswith(paras[-1][-10:]))

    def test_single_paragraph_split_at_sentence_end(self):
        para = "这是一个完整的句子。" * 100          # 400 字，无段边界
        chunks = vb.split_transcript(para, 100)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 100)
            self.assertTrue(c.endswith("。"), f"从句半中间下刀了: {c[-10:]!r}")

    def test_single_sentence_hard_cut(self):
        para = "无标点的超长句子" * 50                # 400 字，一个标点都没有
        chunks = vb.split_transcript(para, 100)
        self.assertEqual([len(c) for c in chunks], [100, 100, 100, 100])
        self.assertEqual("".join(chunks), para)

    def test_empty_text(self):
        self.assertEqual(vb.split_transcript("", 15000), [])


class TestBuildBriefInput(unittest.TestCase):
    """build_brief_input 的 note 参数与占位符替换。"""

    def test_default_note(self):
        out = vb.build_brief_input("系统提示", "用户 {name}", {"name": "值"})
        self.assertIn("<!-- 用法：", out)
        self.assertIn("用户 值", out)
        self.assertNotIn("{name}", out)

    def test_custom_note_replaces_default(self):
        out = vb.build_brief_input("系统提示", "用户提示",
                                   {}, note="分段说明XYZ")
        self.assertIn("<!-- 分段说明XYZ -->", out)
        self.assertNotIn("<!-- 用法：", out)

    def test_unknown_placeholder_kept(self):
        # 模板里与 fields 无关的花括号必须原样保留（format 会误伤，replace 不会）
        out = vb.build_brief_input("sys", "图：{unknown} 保留", {"known": "x"})
        self.assertIn("{unknown}", out)


class _FakeExtractor:
    """离线假提取器：所有方法都抛错——run() 的降级路径应全部兜住。"""

    def http_get(self, url):
        raise RuntimeError("测试环境不联网")

    def get_video_info(self, bvid):
        raise RuntimeError("测试环境不联网")


class TestRunIntegration(unittest.TestCase):
    """run() 端到端（mock 提取器 + --subtitle 本地文件，零网络）。"""

    LONG_VTT_HEAD = "WEBVTT\n\n"
    LONG_CUE = "00:00:{:02d}.000 --> 00:00:{:02d}.500\n"

    def _make_subtitle(self, path, cue_count):
        parts = [self.LONG_VTT_HEAD]
        for i in range(cue_count):
            mm, ss = divmod(i, 60)
            parts.append(self.LONG_CUE.format(mm, ss, mm))
            parts.append(f"第{i:04d}句：这是用于测试长视频分段功能的字幕内容，"
                         f"句子足够长以确保清洗合并后仍然超过分段阈值。\n\n")
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(parts))

    def setUp(self):
        self._orig_loader = vb.load_extractor
        vb.load_extractor = lambda: _FakeExtractor()
        self.tmp = tempfile.mkdtemp(prefix="vb_test_")

    def tearDown(self):
        vb.load_extractor = self._orig_loader

    def _run(self, subtitle, chunk_chars=None):
        return vb.run("BV1tst000001", out_root=self.tmp,
                      subtitle_file=subtitle, chunk_chars=chunk_chars)

    def test_single_package_mode(self):
        sub = os.path.join(self.tmp, "short.vtt")
        self._make_subtitle(sub, cue_count=20)
        out_dir = self._run(sub)

        self.assertTrue(os.path.isfile(os.path.join(out_dir, "brief-input.md")))
        self.assertFalse(os.path.exists(os.path.join(out_dir, "chunks")))
        self.assertFalse(os.path.isfile(os.path.join(out_dir, "brief-merge.md")))

        with open(os.path.join(out_dir, "brief-input.md"), encoding="utf-8") as f:
            brief = f.read()
        self.assertIn("第一部分", brief)
        self.assertIn("第二部分", brief)

        with open(os.path.join(out_dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertFalse(meta["chunked"])
        self.assertEqual(meta["chunk_count"], 1)
        self.assertEqual(meta["subtitle_source"], "user-provided")

    def test_chunked_mode_structure(self):
        sub = os.path.join(self.tmp, "long.vtt")
        self._make_subtitle(sub, cue_count=400)     # 清洗后远超 15000 字
        out_dir = self._run(sub)

        chunks_dir = os.path.join(out_dir, "chunks")
        chunk_files = sorted(f for f in os.listdir(chunks_dir)
                             if re.match(r"chunk-\d+\.md$", f))
        self.assertGreater(len(chunk_files), 1, "长视频没有触发分段")

        # 每份 chunk-N.md 都是完整提示词（system + user 两部份）
        with open(os.path.join(chunks_dir, chunk_files[0]), encoding="utf-8") as f:
            first = f.read()
        self.assertIn("# 第一部分 · 系统提示词", first)
        self.assertIn("# 第二部分 · 用户提示词", first)
        self.assertIn("第 1 段 / 共", first)
        self.assertIn("分段笔记", first)

        # 归并文件：占位说明在位，{chunk_notes} 字面量已被替换掉
        with open(os.path.join(out_dir, "brief-merge.md"), encoding="utf-8") as f:
            merge = f.read()
        self.assertIn("归并要求", merge)
        self.assertIn("此处粘贴", merge)
        self.assertNotIn("{chunk_notes}", merge)

        # brief-input.md 降级为使用说明
        with open(os.path.join(out_dir, "brief-input.md"), encoding="utf-8") as f:
            usage = f.read()
        self.assertIn("使用说明", usage)
        self.assertIn("两步流程", usage)

        with open(os.path.join(out_dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertTrue(meta["chunked"])
        self.assertEqual(meta["chunk_count"], len(chunk_files))
        self.assertEqual(meta["chunk_chars"], 15000)

        # transcript.txt 全量留档：比任何单个 chunk 的正文都长
        with open(os.path.join(out_dir, "transcript.txt"), encoding="utf-8") as f:
            transcript = f.read()
        self.assertGreater(len(transcript), 15000)

    def test_chunk_chars_custom_threshold(self):
        sub = os.path.join(self.tmp, "mid.vtt")
        self._make_subtitle(sub, cue_count=400)
        out_dir = self._run(sub, chunk_chars=100000)   # 阈值拉高 → 不触发分段
        self.assertFalse(os.path.exists(os.path.join(out_dir, "chunks")))

    def test_chunk_chars_zero_disables(self):
        sub = os.path.join(self.tmp, "long2.vtt")
        self._make_subtitle(sub, cue_count=400)
        out_dir = self._run(sub, chunk_chars=0)        # 显式关闭分段
        self.assertFalse(os.path.exists(os.path.join(out_dir, "chunks")))
        with open(os.path.join(out_dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertFalse(meta["chunked"])

    def test_chunk_notes_cover_whole_transcript(self):
        # 分段只影响提示词组织：各 chunk「本段字幕」代码块按序拼接，
        # 应完整覆盖 transcript 内容（一块不丢、一块不多）
        sub = os.path.join(self.tmp, "long3.vtt")
        self._make_subtitle(sub, cue_count=400)
        out_dir = self._run(sub)
        with open(os.path.join(out_dir, "transcript.txt"), encoding="utf-8") as f:
            transcript = re.sub(r"\s+", "", f.read())
        chunks_dir = os.path.join(out_dir, "chunks")
        seg_re = re.compile(r"## 本段字幕\s*\n```\n(.*?)\n```", re.S)
        merged = ""
        for name in sorted(os.listdir(chunks_dir),
                           key=lambda n: int(re.search(r"\d+", n).group())):
            if re.match(r"chunk-\d+\.md$", name):
                with open(os.path.join(chunks_dir, name), encoding="utf-8") as f:
                    m = seg_re.search(f.read())
                self.assertIsNotNone(m, f"{name} 里没找到字幕代码块")
                merged += re.sub(r"\s+", "", m.group(1))
        self.assertEqual(merged, transcript)


if __name__ == "__main__":
    unittest.main(verbosity=2)
