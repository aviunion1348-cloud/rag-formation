# ADR 0002 — Standard-library-only Python

* Status: accepted
* Date: 2026-11-05

## Context

The user's sketch shows LangChain, an agent SDK and a vector database. The
execution environment has Python 3.11.2 with `sqlite3` (FTS5 enabled) but no
numpy, fastapi, pydantic, pytest, pypdf or torch, and PEP 668 refuses `pip install`.

Two responses are possible: ship a requirements file and untested code, or ship
something that runs here. Anything that does not run cannot be verified, and
unverified claims are exactly what the master prompt forbids.

## Decision

Core package `ragformation/` uses only the standard library: `sqlite3`, `http.server`,
`json`, `math`, `re`, `hashlib`, `urllib`, `unittest`. Optional extras (`pypdf`,
`python-docx`) are detected at runtime and their absence produces an actionable
error, never a fake result.

## Consequences

Good:

* The repository runs end-to-end on a bare interpreter: no install, no lockfile,
  no dependency drift. The whole suite (107 tests) runs in 10 s.
* The HTTP API is `http.server` + `ThreadingHTTPServer`; the console is plain
  ES modules. No build step.
* Every dependency decision is explicit rather than inherited.

Costs:

* Hand-written cosine, BM25 plumbing, percentile maths and streaming HTTP — more
  code to own, and no numpy speed.
* No FastAPI validation layer: request parsing and error mapping are manual (and
  tested in `tests/test_api.py`).
* `pypdf` is optional, so PDF support degrades instead of being guaranteed.

## Revisit when

the deployment target allows dependencies. FastAPI + pydantic + a real vector
client would replace `api.py` and the SQLite vector scan without changing the
engine's interfaces. The optional extra list is already isolated in
`requirements-optional.txt`.
