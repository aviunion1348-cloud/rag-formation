# Architecture — as built and as verified in this run

This is the architecture **the merged code actually has**, with each structural
claim tied to a file and to something executed. The design rationale lives in
[`../architecture.md`](../architecture.md); this document is the audit view: what
runs, in what order, with which boundaries, and which of those boundaries were
exercised here.

## 1. Process shape

One Python process, one SQLite file, no broker, no external service required.

| Element | As built | Verified by |
| --- | --- | --- |
| HTTP surface | `ThreadingHTTPServer` from the standard library (`api.py:45`, `api.py:769`) — no FastAPI/Flask in the runtime path | `scripts/run_host_fixtures.sh` starts the wheel-built service and talks to it from Python, Node and Flask hosts (5/5) |
| State | single SQLite database `data/ragformation.db`, WAL, `synchronous=NORMAL` (`store.py:263-264`) | `evidence/recovery_report.json` (`integrity_check=ok` after SIGKILL) |
| Query concurrency | thread pool inside the HTTP server, bounded by `RAG_MAX_CONCURRENT_QUERIES` (default 8) | configured, **not load-tested** — see §7 |
| Outbound dependencies | none by default (`hashing` embeddings + `extractive` generation) | `python3 -m ragformation ingest corpus` then `eval` runs with no network |
| Optional dependencies | numpy (distributional embedder), pypdf (PDF), jsdom (experience matrix only) | installed into throw-away dirs by the scripts that need them; repository never modified |

Consequence: the deployment unit is `python3 -m ragformation serve` plus a data
directory. Everything below is inside that unit.

## 2. Module map (package is 9,929 lines over 22 modules)

| Module | Lines | Responsibility in the running system |
| --- | --- | --- |
| `engine.py` | 1,811 | Orchestration and policy: ingest lifecycle, retrieval→generation→validation, abstention threshold, conflict gate, telemetry |
| `store.py` | 967 | Schema, transactional revision swap, FTS5 BM25, vector blobs, jobs, queries, traces, integrity report |
| `embeddings.py` | 863 | Five provider adapters plus a content-addressed cache and retry/backoff |
| `api.py` | 809 | Routing, auth resolution per route, SSE, static experience assets, error envelopes |
| `generation.py` | 625 | Provider adapters (ollama / openai-compatible / extractive), streaming, TTFT/ITL, sentence selection |
| `loaders.py` | 615 | Bytes → structured blocks; media-type sniffing; size/binary rejection |
| `cli.py` | 563 | `ingest`, `eval`, `serve`, `ask`, `stats`, `doctor`, `rebuild`, `rollback` |
| `evaluation_v2.py` | 465 | Case runner, fact matcher, retrieval/abstention/conflict metrics, splits, calibration |
| `events.py` | 403 | Event envelope (`schemaVersion`), JSON log, SSE fan-out, ring buffer |
| `retrieval.py` | 400 | Vector channel, BM25 channel, RRF fusion, rerank, MMR, context assembly, identifier boost |
| `openapi.py` | 323 | The served contract document |
| `contracts.py` | 256 | Public dataclasses (Answer, Citation, …) and the version constant |
| `chunking.py` | 244 | Heading-aware chunks, overlap that never crosses a heading, section path |
| `auth.py` | 238 | Local mode, API keys, scopes, trusted-proxy principals, `allowedSources` intersection |
| `text.py` | 237 | Normalisation, tokenisation, compounds, stems (`TextSignals`, `matched_terms`) |
| `evaluation.py` | 212 | Golden v1 runner |
| `guardrails.py` | 180 | Instruction-like-content quarantine, credential redaction, citation validation |
| `metrics.py` | 118 | recall@k, MRR, nDCG, duplicate rate |
| `errors.py` | 115 | Error taxonomy with `code`, `message`, `hint`, HTTP mapping |
| `prompts.py` | 102 | Grounding contract sent to a model |
| `config.py` | 275 | Frozen settings from `RAG_*` environment variables |
| `__init__.py` | 102 | Public exports, version `0.2.0` |

## 3. Ingestion path (each stage is separately observable)

```
bytes ──loaders──► blocks ──chunking──► chunks ──embeddings──► vectors ──store.replace_source──► one transaction
```

* `loaders.py` sniffs the media type, rejects binaries and >8 MiB sources
  (`RAG_MAX_SOURCE_BYTES`), and — from the fix in this run — renders JSON as
  attributable `record — field: value` lines rather than bare values.
* `chunking.py` emits heading-aware chunks with a section path; the 40-token
  overlap never crosses a heading boundary (fix #2), so a citation cannot inherit
  the previous record's tail.
* Embeddings are computed **before** the write, into `vectors_staged`, so a
  provider failure cannot leave a half-written revision.
* `store.replace_source` deletes the previous revision's `chunks`, `vectors` and
  `chunks_fts` rows and inserts the new ones inside `BEGIN IMMEDIATE`. Unchanged
  content short-circuits to `unchanged`.
* A `jobs` row exists before work starts and is finished on every path; a restart
  marks stale `running` jobs `failed/interrupted` — never `complete`
  (verified: `evidence/recovery_report.json`, SIGKILL at 0.6 s → `integrity_check=ok`,
  re-ingest succeeds, 400 sources with no duplicates).

## 4. Query path

```
question ─► validate ─► embed query ─┬─► vector channel (cosine) ─┐
                                     └─► BM25 channel (FTS5) ────┴─► RRF(k=60)
      ─► identifier boost ─► rerank ─► MMR(λ=0.72) ─► guardrails scan
      ─► context assembly (≤1400 tokens) ─► abstain gate (0.24) ─► generation
      ─► citation validation ─► conflict gate ─► answer + citations + trace/events
```

| Step | Code | What this run measured |
| --- | --- | --- |
| Embed query | `embeddings.py` | If the provider fails, retrieval degrades to lexical and says so in `warnings` (`degraded_to_lexical_retrieval`) — behaviour present; the failure path is unit-tested, not induced live |
| Two channels + RRF | `retrieval.py` | Ablations in `golden_v2_report.json`: lexical-only recall 0.7027 / nDCG 0.4648; dense 0.9459 / 0.8837; hybrid **0.973 / 0.8959** |
| Identifier boost | `retrieval.py` | Took `t06`/`t09`/`t15` from unretrieved to rank 1; pinned by `tests/test_qualification_fixes.py` |
| Guardrails | `guardrails.py` | Security matrix: quarantined instruction never becomes an answer; jailbreak flagged (`possible_meta_disclosure`) and obeyed nothing (22/22) |
| Abstain gate | `engine.py` | v2 abstention recall 0.8889 on a 0.24 threshold; rationale in [`release_decision.md`](release_decision.md) §4 |
| Citation validation | `engine.py` | Every citation resolves to delivered evidence in all 5 host fixtures; citation precision 1.0 on v1/v2/v3/v4 |
| Conflict gate | `engine.py` | v3 `h09` genuine contradiction surfaced; v4 `h18` no false conflict; iteration disclosed in [`evaluation_v2_failure_analysis.md`](evaluation_v2_failure_analysis.md) §6 |

## 5. Provider matrix — what each one is, and what was exercised

| Provider | Name | Semantic? | Exercised here |
| --- | --- | --- | --- |
| hashing (d512, L2) | `hashing-lexical-v1` | **No** — deterministic feature hashing (the adapter says so in its own `describe()`) | Default for every eval, gate, host fixture and scale run |
| distributional (PPMI+SVD, 192d, trained in-process on this corpus) | `distributional-corpus-v1` | Partly — corpus-internal similarity only | Benchmarked (`semantic_bench.json`, `dense-lsa`/`hybrid-lsa`) |
| spacy (`en_core_web_md`) | `spacy-*` | Yes | **Blocked**: model weights unreachable (GitHub release assets reset; `huggingface.co` fails) |
| ollama (`nomic-embed-text`) | per-model | Yes | **Blocked**: nothing listening on `127.0.0.1:11434` |
| openai (`text-embedding-3-small`) | per-model | Yes | **Blocked**: no `OPENAI_API_KEY` |
| extractive generation | `extractive-synthesis-v1` | n/a | Default generator for all scores reported here |
| ollama / openai generation | per-model | n/a | **Blocked**; stub-verified by `tests/test_ollama_provider.py` (6 tests) only |

The distinction matters: every quality number in this package is measured with a
**non-neural** embedder and a **non-neural** generator. Nothing here measures what
a neural stack would score.

## 6. Storage model and derivation rules

Tables: `meta`, `sources`, `chunks`, `vectors`, `chunks_fts` (FTS5),
`vectors_staged`, `vectors_backup`, `semantic_models`, `embedding_cache`, `jobs`,
`queries`, `feedback`.

* `sources` and `chunks` are the source of truth; `vectors`/`chunks_fts` are
  derived. `rebuild` recomputes them from `sources` (verified: 66 → 66 chunks,
  rebuild 4.8 ms in `evidence/recovery_report.json`).
* `vectors_backup` enables `index/rollback` — verified to restore the previous
  generation and to keep answers byte-identical (5/5).
* Vectors are L2-normalised at write time, so retrieval is a dot product;
  dimension/model drift is caught by the integrity report rather than by silently
  mixing models.
* Deletion is tombstone-then-hard-delete in one transaction, so "deleted" is
  auditable in `chunks`/`vectors`/`chunks_fts` simultaneously.

## 7. What this architecture does **not** provide

Named because a reader will otherwise assume it, and because none of it was
verified here:

1. **Horizontal scale.** One process, one SQLite file. The ANN gate
   (`performance_and_scale.md`) puts supported interactive range at ≤5,000 chunks
   on this 2-vCPU box; 10,000 chunks was measured at 412–444 ms p50 and is
   *not* claimed as supported. No ANN index was added.
2. **Multi-tenant isolation as an audited property.** Tenancy columns and
   server-side `allowedSources` exist and the security matrix shows one tenant
   cannot see another's sources, but this is a single-process role check, not an
   independently audited isolation boundary.
3. **TLS / reverse-proxy termination.** The server speaks HTTP; all host fixtures
   are loopback. Trusted-principal mode assumes a proxy that is not part of this
   repository.
4. **Concurrency and long-run stability.** `RAG_MAX_CONCURRENT_QUERIES=8` is a
   configuration, not a measurement: no sustained load test, no soak test, no
   memory curve over hours was run.
5. **Physical GPU rendering.** The experience layer is verified by renderer mode
   (WebGL2/WebGL1/canvas/no-renderer), not on a GPU — none exists here.
