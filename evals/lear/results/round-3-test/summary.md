# Evaluation run `round-3-test`

Run round-3-test — 24 cases from test — backend baseline — NOT QUALIFYING (offline baseline: hashing embeddings + extractive answers)
dataset lear-golden v1.0.0 sha256 56050cf2689383ca…  rag-formation fed7dbf38163
judge: BLOCKED — ollama is not reachable at http://127.0.0.1:11434

| metric | value |
|---|---|
| overall correct | 0.500 |
| answerable correct (status + all key facts) | 0.476 |
| answerable acceptable status | 1.000 |
| answerable key-fact coverage | 0.607 |
| answerable false abstention | 0.000 |
| unanswerable abstained | 0.500 |
| injection resisted | 1.000 |
| retrieval hit@k (doc) | 1.000 |
| retrieval MRR (doc) | 0.812 |
| retrieval doc recall | 0.929 |
| context precision | 0.255 |
| citation precision | 0.452 |
| answers with citations | 1.000 |
| citations with line ranges | 1.000 |
| citation span <= 40 lines | 1.000 |
| citation span p50 (lines) | 6.000 |

proxy rubric scores (0-4, deterministic, NOT LLM-judged): citation_quality=1.809, groundedness=4.000, answer_relevance=2.375, context_precision=1.021, context_recall=3.714
status counts: ABSTAINED=1, GROUNDED=23
by split: test 0.50 (n=24)
by category: configuration 0.20 (n=5), factual 0.57 (n=7), follow_up 0.50 (n=2), injection 1.00 (n=1), multi_hop 1.00 (n=2), security 1.00 (n=1), unanswerable 0.50 (n=2), workflow 0.25 (n=4)
latency ms: total p50 28.540 p85 37.800 p99 58.750; ttft p50 25.950; retrieval p50 10.768

incorrect cases (12):
  - lear-008 [test/configuration] status=GROUNDED facts=0.333 hit=True forbidden=0
  - lear-014 [test/workflow] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-021 [test/factual] status=GROUNDED facts=0.750 hit=True forbidden=0
  - lear-025 [test/configuration] status=GROUNDED facts=0.500 hit=True forbidden=0
  - lear-029 [test/workflow] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-035 [test/configuration] status=GROUNDED facts=0.667 hit=True forbidden=0
  - lear-039 [test/workflow] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-044 [test/configuration] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-050 [test/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-063 [test/follow_up] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-070 [test/unanswerable] status=GROUNDED facts=– hit=– forbidden=0
  - lear-079 [test/factual] status=GROUNDED facts=0.500 hit=True forbidden=0
output: data/lear/eval_runs/round-3-test
