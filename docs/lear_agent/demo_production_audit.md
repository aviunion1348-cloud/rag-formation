# `lear demo` production audit

This audit came before the productionisation work (master prompt §1). It
re-checks the demo that shipped in PR #4 against the code rather than its docs.
Fixes are in [demo_production_report.md](demo_production_report.md).

## Baseline

| Item | Value |
|---|---|
| rag-formation commit | `614ed099b7a8` (main after PR #4, merged 2026-11-05T20:18:01Z) |
| Lear source | `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf84088c3a5ec05e8700c52185444e215f`, branch `main` |
| Environment | Debian 12, Python 3.11.2, SQLite 3.40.1, 2 vCPU, 3.8 GiB RAM, no GPU |
| Ollama | Not installed and not installable here: the release host is unreachable from the sandbox |
| Last CI | Run 42101889161 on `a061185`: 5/5 jobs passed (Python 3.9/3.11/3.13, node, wheel) |

## Commands re-run at the baseline

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests` | 240 tests OK (matches the README claim) |
| `cd engine && python3 -m unittest discover -s tests` | 29 tests OK |
| `python3 -m ragformation lear --backend baseline demo examiner --plain --source <Lear>` | exit 0 with Lear on `main`; **exit 5 with Lear on a detached HEAD** (finding 6) |
| `python3 -m ragformation lear demo live` | exit 6 at the model gate (as documented) |
| `python3 -m ragformation lear demo effects --json` | 36 entries |
| AST scan of `demo_render.py` at `614ed09` | `Motion` class: 20 public methods; 19 of the 36 rows share a name with one |
| Scan of the packaged bundle `lear_demo_replay_v1.json` for golden questions | 4 found (finding 7) |

## Findings: claims against code

| # | Claim or behaviour at baseline | What the code did | Severity | Resolution |
|---|---|---|---|---|
| 1 | demo_guide: "The library has **36 effects**" | `EFFECTS` held 36 metadata rows, but the `Motion` class had **20** methods. 19 rows map to a `Motion` method by name; 17 rows (e.g. `query_ribbon`, `model_card`, `readiness_card`) have no dedicated drawing method, so whatever drew them was a generic helper (`table`, `panel`, `bar`, `spark`) shared with other content | Docs overclaim | Replaced by the data-driven registry `lear-demo-effects-v2`: 118 recipes, unique purpose and `(layout, motion, purpose)` each, all rendered by tests ([demo_effects.md](demo_effects.md)) |
| 2 | S04: stages "not instrumented" | True: the agent timed only retrieval and generation | Gap | `LearAgent.ask` now records 13 contiguous `perf_counter` spans (`timings.lifecycle`, schema `lear-lifecycle-v1`). This is additive, read-only telemetry, tested by `LifecycleTests` |
| 3 | S05: per-channel ranks `UNAVAILABLE` | True: candidates carry only channel `reranked` | Core limit | Kept as `UNAVAILABLE — CORE ENGINE DOES NOT EXPOSE THIS STAGE`. The smallest future hook: have the core keep each candidate's dense rank, BM25 rank and RRF score on the candidate record. This is not done, because it would change the RAG core |
| 4 | Keys: `Space` = auto, `p` = previous | As documented, but the master prompt requires `Enter`/`Space` continue and `p` pause/resume | Spec mismatch | `Space` now continues and `p` pauses/resumes; previous moved to `b`/`←`; `n`/`→`, digits and arrows added; real-PTY tests |
| 5 | Labels `LIVE`/`RECORDED`/`ILLUSTRATIVE`/`UNAVAILABLE` | `BLOCKED` and `NOT QUALIFYING` were folded into `UNAVAILABLE` or the narrative | Clarity | Six labels, each with a meaning, shown in S00 |
| 6 | The docs-rebuild check is reproducible | It depends on the Lear checkout's **branch**: a detached HEAD writes `HEAD` into the generated `SOURCE_PIN.json` and `source_inventory.md`, so 2/10 files differ and the demo exits 5 | Operator trap | Documented ([demo_runbook.md](demo_runbook.md) §6); check out `main`. The generator is unchanged, because the committed pages are generated |
| 7 | The packaged bundle is "sanitized" | Paths and secrets were redacted, but the bundle shipped in the wheel with the full text of 4 golden questions (lear-012, lear-061, lear-062, lear-068: dev/calibration) and the Lear answer text | Distribution hygiene | The packaged bundle is now redacted by default (v2); full-text bundles stay in the repository; the v1 bundle moved to `docs/lear_agent/evidence/demo/` |
| 8 | (not claimed) | `demo.py` `GOLDEN_FALLBACK` holds 6 golden questions and `bench.py` 1, so they ship in the wheel as the demo's live inputs | Disclosed | All 7 are calibration/dev cases; none are test or holdout. The golden set is public in this repository. `test_no_test_or_holdout_question_ships_in_the_package` fails if a test or holdout question, or any new question, appears in the package |
| 9 | demo_guide: "There is no video" | True | Gap (user request) | A real pty recording, as `.cast` and `.mp4`, is in [demo_video/](demo_video/README.md) |
| 10 | "Teletype is never shown for extractive output" | True in v1 (`teletype` was used only for real streams) | OK | Kept: `answer_stream` types only with `realStream`, as tested |
| 11 | Replay shows `RECORDED REPLAY` | True | OK | Kept; v2 adds dataset, pin, config, models, metric definitions and terminal profile |
| 12 | README/evaluation test count 240 | True at the baseline | OK | Updated to 256 |

## Evidence

- Baseline transcripts (v1): git history of `docs/lear_agent/evidence/demo/*.txt` at `614ed09`.
- v1 bundle (still replays): [`evidence/demo/lear_demo_replay_v1.json`](evidence/demo/lear_demo_replay_v1.json).
- Current transcripts: [demo_evidence.md](demo_evidence.md).
