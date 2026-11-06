# Manager handoff — Lear RAG agent session recordings

Run `lear-rec-20261106T1219Z` · 2026-11-06 · branch `arena/c2d86cd7-rag-formation` · generated 2026-11-06T12:45:34Z

## 1. Executive summary

The existing Lear agent was run for real in all nine scenarios and every run was recorded in the terminal with asciinema (real timing), rendered to 1080p MP4 and verified. Judged by checks registered before execution: **2 PASS, 3 FAIL, 4 PARTIAL, 0 BLOCKED.** The decisive limitation: **Ollama is BLOCKED here** (not installed; model hosts unreachable), so no answer comes from an LLM. All answers come from the agent's labelled offline baseline (hashing embeddings and extractive synthesis). Overall verdict: **PARTIALLY COMPLETE**.

## 2. What the recordings demonstrate (evidence-backed)

- The real retrieval pipeline end to end, with the engine's own persisted stage trace: query embedding, semantic + lexical retrieval, RRF fusion, heuristic rerank, MMR, guardrail scan, context assembly, generation, citation validation (S01, S03, S05-S07).
- Citations with document and line ranges, validated by the agent (every grounded answer).
- Correct abstention on unanswerable questions, by the scope guard and by the support threshold (S04: 3/3).
- Injection attempts produce no secrets, no prompt text and no tool execution; the output guard flags a meta-disclosure request (S05).
- Real session compaction with persisted summaries (3 compactions in S07) and use of a long-term memory note (S02).
- Classified dependency failure without silent fallback, refusal of a non-loopback model URL, rate limiting with retry-after, and recovery (S08: 6/6).
- Reproducible evaluation: 42/42 cases identical to the committed round-3 run; a dummy-user benchmark (S09).

Not demonstrated: any LLM output, semantic embeddings, an LLM judge, model latency or throughput, tool calling.

## 3. Technical environment

| component | value |
|---|---|
| OS / hardware | Debian GNU/Linux 12 (bookworm), kernel 6.1.158+ · x86_64 · 2 vCPU · 3939 MiB RAM · no GPU · no display server |
| runtime / app | Python 3.11.2 (CPython) · SQLite 3.40.1 · ragformation 0.2.0 · Lear agent CLI `python3 -m ragformation lear` |
| LLM | configured ollama:qwen2.5:1.5b — **UNAVAILABLE (BLOCKED)**; used: extractive-synthesis-v1 (**not an LLM**) |
| embeddings | configured ollama:nomic-embed-text — **UNAVAILABLE (BLOCKED)**; used: hashing-lexical-v1 (baseline, lexical) |
| vector store | SQLite ragformation.db: vectors table (exact brute-force cosine in Python) + FTS5 chunks_fts (BM25) |
| reranker | heuristic-coverage-v1 (rule-based support score; not a model), then MMR (lambda 0.72) |
| memory | SQLite agent.db: sessions, messages, compactions (lear-compaction-v1 summaries), memories (scoped long-term memory) |
| recording | asciinema 2.4.0 (asciicast v2, real timing, no idle-time limit) inside util-linux script 2.38.1; MP4 rendered by scripts/session_recordings/render_cast.py (pyte 0.8.2, Pillow 13.0.0, ffmpeg n9.0.2, libx264) |
| test instrumentation | scripts/session_recordings/ (harness, renderer, verifier); no application file was changed |
| injected faults | S08 only: an unreachable non-loopback Ollama URL (192.0.2.10, RFC 5737 TEST-NET-1) set for one command |
| mocked components | none |

## 4. Scenario results

| # | scenario | status | checks | capability | recording |
|---|---|---|---|---|---|
| 01 | Normal successful RAG | **FAIL** | 3/4 | retrieval, reranking, context assembly, extractive answer, citation validation, engine trace | [01_successful_rag_session.mp4](01_successful_rag_session.mp4) |
| 02 | Multi-turn conversation and context retention | **FAIL** | 6/7 | session history, follow-up rewriting, long-term memory note | [02_multiturn_memory_session.mp4](02_multiturn_memory_session.mp4) |
| 03 | Multi-document retrieval | **FAIL** | 1/2 | citations from two distinct documents | [03_multidocument_retrieval.mp4](03_multidocument_retrieval.mp4) |
| 04 | Unanswerable question and abstention | **PASS** | 3/3 | abstention: scope guard and insufficient-support threshold | [04_abstention_session.mp4](04_abstention_session.mp4) |
| 05 | Prompt injection and security guardrails | **PARTIAL** | 4/4 | injection resistance, output guard, no tool execution | [05_guardrail_security_session.mp4](05_guardrail_security_session.mp4) |
| 06 | Conflicting document evidence | **PARTIAL** | 1/2 | surfacing contradictory evidence; conflict detector | [06_conflict_detection_session.mp4](06_conflict_detection_session.mp4) |
| 07 | Memory and context compaction | **PARTIAL** | 2/3 | session compaction (lear-compaction-v1) and recall after it | [07_context_compaction_session.mp4](07_context_compaction_session.mp4) |
| 08 | Error, failure and recovery | **PASS** | 6/6 | dependency failure, config refusal, rate limit and recovery | [08_error_recovery_session.mp4](08_error_recovery_session.mp4) |
| 09 | Evaluation and benchmarking | **PARTIAL** | 4/4 | golden-set evaluation reproduction and dummy-user benchmark | [09_evaluation_benchmark_session.mp4](09_evaluation_benchmark_session.mp4) |

## 5. Performance

All timings below were measured by the agent itself (monotonic clock) and copied verbatim. They are **PIPELINE-ONLY**: generation is extractive, so there is no model latency, no model TTFT and no token throughput in this package.

| scenario | step | total ms | TTFT ms (first extractive chunk) | ITL p50 ms | ITL p99 ms | ITL p95 | tokens/s |
|---|---|---|---|---|---|---|---|
| 01 | a1 | 67.42 | 59.43 | 0.071 | 0.111 | N/A (not emitted) | N/A (estimates only) |
| 02 | t2 | 86.59 | 78.97 | 0.071 | 0.099 | N/A (not emitted) | N/A (estimates only) |
| 03 | q1 | 73.37 | 64.96 | 0.07 | 0.081 | N/A (not emitted) | N/A (estimates only) |
| 05 | p1 | 68.16 | 58.09 | 0.07 | 0.078 | N/A (not emitted) | N/A (estimates only) |
| 06 | k1 | 72.09 | 63.08 | 0.067 | 0.076 | N/A (not emitted) | N/A (estimates only) |
| 07 | f1 | 76.27 | 67.25 | 0.07 | 0.077 | N/A (not emitted) | N/A (estimates only) |

Benchmark (S09, 5 users × 10 requests, pipeline-only): 50/50 ok, 25.822 req/s, total ms p50 63.71 / p99 1599.14, TTFT ms p50 57.72. Model TTFT, ITL p95 and tokens/s: **N/A** — no model ran (Ollama BLOCKED); the agent reports ITL p50/p85/p99, not p95; its token counts are character-based estimates.

## 6. What worked

- Every scenario executed against the real agent and was recorded; 10 valid terminal recordings and 11 valid MP4 files (see verification).
- S04 (abstention) and S08 (error handling and recovery) passed every check.
- Evaluation reproduction was exact (42/42); traces, sessions, compaction and memory records were all persisted and exported.

## 7. What failed

- S01, S03: grounded, cited answers that miss expected key facts (the extractive baseline picks related but not decisive sentences).
- S02, S07: follow-up questions that depend on earlier turns missed the fact (history reaches the baseline only via query rewriting against the previous turn).
- S06: the contradiction is surfaced in the answer text, but the engine's conflict detector did not flag it.
- S08 attempt 1 aborted on a bug in the recording harness (not the agent); fixed, re-recorded, and the failed attempt is kept.

## 8. Unverified

- Any behaviour with a real LLM or semantic embedder (qwen2.5:1.5b, nomic-embed-text): not run, so answer quality, model latency, token throughput and LLM-judge scores are unknown.
- Model sizes: preflight O04 lists the configured models with nominal sizes taken from the settings defaults (nomic-embed-text ~137M, qwen2.5:1.5b ~1.5B, llama3.2:1b ~1.2B); no Ollama metadata could be read to confirm them.

## 9. Recording locations

All files are in `artifacts/agent_session_recordings/`: nine scenario MP4s, `complete_agent_session.mp4`, `terminal/` (asciicasts), `traces/` (per-step outputs, answers, engine traces, events), `evidence/` (preflight, results, render reports, verification, frames, attempts), `session_index.md`, `session_metadata.json`, `recording_verification.md`, `PREFLIGHT_REPORT.md`, `checksums.sha256`.

## 10. Replay instructions

- Videos: open any MP4 (1920x1080 H.264, no audio). Each starts with the real-time playback, then the paced replay.
- Terminal: `pip install asciinema==2.4.0` then `asciinema play terminal/01_successful_rag_session.cast` (add `-s 0.25` for quarter speed). Details: [terminal/README.md](terminal/README.md).
- Re-run: [scripts/session_recordings/README.md](../../scripts/session_recordings/README.md).

## 11. Integrity statement

Every operation shown is from a real execution of the existing agent at the recorded commit. No event, token stream, score, tool call, answer or telemetry value was manufactured. Extractive output is labelled as such and never called LLM output. The only fault injected is the S08 test URL, labelled in the recording. Scenario inputs were registered before execution; failures are reported as failures and nothing was cut. Checksums for every file are in `checksums.sha256`.

