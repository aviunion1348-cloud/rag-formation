# `lear demo` evidence

Everything on this page was produced by the commands shown, on 2026-11-05 (UTC)
in the audit sandbox. The sandbox had no Ollama, no GPU, 2 vCPU and Python
3.11.2 (see [audit_environment.md](audit_environment.md)).

## Recorded replay bundle

[`ragformation/lear/demo_replay/lear_demo_replay_v1.json`](../../ragformation/lear/demo_replay/lear_demo_replay_v1.json)
is shipped in the wheel through `[tool.setuptools.package-data]` in
`pyproject.toml`. It was produced with this command:

```bash
python3 -m ragformation lear --backend baseline demo capture \
    --source <Lear checkout> --out ragformation/lear/demo_replay/lear_demo_replay_v1.json
```

| Field | Value |
|---|---|
| schema | `lear-demo-replay-v1` |
| runId | `demo-20261105T200214Z` |
| capturedAt | `2026-11-05T20:02:14Z` |
| gitCommit / dirty | `132838754d0a` / false (tracked tree clean at capture) |
| backend / qualifying | `baseline` / **false** |
| scenes | S00–S11 (12) |
| body sha256 | `cb7ce8fb8360d57b5848014615c6164565c4df9d2cb45d107b5cb2766f0b3b70` |
| evidence hashes | sha256 of 20 cited repository files, re-checked on every replay |

### Integrity rules

These rules are implemented in `validate_bundle` in
[`demo.py`](../../ragformation/lear/demo.py).

**Hard failures.** Each makes replay exit 5, and the demo then shows explanations only:

- the schema is wrong;
- a provenance field is missing (`provenance`, `runId`, `capturedAt`, `gitCommit`, `backend`, `demoVersion`);
- the body checksum does not match;
- the scene records are malformed.

**Warnings.** A cited evidence file that is missing or has changed since capture. An installed wheel has no `docs/`, so it warns that each file is missing.

The tests are in `ReplayTests` in [`tests/test_lear_demo.py`](../../tests/test_lear_demo.py):

- `test_bundle_round_trip_and_tamper_detection`
- `test_missing_evidence_is_a_warning_not_a_crash`
- `test_replay_marks_every_scene_recorded`
- `test_corrupt_bundle_exits_5_with_explanation_only`
- `test_packaged_bundle_is_valid_and_not_qualifying`

## Transcripts

All files below are in [`evidence/demo/`](evidence/demo/). The first line of
each `.txt` file is the command, and the last line is the exit code.

| File | Command | Exit | What it demonstrates |
|---|---|---|---|
| [`examiner_baseline_plain.txt`](evidence/demo/examiner_baseline_plain.txt) | `lear --backend baseline demo examiner --plain --source <Lear checkout>` | 0 | All 12 scenes live on the baseline: docs 11/11 PASS and 10/10 rebuilt files identical; 355-chunk scratch index; doctor exit 6; lear-012 answered GROUNDED with citation `[4]` → `api_reference.md` lines 32-34 → `prash/cli.py:L1251`, `L351`, `L460` (each within the file at the pin); compactions; guardrails; bench; BLOCKED readiness |
| [`examiner_baseline_s04_s06_s08.json`](evidence/demo/examiner_baseline_s04_s06_s08.json) | `lear --backend baseline demo examiner --json --scene 4,6,8` | 0 | JSON event log. It parses as one object and contains no ANSI escapes (also asserted in tests) |
| [`replay_plain.txt`](evidence/demo/replay_plain.txt) | `lear demo replay --plain` | 0 | 12 scenes, each under `RECORDED REPLAY - run demo-20261105T200214Z …`; no `[LIVE]` label |
| [`live_ollama_plain.txt`](evidence/demo/live_ollama_plain.txt) | `lear demo live --plain` (default ollama backend) | **6** | Stops at S03 with `LIVE MODE STOPPED AT THE DEPENDENCY GATE` and offers replay |
| [`director_ollama_plain.txt`](evidence/demo/director_ollama_plain.txt) | `lear demo director --plain --auto` (default ollama backend) | **6** | Live where possible; blocked scenes are followed by recorded evidence labelled `LIVE BLOCKED` |
| [`effects_plain.txt`](evidence/demo/effects_plain.txt) | `lear demo effects --plain` | 0 | 36 effects, each with trigger, reduced-motion equivalent and data source |

**The live bench numbers differ between runs.** Each run is a new 2-vCPU
measurement over 2–10 requests per level. The bundle's live levels were:

| Users | ok/requests | p50 (ms) | p99 (ms) |
|---|---|---|---|
| 1 | 2/2 | 29.7 | 55.1 |
| 2 | 4/4 | 72.3 | 88.2 |
| 5 | 10/10 | 91.9 | 450.7 |

With n ≤ 10, these p99 values are the maximum and are unstable. That is why the
screen prints a warning. The official recorded baseline is in
[benchmark_report.md](benchmark_report.md).

## Test and packaging results

| Gate | Command | Result |
|---|---|---|
| Demo tests | `python3 -m unittest discover -s tests -p 'test_lear_demo.py'` | 21 tests OK |
| Full unit suite | `python3 -m unittest discover -s tests` | 240 tests OK (219 before the demo + 21) |
| Wheel contents | `python3 -m pip wheel . --no-deps --no-build-isolation -w /tmp/whl` then `python3 -m zipfile -l …` | Contains `ragformation/lear/demo.py`, `demo_render.py` and `demo_replay/lear_demo_replay_v1.json` |
| Installed replay | `/tmp/whlvenv/bin/python -m ragformation lear demo replay --plain`, run from `/tmp` | exit 0, with "evidence missing here" warnings because the wheel has no `docs/` |
| Installed examiner | `RAG_LEAR_DOCS_DIR=<repo>/docs/lear_backend …/python -m ragformation lear --backend baseline --data-dir /tmp/whl-data demo examiner --json` | exit 0. S09 is `UNAVAILABLE` because the golden set is not in the wheel. `/tmp/whl-data` was never created |
| Installed live | `…/python -m ragformation lear demo live --json` | exit 6 |
| TTY path | `TERM=xterm-256color script -qec "… demo director --auto --speed 0 …" /dev/null` | exit 0; colour output ends with an ANSI reset |
| Keys | pty with `Enter`, `d`, `q` | three control prompts, then a clean exit 0 |

## What is not evidence

- **No model result.** The demo contains none: no TTFT, ITL, tok/s or judge score. It cannot produce one in this environment, and the tests assert that it does not invent one.
- **Not a qualifying run.** The baseline answers and timings are pipeline behaviour.
- **The final readiness decision is unchanged.** It is still BLOCKED ([final_report.md](final_report.md)).
