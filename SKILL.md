---
name: video-brief
description: 把B站视频（BV号/av号/视频链接）变成教学式重写的HTML学习材料。自动取字幕（无字幕走硅基流动ASR）→ 清洗成纯文本 → 长视频自动分段做两阶段提取 → 按教学方法论写教学式总结 → 渲染成单文件HTML。触发词：总结这个视频、拆解这个视频、这个视频讲了什么、视频总结、视频讲解、视频转文字、BV号、av号、B站视频、bilibili、B站链接。
---

# video-brief · 视频总结器

输入一个 B站 BV号/av号/视频链接 → 拿到字幕 → 按教学方法论写一份教学式总结
→ 渲染成一份清爽的 HTML。长视频（清洗后超 15000 字）自动分段走两阶段提取。

**这不是摘要工具。** 别人做的是「这个视频讲了三点」的摘要；本 skill 做的是
按教学方法论重写成一份教学材料：先给体系骨架，再逐个概念推到只剩一种可能，
定义给全四要素，主动覆盖疑问，结尾给可复现收口。

面向「要把视频变成自己知识的人」——学生、做调研的人、以及要把视频喂进
自己工作流的 Agent。

## 使用方式

```bash
# 第一步：备料（BV号/av号/链接 → 字幕 → 清洗 → 总结用输入包）
python "C:/Users/Administrator/.workbuddy/skills/video-brief/video_brief.py" BV1xxxxxxx
python "C:/Users/Administrator/.workbuddy/skills/video-brief/video_brief.py" BV1xxxxxxx --out ./myout
python "C:/Users/Administrator/.workbuddy/skills/video-brief/video_brief.py" BV1xxxxxxx --subtitle ./已有字幕.vtt
python "C:/Users/Administrator/.workbuddy/skills/video-brief/video_brief.py" BV1xxxxxxx --page 3   # 多P视频
python "C:/Users/Administrator/.workbuddy/skills/video-brief/video_brief.py" https://www.bilibili.com/video/BV1xxxxxxx/?p=3  # 链接（带分P）
python "C:/Users/Administrator/.workbuddy/skills/video-brief/video_brief.py" av170001            # av号（自动转BV）
python "C:/Users/Administrator/.workbuddy/skills/video-brief/video_brief.py" BV1xxxxxxx --chunk-chars 20000  # 自定义分段阈值（0=关闭分段）
```

## 三步流程

1. **取字幕**：`video_brief.py` 复用 `bili-subtitle-extractor` 的链路——
   有 CC 字幕直接下载解析，没有则下载音频走硅基流动 ASR。
2. **清洗**：去时间戳、去 VTT/SRT 结构残留、去自动字幕滚动重复、合并碎句成
   自然段，产出 `transcript.txt`；同时生成 `meta.json` 和填好占位符的
   `brief-input.md`。
3. **总结 + 渲染**：把 `brief-input.md` 的第一部分作为 system prompt、
   第二部分作为 user prompt 交给 Agent，得到总结 markdown，存为
   `brief.md`；再跑 `render.py` 渲染成单文件 HTML：

```bash
python "C:/Users/Administrator/.workbuddy/skills/video-brief/render.py" output/BV1xx_标题/brief.md
python "C:/Users/Administrator/.workbuddy/skills/video-brief/render.py" brief.md -o out.html --title "标题"
```

**长视频分段模式**：清洗后字幕超过 `--chunk-chars`（默认 15000 字）时自动触发，
`brief-input.md` 变为使用说明，提示词拆成 `chunks/chunk-1.md … N.md`（每段提炼
成分段笔记）+ `brief-merge.md`（N 份笔记归并成终稿）。跑法：
依次把各 chunk 文件交给 Agent 产出 `chunks/note-N.md`，再把笔记按序贴进
`brief-merge.md` 的「分段笔记」一节出终稿。原因与切段规则见 README「设计取舍」。

## 触发条件

用户说以下任意时调用本技能：
- 「总结这个视频」「拆解这个视频」「这个视频讲了什么」+ BV号/av号/链接
- 「视频总结」「视频讲解」+ BV号/av号/链接
- 「把视频变成学习材料」「视频转学习笔记」+ BV号/av号/链接
- 给出 B站链接或 BV号并要求总结、讲解、拆解

## 前置条件

- 环境变量 `SILICONFLOW_API_KEY` 已设置（仅在该视频无 CC 字幕、需要走 ASR 时使用）
- 网络可访问 B站 API 和硅基流动 API
- 无字幕路径额外依赖 `ffmpeg` / `ffprobe`（音频分块）
- 字幕提取依赖同级的 `bili-subtitle-extractor` skill（可用环境变量
  `BILI_SUBTITLE_EXTRACTOR_PY` 覆盖其脚本路径）

## 输出结构

单包模式（短视频）：

```
output/
└── BV号_标题/
    ├── transcript.txt   ← 清洗后的纯文本字幕（Agent 的原料）
    ├── meta.json        ← 视频元信息（标题/UP主/时长/分区/简介/分段信息）
    ├── brief-input.md   ← 完整提示词（system + user，Agent 直接拿着用）
    ├── brief.md         ← Agent 写的总结（需自行保存到此）
    └── brief.html       ← render.py 产出的单文件 HTML
```

分段模式（长视频，超 `--chunk-chars` 自动触发）：

```
output/
└── BV号_标题/
    ├── transcript.txt   ← 全量字幕留档
    ├── meta.json        ← chunked=true，带 chunk_count/chunk_chars
    ├── chunks/
    │   ├── chunk-1.md   ← 第 1 段提示词（产出分段笔记）
    │   ├── chunk-N.md
    │   ├── note-1.md    ← 分段笔记（跑 Agent 后自行保存）
    │   └── note-N.md
    ├── brief-merge.md   ← 归并提示词（笔记按序贴入后出终稿）
    ├── brief-input.md   ← 使用说明（两步流程）
    ├── brief.md         ← 最终总结（需自行保存到此）
    └── brief.html       ← render.py 产出的单文件 HTML
```

## 注意事项

- 本 skill 只处理 B站（YouTube 二期）。
- 画面信息（PPT/代码/白板）拿不到，提示词已要求 Agent 标注「无法从字幕确认」
  而不是编造。
- ASR 字幕会有错字，提示词已要求 Agent 标注「此处转写可能有误」而非默默改掉。
