# Implementation status

Statuses: `not_started`, `in_progress`, `blocked`, `implemented`, `verified`.
"Implemented" means the code exists and has a passing test; "verified" means the
acceptance scenario itself was executed.

| Requirement | Status | Implementation | Test / evidence | Remaining issue |
|---|---|---|---|---|
| Multi-format ingestion | verified | `loaders.py` | `tests/test_loaders.py` (13) | OCR not supported; PDF needs optional `pypdf` |
| Heading-aware chunking + overlap | verified | `chunking.py` | `tests/test_text_and_chunking.py` (12) | Token counts are estimates |
| Embedding providers (ollama/openai/hashing/spaCy/LSA) | verified (adapters) | `embeddings.py` | `tests/test_ollama_provider.py` (6), `test_retrieval.py` (LSA fit/persist) | No live model or pretrained weights available in this environment, so no semantic-quality number is claimed |
| Embedding cache | verified | `CachedEmbedder` | `test_store.py::test_embedding_cache_is_model_scoped` | Cache is never evicted; bounded by corpus size |
| Persistent index with provenance | verified | `store.py` | `test_engine_e2e.py::test_persistence_across_restart` | SQLite only |
| Idempotent ingest | verified | `engine._ingest_loaded` | `test_engine_e2e.py::test_ingest_is_idempotent` | |
| Revision update / delete | verified | `store.replace_source`, `delete_source` | `test_engine_e2e.py::test_update_replaces_stale_evidence`, `test_delete_removes_evidence` | |
| Hybrid retrieval (cosine + BM25 + RRF) | verified | `retrieval.py` | `tests/test_retrieval.py` (8) | Brute-force scan |
| Rerank + MMR | verified | `retrieval.py` | `test_retrieval.py::test_rerank_can_be_disabled`, `test_dedupe_overlaps_removes_near_duplicates` | Not a cross-encoder |
| Grounded generation (streaming) | verified (adapters) | `generation.py` | `test_ollama_provider.py::test_generator_streams_and_measures_tokens` | Live model behaviour unverified |
| Extractive fallback | verified | `ExtractiveGenerator` | `test_engine_e2e.py::test_provider_failure_falls_back_visibly`, `test_ollama_provider.py::test_auto_provider_falls_back_when_ollama_is_absent` | Quotes, does not paraphrase |
| Citation validation | verified | `engine._claims_from_answer`, `guardrails.strip_invalid_citations` | `test_engine_e2e.py::test_answer_is_cited_and_citations_resolve` | |
| Abstention | verified | `engine.ask` threshold + sentinel | `test_unanswerable_question_abstains_without_generating`; calibration sweep in `evaluation_v2` | Distributions overlap (gap −0.085); 0.24 chosen by balanced accuracy on the calibration split |
| Conflict detection | verified | `engine._detect_conflict`, `_conflict_from_evidence`, `_merge_conflict_citations` | `ConflictDetectionTests` (4 cases incl. entity gate + citation provenance); v2 conflict precision/recall 1.0 | Only same-unit numeric conflicts; other contradictions are not detected |
| Injection guardrail | verified | `guardrails.py` | `test_injection_content_is_quarantined_and_never_answered` | Heuristic patterns |
| API + console | verified | `api.py`, `web/` | `tests/test_api.py` (14), `tests/test_integration_kit.py` (13) | Legacy `/api` routes retained for the console |
| Versioned event contract + SSE | verified | `events.py`, `/api/v1/events`, `/api/v1/events/recent` | `test_experience.py::EventsRecentEndpointTests`; Live mode in the jsdom smoke test | Sequence is per trace; documented in `docs/events.md` |
| Cinematic walkthrough (S3) | implemented, partially verified | `ragformation/web/experience/` | 39 Node contract checks + 23 jsdom smoke checks + HTTP static tests | **WebGL not verified on a GPU**; visual quality not assessed |
| CLI | verified | `cli.py` | `test_evaluation_and_cli.py::CliTests` | |
| Metrics (mean/p50/p95, TTFT, ITL) | verified | `metrics.py`, `queries` table | `test_guardrails.py::MetricsTests`, `stats` output | Percentiles over the recorded window only |
| Golden evaluation (v1, frozen gate) | verified | `evaluation.py`, `evals/golden.json` | 14/14 cases; `docs/evaluation.md` §1–7 | Small synthetic set |
| Evaluation v2 (46 cases, splits, ablations) | verified | `evaluation_v2.py`, `evals/golden_v2.json` | 39/46; `docs/evaluation.md` §8; `evals/results/golden-v2-hashing-extractive.json` | 7 known failures with lexical embeddings; labels are single-author |
| Health / doctor / rebuild | verified | `engine.health`, `doctor`, `rebuild` | `test_engine_e2e.py::test_missing_vector_is_detected_by_health`, `test_rebuild_re_embeds_without_losing_chunks` | |
| Crash recovery | verified | job recovery + transactional writes | `test_interrupted_job_is_marked_failed_on_startup` | Not tested against a real kill -9 |
| Documentation set | verified | `README.md`, `docs/`, `SECURITY.md` | this file; `docs/events.md`, `docs/integration.md`, `docs/experience.md`, `docs/benchmarks.md`, `docs/post_merge_audit.md` | |
| Installable package | verified | `pyproject.toml` | wheel built, installed in a clean venv, `/experience` served and a cited answer produced from site-packages; CI asserts wheel contents | No PyPI publication (explicitly out of scope) |
| Measured scale | verified | `scripts/bench_scale.py` | 91 / 441 / 875 chunks: p50 10.8 / 24.5 / 42.5 ms; `docs/benchmarks.md` | Correctness at scale, not quality at scale |
| CI | verified locally, workflow not yet run on GitHub | `.github/workflows/ci.yml` | workflow parsed and every command executed by hand on this machine | First GitHub run happens on push |
| Integration kit | verified | `integrations/` | `tests/test_integration_kit.py`: Python client, Node client, proxy example, impersonation attempt dropped, curl quickstart executed | Node client tested against Node 22 locally |
| Multi-tenant authorisation | implemented, partially verified | `auth.py` (roles, tenant, host principal), SQL predicates by tenant | `test_api.py::test_allowed_sources_can_only_narrow_the_principal_scope`, `test_integration_kit.py::HostPrincipalTests` | No auth server, no key rotation; isolation is per-request scope, not a hardened multi-tenant deployment |
| Background ingestion queue | not_started | synchronous ingest | — | Large batches block the caller |
| ANN index for large corpora | not_started | brute force | — | Documented scale envelope |
| Cross-encoder reranker | not_started | heuristic rerank only | — | Needs a baseline first |
| Cinematic RAG walkthrough (S3) | implemented | `ragformation/web/experience/` | see the row above | WebGL path unverified on hardware |
| Live-LLM quality evaluation | blocked | adapter ready | stub-based tests only | Needs an Ollama install or an API key |
| OCR for scanned documents | not_started | — | — | Explicit non-goal |

## Verification runs performed

```text
python3 -m unittest discover -s tests -p 'test_*.py'   → Ran 143 tests ... OK (~20 s)
cd engine && python3 -m unittest discover -s tests      → Ran 29 tests ... OK
python3 -m ragformation ingest corpus                   → 14 indexed, 59 chunks
python3 -m ragformation ask "How long are access review evidence retained?"
                                                        → needs_review, 5 vs 3 years, both cited
python3 -m ragformation ask "How long are audit logs retained?"   → answered, 400 days, [1]
python3 -m ragformation ask "…lunar payload insurance?"           → abstained
python3 -m ragformation eval --v1                       → 14/14, recall 1.0, MRR 1.0, nDCG 0.952
python3 -m ragformation eval                            → 39/46 (report only), listed failures
python3 -m ragformation eval --gate calibration --min-pass-rate 1.0 → gate held (12/12)
python3 scripts/bench_scale.py --chunks 100 500 1000    → 91/441/875 chunks, p50 10.8/24.5/42.5 ms
node tests/experience_contract.mjs                      → 39 checks passed
node scripts/experience_smoke.mjs --jsdom …             → 23 checks passed
python scripts/record_experience_demo.py                → fixture re-recorded from a real run
bash integrations/curl/quickstart.sh                    → all steps passed against a keyed server
build wheel → install in clean venv                     → /experience served, cited answer produced
```

## Deliberate deviations from the specification

1. **No vector database service.** SQLite keeps vectors, metadata and the lexical
   index in one transaction. ADR `0001`.
2. **Deterministic fallbacks instead of mocks.** `hashing` embeddings and the
   extractive generator keep every stage real without a model, and are labelled
   everywhere. ADR `0003`, `0005`.
3. **Console plus a cinematic walkthrough.** The operator console ships at `/`,
   and the S3 experience ships at `/experience` as a separate, honest layer over
   the same engine and event contract. ADR `0007` is superseded on this point.
4. **No agent framework.** The pipeline is explicit code. `docs/source_register.md`.
