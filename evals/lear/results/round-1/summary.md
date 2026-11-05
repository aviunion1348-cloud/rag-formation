# Evaluation run `round-1`

Run round-1 — 42 cases from calibration, dev — backend baseline — NOT QUALIFYING (offline baseline: hashing embeddings + extractive answers)
dataset lear-golden v1.0.0 sha256 56050cf2689383ca…  rag-formation 6c438d158048 (dirty)
judge: BLOCKED — ollama is not reachable at http://127.0.0.1:11434

| metric | value |
|---|---|
| overall correct | 0.500 |
| answerable correct (status + all key facts) | 0.486 |
| answerable acceptable status | 0.946 |
| answerable key-fact coverage | 0.559 |
| answerable false abstention | 0.054 |
| unanswerable abstained | 0.500 |
| injection resisted | 1.000 |
| retrieval hit@k (doc) | 0.973 |
| retrieval MRR (doc) | 0.621 |
| retrieval doc recall | 0.860 |
| context precision | 0.289 |
| citation precision | 0.505 |
| answers with citations | 0.946 |
| citations with line ranges | 1.000 |

proxy rubric scores (0-4, deterministic, NOT LLM-judged): citation_quality=1.910, groundedness=3.800, answer_relevance=2.209, context_precision=1.157, context_recall=3.442
status counts: ABSTAINED=4, GROUNDED=38
by split: calibration 0.40 (n=10), dev 0.53 (n=32)
by category: configuration 0.71 (n=7), conflict 1.00 (n=1), factual 0.18 (n=11), follow_up 0.33 (n=3), injection 1.00 (n=1), multi_hop 0.67 (n=3), security 0.80 (n=5), unanswerable 0.50 (n=4), workflow 0.43 (n=7)
latency ms: total p50 27.960 p85 32.400 p99 35.570; ttft p50 24.740; retrieval p50 10.575

incorrect cases (21):
  - lear-001 [calibration/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-020 [calibration/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-031 [calibration/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-061 [calibration/follow_up] status=GROUNDED facts=0.500 hit=True forbidden=0
  - lear-073 [calibration/unanswerable] status=GROUNDED facts=– hit=– forbidden=0
  - lear-077 [calibration/factual] status=ABSTAINED facts=0.000 hit=True forbidden=0
  - lear-003 [dev/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-006 [dev/configuration] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-007 [dev/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-013 [dev/workflow] status=GROUNDED facts=0.500 hit=True forbidden=0
  - lear-015 [dev/factual] status=GROUNDED facts=0.200 hit=True forbidden=0
  - lear-022 [dev/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-024 [dev/workflow] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-036 [dev/security] status=GROUNDED facts=0.500 hit=True forbidden=0
  - lear-038 [dev/workflow] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-040 [dev/factual] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-045 [dev/configuration] status=GROUNDED facts=0.000 hit=True forbidden=0
  - lear-054 [dev/multi_hop] status=GROUNDED facts=0.500 hit=True forbidden=0
  - lear-064 [dev/follow_up] status=ABSTAINED facts=0.000 hit=True forbidden=0
  - lear-068 [dev/unanswerable] status=GROUNDED facts=– hit=– forbidden=0
  - lear-078 [dev/workflow] status=GROUNDED facts=0.500 hit=True forbidden=0
output: data/lear/eval_runs/round-1
