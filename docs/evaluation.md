# Evaluation

Numbers below were produced by `make eval` in this repository on 2026-11-05 with
`RAG_EMBED_PROVIDER=hashing` and `RAG_LLM_PROVIDER=extractive` (no model server
was available). The raw report is committed as
`evals/results/extractive-hashing-2026-11-05.json`. Re-run before trusting any of
it.

```bash
make ingest      # corpus/ → 6 sources, 21 chunks
make eval        # 14 golden questions
```

## 1. Corpus and question set

* **Corpus**: 6 synthetic documents (incident runbook, retention policy, deployment
  standard, vendor onboarding HTML, support-macros JSON, prompt-injection fixture),
  21 chunks. Every document states that it is invented for this demonstration.
* **Questions**: 14 in `evals/golden.json` — 11 answerable (single-fact, identifier,
  table-ish, JSON record, cross-section), 2 out-of-corpus, 1 injection attempt.
* **Labels are document-level**: a retrieved chunk counts as relevant when it comes
  from an expected source. A question may therefore be "recalled" by a chunk that
  does not contain the exact fact; the fact checks (`mustContain`) are what catch
  that.
* **Fact checks are deterministic**: substring checks against the answer text for
  required facts, and against forbidden facts (e.g. `canberra`, `approved`, `sk-`)
  for leakage and instruction-following.

Known weakness of this set: 14 questions on 6 documents is a smoke test, not a
benchmark. It is enough to catch regressions and to prove the pipeline works; it
is not evidence of quality on a real corpus.

## 2. Retrieval results

| Metric | Value | Meaning |
|---|---|---|
| Recall@6 | **1.000** | Every answerable question had an expected source in the top 6 |
| MRR@6 | **1.000** | The first relevant chunk was ranked first in every case |
| nDCG@6 | **0.967** | One or two relevant chunks appear slightly below the top |
| Labelled questions | 11 | |

MRR = 1.000 is expected at this scale: the corpus is small and the questions are
close to the document vocabulary. Do not read it as "retrieval is solved".

### Channel comparison

`ragformation eval --mode lexical` and `--mode semantic` run the same questions
through one channel only. On this corpus the lexical channel alone already
achieves recall 1.0 for the same 11 questions, because the questions and passages
share vocabulary. The semantic channel is only meaningful with a neural embedder;
with `hashing` it is a second lexical view and is reported as such.

## 3. Generation and grounding results

| Metric | Value | Meaning |
|---|---|---|
| Cases passed | **14 / 14** | All fact, abstention and leakage checks |
| Answered | 11 | |
| Abstained | 3 | Both out-of-corpus questions and the injection attempt |
| Abstain accuracy | **1.000** | No answerable question abstained; no unanswerable one was answered |
| Fact recall (`mustContain`) | **1.000** | Every required fact appeared in the answer |
| Forbidden-fact rate | **1.000** | No forbidden string (`canberra`, `approved`, `sk-`, `system prompt`) appeared |
| Citation coverage | **1.000** | Every claim carried at least one validated citation |

Because the generator here is **extractive**, "fact recall 1.0" means the retrieved
sentence was quoted correctly — it says nothing about paraphrasing quality. A
neural model's output must be re-evaluated; the checks (`mustContain`,
`mustNotContain`, citation validation, abstention) apply unchanged.

## 4. Abstention calibration

Best support score per golden case (the number compared to
`RAG_ABSTAIN_THRESHOLD`, default `0.22`):

| Case | Support | Outcome |
|---|---|---|
| q12 general knowledge ("capital of Australia") | 0.107 | abstained |
| q13 injection attempt | 0.144 | abstained (quarantined + low support) |
| q11 out-of-corpus (lunar insurance) | 0.212 | abstained |
| q14 backup window | 0.468 | answered |
| q07 rollback window | 0.471 | answered |
| q09 MACRO-AUTH-02 | 0.478 | answered |
| q06 vendor breach notice | 0.486 | answered |
| q02 application log retention | 0.497 | answered |
| q01 audit log retention | 0.519 | answered |
| q05 vendor access | 0.550 | answered |
| q10 deletion window | 0.586 | answered |
| q04 post-incident review | 0.660 | answered |
| q08 migration order | 0.716 | answered |
| q03 SEV-1 paging | 0.728 | answered |

Two observations worth keeping honest:

1. The threshold sits in a wide gap (0.212 → 0.468), so it is not knife-edge on
   this corpus. That is a property of this corpus, not a general guarantee.
2. `q11` at 0.212 is only 0.008 below the threshold. A small change in the corpus
   or embedder could push an out-of-corpus question over the line. This is the
   single most fragile number in the system; re-measure it whenever the embedder
   or chunking changes.

## 5. Latency and throughput

Measured on 300 sequential queries against the 21-chunk index, single process,
Python 3.11, 2 vCPU:

| Metric | mean | p50 | p95 | max |
|---|---|---|---|---|
| End-to-end | 5.81 ms | 5.56 ms | 6.95 ms | 54.68 ms |
| Retrieval | 0.54 ms | 0.52 ms | 0.73 ms | 1.41 ms |
| Generation (extractive) | 1.42 ms | 1.24 ms | 1.68 ms | 49.46 ms |
| TTFT | 0.75 ms | 0.72 ms | 1.03 ms | 1.50 ms |
| ITL | 0.11 ms | 0.09 ms | 0.09 ms | 8.06 ms |

Throughput ≈ **165 queries/s** sequentially. The max values are first-call/JIT-ish
warm-up effects (SQLite page cache, import-time work), not steady state.

These numbers exclude any neural model. With Ollama the generation stage will be
seconds, not milliseconds, and TTFT/ITL become the interesting metrics — which is
exactly why they are measured separately and recorded per query.

Ingestion: 6 documents → 21 chunks in **11 ms**; re-ingest of unchanged files
**1.8 ms** (short-circuit). Database size: 1.6 MB after 300 recorded queries
(traces included).

## 6. What this evaluation does *not* cover

* A live language model. The Ollama adapter is tested against a stub that speaks
  the real HTTP contract, but no model was available here. Prompt obedience,
  hallucination rate and citation behaviour of an actual model are **unverified**.
* Semantic retrieval with a neural embedder. The `hashing` embedder is lexical;
  paraphrase recall is therefore not measured.
* Corpora larger than 21 chunks. No claim is made about scale.
* Multi-hop and table-heavy questions beyond the two that exist here.
* Adversarial prompt injection beyond the fixture in `corpus/` and the unit tests
  in `tests/test_guardrails.py`. Note the fixture's own *description* of an attack
  trips the quarantine pattern, so unrelated questions can carry a
  `quarantined_instruction_like_content` warning; the answer and citation are
  unaffected, and `SECURITY.md` §3 records this as an accepted precision cost.
* Human judgement of answer helpfulness. No LLM-as-judge is used, so no
  "quality score" is claimed.

## 7. Reproducing

```bash
python3 -m unittest discover -s tests -p 'test_*.py'   # 107 tests
python3 -m ragformation ingest corpus
python3 -m ragformation eval --json > /tmp/eval.json
python3 -m ragformation stats --json
```

With a neural provider, expect the retrieval and generation sections to change
substantially and the abstention calibration in §4 to need re-measurement:

```bash
export RAG_EMBED_PROVIDER=ollama RAG_LLM_PROVIDER=ollama
python3 -m ragformation rebuild --force
python3 -m ragformation eval
```
