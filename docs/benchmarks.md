# Measured behaviour at 100 / 500 / 1000 chunks

These numbers were produced by `scripts/bench_scale.py` on this machine, on this
repository, with no network access. Every figure below is reproducible with one
command and is stored verbatim under `evals/results/scale/`.

```bash
python scripts/bench_scale.py --chunks 100 500 1000 --queries 24
```

## Environment

| | |
| --- | --- |
| Platform | Linux 6.1.158 (x86_64), 2 vCPU, container |
| Python | 3.11.2 |
| Embeddings | `hashing` (`semantic: false`), 512 dimensions, L2-normalised, cosine |
| Answer path | `extractive` — **no language model ran** |
| Storage | SQLite (WAL) on local disk, brute-force vector scan |
| Queries | 24 per size: 12 answerable (service availability lookup), 12 off-topic |

## Results

| Chunks | Sources | Ingest (s) | Chunks/s | Query p50 (ms) | Query p95 (ms) | DB on disk | Correct answers |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 91 | 13 | 0.05 | 1906 | 10.8 | 12.3 | 3.7 MiB | 12/12 |
| 441 | 63 | 0.30 | 1447 | 24.5 | 27.8 | 8.0 MiB | 12/12 |
| 875 | 125 | 0.80 | 1098 | 42.5 | 52.7 | 11.7 MiB | 12/12 |

Reading the table:

- **Ingest is fast because nothing is pretending to be a neural encoder.** The
  hashing embedder is a deterministic feature hash; a real transformer encoder
  would dominate this column. The numbers are not comparable to a GPU pipeline.
- **Query latency grows roughly linearly with chunk count** (10.8 → 24.5 → 42.5 ms
  for 91 → 441 → 875 chunks). Retrieval scans every chunk in memory and the
  lexical side uses SQLite FTS5. There is no approximate index. This is the
  documented limit: comfortable at thousands of chunks, not millions.
- **The 12/12 column is a correctness check, not a quality score.** Each answerable
  question asks for a value that exists verbatim in one generated document, and
  the check requires the fact to appear *and* a citation to be present. It does
  not measure paraphrase handling; that is what `evals/golden_v2.json` is for
  (see `docs/evaluation.md`).
- **DB size includes the WAL**, not just the main file, because that is the honest
  on-disk footprint before checkpointing.

## What was not measured

- Any pretrained embedding model (no weights are downloadable in this
  environment; the `semantic` and `lsa` extras exist but were not exercised here).
- Any language-model answer path (no Ollama or API key; CI never calls a paid API).
- Multi-process or concurrent load. The server is threaded, but this benchmark is
  sequential by design so the latency column means one thing.
- PDF/DOCX parsing, OCR, or large-file ingestion.
- Recall quality at these sizes. Scale tests correctness and timing; quality is
  measured on the versioned evaluation sets, not on synthetic templates.

## Reproducing and comparing

Each run writes `evals/results/scale/scale-<n>.json` containing the machine,
provider, corpus, ingest, query and limits metadata, plus a sample answer. If you
change the embedder, re-run and compare files directly — the JSON records the
embedding fingerprint, so a comparison across fingerprints is visibly a
different experiment rather than a silent improvement.

```bash
# after switching providers, the fingerprint in the report changes:
RAG_EMBED_PROVIDER=lsa python scripts/bench_scale.py --chunks 500 --queries 12 --out /tmp/lsa
python -c "import json;print(json.load(open('/tmp/lsa/scale-500.json'))['provider'])"
```
