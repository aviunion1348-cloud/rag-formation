# `lear demo` scene map

Each scene lists where its data comes from, how it is labelled, what verifies
it, and its limits.

Provenance labels:

| Label | Meaning |
|---|---|
| `LIVE` | Produced by a real execution in this process. |
| `RECORDED` | Taken from a named evidence file or the replay bundle. |
| `ILLUSTRATIVE` | A conceptual explanation. |
| `UNAVAILABLE` | Blocked or not measured. |

In replay mode every `LIVE` label is rendered as `RECORDED`.

The tests are in [`tests/test_lear_demo.py`](../../tests/test_lear_demo.py),
abbreviated **TD** below. The recorded run is
[`lear_demo_replay_v1.json`](../../ragformation/lear/demo_replay/lear_demo_replay_v1.json)
(run `demo-20261105T200214Z`, commit `132838754d0a`, baseline backend). Its
plain rendering is [`evidence/demo/replay_plain.txt`](evidence/demo/replay_plain.txt).

| Scene | Data source (real function) | Provenance | What it shows | Verified by | Limitations |
|---|---|---|---|---|---|
| **S00 Boot and trust boundary** | `git rev-parse`, `indexer.load_pin`, `LearSettings`, `is_loopback_url`, decision line of `final_report.md` | LIVE + RECORDED | rag-formation commit, Lear source pin, backend, loopback lamp, recorded readiness (BLOCKED) | TD `test_examiner_json_runs_the_real_pipeline_in_scratch_only` (S00 present) | Readiness is read from the report, never inferred |
| **S01 Source-of-truth archaeology** | `docverify.verify_docs`; with `--source`, `docgen.build_docs` into scratch plus a byte comparison | LIVE | 11/11 checks PASS with `--source`; narrative coverage 43/79 modules (54%), all 79 cited somewhere; 10/10 generated files byte-identical | [`evidence/demo/examiner_baseline_plain.txt`](evidence/demo/examiner_baseline_plain.txt); `tests/test_lear_docs.py` | Without `--source`, 9 checks run and coverage is not measured |
| **S02 Knowledge base construction** | `indexer.rebuild` in scratch, `indexer.stats`, `indexer.load_manifest` | LIVE | 19 documents, 355 chunks, 48,676 estimated tokens; hashing fingerprint labelled `NOT semantic`; one full manifest entry with content hash | transcript; `tests/test_lear_pipeline.py` `IndexerTests` | Baseline (hashing) index only. The qualifying index has never been built |
| **S03 Ollama model gate** | `doctor.run_doctor(probe_generation=True)` | LIVE | Doctor exit 6 and a BLOCKED panel. Embedder, LLM and judge tags are shown as `UNAVAILABLE` and unverified | TD `test_live_mode_stops_at_the_model_gate_with_exit_6`; [`evidence/demo/live_ollama_plain.txt`](evidence/demo/live_ollama_plain.txt) | No model metadata exists, so no parameter count is claimed |
| **S04 Request lifecycle** | One `LearAgent.ask` (golden **lear-012**, dev split) and `limiter.counters` before and after | LIVE | 12-step lifecycle. Only retrieval and generation have timings; the other steps are `not instrumented` | TD live test (S04) | Per-step timings do not exist in the agent |
| **S05 Hybrid retrieval** | `answer.retrieval` (candidates, selected, excluded, quarantined, stages) | LIVE + ILLUSTRATIVE | Score ladder of fused scores; stage waterfall in ms; the dense channel is labelled `feature-hash similarity (baseline, NOT semantic)` | transcript; `tests/test_lear_pipeline.py` | Per-channel ranks are not exposed (`UNAVAILABLE`); low context precision (≈.25–.29) |
| **S06 Grounded answer and citations** | The same answer; `agent.manifest()`; `docverify._SourceReader` line counts at the pin | LIVE + RECORDED | The answer, labelled `BASELINE / NOT QUALIFYING`; each citation mapped to a retrieved chunk, a manifest entry, the document lines and pinned source lines; the recorded round-3 verdict for lear-012 | TD live test (`inManifest`/`retrieved` asserted, TTFT `UNAVAILABLE`) | Model TTFT, ITL and tok/s are `not measured: no model stream` |
| **S07 Sessions, memory and compaction** | A second `LearAgent` sharing the engine (budget 300, keep 2, scratch DB); `MemoryStore`; `SessionStore` | LIVE | Five turns, two of them follow-ups with retrieval rewrites; compactions 487→447, 607→453, 564→445 estimated tokens; an outdated memory note ("50") loses to the docs ("5"); the memory guard refuses instructions (exit 2); another user sees 0 memories and cannot open the session (exit 2) | transcript; `tests/test_lear_core.py` compaction and memory tests | The extractive baseline never reads memory. Whether an LLM respects "memory cannot override docs" is UNVERIFIED |
| **S08 Guardrails and refusal** | `ask` with golden lear-068, lear-069 and lear-059; `strip_invalid_citations`; a scratch injected corpus; a real ollama-backend agent; a real `LearRateLimiter` with 1 request per session per minute | LIVE + RECORDED | Results in the table below | TD live test (exit codes 2/3/6/7 and quarantine asserted) | CONFLICTED and PARTIAL are not reproducible on the baseline |
| **S09 Evaluation and rounds** | `evalset.validate`, `report.evaluation_runs` (committed runs only), regression flips computed from `run.json` | LIVE (computed) + RECORDED | 80 cases (10/32/24/14); VALID 6/6; 7 recorded runs; flips R0→R1 fixed lear-005 and lear-028, R1→R2 fixed lear-068, R2→R3 fixed lear-064, test R0→R3 fixed lear-065 and lear-071; **0 regressions**; `JUDGE BLOCKED — SCORES NOT MEASURED` | transcript; [evaluation_dataset.md](evaluation_dataset.md) | Offline scorer only. The holdout was used once; a qualifying claim needs a new versioned check set |
| **S10 Benchmark lab** | Live `run_bench(scratch, users [1,2,5], 2 requests)` plus the recorded official and rate-limit runs | LIVE + RECORDED | Live pipeline-only table, recorded `RECORDED PIPELINE-ONLY BASELINE` table, lanes per request, and the warning `n < 100 … p99 is effectively the maximum and unstable` | transcript; [benchmark_report.md](benchmark_report.md) | No model decode is included; TTFT, ITL and tok/s are `UNAVAILABLE` |
| **S11 Report and readiness** | `report.build_report(scratch)`, the doctor result, the final report decision line | LIVE + RECORDED | `OVERALL: BLOCKED — LOCAL OLLAMA MODEL QUALIFICATION NOT COMPLETED IN THE RECORDED ENVIRONMENT.` plus the 8 steps to qualify | transcript; [final_report.md](final_report.md) | Readiness follows the weakest gate |

## S08 step results (recorded bundle)

| Step | Expected | Observed | Verdict |
|---|---|---|---|
| Input over 2000 characters | exit 2 | exit 2 | MATCH |
| Unknown term, lear-068 ("Kafka") | exit 3 | exit 3 (`unknown_term`) | MATCH |
| Unanswerable, lear-069 | exit 3 | exit 3 (`insufficient_support`) | MATCH |
| Conflicting evidence, lear-059 | CONFLICTED (exit 4) | GROUNDED (exit 0) | LIMITATION: the core conflict detector compares only day/hour/%-unit values across two sources |
| Citation marker `[99]` with no passage | removed | `[99]` removed, `[1]` kept | MATCH |
| Planted instruction page | quarantined, answer not taken over | 1 chunk quarantined (answer_forcing, instruction_override, role_hijack, secret_exfiltration); no `APPROVED` in the answer | MATCH |
| Ollama backend without a model | exit 6, no silent extractive fallback | exit 6 | MATCH |
| Second request inside a 1-per-minute session window | exit 7 | exit 7 (`limit 1/60s; retry in …`) | MATCH |
| Partial coverage | PARTIAL (exit 4) | 0 PARTIAL answers across 7 recorded runs | LIMITATION |

## Director fallback rules

Director mode on the default `ollama` backend without Ollama
([`evidence/demo/director_ollama_plain.txt`](evidence/demo/director_ollama_plain.txt)):

- **S00, S01, S03, S09 and S11** stay live.
- **S08** stays live for the steps that need no index: input validation, provider failure (exit 6) and the rate limit (exit 7).
- **S02 and S04–S07**, which need an index for the configured backend, show `UNAVAILABLE` followed by the bundle's version of the scene, labelled `LIVE BLOCKED: showing recorded evidence instead`.
- **S10** shows only the recorded tables.
- **Exit code:** 6.

**Live mode** stops after S03 instead of falling back.
