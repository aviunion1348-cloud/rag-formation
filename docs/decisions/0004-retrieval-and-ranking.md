# ADR 0004 — Hybrid retrieval, reciprocal rank fusion, heuristic rerank

* Status: accepted
* Date: 2026-11-05

## Context

Embedding similarity alone misses exact identifiers and rare tokens (`SEV-1`,
`MACRO-AUTH-02`, `400 days`); BM25 alone misses paraphrase. The sketch asks for
both plus a rerank stage, and the master prompt asks for evidence of each stage.

## Decision

1. **Two channels, run always.** Cosine over stored vectors (`dense`) and FTS5
   BM25 (`lexical`), each returning a ranked candidate list. `--mode lexical` and
   `--mode semantic` exist for measurement, not for production.
2. **Fuse with RRF, k = 60.** `score = Σ 1/(60 + rank)`. Rank-based fusion needs no
   score normalisation between incomparable scales. Ties break deterministically by
   chunk id.
3. **Score the evidence, not just the rank.** `support_score` is
   `0.65 * lexical + 0.35 * semantic` where
   `lexical = 0.75 * coverage + 0.25 * phrase`,
   `coverage = matched question terms / all question terms`,
   `phrase = matched bigrams / all bigrams`,
   `semantic = clamp((cosine + 0.15) / 1.15, 0, 1)`.
   Coverage dominates deliberately: an answer must be supported by the question's
   own vocabulary. This number drives abstention (ADR 0006) and is reported per
   query, so a wrong answer is explainable.
4. **Rerank is heuristic and separable.** Matched-term weight, section-path bonus,
   position penalty. `RAG_RERANK=0` disables it and the tests assert ordering can
   change (`test_rerank_can_be_disabled`).
5. **Diversify with MMR, λ = 0.72**, run over a bounded pool with a precomputed
   pairwise similarity matrix (`Retriever._mmr`), so the greedy loop does not
   recompute dot products. Near-duplicate chunks are dropped before assembly
   (`test_dedupe_overlaps_removes_near_duplicates`).
6. **Matching tolerates stems and prefixes, and nothing more.** `TextSignals.matches`
   accepts exact tokens, stems, and prefixes ≥4 chars of the term. Compound joining
   (`audit` + `logs` → `auditlogs`) uses `has_exact`, exact/stem only — prefix
   joining created cross-contamination that pushed the wrong passage to rank 1
   (see `docs/retrieval.md` §2). This is a correctness rule, not a tuning knob.

## Consequences

Good: identifier and paraphrase queries both work; every stage is measurable in
isolation (`retrievalMs` vs `generationMs`); ranking is deterministic and
explainable.

Costs: hand-tuned weights (0.65/0.35, λ 0.72, prefix ≥4) are fitted to a 21-chunk
corpus. RRF discards score magnitude, so a single very strong match cannot
outvote broad weak agreement. No cross-encoder.

## Revisit when

a labelled set larger than 14 questions exists: sweep 0.65/0.35 and λ against
nDCG, and add a cross-encoder if p95 has headroom.
