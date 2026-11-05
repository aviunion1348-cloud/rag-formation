# rag-formation

A complete, runnable **Retrieval-Augmented Generation** system: documents go in,
grounded answers with resolvable citations come out. Python standard library
only — no framework, no build step, no cloud account required.

```bash
make ingest                                         # index the sample corpus
make ask Q="How long are audit logs retained?"      # cited answer in the terminal
make serve                                          # web console on :8787
make test                                           # 107 tests, no network
```

```
Sources → extraction → normalisation → heading-aware chunking → embeddings
        → SQLite index (vectors + FTS5) → hybrid retrieval (cosine + BM25 + RRF)
        → rerank → guardrails → context assembly → grounded generation
        → server-side citation validation → telemetry (mean / p50 / p95, TTFT, ITL)
```

## What is in this repository

| Path | What it is | Status |
|---|---|---|
| `ragformation/` | The RAG system: ingestion, retrieval, generation, guardrails, API, console, CLI | Verified (107 tests, golden evaluation) |
| `corpus/` | Six synthetic documents used as the demonstration corpus (clearly labelled as invented) | Fixtures |
| `evals/golden.json` | 14 golden questions with expected sources and required/forbidden facts | Used by `make eval` |
| `docs/` | Source register, requirements, architecture, ingestion, retrieval, evaluation, operations, ADRs | Current |
| `engine/` | The earlier deterministic walkthrough engine (fixture corpus, trace events for a visual frontend) | Kept from PR #1, 29 tests pass |

`engine/` is the previous increment: a self-contained demo that replays a fixed
trace. `ragformation/` is the real system — it ingests files you give it,
persists an index, and answers with grounded citations. They share no runtime
code; `docs/source_register.md` explains why both are kept.

## Quick start

Requirements: **Python 3.10+**. Nothing else. (Optional: Ollama for neural
embeddings and generated prose — see below.)

```bash
# 1. Index the sample corpus (idempotent; re-running changes nothing)
python3 -m ragformation ingest corpus

# 2. Ask a question
python3 -m ragformation ask "When must the incident commander be paged for a SEV-1 incident?"
# status: answered
# For a SEV-1 incident the incident commander must be paged within **10 minutes**
# of classification. [1]
# citations
#   [1] incident-response-runbook · 2. Escalation Window p.2 (…, support 0.73)
# metrics: {"totalMs": 6.1, "retrievalMs": 0.6, "ttftMs": 0.7, "bestSupport": 0.729}

# 3. Ask something the corpus cannot answer
python3 -m ragformation ask "What is the lunar payload insurance policy?"
# status: abstained  (no passage passed the support threshold; nothing was invented)

# 4. Web console + JSON API
python3 -m ragformation serve --port 8787
```

Run `python3 -m ragformation doctor` to see exactly which providers are active
and what is missing.

## Neural models (optional, and never faked)

Out of the box the system runs with honest local fallbacks so that **every**
stage is real even without a model server:

| Stage | Default without Ollama | With Ollama |
|---|---|---|
| Embeddings | `hashing-lexical-v1` — deterministic feature hashing. **Not** a neural encoder; similarity reflects shared words/bigrams | `nomic-embed-text` (768-d neural embeddings) |
| Generation | `extractive-synthesis-v1` — quotes the best evidence sentences, each bound to its chunk id | `llama3.2:3b` streamed from Ollama |

Nothing silently substitutes one for the other: the provider, model and a
`semantic: true/false` flag are shown in `health`, the CLI banner, the console
header, every answer payload and `doctor`.

```bash
# Enable the neural path (the stack sketched on the whiteboard)
ollama pull nomic-embed-text
ollama pull llama3.2:3b
export RAG_EMBED_PROVIDER=ollama RAG_LLM_PROVIDER=ollama
python3 -m ragformation rebuild      # re-embed the existing chunks
python3 -m ragformation ask "How long are audit logs retained?"
```

Streaming is used for every generator, so **TTFT** (time to first token) and
**ITL** (inter-token latency) are measured from real token arrivals.

## CLI

| Command | Purpose |
|---|---|
| `init` | Create the data directory and database |
| `ingest PATH…` | Ingest files or directories (`--force` to reindex unchanged files) |
| `sources` / `chunks SOURCE_ID` | Inspect the index and chunk provenance |
| `ask "QUESTION"` | Ask (`--stream`, `--json`, `--show-trace`, `--show-evidence`, `--top-k`, `--mode`) |
| `serve` | HTTP API + web console |
| `stats` | Mean / p50 / p95 for latency, retrieval, generation, TTFT, ITL |
| `eval` | Run the golden set (retrieval and generation measured separately) |
| `doctor` | Provider, index-integrity and corpus checks |
| `rebuild` | Re-embed stored chunks (recovery path after a model change) |
| `delete SOURCE_ID` | Remove a source and every derived chunk, vector and lexical row |
| `feedback QUERY_ID RATING` | Record 1 / 0 / -1 against a query id |

Exit codes: `0` answered · `3` abstained · `4` needs review · `2` usage error ·
`1` failure. Useful for scripts and CI.

## HTTP API and console

`python3 -m ragformation serve` exposes a JSON API and a single-page console
(no bundler, relative URLs only, so it works behind a proxy):

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Provider reality, index counts, integrity problems |
| GET | `/api/config` | Effective configuration (no secrets, no paths) |
| GET | `/api/sources` | Indexed sources with chunk counts and revisions |
| GET | `/api/sources/{id}/chunks` | Chunk inventory with provenance |
| POST | `/api/ask` | `{question, topK, mode, temperature, topP}` → answer, citations, trace, metrics |
| POST | `/api/sources/upload` | `{filename, contentBase64}` — parsed in memory |
| POST | `/api/sources/text` | `{title, text}` — index a pasted note |
| POST | `/api/ingest` | `{path}` — server-side path (can be disabled) |
| POST | `/api/rebuild` | Re-embed stored chunks |
| DELETE | `/api/sources/{id}` | Remove a source and its derived data |
| GET | `/api/stats` | Aggregate latency/quality percentiles |
| POST | `/api/feedback` | `{queryId, rating, note}` |

Set `RAG_API_KEY` to require `x-api-key` on every `/api` route except health.

## Verification status

Measured on this repository with `RAG_EMBED_PROVIDER=hashing`,
`RAG_LLM_PROVIDER=extractive` (no model server available in the build
environment):

| Signal | Result |
|---|---|
| Test suite | **107/107 pass** (`python3 -m unittest discover -s tests`) — no network, no paid API |
| Legacy `engine/` suite | 29/29 pass |
| Golden evaluation | 14/14 cases, recall@6 **1.00**, MRR **1.00**, nDCG@6 **0.967**, abstain accuracy **1.00**, fact recall **1.00**, forbidden-fact rate **1.00** |
| Ingest | 6 documents → 21 chunks in **11 ms**; re-ingest (idempotent) **1.8 ms** |
| Query latency | mean **5.8 ms**, p50 **5.6 ms**, p95 **7.0 ms** over 300 queries (~165 q/s sequential) |
| Ollama adapter | Verified against a local stub speaking `/api/tags`, `/api/embed`, `/api/chat` (streaming, TTFT/ITL measured) — **not** verified against a live model in this environment |
| Tested scale | 6 documents / 21 chunks. Retrieval is brute-force over in-memory vectors; see `docs/retrieval.md` for the documented limit |

Honest gaps are listed in `docs/implementation_status.md` and §3 of
`SECURITY.md`. The short version: no live LLM was available here, injection
detection is heuristic, and this is a single-user local deployment.

## Documentation

* [docs/architecture.md](docs/architecture.md) — components and data flow
* [docs/requirements.md](docs/requirements.md) — requirements and acceptance criteria
* [docs/source_register.md](docs/source_register.md) — every source, what it decided, conflicts resolved
* [docs/implementation_status.md](docs/implementation_status.md) — requirement-by-requirement status with evidence
* [docs/tutorial_to_implementation_map.md](docs/tutorial_to_implementation_map.md) — whiteboard/notes → code
* [docs/ingestion.md](docs/ingestion.md), [docs/retrieval.md](docs/retrieval.md) — the two halves that decide quality
* [docs/evaluation.md](docs/evaluation.md) — corpus, methodology, measured results, threshold calibration
* [SECURITY.md](SECURITY.md) — threat model, controls with tests, stated limits
* [evals/results/](evals/results/) — committed evaluation reports
* [docs/operations.md](docs/operations.md) — run, configure, monitor, rebuild, back up
* [docs/decisions/](docs/decisions/) — ADRs: vector store, stdlib stack, embeddings, ranking, generation, citations/abstention, scope
* [SECURITY.md](SECURITY.md) — threat model, controls, limits

## Licence

No licence file is present in this repository; treat it as all rights reserved
by the repository owner until one is added.
