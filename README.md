<!-- badges -->
[![License](https://img.shields.io/github/license/huanweide/video-brief)](LICENSE)
[![Stars](https://img.shields.io/github/stars/huanweide/video-brief)](https://github.com/huanweide/video-brief/stargazers)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Deps](https://img.shields.io/badge/core-zero--stdlib-4ecca3)](https://github.com/huanweide/video-brief)
<!-- /badges -->

# video-brief · 视频总结器

> **English TL;DR:** Turn any Bilibili video into a *teaching-style* rewrite, not a shallow summary. Feed a BV id → fetch/transcribe subtitles → clean them → assemble a teaching-method prompt pack → you (or any LLM agent) write the brief → render to a single portable HTML. Long videos auto-chunk (map-reduce) so the middle never gets lost.

输入一个 B站 BV号/av号/视频链接，拿到字幕，按教学方法论写一份教学式总结，
渲染成一份清爽的单文件 HTML。长视频自动分段做两阶段提取，不丢中段。

## 一句话定位

**不是摘要，是教学式重写。** 别人告诉你视频说了什么；本工具让你能学会它。

> **Topics（GitHub 仓库标签建议）**：`video-summary` `bilibili` `subtitle` `asr` `teaching` `prompt-engineering` `html` `python` `learning` `knowledge-management` `chinese`

## 和「视频摘要工具」的区别

视频摘要回答「它说了什么」，输出一份信息清单，读完你知道有这回事，
但挂不进自己的知识体系。video-brief 按教学方法论重写：先给体系骨架
（总问题/概念/依赖关系/分工），再逐个概念推到只剩一种可能（含反面排除），
定义给全四要素（精确定义/存在理由/边界/易混对象），主动覆盖学习者最可能问的
3 到 5 个疑问，动手类内容结尾给可复现收口。读完成果的标准是
**学习者挑不出漏洞**，不是「知道了三个要点」。

## 三步流程

```
BV号 ──① 取字幕──> 原料字幕 ──② 清洗──> 纯文本 + 元信息 + 提示词输入包
                                              │
                                              ▼
                                    Agent 按教学方法论写总结
                                              │
                                              ▼
                              brief.md ──③ 渲染──> brief.html（单文件）
```

1. **取字幕**：复用 `bili-subtitle-extractor` 的链路——有 CC 字幕直接下载，
   没有则下载音频走硅基流动 ASR。脚本不重写字幕链路。输入支持 BV号/av号/
   任意 B站链接（带 `?p=N` 自动认分P）；字幕文件 `.srt`/`.vtt` 都认。
2. **清洗**：`video_brief.py` 的 `clean_transcript()` 负责把字幕原料变成
   适合 LLM 阅读的纯文本：去时间戳、去 VTT/SRT 结构残留、去自动字幕滚动重复、
   合并过碎短句、归一化空白。同时生成视频元信息和填好占位符的提示词输入包。
3. **总结 + 渲染**：把输入包交给任意 LLM Agent 写总结（markdown），
   `render.py` 渲染成 CSS 内联的单文件 HTML，拷走一个文件就能看。

**长视频自动分段（v1.1）**：清洗后字幕超过 15000 字（`--chunk-chars` 可调），
单 prompt 塞整节课要么爆上下文、要么模型 lost-in-the-middle 丢中段。
此时自动切成两阶段：每段一份提示词单独提炼成分段笔记，再把 N 份笔记
归并成终稿。这是 bili-subtitle 项目验证过的 map-reduce 路子。

## 安装

```bash
git clone https://github.com/huanweide/video-brief.git
cd video-brief
# 核心零第三方依赖（清洗 / 渲染纯 Python 标准库）；无 CC 字幕走 ASR 时复用 bili-subtitle-extractor 链路
# 可选：无字幕音频转码需要 ffmpeg（Windows: winget install Gyan.FFmpeg / macOS: brew install ffmpeg / Linux: sudo apt install ffmpeg）
```

## 快速开始

```bash
# 前置：仅在该视频无 CC 字幕时需要（ASR 用）
export SILICONFLOW_API_KEY="你的key"
# 同一场景还需要 ffmpeg / ffprobe 在 PATH 中（音频分块转码用）：
#   Windows: winget install Gyan.FFmpeg  （或 ffmpeg.org 下载解压，bin 加入 PATH）
#   macOS:   brew install ffmpeg
#   Linux:   sudo apt install ffmpeg
# 缺了不会撞系统错误——脚本会先预检，直接把上面的安装指引打给你。

# 第一步：备料
python video_brief.py BV1a14y187qn
# 产出 output/BV1a14y187qn_标题/{transcript.txt, meta.json, brief-input.md}

# 第二步：把 brief-input.md 的第一部分作为 system prompt、第二部分作为
# user prompt 交给 Agent，把产出的 markdown 保存为同目录 brief.md

# 第三步：渲染
python render.py output/BV1a14y187qn_标题/brief.md
# 产出同目录 brief.html
```

已有字幕文件时跳过抓取（`.srt`/`.vtt` 均可）：

```bash
python video_brief.py BV1a14y187qn --subtitle ./已有字幕.vtt
```

链接/av号/分段阈值（v1.1）：

```bash
python video_brief.py https://www.bilibili.com/video/BV1a14y187qn/?p=3  # 链接带分P
python video_brief.py av170001                                        # av号（自动转BV）
python video_brief.py BV1a14y187qn --chunk-chars 20000                # 自定义分段阈值
python video_brief.py BV1a14y187qn --chunk-chars 0                    # 关闭分段，强制单包
```

## 输出结构

单包模式（短视频，默认）：

```
output/BV号_标题/
├── transcript.txt   清洗后的纯文本字幕（Agent 的原料）
├── meta.json        视频元信息（标题/UP主/时长/分区/简介/来源/分段信息）
├── brief-input.md   完整提示词（system + user，Agent 直接拿着用）
├── brief.md         Agent 写的教学式总结（需自行保存到此）
└── brief.html       render.py 产出的单文件 HTML
```

分段模式（长视频，超过 `--chunk-chars` 自动触发）：

```
output/BV号_标题/
├── transcript.txt      全量字幕留档（不直接进提示词）
├── meta.json           chunked=true，带 chunk_count/chunk_chars
├── chunks/
│   ├── chunk-1.md      第 1 段提示词（system + user，产出分段笔记）
│   ├── ...
│   ├── chunk-N.md
│   ├── note-1.md       分段笔记（跑 Agent 后自行保存到此）
│   └── ...
├── brief-merge.md      归并提示词：把 N 份笔记按序贴进「分段笔记」一节出终稿
├── brief-input.md      使用说明（两步流程）
├── brief.md            最终教学式总结（需自行保存到此）
└── brief.html          render.py 产出的单文件 HTML
```

`brief.html` 顶部自动生成视频信息头：标题、UP主、BV号、原视频链接、生成时间。
正文约 720px 居中，浅色主题，代码块浅灰底圆角，表格带边框斑马纹，引用块左侧竖线。

## 当前不能做什么

- **不处理画面**：PPT、代码、白板、屏幕演示里的信息拿不到。提示词已要求
  Agent 在这种情况下明确标注「画面内容无法从字幕确认」，并列出需要读者
  自己去看原视频的清单，而不是凭常识编造。
- **无字幕时走 ASR 会有错字**：专有名词、人名、数字、中英混说处出错概率较高。
  提示词已要求 Agent 标注「此处转写可能有误」而不是默默改掉——诚实高于流畅。
- **只支持 B站**：YouTube 二期。
- **总结质量取决于 Agent**：脚本只负责把原料备干净、把提示词备好；
  最终质量由 Agent 按 `prompts/brief-system.md` 的铁律执行。

## 设计取舍

**为什么脚本不自己写总结。** 写总结是 Agent 的活，脚本只负责把原料备干净。
职责划清之后，两边都可以单独替换：换更强的模型写总结，原料链路不动；
换字幕来源，提示词不动。脚本这边保证的是确定性的部分——字幕抓取、
清洗、元信息、提示词组装；Agent 那边负责不确定性的部分——理解和重写。

**为什么输出 HTML。** 单文件（CSS 内联进 `<style>`），拷走一个文件就能看，
不发依赖、不掉样式；浅色清爽主题为长文阅读设计，能安静读完一篇几千字；
可分享、可沉淀，将来可以批量归档成个人知识库。

**为什么长视频要分段（v1.1）。** 一节 40-60 分钟的课清洗后约 1.5-2 万字，
塞进一个 prompt 有两个败法：爆上下文直接失败，或者模型 lost-in-the-middle
把中段讲的内容丢干净——而中段往往是承重墙。切段后每块单独提炼成分段笔记
（小产出），再用小得多的输入做归并（小输入），两阶段都避开这两个坑。
分段提示词（`prompts/brief-chunk-user.md`）明令禁止写全局总结——每一段
只看得见自己那块，没资格对全片下结论；归并提示词
（`prompts/brief-merge-user.md`）负责去重、跨段重组、保留存疑、承认缺口。
切点优先落在段尾，单段超限按句末标点切，绝不从半句话中间下刀——
Agent 读到半句话会顺着猜，猜出来的内容会以「视频里说过」的身份混进总结。
## 效果预览

> 截图待补。下面是一次真实运行的产物结构（单文件 HTML，CSS 内联，拷走即看）：

```
output/BV1a14y187qn_标题/
├── brief.html   # 教学式总结：体系骨架 + 逐概念推导 + 疑点覆盖 + 可复现收口
└── brief.md     # 同一份总结的 Markdown 源
```

把 `brief.html` 发给朋友或存进个人知识库，一个文件带走全部内容。

## 🧰 同一作者的其他工具

> 作者在做一系列开源效率工具，欢迎顺手点 Star 支持：

- 📚 长篇创作 — AI 小说工坊，本地规则引擎零依赖：[点此访问](https://github.com/huanweide/novel-smith)
- 🎓 网课助手 — 智慧树自动播放/续播/AI 答题：[点此访问](https://github.com/huanweide/zhihuishu-helper)
- 🎬 字幕/视频 — B站字幕一键提取（零 Key 取官方字幕）：[点此访问](https://github.com/huanweide/bili-subtitle-universal)
- 📺 实时字幕 — 直播/会议实时字幕：[点此访问](https://github.com/huanweide/live-subtitle)
- 🖥️ 总览 — 作者全部开源项目集散：[点此访问](https://github.com/huanweide/family-showcase)
- 🩺 代码健康 — 零依赖多轴静态体检：[点此访问](https://github.com/huanweide/doctors)
