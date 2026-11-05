# `lear demo` production report

This report covers the productionisation of the terminal demo merged in PR #4.
The earlier reports ([final_report.md](final_report.md), the round reports) are
unchanged; this work does not change their conclusions.

| | |
|---|---|
| Baseline | `614ed099b7a8` (main), 240 tests; audit: [demo_production_audit.md](demo_production_audit.md) |
| Code | `954c8d624515` |
| Evidence and video | `53733261bcc5` (captured at `954c8d624515`, clean tree) |
| Branch | `arena/227b3998-rag-formation` (pushed; not merged) |
| Tests | **256** (143 core, 9 audit regressions, 104 Lear agent, 37 of them for `lear demo`) plus the legacy engine's 29 |

## Readiness labels (§17)

| Subject | Label | Why |
|---|---|---|
| Terminal demo | **DEMO-READY-REPLAY-ONLY** | Every mode works and is tested. Replay and the offline baseline run anywhere (exit 0). The qualifying live path (local Ollama models) cannot run in the recorded environment: `demo live` stops at the model gate with exit 6, as designed, and never manufactures an answer |
| Local model qualification | **BLOCKED** | No Ollama in the recorded environment. Nothing was qualified, so neither QUALIFIED-LOCAL-MODEL nor NOT-QUALIFIED applies |
| Overall Lear agent | **BLOCKED** (unchanged) | `OVERALL: BLOCKED — LOCAL OLLAMA MODEL QUALIFICATION NOT COMPLETED IN THE RECORDED ENVIRONMENT.` ([final_report.md](final_report.md)) |

## Improvement rounds (§14)

### R1: truth and provenance

- **Baseline:** `614ed099b7a8`.
- **Hypothesis:** every number on screen can come from a measured span or a named file, with no new effect on retrieval; replay can carry enough provenance to be checked offline.
- **Changes:**
  - `LearAgent.ask` records 13 contiguous `perf_counter` spans: `timings.lifecycle`, schema `lear-lifecycle-v1`, correlation id. The spans are read-only telemetry, and `timings.totalMs` is unchanged.
  - S04 shows them as a waterfall and a table.
  - S05 adds context assembly (budget gauge, chunk strip, prompt sections with the untrusted-evidence delimiters) and the query vector, and stamps per-channel ranks `UNAVAILABLE — CORE ENGINE DOES NOT EXPOSE THIS STAGE`.
  - S09 adds the five proxy dimensions, `JUDGE BLOCKED — SCORES NOT MEASURED` and a failure explorer.
  - S10 stamps `INDICATIVE — SMALL SAMPLE` on every percentile.
  - S11 keeps the CONFLICTED and PARTIAL limitations listed.
  - New `BLOCKED` and `NOT QUALIFYING` labels.
  - Replay v2 meta: dataset, pin, config fingerprint, models, metric definitions, terminal profile, limitations. The checksum is verified first. Capture is atomic and redacted by default.
  - Scratch removal refuses anything but a real `lear-demo-*` temp dir.
- **Regression tests (written first; red on the old agent):** `LifecycleTests` (3), `SafetyTests` (5), `test_capture_is_atomic_redacted_by_default_and_full_on_request`, `test_full_and_v1_bundles_in_the_repository_still_replay`.
- **Commands:** `python3 -m unittest discover -s tests`; `lear --backend baseline demo examiner --plain --source ../lear-backend`.
- **Results:**
  - Examiner exit 0.
  - The spans tile the request: sum 75.744 ms against a lifecycle total of 75.762 ms in the video run; the test tolerance is ±0.1 ms.
  - Selected chunk ids are identical between a plain `ask`, a second `ask` and the demo's S05.
  - The permanent data directory hash is unchanged.
- **Decision:** keep.

### R2: clarity and human operation

- **Baseline:** R1 working tree.
- **Hypothesis:** an operator needs pause, back, jump and help, and every effect needs a real purpose and a static equivalent. Effects that cannot be told apart do not help.
- **Changes:**
  - `demo_effects.py` is a versioned, data-driven registry (`lear-demo-effects-v2`): 118 recipes in 40 families, from 31 layouts × 11 motions. Each recipe has a unique purpose, and its static form is its final frame.
  - `lear demo effects --preview <id>|all`.
  - Keys: `Enter`/`Space` next, `p` pause/resume auto, `b`/`←` back, `n`/`→` next, digits, `r`, `d`/`e`, `m`, `?`, `q`; arrow escape sequences are parsed.
  - The auto dwell is `6 s × --speed`.
  - The controls line fits in 110 columns.
- **Regression tests:** `EffectRegistryTests` (6), `PtyKeyTests` (2, real pseudo-terminal).
- **Results:**
  - All 118 recipes render ASCII-only with no ANSI under `--plain`.
  - Animated lines equal static lines for every recipe, and the frame count is bounded.
  - In the PTY test, keys `n b → 9 q` give headers S00, S01, S00, S01, S09 and exit 0; `Ctrl-C` gives exit 130 and an ANSI reset.
- **Revised during the round:** the first controls line wrapped at 120 columns, and the `core.other (derived: …)` label was cut off. Both were shortened, and the bundles and video were re-captured.
- **Decision:** keep.

### R3: reproducibility and packaging

- **Baseline:** R2 working tree.
- **Hypothesis:** an installed wheel can demo without private data, and someone can watch the demo without installing anything.
- **Changes:**
  - The packaged bundle is redacted (v2), and the v1 bundle moved out of the package.
  - A package scan test.
  - `scripts/measure_demo_render.py`.
  - A stdlib pty recorder (`scripts/record_demo_video.py`) and a renderer (`scripts/render_demo_video.py`, optional tooling).
  - A committed `.cast`, MP4 and poster.
  - `demo_effects.md`, generated from the registry and sync-tested.
- **Regression tests:** `test_packaged_bundle_is_valid_redacted_and_not_qualifying`, `test_no_test_or_holdout_question_ships_in_the_package`, `test_registry_page_is_generated_from_the_current_registry`.
- **Results (clean venv, wheel installed, run from `/tmp`):**
  - `demo replay` exits 0, with the checksum verified.
  - `demo examiner --json` exits 0. S09 shows `EVALUATION DATA UNAVAILABLE - PROVIDE AN AUTHORISED SOURCE CHECKOUT`, and `--data-dir` was never created.
  - `demo live` exits 6.
  - `effects --preview all` exits 0, and an unknown effect id exits 2.
  - The wheel has no `docs/`, no v1 bundle and no test or holdout question.
  - The MP4 render is byte-identical across runs.
- **Decision:** keep.

## Acceptance matrix (§18)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Audit of the existing demo, claims against code (§1) | PASS | [demo_production_audit.md](demo_production_audit.md) |
| 2 | Modes, flags, S00–S11, exit codes 0–7 and 130 preserved | PASS | `ExitCodeTests`, `LiveBaselineTests`; [cli.py](../../ragformation/lear/cli.py) |
| 3 | Six provenance labels; replay shows `RECORDED REPLAY` | PASS | `test_replay_marks_every_scene_recorded`; [replay_plain.txt](evidence/demo/replay_plain.txt) |
| 4 | ≥100 genuinely distinct effects, data-driven, all fields (§5) | PASS | [demo_effects.md](demo_effects.md); `EffectRegistryTests` |
| 5 | Controls, PTY tests, terminal restored on every exit (§5.3) | PASS | `PtyKeyTests`, `test_ctrl_c_returns_130_and_resets_the_terminal` |
| 6 | Cold open, context assembly, retrieval trace, selected ids unchanged by diagnostics (§6) | PASS | S00, S05; `test_selected_ids_are_identical_with_and_without_demo_diagnostics` |
| 7 | Per-channel dense/BM25/RRF ranks (§6) | PARTIAL | Shown as `UNAVAILABLE — CORE ENGINE DOES NOT EXPOSE THIS STAGE`; hook described in audit finding 3 |
| 8 | Five-dimension evaluation view, `JUDGE BLOCKED`, failure explorer (§7) | PASS | S09 in [examiner_baseline_plain.txt](evidence/demo/examiner_baseline_plain.txt) |
| 9 | LLM-judge scores | BLOCKED | No local judge model; `JUDGE BLOCKED — SCORES NOT MEASURED` |
| 10 | Fresh holdout check (§7) | N/A | The holdout was used once (`final-holdout`); a fresh check needs a new dataset version. None was run |
| 11 | Per-stage monotonic spans, correlation id, stage schema version (§8) | PASS | `LifecycleTests`; S04 |
| 12 | p85/p99 on small samples labelled `INDICATIVE — SMALL SAMPLE` (§8) | PASS | S10 |
| 13 | TTFT / ITL / tok/s only from real streams | PASS (none shown) | S06/S10 show `UNAVAILABLE: not measured: no model stream` |
| 14 | Replay bundle provenance, checksum first, exit 5 on corruption, atomic capture, path redaction (§10) | PASS | [demo_replay_format.md](demo_replay_format.md); `ReplayTests`, `SafetyTests` |
| 15 | Permanent data unchanged by a live demo | PASS | `test_permanent_data_directory_is_byte_identical_after_a_live_demo` |
| 16 | Clean-venv wheel test; `EVALUATION DATA UNAVAILABLE` message (§11) | PASS | R3 results above |
| 17 | No proprietary Lear docs or private eval data in distributables (§11) | PARTIAL | No docs, no test or holdout text, and a redacted bundle. The 7 calibration/dev questions the demo asks live ship in code; they are public in this repository (audit finding 8) |
| 18 | Three rounds with hypothesis, tests, results, decision (§14) | PASS | This page |
| 19 | No heavy media dependencies; renderer overhead measured; `--json` pure (§15) | PASS | [render_overhead_2026-11-06.json](evidence/demo/render_overhead_2026-11-06.json); `test_examiner_json_runs_the_real_pipeline_in_scratch_only` |
| 20 | Docs set (§16) | PASS | [demo_guide.md](demo_guide.md), [demo_scenes.md](demo_scenes.md), [demo_evidence.md](demo_evidence.md), [demo_effects.md](demo_effects.md), [demo_runbook.md](demo_runbook.md), [demo_replay_format.md](demo_replay_format.md) |
| 21 | Live model path | BLOCKED | `demo live` exit 6 ([live_ollama_plain.txt](evidence/demo/live_ollama_plain.txt), video 0:00–0:15) |
| 22 | Demo video and one-click downloads (user request) | PASS | [demo_video/README.md](demo_video/README.md); links checked: ZIP `302 → codeload 200 application/zip attachment`; MP4 `302 → raw.githubusercontent.com`, with the GitHub blob sha equal to the local file |

Totals: 17 PASS, 2 PARTIAL, 2 BLOCKED, 0 FAIL, 1 N/A (row 13 counts as PASS).

## Handoff (§20)

1. **Branch:** `arena/227b3998-rag-formation`, pushed (`954c8d624515` code, `53733261bcc5` evidence, then docs). It is **not merged**; merging needs your go-ahead.
2. **Run it:** `python3 -m ragformation lear demo replay` (any machine) or `python3 -m ragformation lear --backend baseline demo director --source ../lear-backend`.
3. **Watch it:** [`demo_video/lear_demo_terminal.mp4`](demo_video/lear_demo_terminal.mp4?raw=true), 2 min 14 s, real recording.
4. **Labels:** demo DEMO-READY-REPLAY-ONLY; local model qualification BLOCKED; overall BLOCKED (unchanged).
5. **Tests:** 256 plus the legacy 29; all pass locally on Python 3.11 (the grammar of the changed files is also checked against Python 3.9). CI runs on pushes and pull requests to `main`, so it has not run on this branch yet; it will run when a pull request is opened.
6. **Core change:** one additive, read-only telemetry change in `ragformation/lear/agent.py` (lifecycle spans). The retrieval, generation and scoring code is untouched.
7. **Effects:** 118 recipes, 66 wired into scenes, all previewable; the page is generated from the registry.
8. **Keys changed:** `p` now pauses/resumes (it was "previous"); previous is `b`/`←`.
9. **Replay:** the wheel ships a redacted v2 bundle; full-text bundles (v2 and v1) are in `docs/lear_agent/evidence/demo/`.
10. **Not shown, because not exposed by the core:** per-channel dense/BM25/RRF ranks.
11. **Not measured:** model TTFT, ITL, tok/s, judge scores and the qualifying index.
12. **Baseline limitations stay visible:** CONFLICTED not detected for lear-059 (the detector ignores seconds); PARTIAL never produced; low context precision.
13. **Operator trap:** the Lear checkout must be on `main`, not a detached HEAD, for the S01 rebuild check ([runbook §6](demo_runbook.md)).
14. **Small-sample percentiles** are labelled `INDICATIVE`; the official bench is `RECORDED PIPELINE-ONLY BASELINE`.
15. **Data safety:** scratch-only writes, guarded deletion, permanent-data hash test, loopback-only models.
16. **Distribution:** no docs or evals in the wheel; 7 calibration/dev questions ship as demo inputs (audit finding 8).
17. **Media tooling** (`pyte`, Pillow, `imageio-ffmpeg`) is used only by the render script, not by the project.
18. **Renderer cost:** 6.3 ms static and 11.6 ms with all frames for 12 scenes; pacing is 21 s at speed 1 and 0 with `--speed 0`.
19. **To qualify a model:** follow the 8 steps on S11. Run Ollama on 127.0.0.1; pull and verify the three models; `lear doctor` must exit 0; rebuild the index with `--full`; run calibration/dev with the judge on; recalibrate the abstention threshold on calibration/dev only; score the test split once; bench with model streaming. A qualifying final claim also needs a new, versioned check set, because the holdout was already used.
20. **Next small hooks:** per-candidate channel ranks in the core; a CONFLICTED detector that handles seconds; a new holdout version for a fresh check.

**Examiner paragraph.** `lear demo` is a terminal walk-through of the real Lear
agent pipeline, not a mock-up. It indexes the pinned Lear documentation, answers
a real question with citations traced to Lear source lines, and shows memory,
guardrails, the evaluation history and a benchmark, labelling every value
`LIVE`, `RECORDED`, `ILLUSTRATIVE`, `UNAVAILABLE`, `BLOCKED` or
`NOT QUALIFYING`. In the recorded environment no local Ollama model was
available. The qualifying path therefore stops at the model gate (exit 6),
the offline baseline runs live and is labelled not qualifying, and a
checksummed replay of a real capture runs anywhere. The demo is
DEMO-READY-REPLAY-ONLY. Model qualification is BLOCKED, and nothing in the
demo, the video or the evidence claims otherwise.
