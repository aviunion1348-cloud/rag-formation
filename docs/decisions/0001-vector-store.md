# ADR 0001 — Use SQLite for vectors, metadata and the lexical index

* Status: accepted
* Date: 2026-11-05

## Context

The pipeline needs a persistent store for chunks, embeddings, provenance and
full-text search. A dedicated vector database (Qdrant, pgvector, Chroma) is the
obvious default. Constraints here: no service to run, no package index access
(PEP 668 blocks `pip install`), a corpus of 21 chunks, and a hard requirement that
ingestion be transactional — a half-written source must never become searchable.

## Decision

Store everything in one SQLite file, `data/ragformation.db`:

* `sources`, `chunks`, `vectors` (`packed float32` BLOB + `dim` + `model`),
  `chunks_fts` (FTS5, porter tokenizer), `queries`, `jobs`, `feedback`.
* Similarity is computed in Python over unpacked vectors, filtered by model and
  dimension.
* Source replacement and deletion happen in a single transaction that touches
  chunks, vectors and FTS rows together.

## Consequences

Good:

* Atomic ingest, delete and rebuild — the invariant "every chunk has a vector and
  an FTS row" is enforceable and checked by `/api/health`.
* Zero services, zero network, trivially backupable (one file), and the whole
  state fits in memory at this scale.
* FTS5 gives real BM25, not an approximation.

Costs:

* Brute-force similarity scan: O(n) per query. At 21 chunks this is 0.54 ms; at
  ~100k chunks it would dominate.
* No ANN index, no sharding, single-writer lock.

## Migration seam

If scale demands a vector service, only two methods change:

* `Store.vectors_for(chunk_ids) -> dict[str, list[float]]`
* `Retriever.search(...) -> list[Candidate]`

`Retriever` receives vectors through that accessor and never opens the database
itself, so a Qdrant-backed `Store` implementation is a drop-in. The evaluation
harness would compare old and new retrieval on `evals/golden.json` before cutover.

## Revisit when

any of: >50k chunks, >20 concurrent writers, or a measured retrieval p95 above
the query budget.
