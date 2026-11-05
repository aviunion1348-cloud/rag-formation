# Performance and scale (Stage I) — 56 → 8 750 chunks, with the ANN gate

Measured with `scripts/bench_scale.py` on 2 vCPU / 3.9 GiB, single process, single
thread, no cache warm-up, hashing embeddings (512-d) and the extractive answer
path. Each row is a separate process and a throw-away data directory. Raw reports:
[`evidence/scale/`](evidence/scale/).

| Requested | Chunks | Sources | Ingest | Ingest rate | Query p50 | Query p95 | Correct answers | DB on disk |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 59 (the real corpus) | 56 | 8 | 0.02 s | 2 590 /s | **7.6 ms** | 8.9 ms | 12/12 | 2.2 MB |
| 250 | 224 | 32 | 0.11 s | 2 004 /s | **12.2 ms** | 13.0 ms | 12/12 | 6.0 MB |
| 1 000 | 875 | 125 | 0.64 s | 1 364 /s | **32.6 ms** | 39.1 ms | 12/12 | 11.5 MB |
| 5 000 | 4 375 | 625 | 9.4 s | 466 /s | **146.7 ms** | 165.7 ms | 10/10 | 43.7 MB |
| 10 000 | 8 750 | 1 250 | 35.5 s | 247 /s | **414.8 ms** | 437.1 ms | 10/10 | 83.7 MB |

"Correct answers" is a spot check, not a warm feeling: each question asks for a
specific `svc-NNN` availability value and the answer must contain that exact value
with a citation. A fast system that answers the wrong thing is a failure, and an
earlier run of this benchmark did fail one of ten — that failure is kept below.

## 1. The shape of the curve

Two independent runs of this benchmark exist for the two largest sizes and they
agree within run-to-run noise on this shared box: 5 000 chunks measured 173.7 ms p50
in the first run and 146.7 ms p50 in the final gate run; 10 000 chunks measured
420.7 ms and 414.8 ms. Both 5k runs pass the gate with ≥30 % headroom and both 10k
runs fail it, so the decision below does not depend on which sample is quoted. The
table quotes the final gate run (`local_gates-…T155552Z.log`); the earlier sample is
retained in the run history rather than deleted.

Query latency is flat-ish to 1k chunks and then linear in corpus size
(≈ 0.047 ms per chunk beyond that), because the vector channel is an exact scan of
every stored vector and BM25 runs over the same corpus. Ingest throughput falls
from ≈ 2 600 to ≈ 240 chunks/s as the corpus grows (SQLite write amplification and
vector storage), which is expected and not user-facing.

## 2. The failure this stage found (and fixed)

At 8 750 chunks the 10k run answered **9/10** correct: `svc-003` was answered with
`svc-297`, `svc-833` and `svc-921`. Root cause: on a corpus of near-identical
documents every chunk matches the question's ordinary words, so the identifier in
the question is the only discriminating signal — and it was worth a quarter of a
coverage score. Fix: an exact-identifier boost in the support score
(`ragformation/retrieval.py`); the same corpus then answered **10/10** at both
5k and 10k, with no change to v1/v2 (45/46, 14/14).

A second defect surfaced in the same run and is fixed too: two *different*
services reporting different availability values were surfaced as a
`needs_review` contradiction about `svc-001`. A contradiction now requires that,
when the question names an entity, both statements mention it.

## 3. ANN decision gate

The gate, stated as a rule rather than a preference:

> Keep the exact scan while p50 query latency stays **≤ 250 ms** on the target
> hardware. Above that, either add an approximate nearest-neighbour index or
> shard the corpus; do not advertise the larger corpus size until one of those
> exists.

Applied to the measurements:

| Corpus | p50 | Gate (≤ 250 ms) |
| --- | --- | --- |
| 56 | 7.6 ms | pass |
| 224 | 12.2 ms | pass |
| 875 | 32.6 ms | pass |
| 4 375 | 146.7 ms | pass (with 41 % headroom) |
| 8 750 | 414.8 ms | **fails** |

**Decision:** the supported operating range for this release is **≤ 5 000 chunks**
on hardware like this; 10 000 chunks is measured but **not** supported, and the
number is published as a curve, not as a claim. No ANN index is implemented in
this release: adding one now would be an unmeasured performance feature, and the
qualification rule is to stop adding capability once the acceptance gates are
decided. The gate above is the precondition for the next size step.

## 4. What is not measured

* Concurrency: every number is one process answering one question at a time. No
  throughput-under-load claim.
* A neural embedder, which would change both the scan cost (dense vectors, 384–768
  dimensions) and the quality.
* Disk pressure and cold-cache behaviour beyond what a 2 vCPU sandbox shows.
* Memory: the running process was not instrumented, so no RSS curve is published.
