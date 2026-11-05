# Technical specification — the contract as it exists

Scope: what a caller, an operator or an integrator can rely on, with the source of
each statement. Behaviour that exists but was **not** exercised in this environment
is marked *unverified* rather than described as if it were certified.

Version identifiers in force: package `0.2.0` (`ragformation/__init__.py`), API
`apiVersion` `1`, event `schemaVersion` `1.0` (`events.py:EVENT_SCHEMA_VERSION`),
dataset revisions `golden-v2-2026-11-05`, `holdout-v3-2026-11-05`,
`holdout-v4-2026-11-05`.

## 1. HTTP surface

Two generations of routes are served by the same process. `/api/*` is the original
0.1 surface kept as a compatibility alias; `/api/v1/*` is the versioned contract
and the one the OpenAPI document describes.

| Method | Path | Action (role) | Verified |
| --- | --- | --- | --- |
| GET | `/api/v1/health`, `/healthz` | public probe | host fixtures: 200 |
| GET | `/api/v1/ready`, `/readyz` | public probe | host fixtures |
| GET | `/api/v1/config` | read | yes |
| GET | `/api/v1/sources` | read | yes (`{"results":[…]}`) |
| POST | `/api/v1/sources` | ingest (writer) | yes (`{"title","text"}` or upload) |
| GET | `/api/v1/sources/{id}` | read | yes |
| DELETE | `/api/v1/sources/{id}` | delete (writer) | yes |
| GET | `/api/v1/sources/{id}/chunks` | read | yes |
| GET | `/api/v1/ingestions/{jobId}` | read | yes |
| POST | `/api/v1/query`, `/api/v1/ask` | query (viewer) | yes, incl. 401/403 paths |
| GET | `/api/v1/traces`, `/api/v1/traces/{queryId}` | traces (operator) | yes (403 for writer) |
| GET | `/api/v1/index/stats` | stats (operator) | yes |
| POST | `/api/v1/index/rebuild` | rebuild (admin) | yes |
| POST | `/api/v1/index/rollback` | rebuild (admin) | yes (`evidence/recovery_report.json`) |
| GET | `/api/v1/events` | event stream (SSE) | yes, 65 frames in the Node fixture |
| GET | `/api/v1/events/recent` | recent events, ring buffer | yes |
| GET | `/api/v1/openapi.json` | public | yes; does not contain the API key |
| GET | `/`, `/experience` | console and S3D experience | yes (16/16 scenes render) |

There is **no** `POST /api/v1/ask/stream`. Streaming is `GET /api/v1/events`
(server-sent events), and the ask response itself is a single JSON object.

Response headers that matter: `X-Content-Type-Options: nosniff`, `Cache-Control:
no-store` on API responses, and `Access-Control-Allow-Origin` only when
`RAG_CORS_ORIGIN` is set (never `*`). All three are asserted in
`scripts/security_matrix.py` (22/22 rows).

## 2. Authentication and authorisation

| Mode | Condition | Principal |
| --- | --- | --- |
| Local | no `RAG_API_KEY`, loopback caller | `local-operator` with all four roles |
| API key | `RAG_API_KEY` set; caller sends `x-api-key` | roles from the key's configured scope; default `viewer` |
| Trusted proxy | `RAG_TRUST_PROXY=1` | identity taken from `x-rag-principal` (header name configurable via `RAG_PRINCIPAL_HEADER`) — only safe behind a proxy that overwrites it |

Actions map to roles (`auth.py:ACTIONS`): `read`/`query` → any role; `ingest`/
`delete` → writer+; `stats`/`traces` → operator+; `rebuild`/`manage` → admin only.

`allowedSources` is intersected server-side: a request may only narrow, never
widen, what the principal may see (`Principal.scope`). Verified twice in the
security matrix — an unknown `allowedSources` value yields *no* evidence rather
than "no filter", and a principal in another tenant sees zero local sources.

Not present: multi-key configuration, per-key rate limits, key rotation, OIDC/JWT
validation. One `RAG_API_KEY` covers the process; that is the whole auth model.

## 3. Answer contract

`POST /api/v1/ask` returns, at minimum: `status` (`answered` | `needs_review` |
`abstained`), `answer` text, `citations[]` each with `sourceId`, `chunkId`,
`title` and `excerpt`, `evidence[]` (the delivered context), `warnings[]`,
`conflict` (when a numeric contradiction was surfaced), `traceId`, and usage/
latency fields.

Guarantees that were exercised:

* **Every citation resolves to delivered evidence.** Asserted in all five host
  fixtures and by citation-precision 1.0 on golden v1, golden v2, both holdouts.
* **Abstention is a first-class answer.** `abstained` carries the reason
  (`no_candidates`, below threshold) and, in the eval harness, is scored
  separately from "wrong".
* **Conflicts are surfaced, never averaged.** Two sources disagreeing about the
  same subject and unit produce `status: needs_review` plus both values with
  provenance (`engine.py::_detect_conflict`). Iteration history in
  [`evaluation_v2_failure_analysis.md`](evaluation_v2_failure_analysis.md) §6.
* **Instruction-like content is quarantined.** A document telling the model to
  ignore its instructions never becomes an answer; it appears as
  `quarantined_instruction_like_content` in `warnings` (security matrix rows 15–16).

## 4. Scoring and policy — the actual numbers

| Quantity | Formula / value | Code |
| --- | --- | --- |
| lexical support | `0.75·coverage + 0.25·phrase` | `retrieval.py::support_score` |
| final support | `0.65·lexical + 0.35·semantic`, `semantic = clamp((cos+0.15)/1.15)` | same |
| identifier boost | if the question names an id (`svc-003`, `MACRO-BILLING-01`, `p95`): `support = support·(1−0.45·exact) + 0.45·exact` | same (added in this run, fix #5) |
| fusion | Reciprocal Rank Fusion, `k=60` (`RAG_RRF_K`) | `retrieval.py` |
| diversity | MMR, `λ=0.72` (`RAG_MMR_LAMBDA`) | `retrieval.py::mmr` |
| context budget | 1,400 tokens (`RAG_MAX_CONTEXT_TOKENS`), hard cut with recorded reasons | `assemble_context` |
| abstention | `support < 0.24` → abstain (`RAG_ABSTAIN_THRESHOLD`) | `engine.py`; rationale in [`release_decision.md`](release_decision.md) §4 |
| conflict "distinctive" term | document frequency ≤ `max(1, len(selected)//4)` | `engine.py::_detect_conflict` |
| fact match (canonical) | lowercase, strip `*_``, hyphen between digit and letter → space, trailing plural `s` folded (not `ss`), then **contiguous, ordered** token match | `evaluation_v2.py::_canonical_tokens` |

The canonical matcher is a measurement rule, not a relaxation: a paraphrase still
fails, and `--fact-matcher legacy` reproduces the previous substring rule
(45/46 canonical vs 44/46 legacy on golden v2 — the difference is `t15`, where the
answer says "**35-day** retention" and the label says "35 days").

## 5. Configuration surface

All settings come from `RAG_*` environment variables through a frozen dataclass
(`config.py`), validated at startup; an invalid value is a `config_error` at boot,
not a surprise at request time.

| Group | Variables (default) |
| --- | --- |
| Storage | `RAG_DATA_DIR` (`data`), `RAG_DB_FILENAME` (`ragformation.db`) |
| Embeddings | `RAG_EMBED_PROVIDER` (`auto`), `RAG_EMBED_MODEL` (`nomic-embed-text`), `RAG_EMBED_DIMENSION` (512, hashing only), `RAG_EMBED_BATCH_SIZE` (32), `RAG_SPACY_MODEL` (`en_core_web_md`), `RAG_DISTRIBUTIONAL_DIMENSION`/`_WINDOW`/`_MIN_COUNT` (192/6/1), `RAG_REQUIRE_SEMANTIC` (0) |
| Generation | `RAG_LLM_PROVIDER` (`auto`), `RAG_LLM_MODEL` (`llama3.2:3b`), `RAG_TEMPERATURE` (0.3), `RAG_TOP_P` (0.9), `RAG_MAX_OUTPUT_TOKENS` (512) |
| Retrieval | `RAG_TOP_K` (6), `RAG_CANDIDATE_MULTIPLIER` (4), `RAG_RETRIEVAL_MODE` (`hybrid`), `RAG_RRF_K` (60), `RAG_RERANK` (1), `RAG_MMR_LAMBDA` (0.72), `RAG_MAX_CONTEXT_TOKENS` (1400), `RAG_ABSTAIN_THRESHOLD` (0.24) |
| Chunking | `RAG_CHUNK_TARGET_TOKENS` (180), `_MAX_TOKENS` (300), `_OVERLAP_TOKENS` (40) |
| Providers | `RAG_OLLAMA_HOST` (`http://127.0.0.1:11434`), `RAG_OPENAI_BASE_URL`, `RAG_REQUEST_TIMEOUT_S` (60), `RAG_MAX_RETRIES` (2) |
| Limits | `RAG_MAX_SOURCE_BYTES` (8 MiB), `RAG_RATE_LIMIT_PER_MINUTE` (600), `RAG_MAX_CONCURRENT_QUERIES` (8) |
| Security | `RAG_API_KEY` (unset), `RAG_ALLOW_PATH_INGEST` (1), `RAG_CORS_ORIGIN` (empty), `RAG_TRUST_PROXY` (0), `RAG_PRINCIPAL_HEADER` (`x-rag-principal`), `RAG_ALLOW_INSECURE_PUBLIC` (0), `RAG_DEFAULT_TENANT` (`local`) |
| Serving | `RAG_HOST` (`127.0.0.1`), `RAG_PORT` (8787), `RAG_PERSIST_TRACES` (1), `RAG_LOG_LEVEL` (`INFO`) |

There is no `Settings.with_overrides`; tests construct settings directly, and
`python3 -m ragformation doctor` prints the resolved configuration.

## 6. Error taxonomy

`errors.py` defines stable codes; clients must branch on `error`, not on the
message. HTTP mapping is a class attribute:

| Code | HTTP | Meaning |
| --- | --- | --- |
| `validation_error` | 400 | Bad body/parameter (e.g. "question is too long") |
| `unauthorized` | 401 | Missing/invalid key |
| `forbidden` | 403 | Authenticated, role insufficient |
| `not_found` | 404 | Unknown source/trace (also static-file traversal) |
| `index_busy` / `embedding_mismatch` | 409 | Rebuild in flight; stored vectors from another fingerprint |
| `source_too_large` | 413 | > `RAG_MAX_SOURCE_BYTES` |
| `unsupported_format` | 415 | Extension/media type not accepted |
| `extraction_error` | 422 | Document parsed but nothing extractable |
| `rate_limited` | 429 | Concurrency/rate bound hit |
| `provider_error` | 502 | Provider returned something unusable |
| `provider_unavailable` | 503 | Provider unreachable — retrieval degrades to lexical |
| `provider_timeout` | 504 | Provider exceeded `RAG_REQUEST_TIMEOUT_S` |
| `config_error` | 500 | Invalid configuration at boot |

Messages never embed credentials, absolute paths or raw document text
(`errors.py` module docstring; security matrix asserts the key appears in neither
the OpenAPI document nor the event log).

## 7. Event contract

`events.py` emits typed envelopes: `schemaVersion` (`1.0`), `eventId`, `traceId`,
`sequence`, `timestamp`, `stage`, `status` (`started`/`progress`/`completed`/
`failed`/`skipped`), `durationMs`, `counts`, plus a redacted `summary` capped at
240 characters. Rules the code enforces and the experience matrix exercises:

* only stages that actually ran are emitted — a skipped stage says `skipped` with
  a reason, never a fabricated `completed`;
* `mode` distinguishes `live`, `demo`, `extractive`, `fallback`, so a replayed
  fixture can never be mistaken for a live model call;
* subscribers get bounded queues; overflow drops the oldest event and increments a
  counter the client can read to detect a gap.

## 8. CLI

`init`, `ingest`, `sources`, `chunks`, `delete`, `rebuild`, `ask`, `serve`,
`stats`, `eval`, `doctor`, `export`, `feedback`. The `eval` command gained
`--fact-matcher {canonical,legacy}` in this run and `--dataset`/`--out` for
holdouts. The console script is not on `PATH` in this sandbox; `python3 -m
ragformation …` is used everywhere in this package.

## 9. Deployability statements — what is and is not supported

| Property | Status | Basis |
| --- | --- | --- |
| Loopback single-process serving | verified | 5 host fixtures |
| Wheel install into a clean venv, imported by an unrelated repo | verified | host fixture E |
| Data directory backup/restore, rebuild, rollback, crash recovery | verified | `recovery_report.json` 14/14 |
| Interactive latency ≤5,000 chunks on 2 vCPU | verified | `evidence/scale/` |
| Interactive latency at 10,000 chunks | **measured, not supported** | p50 412–444 ms vs a 250 ms gate |
| TLS termination, reverse proxy, container image | **out of scope** | none provided or tested |
| Sustained concurrency / soak | **unverified** | no load test was run |
| Multi-tenant isolation as an audited boundary | **unverified** | only single-process role checks |
| Neural embeddings, hosted LLM, GPU rendering | **unverified** | providers unreachable in this environment |
