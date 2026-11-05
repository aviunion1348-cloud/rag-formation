# From the whiteboard to the code

No tutorial PDF or assignment rubric exists in the repository or the attachments.
The design inputs are the whiteboard photograph (S1) and the two-part master
prompt (S2, S3). This file maps every element of the whiteboard to the code that
implements it, the test that proves it, and any deviation.

## 1. The whiteboard, element by element

| Whiteboard element (as drawn) | Meaning | Implemented in | Proof | Status |
|---|---|---|---|---|
| "Ollama (…4B)" with arrow into the pipeline | Local LLM for generation, small model budget | `generation.py::OllamaGenerator`, default `llama3.2:3b` (`RAG_LLM_MODEL`) | `tests/test_ollama_provider.py::test_generator_streams_and_measures_tokens` (against a stub speaking Ollama's real API) | verified against stub; live model not available |
| "200m<" beside embeddings | Small local embedding model | `embeddings.py::OllamaEmbedder`, default `nomic-embed-text` | `…::test_embedder_health_and_dimension` | verified against stub |
| "Vector db" | Vector store | `store.py` vector table + `retrieval.py` cosine scan | `tests/test_store.py`, `tests/test_retrieval.py` | adapted: SQLite instead of a separate vector service (`docs/decisions/0001-vector-store.md`) |
| "Semantic" (boxed) | Semantic retrieval | Cosine similarity over embeddings, `mode=semantic` | `test_retrieval.py::test_hybrid_returns_the_relevant_chunk_first` | implemented for neural embeddings; the hashing fallback is **lexical, not semantic** and is labelled so |
| "Generate" | Generation stage | `generation.py` (streaming) | `test_generator_streams_and_measures_tokens` | verified |
| "Guardrails" | Output/content controls | `guardrails.py`: injection quarantine, credential redaction, citation validation, URL neutralisation | `tests/test_guardrails.py`, `test_engine_e2e.py::test_injection_content_is_quarantined_and_never_answered` | verified |
| "CLI … reasoning" | Command-line interface | `cli.py` (13 subcommands) | `tests/test_evaluation_and_cli.py` | verified |
| "agents sdk" and "langchain" (both struck through) | The author does not want a framework | Explicit pipeline code, no agent framework, no orchestration library | whole codebase | deliberately omitted |
| "System prompts" | Grounding prompt | `prompts.py::SYSTEM_PROMPT` | `tests/test_guardrails.py::PromptTests` | verified |
| "context rerank" | Second-stage ranking | `retrieval.py::Candidate.support` + `heuristic-coverage-v1` rerank | `test_retrieval.py::test_rerank_can_be_disabled` | adapted: transparent feature blend, **not** a cross-encoder |
| "Citations" | Traceable citations | `engine.py::_citation_for`, claim mapping, validation | `test_engine_e2e.py::test_answer_is_cited_and_citations_resolve` | verified |
| "answer tool" | Answer composition | `engine.ask()` → `QueryResult` (answer, claims, citations, trace, metrics) | same tests | verified |
| "doc → chunk by heading/topic" | Structure-aware chunking | `chunking.py::Chunker` (heading starts a chunk, section path, measured overlap) | `tests/test_text_and_chunking.py::ChunkingTests` | verified |
| "RAG engine" (central label) | The orchestrator | `engine.py::RAGEngine` | `tests/test_engine_e2e.py` |verified |
| "Metrics: top_k, top_p, temperature 0.3" | Generation/retrieval parameters | `config.py` (`top_k=6`, `top_p=0.9`, `temperature=0.3`), exposed in CLI/API/UI | `docs/evaluation.md` §5 | verified |
| "mean, p50, p95" | Latency statistics | `metrics.py::summarize`, surfaced by `stats`, `/api/stats`, console metrics tab | `tests/test_guardrails.py::MetricsTests`, `eval` output | verified |
| "TTFT / ITL" | Streaming latency metrics | `metrics.py::StreamTiming`, recorded per query; one row per metric in SQLite | `test_generator_streams_and_measures_tokens`, `stats` output | verified |

Anything on the whiteboard that is **not** in this table does not exist in the
build; nothing was added silently under a whiteboard label.

## 2. Gaps between the sketch and the honest implementation

1. **The sketch assumes a live Ollama.** This environment has no Ollama binary,
   no weights and no registry access. Rather than stub the whole stage, the
   adapter is real and verified against a server that implements Ollama's
   documented endpoints. With `RAG_EMBED_PROVIDER=ollama` and a running Ollama,
   the same code path is used — nothing needs to change.
2. **A neural embedding is not the default here.** `hashing-lexical-v1` is a
   deterministic feature-hash projection. It keeps the pipeline runnable and
   testable, and the system states `semantic: false` in health, CLI, console and
   every answer payload. This is the single largest deviation, so it is loud.
3. **"Vector db" became SQLite.** For a corpus of this size an exact brute-force
   scan is faster and simpler than a network round trip, and it keeps the write,
   the metadata and the lexical index in one transaction. The migration path is
   documented (`docs/decisions/0001-vector-store.md`).
4. **Temperature is a parameter, not a truth control.** It is passed to the
   provider and recorded per query. With the extractive fallback it has no
   effect — and the payload says so (`tokensEstimated`, provider kind
   `extractive`) instead of pretending otherwise.
5. **Guardrails are post-generation *and* pre-generation.** The sketch places
   them after generation; the build also filters retrieved content before it
   reaches the prompt (quarantine) and revalidates citations afterwards. Same
   label, stricter behaviour.

## 3. Where the specification (S2) went beyond the sketch

| S2 requirement | Implementation |
|---|---|
| Transactional revisions, crash safety | `store.replace_source` in one `BEGIN IMMEDIATE`; interrupted jobs marked failed |
| Idempotent ingestion | Content hash + embedding fingerprint + chunking fingerprint short-circuit |
| Embedding provenance and cache | `vectors.model`, `embedding_cache(text_hash, model)`, `embedding_mismatch` refusal |
| Lexical + hybrid retrieval | SQLite FTS5 BM25 (porter), RRF k=60 |
| Abstention and conflict handling | Support threshold, `abstained` status, conflict detector → `needs_review` |
| Adversarial tests | `tests/test_engine_e2e.py` (injection, access filter, provider failure), `tests/test_api.py` (auth, traversal, malformed JSON) |
| Evaluation with a labelled set | `evals/golden.json` + `evaluation.py` + `docs/evaluation.md` |
| Operations (health, doctor, rebuild, backup) | `health()`, `doctor()`, `rebuild()`, `docs/operations.md` |
| Documentation set | `docs/` (this file included) |

## 4. Out of scope here (S3)

The cinematic "RAG Experience" walkthrough (three modes, storyboard, reusable
integration package) is specified by the second half of the master prompt. It is
not implemented in this increment. `engine/` — kept from PR #1 — continues to
emit the versioned `RagExperienceEvent` trace contract such a frontend would
consume, so that work is not blocked.
