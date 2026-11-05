# Retrieval, ranking and grounding

## 1. Channels

| Channel | Mechanism | Strength | Weakness |
|---|---|---|---|
| Semantic | Cosine similarity between the query vector and stored chunk vectors | Paraphrase, synonyms, concept-level matches — **when the embedder is neural** | Needs a model; scores are not probabilities; the hashing fallback only measures lexical overlap |
| Lexical | SQLite FTS5 BM25 (`porter unicode61`) | Exact identifiers, codes (`MACRO-AUTH-02`), numbers, rare terms | Misses paraphrase; word order ignored |
| Hybrid (default) | Reciprocal Rank Fusion of both channels, `k=60` | No score calibration needed; robust when one channel fails | Slightly more work per query; can dilute a single very strong signal |

Fusion is RRF, not a weighted score blend, because cosine and BM25 live on
different scales and RRF depends only on ranks.

Fallback behaviour: if query embedding fails, the request degrades to lexical
retrieval and records `degraded_to_lexical_retrieval` in `warnings`.

## 2. Ranking pipeline

```
candidates = semantic(top_k × 4) ∪ lexical(top_k × 4)
    → RRF fusion
    → support score (heuristic-coverage-v1)
    → MMR (bounded pool, similarity matrix precomputed)
    → context assembly under a hard token budget
```

**Support score** (`0..1`) answers "does this passage address the question?":

```
coverage  = matched question terms / all question terms      (stem + prefix tolerant)
phrase    = matched adjacent-term bigrams / all bigrams
lexical   = 0.75·coverage + 0.25·phrase
semantic  = clamp((cosine + 0.15) / 1.15, 0, 1)
support   = 0.65·lexical + 0.35·semantic
```

Term matching tolerates stems (`deleted`/`deletion`, `backups`/`backup`), prefixes
(`deploy`/`deployment`) and split compounds (`roll back`/`rollback`) in either
direction. This is what makes the lexical side useful for policy text; it is also
why the score is explicitly **not a calibrated probability**.

**MMR** (`λ = 0.72`) suppresses near-duplicate passages so overlapping chunks from
one document cannot fill the context window. Pairwise similarities are computed
once per query over a bounded pool.

## 3. Candidates, budget and exclusions

* Retrieval returns `top_k × 2` candidates; the context assembler keeps only what
  fits in `RAG_MAX_CONTEXT_TOKENS` (default 1400 estimated tokens).
* Every excluded candidate is reported with a reason
  (`ranked_outside_top_k` or `context_token_budget`) and shown in the console's
  Safety tab — the system never silently drops evidence.
* Access filtering (`allowed_sources`) is applied **inside** the retriever before
  scoring, and again when citations are resolved.

## 4. Grounding and abstention

The context is assembled as numbered evidence blocks:

```
<evidence id="1" section='2. Retention Periods' page=1 source='Data Retention Policy'>
Audit logs are retained for 400 days in total, …
</evidence>
```

The system prompt requires `[n]` markers, forbids outside knowledge, and requires
the exact sentinel `INSUFFICIENT_EVIDENCE` when evidence is insufficient.

Abstention happens in two places, deliberately:

1. **Before generation** — if the best support score is below
   `RAG_ABSTAIN_THRESHOLD` (default 0.22), no model is called at all
   (`abstain_reason=insufficient_support`, and the trace marks generation as
   `skipped`).
2. **After generation** — if the model emits the refusal sentinel
   (`model_declined_insufficient_evidence`).

Other abstention reasons: `no_retrieved_evidence` (empty corpus or no match),
`no_usable_evidence` (everything retrieved was quarantined).

The threshold is set from the labelled set, not guessed. Measured on
`evals/golden.json` (support score of the best passage):

| Group | Range |
|---|---|
| Unanswerable / out-of-corpus / injection questions (3) | 0.107 – 0.212 |
| Answerable questions (11) | 0.468 – 0.728 |

The default threshold (0.22) sits inside a 0.256-wide gap, i.e. it is not
knife-edge on this corpus. That gap is corpus- and model-specific: re-measure it
after changing the embedder, and see `docs/evaluation.md` §4 before reusing the
default elsewhere.

Answers that cite nothing are marked `needs_review` rather than presented as
grounded. Numeric disagreement between two *cited* sources also yields
`needs_review` with both statements shown.

## 5. Citation validation

A citation is only accepted if it resolves to a passage the server actually
retrieved for this query:

1. Markers `[n]` are matched against the evidence ids assigned during context
   assembly.
2. Unknown markers are removed and counted (`citationsInvalid`).
3. Each surviving citation is rebuilt from the chunk record — source id, title,
   section, page, excerpt, support — so the model cannot invent a title or page.
4. URLs in the answer that do not appear in the evidence become
   `[unverified-link-removed]`.
5. Credential-shaped strings are redacted.
6. Claims are split from the answer with their markers; `claimCitationRate`
   reports how many claims carry support.

## 6. Tuning knobs

| Variable | Default | Effect |
|---|---|---|
| `RAG_TOP_K` | 6 | Evidence passages considered |
| `RAG_RETRIEVAL_MODE` | `hybrid` | `semantic`, `lexical` or both |
| `RAG_RERANK` | 1 | Enable `heuristic-coverage-v1` ordering |
| `RAG_MMR_LAMBDA` | 0.72 | Diversity vs relevance |
| `RAG_MAX_CONTEXT_TOKENS` | 1400 | Hard context budget |
| `RAG_ABSTAIN_THRESHOLD` | 0.22 | Support below which the system refuses to answer |
| `RAG_CHUNK_*` | 180/300/40 | Chunk size, max size, overlap |

Change one at a time and re-run `make eval`; `docs/evaluation.md` records the
before/after you should expect to see.

## 7. Known limits

* **Brute-force search.** Every query scans all vectors in Python. Measured 0.5–0.7 ms
  at 21 chunks; roughly linear growth. Beyond ~10⁵ chunks, replace
  `Retriever.search` with an ANN index — `Store.vectors_for` is the seam.
* **No cross-encoder reranker.** The reranker is a transparent feature blend.
  A cross-encoder would likely improve ordering on ambiguous questions; it is
  deferred because it would add a heavy dependency and latency without a measured
  baseline here.
* **Lexical fallback is not semantic.** With `hashing` embeddings, "how long do we
  keep audit trails" will not find "audit logs are retained" unless the words
  overlap. This is stated in health, CLI, console and API, not hidden.
* **English-oriented tokenisation.** Porter stemming and the stopword list are
  English; other languages will retrieve but with weaker lexical signal.
