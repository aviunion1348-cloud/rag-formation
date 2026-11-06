# `lear demo` evidence

Everything on this page was produced by the commands shown, on 2026-11-05 (UTC)
in the audit sandbox, at rag-formation commit `954c8d624515` with a clean
tracked tree. The sandbox had no Ollama, no GPU, 2 vCPU and Python 3.11.2 (see
[audit_environment.md](audit_environment.md)). The Lear source was
`Lear-backend-Avi` @ `60cf9abf`, on branch `main`.

## Replay bundles

The format is described in [demo_replay_format.md](demo_replay_format.md).

| Bundle | Run | Body sha256 | Text | Ships in wheel |
|---|---|---|---|---|
| [`ragformation/lear/demo_replay/lear_demo_replay_v2.json`](../../ragformation/lear/demo_replay/lear_demo_replay_v2.json) | `demo-20261105T205910Z` | `84cefeec093c0296f2863d768ce0a0cc1e163589bb2dec5d3f20a86a45593f68` | redacted | yes |
| [`evidence/demo/lear_demo_replay_v2_full.json`](evidence/demo/lear_demo_replay_v2_full.json) | `demo-20261105T205915Z` | `772b94cc75c49cdacf474208717da0ed823b97d5a8fdc60a4bc33ccb9175a5a0` | full | no |
| [`evidence/demo/lear_demo_replay_v1.json`](evidence/demo/lear_demo_replay_v1.json) | `demo-20261105T200214Z` (commit `132838754d0a`) | `cb7ce8fb8360d57b5848014615c6164565c4df9d2cb45d107b5cb2766f0b3b70` | full | no (moved out of the package) |

The v2 bundles were captured with these commands:

```bash
python3 -m ragformation lear --backend baseline demo capture --source ../lear-backend \
    --out ragformation/lear/demo_replay/lear_demo_replay_v2.json
python3 -m ragformation lear --backend baseline demo capture --full --source ../lear-backend \
    --out docs/lear_agent/evidence/demo/lear_demo_replay_v2_full.json
```

Both bundles record `backend: baseline`, `qualifying: false` and
`models.availability: UNVERIFIED: doctor did not pass`. Each holds the sha256
of 19 cited repository files.

## Transcripts

All files are in [`evidence/demo/`](evidence/demo/). Commands are run from the
repository root, with `--source ../lear-backend` where shown.

| File | Command | Exit | What it demonstrates |
|---|---|---|---|
| [`examiner_baseline_plain.txt`](evidence/demo/examiner_baseline_plain.txt) | `lear --backend baseline demo examiner --plain --source ../lear-backend` | 0 | All 12 scenes live on the baseline. See the list after this table |
| [`examiner_baseline_s04_s06_s08.json`](evidence/demo/examiner_baseline_s04_s06_s08.json) | `lear --backend baseline demo examiner --json --scene 4,6,8` | 0 | One JSON object, no ANSI (also asserted in tests) |
| [`replay_plain.txt`](evidence/demo/replay_plain.txt) | `lear demo replay --plain` | 0 | Checksum verified first; all 12 scenes under `RECORDED REPLAY - run demo-20261105T205915Z …`; no `[LIVE]` label |
| [`live_ollama_plain.txt`](evidence/demo/live_ollama_plain.txt) | `lear demo live --plain` (default ollama backend) | **6** | S02/S03 `BLOCKED`; stops with `LIVE MODE STOPPED AT THE DEPENDENCY GATE` and offers replay |
| [`director_ollama_plain.txt`](evidence/demo/director_ollama_plain.txt) | `lear demo director --plain` (default ollama backend) | **6** | Live where possible; blocked scenes followed by recorded evidence labelled `LIVE BLOCKED` |
| [`effects_plain.txt`](evidence/demo/effects_plain.txt) | `lear demo effects --plain` | 0 | Registry `lear-demo-effects-v2`: 118 recipes, 40 families |
| [`effects_preview_plain.txt`](evidence/demo/effects_preview_plain.txt) | `lear demo effects --plain --preview all` | 0 | Every recipe rendered from labelled example data (`[ILLUSTRATIVE]`) |
| [`render_overhead_2026-11-06.json`](evidence/demo/render_overhead_2026-11-06.json) | `python3 scripts/measure_demo_render.py --bundle docs/lear_agent/evidence/demo/lear_demo_replay_v2_full.json` | 0 | 12 scenes: 6.33 ms static, 11.59 ms with all 560 frames, 20.96 s of deliberate pacing at speed 1 |

The examiner transcript shows, scene by scene:

- **S01:** docs checks 11/11 PASS; 10/10 rebuilt files byte-identical.
- **S02:** a 355-chunk scratch index.
- **S03:** doctor exit 6.
- **S04:** 13 lifecycle spans.
- **S05:** 10 selected chunks, 2 excluded by the context budget, context 1684/1800 tokens.
- **S06:** the lear-012 answer with its citation traced to Lear source lines.
- **S08:** 7 MATCH and 2 LIMITATION.
- **S09:** five proxy dimensions and the failure explorer.
- **S10:** live and recorded bench, `INDICATIVE — SMALL SAMPLE`.
- **S11:** `OVERALL: BLOCKED`.

**Bench numbers differ between runs.** Each live run is a new 2-vCPU
measurement over 2–10 requests per level, so its p99 is the maximum of a small
sample. The screen labels it `INDICATIVE — SMALL SAMPLE`. The official recorded
baseline is in [benchmark_report.md](benchmark_report.md).

## Video

[`demo_video/`](demo_video/README.md) holds a real pty recording (asciicast v2,
1157 events, 132 s) of `lear demo live` (exit 6) followed by the baseline
director, navigated with real key presses (exit 0). It is rendered to MP4
(H.264, 984×834, 10 fps, 2 min 14 s, 4.3 MB) and to one poster frame.

## Test and packaging results

| Gate | Command | Result |
|---|---|---|
| Demo tests | `python3 -m unittest discover -s tests -p 'test_lear_demo.py'` | 37 tests OK |
| Full unit suite | `python3 -m unittest discover -s tests` | 256 tests OK |
| Legacy engine | `cd engine && python3 -m unittest discover -s tests` | 29 tests OK |
| Python 3.9 grammar | `ast.parse(..., feature_version=(3, 9))` on the changed files | OK |
| Wheel contents | `python3 -m pip wheel . --no-deps --no-build-isolation -w /tmp/whl` | Contains `demo.py`, `demo_render.py`, `demo_effects.py` and `demo_replay/lear_demo_replay_v2.json`; no `docs/`, no v1 bundle, no test or holdout question |
| Installed replay | clean venv, from `/tmp`: `python -m ragformation lear demo replay --plain` | exit 0; checksum verified; "evidence missing here" warnings, because the wheel has no `docs/` |
| Installed examiner | `… lear --backend baseline --data-dir /tmp/whl-data --docs-dir <repo>/docs/lear_backend demo examiner --json` | exit 0; S09 `UNAVAILABLE` with `EVALUATION DATA UNAVAILABLE - PROVIDE AN AUTHORISED SOURCE CHECKOUT`; `/tmp/whl-data` never created |
| Installed live | `… lear demo live --json` | exit 6 |
| Installed effects | `… lear demo effects --preview all --plain` / `--preview nope` | exit 0 / exit 2 |
| Real TTY | `PtyKeyTests` (pseudo-terminal, `TERM=xterm-256color`) | keys `n b → 9 q` → S00, S01, S00, S01, S09, exit 0, ends with an ANSI reset; Ctrl-C → exit 130 with the reset |

## What is not evidence

- **No model result.** There is no TTFT, ITL, tok/s or judge score. The demo cannot produce one in this environment, and the tests assert that it does not invent one.
- **Not a qualifying run.** Baseline answers and timings are pipeline behaviour, labelled `NOT QUALIFYING`.
- **The readiness decision is unchanged.** It is still BLOCKED ([final_report.md](final_report.md)).
