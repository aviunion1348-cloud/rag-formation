# Semantic provider benchmark and go/no-go (Stage C)

The question this stage has to answer honestly: **does this build retrieve by
meaning, or only by words?** The short answer is *only by words*, and this
document shows the measurement that led there, including the attempt to do
better.

## 1. The pretrained-model path is blocked in this environment

Not an opinion — a probe, repeated here with the exact failures
(see [`environment.txt`](environment.txt)):

| Host needed for pretrained weights | Result |
| --- | --- |
| `huggingface.co` (sentence-transformers, model2vec, fastembed) | connection failed |
| `objects.githubusercontent.com` (GitHub release assets: spaCy, GloVe, fastText) | connection failed |
| `release-assets.githubusercontent.com` (the actual redirect target) | TLS handshake failure |

A concrete attempt in this run: `curl -sIL` on
`https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.7.1/en_core_web_md-3.7.1-py3-none-any.whl`
returns `302` and then fails to connect to the signed asset host. `pip` can reach
`pypi.org`, but no package on PyPI ships the vectors themselves; they all fetch
at runtime.

Consequence: the only *genuinely distributional* representation available is one
this repository can fit itself — `DistributionalEmbedder` (TF-IDF + truncated SVD,
i.e. LSA) — and it is labelled `semantic: true` **only inside its own corpus**.
It is not a neural encoder, and this document does not call it one.

## 2. Five configurations, same corpus, same queries

`scripts/bench_semantic_providers.py` ingests the identical 14-document corpus
into a fresh data directory per configuration and runs golden v1 (14 questions)
and golden v2 (46 cases). Raw output: [`semantic_bench.json`](semantic_bench.json).

| Configuration | Semantic? | v2 pass | v1 pass | v1 nDCG | recall@6 | precision@6 | MRR | paraphrase | exact-id+structured | off-corpus abstain | p50 | index |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| lexical BM25 only | no | **28/46** | 5/14 | 0.174 | 0.919 | 0.245 | 0.393 | 2/3 | 3/5 | 9/9 | 7.3 ms | — |
| dense hashing (control) | no | 43/46 | 14/14 | 0.951 | 0.973 | 0.378 | 0.917 | 2/3 | 4/5 | 8/9 | 10.0 ms | 1.2 MB |
| dense LSA | corpus only | 44/46 | 13/14 | 0.896 | 1.000 | **0.496** | 0.944 | **3/3** | **5/5** | 7/9 | 23.1 ms | 1.5 MB |
| hybrid hashing (**shipped default**) | no | **45/46** | 14/14 | 0.951 | 1.000 | 0.410 | 0.944 | **3/3** | **5/5** | 8/9 | 10.0 ms | 1.2 MB |
| hybrid LSA | corpus only | 44/46 | 13/14 | 0.905 | 1.000 | 0.478 | 0.944 | **3/3** | **5/5** | 7/9 | 25.4 ms | 1.5 MB |

All five are deterministic on a re-run of the same questions (checked per
configuration: the LSA path seeds its projection at `rng(0)` in
`embeddings.py:646`), and none of them makes a network call.

Two samples of this benchmark exist because it ran twice: once before the
conflict-gate change described in
[`evaluation_v2_failure_analysis.md`](evaluation_v2_failure_analysis.md) §6, once
in the final gate run. The two LSA rows moved 43/46 → 44/46 between them; the
hashing rows (43/46, 45/46) did not move at all. The change is in how a
contradiction is surfaced, and an LSA retrieval exposes one different pair of
passages than hashing does, so a case that was scored `needs_review` now answers.
The table above quotes the final run; the earlier sample is kept in the run
history. **The go/no-go verdict does not depend on the difference** — every clause
in §4 fails or passes by margins much larger than one case, and the winner is still
the shipped `hybrid hashing` at 45/46.

What the table actually says:

* **Vectors earn their place.** BM25 alone answers 28/46; any vector channel gets
  to 43–45/46 (44–45 in the final run). The gap is in `direct` (12/24 → 24/24), `multihop` (0/2 → 2/2) and
  the `distractor` case.
* **LSA is a real, measurable difference, not a placebo.** It raises recall to
  1.00 and precision@6 from 0.41 to 0.50, and it is the only configuration that
  gets the full paraphrase and exact-id/structured groups right when used alone.
* **But it costs the two things this release is judged on.** It loses the
  distractor case, abstains on one fewer off-corpus question than the default
  (7/9 vs 8/9 → *more* false answers), is 2.5× slower, and is 13/14 on v1 against
  14/14. On a 66-chunk corpus its SVD basis is fitted to `66 × 192` numbers, so it
  is fitted noise as much as fitted meaning; the win on paraphrase does not
  survive contact with the refusal behaviour.

## 3. The decision rule, fixed before the numbers were read

Stated in the run's plan and applied afterwards, not adjusted to fit:

> Promote the distributional provider to the default **iff** all of:
> (a) paraphrase pass rate improves by ≥1 case over the default;
> (b) no regression on exact-id + structured;
> (c) overall v2 pass count is not lower;
> (d) off-corpus abstention is not lower;
> (e) p50 query latency ≤ 2× the default and ≤ 100 ms;
> (f) v1 recall/MRR is not lower.
> Otherwise keep the default and publish the provider as opt-in with its numbers.

Applied to `hybrid-lsa` vs `hybrid-hashing`:

| Clause | Result | Verdict |
| --- | --- | --- |
| (a) paraphrase ≥ +1 | 3/3 vs 3/3 | fail |
| (b) no regression exact-id/structured | 5/5 vs 5/5 | pass |
| (c) overall not lower | 44 vs 45 | **fail** |
| (d) abstention not lower | 7/9 vs 8/9 | **fail** |
| (e) latency ≤ 2× | 25.4 ms vs 10.0 ms (2.5×) | **fail** |
| (f) v1 not lower | 13/14 vs 14/14 | **fail** |

## 4. Outcome

**No-go for promoting a semantic provider; the semantic claim is withheld.**

* The shipped default stays `hybrid` + `hashing`, and every surface keeps
  reporting `semantic: false` (health, `doctor`, ingest output, docs, UI).
* `RAG_EMBED_PROVIDER=distributional` remains available as an opt-in, with
  `pip install "ragformation[semantic]"` for numpy; it is documented as
  *corpus-fitted*, experimental, and slower, **not** as a semantic upgrade.
* No pretrained model is bundled or downloaded, and this release does not claim
  semantic retrieval, semantic search quality, or embedding-model parity.

## 5. What would change the verdict

1. A reachable model host (or an operator-supplied local model directory) so a
   real encoder can be measured with the same harness.
2. A corpus large enough for LSA to have a stable basis: the current 66 chunks
   give 66 documents to fit 192 dimensions.
3. An abstention signal that survives a richer representation — LSA's extra
   similarity currently *hurts* refusal, so a semantic default needs a
   calibrated confidence gate (see [`release_decision.md`](release_decision.md) §4),
   not just a better vector.
