# Baseline — what existed before this qualification run

Captured 2026-11-05 on the merged `main` commit, before any change in this stage.
Everything below is either a command whose output is quoted, or a claim whose
source is named. Nothing in this file is an estimate presented as a measurement.

## 1. Repository identity

| Item | Value | How it was read |
| --- | --- | --- |
| Repository | `aviunion1348-cloud/rag-formation` | `git remote -v` |
| Baseline commit | `986ba5587648c4d3bdded4e9889ef8cdbcdfb919` | `git rev-parse HEAD` |
| Baseline commit message | `Merge pull request #3 from aviunion1348-cloud/arena/5f16d127-rag-formation` | `git show --no-patch 986ba55` |
| Merge author / date | Avi Bhardwaj, Thu Nov 5 20:24:30 2026 +0530 | `git show --no-patch` |
| `main`, `origin/main`, HEAD at baseline | all `986ba55` | `git rev-parse main origin/main HEAD` |
| Working branch for this run | `arena/5f16d127-rag-formation` | platform-assigned; the earlier `arena/56047441-…` name in my notes is stale |
| Working tree at baseline | clean | `git status --porcelain` |
| Local history depth | grafted at `986ba55` (shallow clone) | `git log --oneline` shows one commit; the five PR commits are not present locally |

Consequence: history archaeology is not available in this sandbox. Statements
about earlier commits come from the merged PR record and from `docs/handoff.md`,
not from `git log`.

## 2. Environment

Full probe output: [`environment.txt`](environment.txt).

| Resource | Value |
| --- | --- |
| OS / kernel | Debian GNU/Linux 12 (bookworm), Linux 6.1.158+ x86_64 |
| CPU / RAM | 2 vCPU, 3.9 GiB total (3.7 GiB available) |
| Python | 3.11.2 (`/usr/bin/python3`), PEP 668 system interpreter |
| Node | v22.22.3, npm 10.9.8 |
| GPU | none: `nvidia-smi` absent, no `/dev/dri` render device |
| Local model runtime | none: nothing listening on `127.0.0.1:11434` |
| Hosted provider key | absent: `OPENAI_API_KEY` unset |
| Optional Python packages | numpy, scipy, sklearn, spacy, fastapi, flask, uvicorn, pypdf — all absent from the system interpreter |

### Network egress (measured, not assumed)

| Host | Result |
| --- | --- |
| `pypi.org` | 200 |
| `github.com`, `codeload.github.com` | 200 / 301 |
| `huggingface.co` | connection failed |
| `objects.githubusercontent.com` | connection failed |
| `release-assets.githubusercontent.com` | TLS handshake failure |

Consequence, and the reason Stage C is shaped the way it is: **no pretrained
embedding weights can be obtained in this sandbox**. spaCy models, GloVe,
fastText, sentence-transformers and Ollama pulls all resolve through hosts that
fail here. Only source tarballs on `github.com`/`codeload` and wheels on PyPI are
reachable.

## 3. Baseline test and evaluation evidence (re-run, not quoted)

| Suite | Command | Result |
| --- | --- | --- |
| Python unit/integration | `python3 -m unittest discover -s tests -p 'test_*.py'` | **143 tests, OK** (≈19 s) |
| Legacy `engine/` suite | `cd engine && python3 -m unittest discover -s tests` | **OK** |
| Experience contracts (Node) | `node tests/experience_contract.mjs` | `all checks passed` |
| Golden v1 | `python3 -m ragformation eval --v1` | 14/14, recall 1.0, MRR 1.0, nDCG 0.952 |
| Golden v2 | `python3 -m ragformation eval` | **39/46**, recall@6 1.0, MRR 0.921, citation precision 1.0, abstention recall 0.889 |
| Calibration split | inside v2 report | 12/12 |
| Wheel | build + install in a clean venv | 165 KB wheel, version 0.2.0, serves `/experience`, `/api/v1/openapi.json`, `/api/v1/events/recent` |

Baseline v2 failures: `d07, t06, t08, t09, t15, t18, t23` (39/46). This is the
starting point for Stage B; the per-case dossier of that baseline run is in
[`evaluation_v2_failure_analysis.md`](evaluation_v2_failure_analysis.md).

## 4. Capability inventory at baseline

Documented in the repository and re-checked by execution:

* **Ingestion**: md, txt, rst, html, csv, json, docx, pdf; heading-aware chunking with
  measured overlap, deterministic chunk ids, content hashing, unchanged-source skip.
* **Retrieval**: BM25 + vector channels fused with RRF, optional MMR, support score
  used for reranking and for abstention.
* **Generation**: `ollama` (streaming, real TTFT/ITL), `openai`-compatible, and the
  deterministic `extractive-synthesis-v1` fallback that quotes evidence and binds
  each quote to a chunk id.
* **Guardrails**: quarantine of instruction-like content, credential redaction,
  abstention below a support threshold, numeric-conflict surfacing with
  `needs_review`.
* **API**: FastAPI-free stdlib HTTP server; endpoints for ask (JSON + SSE), sources,
  chunks, stats, traces, feedback, events, experience; OpenAPI document at
  `/api/v1/openapi.json`.
* **Auth**: local (single-tenant) or API keys with scopes `read`, `query`, `ingest`,
  `delete`, `rebuild`, `admin`; `allowedSources` intersected server-side.
* **Experience**: server-rendered S3D scene plus DOM transcript, keyboard and
  screen-reader paths, reduced-motion and contrast settings.
* **Ops**: `/healthz`, `/readyz`, JSON events log, traces, feedback capture,
  `bench_scale.py` for scale curves.

## 5. Known limitations carried into this run

From `docs/handoff.md` §9 and re-observed here:

1. No neural embedding, no hosted LLM, no GPU in the build environment → semantic
   quality and neural generation are **unverified**, and are labelled as such.
2. Abstention classes overlap: some answerable questions score below some
   unanswerable ones.
3. Single-tenant by default; multi-tenancy exists in the schema but is not
   independently audited here.
4. `unittest discover -t .` fails; the `-s tests -p 'test_*.py'` form is required.
5. The `ragformation` console script is not on `PATH` in this sandbox;
   `python3 -m ragformation` is used throughout.

## 6. What this baseline does *not* establish

Green tests and a green CI run do not make this system production-ready, and this
document does not claim that. It records a starting point: a functional
single-tenant RAG service with honest labels, verified locally, whose semantic,
multi-tenant, physical-GPU and public-deployment properties remain unverified
because the environment cannot exercise them.
