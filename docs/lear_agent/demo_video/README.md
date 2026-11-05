# Demo videos

There are two videos. The **walkthrough film** is a narrated, animated explainer of how the take-home brief is met. The **terminal video** is a real recording of `lear demo`.

## Walkthrough film (4K)

| File | What it is | Size |
|---|---|---|
| [`lear_walkthrough_4k.mp4`](lear_walkthrough_4k.mp4?raw=true) | H.264 + AAC, 3840×2160, 24 fps, 3 min 37 s. Voiceover, synthesized music and sound effects, burned-in subtitles. | 58.9 MB |
| [`lear_walkthrough_1080p.mp4`](lear_walkthrough_1080p.mp4?raw=true) | The same film at 1920×1080 | 24.3 MB |
| [`lear_walkthrough_poster.png`](lear_walkthrough_poster.png) | One frame at 3:24: the requirements matrix and links | 1.1 MB |
| [`walkthrough_animation_manifest.json`](walkthrough_animation_manifest.json) | All 3,322 timeline animations, each with its timing and data source | 410 KB |

**One-click download (this branch):** [4K MP4](https://github.com/aviunion1348-cloud/rag-formation/raw/arena/227b3998-rag-formation/docs/lear_agent/demo_video/lear_walkthrough_4k.mp4) · [1080p MP4](https://github.com/aviunion1348-cloud/rag-formation/raw/arena/227b3998-rag-formation/docs/lear_agent/demo_video/lear_walkthrough_1080p.mp4)

![Walkthrough film: requirements matrix frame](lear_walkthrough_poster.png)

### What the film shows (7 scenes)

1. **Opening.** What Lear Local is.
2. **The brief.** The three pillars (knowledge base, local agent, measurement) and how each one is met.
3. **Knowledge base.**
   - 454 tracked files at the Lear pin become generated docs: 87 routes, 38 environment variables and 79 modules.
   - The verifier passes 11/11 checks, with 340/340 claims labelled.
   - The docs are split into 355 heading-based chunks and stored in a SQLite vector index.
4. **The agent.**
   - A recorded question (golden case lear-012) goes through dense + BM25 → RRF → rerank → MMR to 10 selected chunks.
   - The context is 1,684/1,800 tokens, and the answer is cited to a Lear source line.
   - It also shows sessions, compaction (487→447, 607→453, 564→445), user-scoped memory and the 9 guardrail checks.
5. **Measurement.**
   - The 13 lifecycle spans.
   - The load test at 1/2/5/10 users, which is pipeline-only.
   - The rate limiter: 10 OK and 10 RATE_LIMITED.
   - The 80-question golden set and the 5 proxy dimensions.
   - Three improvement rounds (.452 → .548) and the holdout (.643), used once.
6. **What we won't fake.** Ollama is BLOCKED in the sandbox. This scene shows what was measured, what is blocked, and the documented qualification runbook.
7. **Run it.** `lear demo replay`, the 25-row requirements matrix (9 PASS / 10 PARTIAL / 6 BLOCKED / 0 FAIL) and the links.

### What is real

- **Numbers.** Every number on screen comes from [`scripts/walkthrough/walkthrough_data.json`](../../../scripts/walkthrough/walkthrough_data.json), which was extracted from this repository's evidence. The manifest gives each data-bound element's source.
- **Labels.** The heads-up display reads `offline baseline · NOT QUALIFYING` on every frame. The bench figures are stamped pipeline-only, and the judge scores are labelled as deterministic proxies.
- **Sound.** The narration is text-to-speech. The music and the 511 sound effects are synthesized by [`scripts/walkthrough/audio.py`](../../../scripts/walkthrough/audio.py), with no samples.

### What the film is not

- **Not a screen recording.** It is a rendered explainer. For the real CLI, watch the terminal video below or run `python3 -m ragformation lear demo replay`.
- **Not a qualifying model run.** No local Ollama model ran.
- **Not a quality score.** "3,322 animations" counts the animated elements on the timeline: 2,651 bound to data, 247 text and 424 decorative.

How it was made: [scripts/walkthrough/README.md](../../../scripts/walkthrough/README.md).

| File | sha256 |
|---|---|
| `lear_walkthrough_4k.mp4` | `484f5144f3e9fe0611694e497b83a3fafbcb9055d05ba95753908c71a6b7be5d` |
| `lear_walkthrough_1080p.mp4` | `5e4cb953d0ab124c10f98e6a57f1341e2661d9e85abcd3edde36769d7319d5b6` |
| `lear_walkthrough_poster.png` | `64f54f7e91f472d2258921aa8e3af6d32441c5e52d471414d56e59bf37f061fc` |
| `walkthrough_animation_manifest.json` | `eaec9c39921cc55b4d1582f44f256105bfd64b83e72c47b6827cb9c46b401a7e` |

## Terminal demo video (`lear demo`)

| File | What it is | Size |
|---|---|---|
| [`lear_demo_terminal.mp4`](lear_demo_terminal.mp4?raw=true) | H.264 MP4 (yuv420p), 984×834, 10 fps, 2 min 14 s | 4.3 MB |
| [`lear_demo_terminal.cast`](lear_demo_terminal.cast?raw=true) | The same session as an asciicast v2 recording (plays in `asciinema play`, keeps the real text and timing) | 134 KB |
| [`lear_demo_poster.png`](lear_demo_poster.png) | One frame at 64 s (S04 request lifecycle) | 112 KB |

**One-click download (this branch):**
[MP4](https://github.com/aviunion1348-cloud/rag-formation/raw/arena/227b3998-rag-formation/docs/lear_agent/demo_video/lear_demo_terminal.mp4) ·
[cast](https://github.com/aviunion1348-cloud/rag-formation/raw/arena/227b3998-rag-formation/docs/lear_agent/demo_video/lear_demo_terminal.cast) ·
[whole project ZIP](https://github.com/aviunion1348-cloud/rag-formation/archive/refs/heads/arena/227b3998-rag-formation.zip)

![lear demo, S04 request lifecycle](lear_demo_poster.png)

### What the video shows

1. **`python3 -m ragformation lear demo live --source ../lear-backend`** on the
   default (Ollama) backend. The sandbox has no Ollama, so live mode stops at the
   model gate: S02 and S03 are `BLOCKED`, `LIVE MODE STOPPED AT THE DEPENDENCY
   GATE` offers replay, and the exit code is **6**. No answer is produced.
2. **`python3 -m ragformation lear --backend baseline demo director --source
   ../lear-backend`**: the offline baseline pipeline (hashing embedder,
   extractive generator) run for real in a scratch directory, labelled
   `NOT QUALIFYING`. The director is navigated with real key presses (`?` for
   help, then `Enter` per scene) through S00–S11. Exit code **0**.

Every scene's numbers are measured during the recording, at rag-formation commit
`954c8d624515` (clean tree, shown in S00) against the Lear source pin
`60cf9abf` (the docs rebuild in S01 is byte-identical, 10/10). The live
latencies are pipeline-only timings from a 2-vCPU sandbox, not model latency.

### How it was made (reproducible)

```bash
# 1. record a real pseudo-terminal session (stdlib only) -> asciicast v2
python3 scripts/record_demo_video.py --source ../lear-backend \
    --out docs/lear_agent/demo_video/lear_demo_terminal.cast        # exits 0 only if live=6 and director=0

# 2. render the recording to MP4 (optional tooling, NOT a project dependency)
python3 -m venv /tmp/vidvenv && /tmp/vidvenv/bin/pip install pyte Pillow imageio-ffmpeg
/tmp/vidvenv/bin/python scripts/render_demo_video.py \
    docs/lear_agent/demo_video/lear_demo_terminal.cast docs/lear_agent/demo_video/lear_demo_terminal.mp4 \
    --poster docs/lear_agent/demo_video/lear_demo_poster.png --poster-at 64
```

- **What is real and what the recorder adds.** Everything after each command
  line is the process's own output, read from a pseudo-terminal
  (`TERM=xterm-256color`, 120×45) with its real timing. Key presses are written
  into the same terminal. Only the shell prompt, the typed command lines and the
  `echo $?` lines come from the recorder, which has no shell; the exit codes it
  prints are the real ones.
- **Rendering.** `pyte` emulates the terminal, Pillow draws each cell in DejaVu
  Sans Mono, and the ffmpeg binary from `imageio-ffmpeg` encodes H.264 at CRF 28.
  Rendering is deterministic: the same `.cast` gives a byte-identical MP4.
- **No media dependencies in the product.** `pyte`, Pillow and `imageio-ffmpeg`
  are used only by `scripts/render_demo_video.py`. They are not in
  `pyproject.toml`, and the demo runs without them.

| File | sha256 |
|---|---|
| `lear_demo_terminal.mp4` | `a8fda38e5bfce15242109b2c01ce8315773bc9b0e2aa62d16d44cecdd265b823` |
| `lear_demo_terminal.cast` | `bc4558f9e59e5cc5e09adfa3b166b4b620a72f713d852dc4bac58d3b50dd28d3` |
| `lear_demo_poster.png` | `631b647cde92cf697938192a0002ed39bd998cfd0162c64cff60f99aca65b389` |

### What the video is not

- **Not a qualifying model run.** No local Ollama model ran. There is no TTFT,
  ITL, tokens/s or judge score anywhere in it, and S11 says
  `OVERALL: BLOCKED — LOCAL OLLAMA MODEL QUALIFICATION NOT COMPLETED IN THE
  RECORDED ENVIRONMENT.`
- **Not edited.** There are no cuts, overlays or sped-up sections. Scenes longer
  than 45 rows scroll as they do in a real terminal. Pause the video, or play the
  `.cast` file, to read them.
