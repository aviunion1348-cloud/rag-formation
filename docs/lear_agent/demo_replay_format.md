# `lear demo` replay bundle format

A replay bundle is one JSON file holding the scene records of a real capture
(`lear demo capture`). `lear demo replay` renders it without Ollama, without an
index and without the network. The implementation is `build_bundle`,
`write_atomic`, `validate_bundle` and `load_bundle` in
[`ragformation/lear/demo.py`](../../ragformation/lear/demo.py).

## Bundles in this repository

| File | Schema | Run | Commit | Text | Where it ships |
|---|---|---|---|---|---|
| [`ragformation/lear/demo_replay/lear_demo_replay_v2.json`](../../ragformation/lear/demo_replay/lear_demo_replay_v2.json) | `lear-demo-replay-v2` | `demo-20261105T205910Z` | `954c8d624515` (clean) | **redacted** | in the wheel (`package-data`) |
| [`evidence/demo/lear_demo_replay_v2_full.json`](evidence/demo/lear_demo_replay_v2_full.json) | `lear-demo-replay-v2` | `demo-20261105T205915Z` | `954c8d624515` (clean) | full | repository only |
| [`evidence/demo/lear_demo_replay_v1.json`](evidence/demo/lear_demo_replay_v1.json) | `lear-demo-replay-v1` | `demo-20261105T200214Z` | `132838754d0a` (clean) | full | repository only (moved out of the package) |

`lear demo replay` without `--bundle` uses the full v2 bundle in a source
checkout and the packaged, redacted one in an installed wheel (`default_bundle`).

## Top level

```json
{"schema": "lear-demo-replay-v2", "meta": {...}, "scenes": [...], "sha256": "<hex>"}
```

`sha256` is the SHA-256 of the canonical JSON of `{"meta", "scenes"}`
(`evalset.canonical`: `sort_keys=True`, `separators=(",", ":")`,
`ensure_ascii=False`, UTF-8).

## `meta` (v2)

| Field | Meaning |
|---|---|
| `provenance` | `LIVE_CAPTURE`: the scenes were produced by real calls |
| `runId` / `replayId` | `demo-<UTC timestamp>` |
| `capturedAt`, `timezone` | ISO-8601 UTC time of capture, and `UTC` |
| `gitCommit`, `gitDirty` | rag-formation commit (12 hex) and whether tracked files had local changes |
| `backend`, `qualifying` | `baseline` / `false` here; `qualifying` is true only for a passing Ollama run |
| `demoVersion`, `effectsRegistry` | `lear-demo-v2`, `lear-demo-effects-v2` |
| `datasetVersion` | path and file sha256 of `evals/lear/golden_lear_v1.json` (`null` when absent) |
| `sourcePin` | the Lear repository, commit and branch the docs were generated from |
| `configFingerprint` | sha256 of the settings that change behaviour (backend, models, top-k, budgets, limits) and the settings themselves |
| `models` | embedder, LLM and judge tags, whether doctor passed, and `availability` (`UNVERIFIED: doctor did not pass` here) |
| `metricDefinitions` | TTFT, ITL, decode tok/s, percentiles, lifecycle and correct: what each number means |
| `terminalProfile` | capture terminal facts (captures record data, not frames) |
| `limitations` | the union of the scenes' limitations (13 here) |
| `contentRedacted`, `redactionPolicy` | whether question/answer text was redacted, and the policy id (`lear-demo-redaction-v1`) |
| `evidence` | sha256 of every cited repository file at capture (19 files) |

## Scene records

Each scene has `id`, `title`, `provenance`, `commands`, `facts` (each fact has
`k`, `v`, provenance `p` and an optional `src`), `steps` (expected vs observed
with a verdict), `data` (the scene's visual data), `evidence`, `limitations`,
`blocked`, `narrative` and `collectMs`.

## Validation order (`validate_bundle`)

1. **Checksum first.** Recompute `sha256` before trusting any field. A mismatch is a hard failure.
2. **Schema** must be `lear-demo-replay-v1` or `-v2`.
3. **Required meta.** v1 needs `provenance`, `runId`, `capturedAt`, `gitCommit`, `backend` and `demoVersion`; v2 adds `replayId`, `timezone`, `datasetVersion`, `sourcePin`, `configFingerprint`, `models`, `metricDefinitions`, `terminalProfile` and `limitations`. A key that is absent, `null` or `""` is a hard failure; an empty list is allowed.
4. **Scene shape.** `scenes` must be a list of objects, each with an `id`.
5. **Evidence drift.** A cited file that is missing or changed since capture is a **warning**, not a failure. An installed wheel has no `docs/`, so it warns for each file.

Any hard failure makes `replay` exit **5** and show explanations only. The
tests are `ReplayTests` in [`tests/test_lear_demo.py`](../../tests/test_lear_demo.py).

## Capture safety

- **Atomic.** `write_atomic` writes `.<name>.<pid>.tmp` in the target directory, calls `fsync`, sets mode `0600`, then `os.replace`s it. A failed write leaves the old bundle intact and no temporary file behind (`test_atomic_write_keeps_the_old_bundle_when_the_replace_fails`).
- **Sanitised.** The scratch path becomes `<scratch>`, the home directory becomes `~`, and credential-shaped strings (`SECRET_PATTERNS`) become `[REDACTED]`.
- **Redacted by default.** Golden questions, follow-up history, answer text and retrieval rewrites are replaced with tags such as `[REDACTED question lear-012: 46 chars, sha256 <12 hex>]` (`_redaction_tag`); ids, statuses, metrics, timings and citation locations stay. `--full` keeps the text and is meant for the repository only (`test_capture_is_atomic_redacted_by_default_and_full_on_request`).
- **No test or holdout text in the package.** `test_no_test_or_holdout_question_ships_in_the_package` scans every shipped package file.
