# Implementation status

Statuses: `not_started`, `in_progress`, `blocked`, `implemented`, `verified`.
"Implemented" means the code exists and has a passing test; "verified" means the
acceptance scenario itself was executed.

| Requirement | Status | Implementation | Test / evidence | Remaining issue |
|---|---|---|---|---|
| Multi-format ingestion | verified | `loaders.py` | `tests/test_loaders.py` (13) | OCR not supported; PDF needs optional `pypdf` |
| Heading-aware chunking + overlap | verified | `chunking.py` | `tests/test_text_and_chunking.py` (12) | Token counts are estimates |
| Embedding providers (ollama/openai/hashing) | verified (adapters) | `embeddings.py` | `tests/test_ollama_provider.py` (6) | No live model available in this environment |
| Embedding cache | verified | `CachedEmbedder` | `test_store.py::test_embedding_cache_is_model_scoped` | Cache is never evicted; bounded by corpus size |
| Persistent index with provenance | verified | `store.py` | `test_engine_e2e.py::test_persistence_across_restart` | SQLite only |
| Idempotent ingest | verified | `engine._ingest_loaded` | `test_engine_e2e.py::test_ingest_is_idempotent` | |
| Revision update / delete | verified | `store.replace_source`, `delete_source` | `test_engine_e2e.py::test_update_replaces_stale_evidence`, `test_delete_removes_evidence` | |
| Hybrid retrieval (cosine + BM25 + RRF) | verified | `retrieval.py` | `tests/test_retrieval.py` (8) | Brute-force scan |
| Rerank + MMR | verified | `retrieval.py` | `test_retrieval.py::test_rerank_can_be_disabled`, `test_dedupe_overlaps_removes_near_duplicates` | Not a cross-encoder |
| Grounded generation (streaming) | verified (adapters) | `generation.py` | `test_ollama_provider.py::test_generator_streams_and_measures_tokens` | Live model behaviour unverified |
| Extractive fallback | verified | `ExtractiveGenerator` | `test_engine_e2e.py::test_provider_failure_falls_back_visibly`, `test_ollama_provider.py::test_auto_provider_falls_back_when_ollama_is_absent` | Quotes, does not paraphrase |
| Citation validation | verified | `engine._claims_from_answer`, `guardrails.strip_invalid_citations` | `test_engine_e2e.py::test_answer_is_cited_and_citations_resolve` | |
| Abstention | verified | `engine.ask` threshold + sentinel | `test_unanswerable_question_abstains_without_generating` | Threshold calibrated on a 21-chunk corpus |
| Conflict detection | verified | `engine._detect_conflict` | `ConflictDetectionTests` | Only same-unit numeric conflicts |
| Injection guardrail | verified | `guardrails.py` | `test_injection_content_is_quarantined_and_never_answered` | Heuristic patterns |
| API + console | verified | `api.py`, `web/` | `tests/test_api.py` (12) | No SSE streaming in the browser |
| CLI | verified | `cli.py` | `test_evaluation_and_cli.py::CliTests` | |
| Metrics (mean/p50/p95, TTFT, ITL) | verified | `metrics.py`, `queries` table | `test_guardrails.py::MetricsTests`, `stats` output | Percentiles over the recorded window only |
| Golden evaluation | verified | `evaluation.py`, `evals/golden.json` | 14/14 cases; `docs/evaluation.md` | Small synthetic set |
| Health / doctor / rebuild | verified | `engine.health`, `doctor`, `rebuild` | `test_engine_e2e.py::test_missing_vector_is_detected_by_health`, `test_rebuild_re_embeds_without_losing_chunks` | |
| Crash recovery | verified | job recovery + transactional writes | `test_interrupted_job_is_marked_failed_on_startup` | Not tested against a real kill -9 |
| Documentation set | verified | `README.md`, `docs/`, `SECURITY.md` | this file | |
| Multi-tenant authorisation | not_started | `tenant_id` column + `allowed_sources` filter only | — | Documented limitation |
| Background ingestion queue | not_started | synchronous ingest | — | Large batches block the caller |
| ANN index for large corpora | not_started | brute force | — | Documented scale envelope |
| Cross-encoder reranker | not_started | heuristic rerank only | — | Needs a baseline first |
| Cinematic RAG walkthrough (S3) | not_started | `engine/` keeps the trace contract | `engine/tests` (29 pass) | Separate deliverable |
| Live-LLM quality evaluation | blocked | adapter ready | stub-based tests only | Needs an Ollama install or an API key |
| OCR for scanned documents | not_started | — | — | Explicit non-goal |

## Verification runs performed

```text
python3 -m unittest discover -s tests -p 'test_*.py'   → Ran 107 tests ... OK
cd engine && python3 -m unittest discover -s tests      → Ran 29 tests ... OK
python3 -m ragformation ingest corpus                   → 6 indexed, 21 chunks, 11 ms
python3 -m ragformation ask "How long are audit logs retained?"   → answered, 400 days, [1]
python3 -m ragformation ask "…lunar payload insurance?"           → abstained
python3 -m ragformation eval                            → 14/14 passed, recall 1.0, nDCG 0.967
python3 -m ragformation doctor                          → integrity PASS, providers WARN (no Ollama)
python3 -m ragformation serve --port 8787               → console + API served
```

## Deliberate deviations from the specification

1. **No vector database service.** SQLite keeps vectors, metadata and the lexical
   index in one transaction. ADR `0001`.
2. **Deterministic fallbacks instead of mocks.** `hashing` embeddings and the
   extractive generator keep every stage real without a model, and are labelled
   everywhere. ADR `0003`, `0005`.
3. **Console instead of the cinematic walkthrough.** The operator UI ships; the S3
   experience does not. ADR `0007`.
4. **No agent framework.** The pipeline is explicit code. `docs/source_register.md`.
