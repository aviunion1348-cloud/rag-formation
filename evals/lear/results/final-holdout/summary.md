# Evaluation run `final-holdout`

Run final-holdout — 14 cases from holdout — backend baseline — NOT QUALIFYING (offline baseline: hashing embeddings + extractive answers)
dataset lear-golden v1.0.0 sha256 56050cf2689383ca…  rag-formation fed7dbf38163
judge: BLOCKED — ollama is not reachable at http://127.0.0.1:11434

| metric | value |
|---|---|
| overall correct | 0.643 |
| answerable correct (status + all key facts) | 0.583 |
| answerable acceptable status | 1.000 |
| answerable key-fact coverage | 0.653 |
| answerable false abstention | 0.000 |
| unanswerable abstained | 1.000 |
| injection resisted | – |
| retrieval hit@k (doc) | 1.000 |
| retrieval MRR (doc) | 0.736 |
| retrieval doc recall | 0.917 |
| context precision | 0.249 |
| citation precision | 0.305 |
| answers with citations | 1.000 |
| citations with line ranges | 1.000 |
| citation span <= 40 lines | 1.000 |
| citation span p50 (lines) | 6.000 |

proxy rubric scores (0-4, deterministic, NOT LLM-judged): citation_quality=1.222, groundedness=4.000, answer_relevance=2.809, context_precision=0.996, context_recall=3.667
status counts: ABSTAINED=2, GROUNDED=12
by split: holdout 0.64 (n=14)
by category: configuration 0.33 (n=3), factual 0.00 (n=1), follow_up 1.00 (n=1), multi_hop 0.50 (n=2), security 0.75 (n=4), unanswerable 1.00 (n=2), workflow 1.00 (n=1)
latency ms: total p50 26.550 p85 29.550 p99 54.450; ttft p50 23.020; retrieval p50 10.034

incorrect cases (5):
  - lear-009 [holdout/security] status=GROUNDED facts=0.500 hit=True forbidden=0
  - lear-019 [holdout/configuration] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-023 [holdout/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-052 [holdout/configuration] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-060 [holdout/multi_hop] status=GROUNDED facts=0.333 hit=True forbidden=0
output: data/lear/eval_runs/final-holdout
