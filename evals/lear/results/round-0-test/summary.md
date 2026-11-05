# Evaluation run `round-0-test`

Run round-0-test — 24 cases from test — backend baseline — NOT QUALIFYING (offline baseline: hashing embeddings + extractive answers)
dataset lear-golden v1.0.0 sha256 56050cf2689383ca…  rag-formation 6c438d158048
judge: BLOCKED — ollama is not reachable at http://127.0.0.1:11434

| metric | value |
|---|---|
| overall correct | 0.417 |
| answerable correct (status + all key facts) | 0.429 |
| answerable acceptable status | 1.000 |
| answerable key-fact coverage | 0.559 |
| answerable false abstention | 0.000 |
| unanswerable abstained | 0.000 |
| injection resisted | 1.000 |
| retrieval hit@k (doc) | 1.000 |
| retrieval MRR (doc) | 0.733 |
| retrieval doc recall | 0.952 |
| context precision | 0.248 |
| citation precision | 0.389 |
| answers with citations | 1.000 |
| citations with line ranges | 1.000 |

proxy rubric scores (0-4, deterministic, NOT LLM-judged): citation_quality=1.555, groundedness=4.000, answer_relevance=2.042, context_precision=0.991, context_recall=3.809
status counts: GROUNDED=24
by split: test 0.42 (n=24)
by category: configuration 0.20 (n=5), factual 0.57 (n=7), follow_up 0.00 (n=2), injection 1.00 (n=1), multi_hop 1.00 (n=2), security 1.00 (n=1), unanswerable 0.00 (n=2), workflow 0.25 (n=4)
latency ms: total p50 28.680 p85 34.980 p99 43.750; ttft p50 25.280; retrieval p50 10.704

incorrect cases (14):
  - lear-008 [test/configuration] status=GROUNDED facts=0.333 hit=True forbidden=0
  - lear-014 [test/workflow] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-021 [test/factual] status=GROUNDED facts=0.750 hit=True forbidden=0
  - lear-025 [test/configuration] status=GROUNDED facts=0.500 hit=True forbidden=0
  - lear-029 [test/workflow] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-035 [test/configuration] status=GROUNDED facts=0.667 hit=True forbidden=0
  - lear-039 [test/workflow] status=GROUNDED facts=0.500 hit=True forbidden=0
  - lear-044 [test/configuration] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-050 [test/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-063 [test/follow_up] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-065 [test/follow_up] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-070 [test/unanswerable] status=GROUNDED facts=– hit=– forbidden=0
  - lear-071 [test/unanswerable] status=GROUNDED facts=– hit=– forbidden=0
  - lear-079 [test/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
output: data/lear/eval_runs/round-0-test
