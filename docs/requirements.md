# Requirements and acceptance criteria

Statuses: `verified` (evidence exists and was run), `implemented` (code exists,
covered by a test but not the full acceptance scenario), `partial`, `deferred`.

## A. Core RAG path (from S1 + S2)

| ID | Requirement | Acceptance criterion | Status | Evidence |
|---|---|---|---|---|
| R1 | Accept multiple source formats | Markdown, text, HTML, JSON, CSV, DOCX parse into structured blocks with page/section provenance; unsupported input fails with a stable error code | verified | `tests/test_loaders.py` (13 tests) |
| R2 | Extract structure, not just text | Headings, paragraphs, lists, tables and code survive extraction; page numbers retained where the format has them | verified | `tests/test_loaders.py::test_markdown_preserves_headings_and_paragraphs`, `…test_docx_is_parsed_without_third_party_libs` |
| R3 | Chunk by heading/topic (S1) | A heading always begins a new chunk and is carried inside it; section path stored per chunk; overlap measured and recorded | verified | `tests/test_text_and_chunking.py::ChunkingTests` |
| R4 | Real embeddings, configurable backend | Ollama, OpenAI-compatible and a labelled lexical fallback; dimension probed from the model, never hard-coded | verified (adapter) / unverified (live model) | `tests/test_ollama_provider.py`; `docs/evaluation.md` §4 |
| R5 | Persistent index with provenance | Vectors, chunk text, source id, revision, section, page and content hash survive a restart | verified | `tests/test_engine_e2e.py::test_persistence_across_restart`, `test_chunks_carry_provenance` |
| R6 | Idempotent ingestion | Re-ingesting unchanged content creates no duplicates and no new vectors | verified | `…::test_ingest_is_idempotent` |
| R7 | Update / replace a source | New revision replaces old chunks atomically; stale evidence can no longer be retrieved | verified | `…::test_update_replaces_stale_evidence`, `test_store.py::test_replace_source_swaps_revision_and_drops_stale_chunks` |
| R8 | Delete a source | Chunks, vectors and lexical rows removed in one transaction | verified | `…::test_delete_removes_evidence`, `test_store.py::test_delete_removes_chunks_vectors_and_lexical_rows` |
| R9 | Semantic + lexical retrieval with fusion | Cosine search, SQLite BM25, RRF fusion; modes selectable and separately measured | verified | `tests/test_retrieval.py`; `docs/evaluation.md` §3 |
| R10 | Reranking (S1 "context rerank") | Second-stage heuristic rerank (`heuristic-coverage-v1`), switchable, labelled as not a cross-encoder | verified | `tests/test_retrieval.py::test_rerank_can_be_disabled` |
| R11 | Grounded generation with system prompts (S1) | Prompt restricts the model to evidence, forbids following embedded instructions, requires `[n]` markers | verified (prompt and validation) / unverified (live model obedience) | `tests/test_guardrails.py::PromptTests` |
| R12 | Citations that resolve to real locations | Every citation maps to an indexed chunk with source, section, page and excerpt; invalid markers stripped and counted | verified | `…::test_answer_is_cited_and_citations_resolve` |
| R13 | Honest abstention | Insufficient evidence produces `status=abstained` with a machine reason and no invented answer | verified | `…::test_unanswerable_question_abstains_without_generating`, `test_empty_corpus_abstains` |
| R14 | Conflicting evidence surfaced | Numeric disagreement between two cited sources sets `needs_review` and reports both statements | verified | `…::ConflictDetectionTests`, `test_conflicting_numbers_are_surfaced_not_merged` |
| R15 | Guardrails after generation (S1) | Injection quarantine, credential redaction, invalid-citation stripping, unverified-URL neutralisation, output warnings | verified | `tests/test_guardrails.py`, `…::test_injection_content_is_quarantined_and_never_answered` |
| R16 | CLI (S1) | `init`, `ingest`, `sources`, `chunks`, `ask`, `serve`, `stats`, `eval`, `doctor`, `rebuild`, `delete`, `feedback`, `export` with meaningful exit codes | verified | `tests/test_evaluation_and_cli.py::CliTests` |
| R17 | Metrics (S1: mean/p50/p95, TTFT, ITL, top_k/top_p/temperature) | Per-query and aggregate latency observability; streaming metrics measured from real token arrivals | verified | `tests/test_guardrails.py::MetricsTests`; `docs/evaluation.md` §5 |
| R18 | Reranker/provider failure is survivable | A failing provider falls back to the extractive generator and records `fallback_provider_used` plus the original error | verified | `…::test_provider_failure_falls_back_visibly` |
| R19 | Index integrity + rebuild | `health`/`doctor` detect missing vectors, orphans and dimension drift; `rebuild` re-embeds from stored chunks | verified | `…::test_missing_vector_is_detected_by_health`, `test_rebuild_re_embeds_without_losing_chunks` |
| R20 | Observable ingestion lifecycle | Job records with stage, status, error code and retry-safety; interrupted jobs are marked failed on startup | verified | `…::test_interrupted_job_is_marked_failed_on_startup`, `test_store.py::test_jobs_are_visible` |

## B. Safety and security (S2 §12)

| ID | Requirement | Acceptance criterion | Status | Evidence |
|---|---|---|---|---|
| S1 | Retrieved text is data | Instruction-like passages quarantined and excluded from the prompt | verified | injection tests above |
| S2 | No secret leakage | Credential-shaped output redacted; no prompts or documents logged | verified | `test_credentials_are_replaced`; API error paths return codes only |
| S3 | Upload safety | Size limit, content sniffing, binary rejection, no filesystem paths from user input | verified | `tests/test_loaders.py`; `tests/test_api.py` |
| S4 | Access filtering | `allowed_sources` enforced inside retrieval and citation resolution | verified | `…::test_access_filter_excludes_other_sources` |
| S5 | Optional API auth | `RAG_API_KEY` gates every `/api` route except health | verified | `test_api.py::test_api_key_is_enforced_when_configured` |
| S6 | Multi-tenant isolation | Real per-user authorisation | deferred | Documented limitation (`SECURITY.md` §3.3) |

## C. Documentation and operations (S2 §15)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| D1 | README with quick start that works on a clean checkout | verified | commands in `README.md` were executed in this environment |
| D2 | Source register, requirements, architecture, ingestion, retrieval, evaluation, operations, ADRs | verified | `docs/` |
| D3 | Reproducible test command, no network or paid API | verified | `make test` |
| D4 | Golden evaluation with methodology and real numbers | verified | `docs/evaluation.md` |
| D5 | `.env.example`, `Makefile`, `.gitignore` for local data | verified | repository root |
| D6 | Cinematic RAG walkthrough (S3) | deferred | Out of scope for this increment; `engine/` keeps the trace contract alive for it |

## D. Explicit non-goals for this increment

* OCR of scanned documents, spreadsheet ingestion beyond CSV, and web crawling.
* Background job queue, multi-process workers, horizontal scale.
* User accounts, sessions, per-user authorisation.
* The cinematic 3D walkthrough described by S3.
