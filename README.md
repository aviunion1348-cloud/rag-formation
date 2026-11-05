# rag-formation

A complete, runnable **Retrieval-Augmented Generation** system: documents go in,
grounded answers with resolvable citations come out. Python standard library
only — no framework, no build step, no cloud account required.

```bash
make ingest                                         # index the fixture corpus (14 documents)
make ask Q="How long are audit logs retained?"      # cited answer in the terminal
make serve                                          # console on :8787, film at /experience
make test                                           # 143 tests, no network
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
| `ragformation/` | The RAG system: ingestion, retrieval, generation, guardrails, API, console, CLI | Verified (143 tests, two evaluation sets) |
| `ragformation/web/experience/` | The S3 Experience: a 16-scene walkthrough with Director / Live / Explorer modes | Verified headlessly; WebGL unchecked on a GPU (see `docs/experience.md`) |
| `corpus/` | Fourteen synthetic documents, clearly labelled as invented (one pair deliberately contradicts) | Fixtures |
| `evals/golden.json` | 14 golden questions, frozen: the regression gate | `make eval` |
| `evals/golden_v2.json` | 46 versioned cases with dev/calibration/test splits and ten categories | `ragformation eval` |
| `evals/results/` | Committed reports: golden v1/v2, paraphrases, scale benchmarks | Measurements |
| `integrations/` | Python client, Node client, host-principal proxy example, curl quickstart | Executed by tests |
| `scripts/` | Demo recorder, scale benchmark, paraphrase benchmark, experience smoke test | Tools |
| `docs/` | Architecture, ingestion, retrieval, evaluation, benchmarks, events, integration, experience, operations, ADRs | Current |
| `engine/` | The earlier deterministic walkthrough engine (fixture corpus, trace events for a visual frontend) | Kept from PR #1, 29 tests pass |

`engine/` is the previous increment: a self-contained demo that replays a fixed
trace. `ragformation/` is the real system — it ingests files you give it,
persists an index, and answers with grounded citations. They share no runtime
code; `docs/source_register.md` explains why both are kept.

## Quick start

Requirements: **Python 3.9+**. Nothing else. (Optional: Ollama for neural
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
| Embeddings | `hashing-lexical-v1` — deterministic feature hashing. **Not** a neural encoder; similarity reflects shared words/bigrams. `lsa` (corpus-trained TF-IDF + SVD, `pip install "ragformation[lsa]"`) is available and still labelled honestly | `nomic-embed-text` (768-d neural embeddings) |
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
| `eval` | Run the v2 set (measurement) or `--v1` (frozen gate); `--gate test` makes it a CI verdict |
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
| GET | `/api/v1/health` | Provider reality, index counts, integrity problems, security posture |
| GET | `/api/v1/ready` | Readiness with reasons |
| POST | `/api/v1/query` | `{question, topK, mode, allowedSources}` → answer, citations, warnings |
| GET | `/api/v1/traces/{queryId}` | Stored stage trace (no document text) |
| GET | `/api/v1/events` | Server-sent stage stream; `…/events/recent` polls |
| GET | `/api/health` | Legacy alias kept for the console |
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
Put the engine behind your own authenticated proxy and set `RAG_TRUST_PROXY=1` to
have it trust a server-side principal header — the whole boundary is documented
in `docs/integration.md`, and `integrations/host_principal/proxy_example.py` is a
working example (the tests prove an impersonation attempt is dropped).

The walkthrough film is at **`/experience`**: Director mode replays a recording
made from a real run, Live mode follows the running server's event stream, and
Explorer mode reads the real index. It runs offline, ships in the wheel, honours
`prefers-reduced-motion`, and falls back from WebGL2 → WebGL1 → Canvas 2D →
transcript. `docs/experience.md` states precisely what was and was not verified.

## Verification status

Measured on this repository with `RAG_EMBED_PROVIDER=hashing`,
`RAG_LLM_PROVIDER=extractive` (no model server available in the build
environment):

| Signal | Result |
|---|---|
| Test suite | **143/143 pass** (`python3 -m unittest discover -s tests`, ~20 s) — no network, no paid API |
| Legacy `engine/` suite | 29/29 pass |
| Golden v1 (frozen gate) | 14/14 cases, recall@6 **1.00**, MRR **1.00**, nDCG@6 **0.952**, abstain accuracy **1.00**, fact recall **1.00**, forbidden-fact rate **1.00** |
| Evaluation v2 (measurement) | **39/46** cases on 59 chunks: recall@6 1.00, precision@6 0.365, MRR 0.921, nDCG 0.918; answered 33 / needs_review 3 / abstained 10; citation precision 1.00; abstention recall 0.889. Seven failures are listed, not hidden |
| Conflict detection | The corpus contains a deliberate 5-years vs 3-years contradiction: both values are surfaced with provenance and the answer is marked `needs_review`; a control question stays `answered` |
| Ingest | 14 documents → 59 chunks in **~40 ms**; re-ingest is idempotent |
| Query latency | mean **10.8 ms**, p95 **14.6 ms** at 59 chunks; **10.8 / 42.5 ms** p50 at 91 / 875 chunks (`docs/benchmarks.md`) |
| The S3 Experience | 16 scenes; booted headlessly in all three modes (23 checks) plus 39 DOM-free contract checks; **WebGL not verified on a GPU** |
| Packaging | Wheel built, installed in a clean venv, served `/experience` and answered a cited query from site-packages |
| Ollama adapter | Verified against a local stub speaking `/api/tags`, `/api/embed`, `/api/chat` (streaming, TTFT/ITL measured) — **not** verified against a live model in this environment |
| Tested scale | 875 chunks / 125 sources measured; retrieval is a brute-force scan over in-memory vectors, so treat thousands of chunks as the comfortable range |

Honest gaps are listed in `docs/implementation_status.md`, `docs/post_merge_audit.md`
§4 and §3 of `SECURITY.md`. The short version: no live LLM or GPU was available
here, no pretrained embedding model was measured, injection detection is
heuristic, and the default deployment is single-user local.

## Documentation

* [docs/architecture.md](docs/architecture.md) — components and data flow
* [docs/requirements.md](docs/requirements.md) — requirements and acceptance criteria
* [docs/source_register.md](docs/source_register.md) — every source, what it decided, conflicts resolved
* [docs/implementation_status.md](docs/implementation_status.md) — requirement-by-requirement status with evidence
* [docs/tutorial_to_implementation_map.md](docs/tutorial_to_implementation_map.md) — whiteboard/notes → code
* [docs/ingestion.md](docs/ingestion.md), [docs/retrieval.md](docs/retrieval.md) — the two halves that decide quality
* [docs/evaluation.md](docs/evaluation.md) — both evaluation sets, methodology, measured results, calibration, ablations
* [docs/benchmarks.md](docs/benchmarks.md) — measured ingest/query behaviour at 100 / 500 / 1000 chunks
* [docs/events.md](docs/events.md) — the versioned event contract and how to consume it
* [docs/integration.md](docs/integration.md) — deployment shapes, auth modes, tenancy, error contract
* [docs/experience.md](docs/experience.md) — the film, its modes, its fallbacks, and what was not verified
* [docs/post_merge_audit.md](docs/post_merge_audit.md) — post-merge audit: gaps found, fixes, remaining work
* [docs/handoff.md](docs/handoff.md) — the 12-part handoff, with the readiness label and the evidence ledger
* [docs/final_qualification/final_report.md](docs/final_qualification/final_report.md) — independent qualification: what was measured, what was fixed, what remains unverified, and the binding release decision
* [SECURITY.md](SECURITY.md) — threat model, controls with tests, stated limits
* [evals/results/](evals/results/) — committed evaluation reports
* [docs/operations.md](docs/operations.md) — run, configure, monitor, rebuild, back up
* [docs/decisions/](docs/decisions/) — ADRs: vector store, stdlib stack, embeddings, ranking, generation, citations/abstention, scope

## Licence

No licence file is present in this repository; treat it as all rights reserved
by the repository owner until one is added.
