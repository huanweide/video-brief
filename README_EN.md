**中文** · [README.md](./README.md)

<p align="center">
  <img src="assets/banner.svg" alt="video-brief">
</p>

<p align="center">
  <a href="#overview">Overview</a> · <a href="#the-three-step-flow">Flow</a> · <a href="#quick-start">Quick start</a> · <a href="#design-tradeoffs">Design</a> · <a href="#current-limits">Limits</a>
</p>

[![License](https://img.shields.io/github/license/huanweide/video-brief)](LICENSE)
[![CI](https://github.com/huanweide/video-brief/actions/workflows/ci.yml/badge.svg)](https://github.com/huanweide/video-brief/actions/workflows/ci.yml)
[![Stars](https://img.shields.io/github/stars/huanweide/video-brief)](https://github.com/huanweide/video-brief/stargazers)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Deps](https://img.shields.io/badge/core-zero--stdlib-4ecca3)](https://github.com/huanweide/video-brief)

# video-brief · Bilibili video → teaching-style rewrite

> **TL;DR:** Turn any Bilibili video into a *teaching-style* rewrite, not a shallow summary. Feed a BV id → fetch/transcribe subtitles → clean them → assemble a teaching-method prompt pack → you (or any LLM agent) write the brief → render to a single portable HTML. Long videos auto-chunk (map-reduce) so the middle never gets lost.

Give it a Bilibili BV/av id or video link, get the subtitles, write a teaching-style summary following a teaching methodology, and render it into a clean single-file HTML. Long videos auto-chunk into a two-stage extraction so the middle is never dropped.

## Overview

**Not a summary — a teaching-style rewrite.** Other tools tell you what the video said; this tool lets you *learn* it.

> **Suggested GitHub topics:** `video-summary` `bilibili` `subtitle` `asr` `teaching` `prompt-engineering` `html` `python` `learning` `knowledge-management` `chinese`

## How it differs from "video summary tools"

A video summary answers "what did it say" with an information list — you know it happened, but it doesn't slot into your own knowledge system. video-brief rewrites following a teaching methodology: first the skeleton (central question / concepts / dependencies / division of labor), then pushes each concept until only one possibility remains (with reverse exclusions), definitions with all four elements (precise definition / reason to exist / boundary / easily-confused objects), proactively covering the 3–5 questions a learner is most likely to ask, and a reproducible wrap-up for hands-on content. The standard for a finished brief is **the learner can't poke a hole in it**, not "knows three bullet points".

---

## Who should use it

- You watch Bilibili course / tutorial videos and want to **actually learn and retain** them, not just get a recap.
- You build a **personal knowledge base** and want clean, portable HTML notes you can archive.
- You want the *materials prepared* (clean transcript + ready prompt) so any LLM agent writes a high-quality brief.

## Who should NOT use it

- You want **YouTube** support — that's planned for v2; currently Bilibili only.
- You need content from **frames / PPT / whiteboard / screen demos** — it's subtitle-only (the prompt asks the agent to flag this honestly).
- You expect the tool to **write the summary for you** — it prepares the inputs; the agent writes the brief.

---

## The three-step flow

```
BV id ──① fetch subtitles──> raw subtitles ──② clean──> plain text + meta + prompt input pack
                                                        │
                                                        ▼
                                          Agent writes brief per teaching method
                                                        │
                                                        ▼
                            brief.md ──③ render──> brief.html (single file)
```

1. **Fetch subtitles** — reuses `bili-subtitle-extractor`'s pipeline: download CC subtitles directly if present, otherwise download audio and run SiliconFlow ASR. The script does not re-implement the subtitle pipeline. Accepts BV id / av id / any Bilibili link (auto-detects `?p=N` for multi-part); `.srt`/`.vtt` subtitle files also accepted.
2. **Clean** — `video_brief.py`'s `clean_transcript()` turns raw subtitles into LLM-friendly plain text: strip timestamps, strip VTT/SRT structure residue, strip auto-subtitle scroll repetition, merge over-fragmented short lines, normalize whitespace. Also generates video meta and a placeholder-filled prompt input pack.
3. **Summarize + render** — hand the input pack to any LLM agent to write the brief (markdown); `render.py` renders it into a CSS-inlined single-file HTML you can read by copying one file.

**Long-video auto-chunk (v1.1):** when cleaned subtitles exceed 15000 chars (`--chunk-chars` adjustable), stuffing a whole lecture into one prompt either blows the context or makes the model lose the middle. It then auto-switches to two stages: each chunk gets its own prompt to extract a chunk note, then the N notes are merged into the final draft. This is the map-reduce approach validated by the bili-subtitle project.

---

## Install

```bash
git clone https://github.com/huanweide/video-brief.git
cd video-brief
# Core has zero third-party dependencies (clean / render are pure Python stdlib);
# ASR reuses the bili-subtitle-extractor pipeline when no CC subtitles.
# Optional: audio transcoding for no-subtitle videos needs ffmpeg
#   Windows: winget install Gyan.FFmpeg  (or download from ffmpeg.org, add bin to PATH)
#   macOS:   brew install ffmpeg
#   Linux:   sudo apt install ffmpeg
```

---

## Quick start

```bash
# Prerequisite: only needed when the video has no CC subtitles (for ASR)
export SILICONFLOW_API_KEY="your-key"
# Same scenario also needs ffmpeg / ffprobe in PATH (audio chunk transcoding):
#   Windows: winget install Gyan.FFmpeg
#   macOS:   brew install ffmpeg
#   Linux:   sudo apt install ffmpeg
# Missing it won't crash the system — the script pre-checks and prints the install guide above.

# Step 1: prepare materials
python video_brief.py BV1a14y187qn
# Produces output/BV1a14y187qn_title/{transcript.txt, meta.json, brief-input.md}

# Step 2: use part 1 of brief-input.md as system prompt, part 2 as user prompt for an agent,
#         save the produced markdown as brief.md in the same directory

# Step 3: render
python render.py output/BV1a14y187qn_title/brief.md
# Produces brief.html in the same directory
```

With an existing subtitle file, skip fetching (`.srt`/`.vtt` both work):

```bash
python video_brief.py BV1a14y187qn --subtitle ./existing_subtitle.vtt
```

Link / av id / chunk threshold (v1.1):

```bash
python video_brief.py https://www.bilibili.com/video/BV1a14y187qn/?p=3  # link with multi-part
python video_brief.py av170001                                        # av id (auto to BV)
python video_brief.py BV1a14y187qn --chunk-chars 20000                # custom chunk threshold
python video_brief.py BV1a14y187qn --chunk-chars 0                    # disable chunking, force single pack
```

---

## Output structure

Single-pack mode (short video, default):

```
output/BV_id_title/
├── transcript.txt    cleaned plain-text subtitles (agent's raw material)
├── meta.json         video meta (title / UP / duration / category / description / source / chunk info)
├── brief-input.md    full prompt (system + user, agent uses directly)
├── brief.md          agent-written teaching-style summary (save here yourself)
└── brief.html        single-file HTML produced by render.py
```

Chunk mode (long video, auto-triggered past `--chunk-chars`):

```
output/BV_id_title/
├── transcript.txt       full subtitles archive (not fed to prompt directly)
├── meta.json            chunked=true, with chunk_count/chunk_chars
├── chunks/
│   ├── chunk-1.md       chunk 1 prompt (system + user, produces a chunk note)
│   ├── ...
│   ├── chunk-N.md
│   ├── note-1.md        chunk note (save here after running the agent)
│   └── ...
├── brief-merge.md       merge prompt: paste N notes in order into the "chunk notes" section for final draft
├── brief-input.md       usage notes (two-step flow)
├── brief.md             final teaching-style summary (save here yourself)
└── brief.html           single-file HTML produced by render.py
```

`brief.html` auto-generates a video info header at the top: title, UP name, BV id, original link, generation time. Body is ~720px centered, light theme, code blocks with light-gray rounded background, tables with bordered zebra stripes, blockquotes with a left vertical line.

---

## Current limits

- **No frame handling** — info in PPT, code, whiteboard, or screen demos is unreachable. The prompt instructs the agent to explicitly flag "frame content can't be confirmed from subtitles" and list what the reader should watch in the original video, rather than fabricate from common sense.
- **ASR on no-subtitle videos has transcription errors** — proper nouns, names, numbers, and mixed Chinese-English are more error-prone. The prompt instructs the agent to mark "transcription may be wrong here" rather than silently "fix" it — honesty over fluency.
- **Bilibili only** — YouTube is v2.
- **Summary quality depends on the agent** — the script only prepares clean materials and a ready prompt; final quality is executed by the agent following the iron rules in `prompts/brief-system.md`.

---

## Design tradeoffs

**Why the script doesn't write the summary itself.** Writing the summary is the agent's job; the script only prepares clean materials. Once responsibilities are clear, both sides are independently replaceable: swap in a stronger model to write the summary without touching the material pipeline; swap the subtitle source without touching the prompt. The script guarantees the deterministic part — subtitle fetch, clean, meta, prompt assembly; the agent handles the uncertain part — understanding and rewriting.

**Why output HTML.** Single file (CSS inlined into `<style>`), copy one file and it works, no dependency drift, no style loss; the light, clean theme is designed for long-form reading so you can quietly finish a few-thousand-word piece; shareable, archivable, and can later be batch-archived into a personal knowledge base.

**Why chunk long videos (v1.1).** A 40–60 minute lecture cleans to ~15k–20k chars; stuffing it into one prompt fails two ways: blow the context and fail outright, or the model loses the middle (lost-in-the-middle) — and the middle is often the load-bearing wall. After chunking, each piece extracts a chunk note (small output), then a much smaller input does the merge (small input); both stages avoid these two pitfalls. The chunk prompt (`prompts/brief-chunk-user.md`) explicitly forbids writing a global summary — each chunk only sees its own slice and has no right to conclude about the whole film; the merge prompt (`prompts/brief-merge-user.md`) handles dedup, cross-chunk reorganization, preserving doubts, and acknowledging gaps. Cut points prefer chapter ends; a single over-limit chunk cuts at sentence-ending punctuation, never mid-sentence — an agent reading a half-sentence will guess, and guessed content enters the summary disguised as "said in the video".

---

## Effect preview

> Screenshot to be added. Below is the product structure from a real run (single-file HTML, CSS inlined, copy-and-go):

```
output/BV1a14y187qn_title/
├── brief.html   # teaching-style summary: skeleton + per-concept derivation + doubt coverage + reproducible wrap-up
└── brief.md     # same summary's Markdown source
```

Send `brief.html` to a friend or store it in your personal knowledge base — one file carries everything.

---

## Roadmap

- [x] Subtitle fetch + clean + meta + prompt pack
- [x] Single-file HTML render (light theme, portable)
- [x] Long-video auto-chunk (map-reduce, v1.1)
- [ ] YouTube support (v2)
- [ ] More teaching-method templates
- [ ] Batch archive into a knowledge base

## Contributing

Found a transcription edge case or a teaching-method improvement? File an issue or PR. Keep the clean/render paths dependency-free.

## License

MIT.
