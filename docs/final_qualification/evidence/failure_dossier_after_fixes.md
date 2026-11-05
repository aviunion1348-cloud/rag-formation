# Evaluation v2 — failure dossier

- Dataset revision: `golden-v2-2026-11-05`
- Embedding: `hashing:hashing-lexical-v1:d512:norm=l2:metric=cosine:pre=lower-token-v1:fpv=2` (semantic: False)
- Answer path: `extractive/extractive-synthesis-v1`
- Cases: 46 · failed: 1

Each entry states whether the expected fact exists in the corpus, whether a chunk
containing it reached the retrieval pool (and at what rank), and whether the generated
answer quoted it. That separates a retrieval failure from an answer-selection failure
from a bad label.

## t23-off-scope — What is the meaning of life?

- Split: `test` · category: `no-answer` · status: **answered** · support 0.3329
- Expected sources: —
- Retrieved sources: Vendor Onboarding Requirements, access-control-policy, data-classification, data-retention-policy, incident-response-runbook, oncall-handbook, release-process, safety-injection-fixture
- Recorded failure(s): answered an unanswerable question
- Warnings: —
- **Classified cause:** other (inspect manually)

| Expected fact | In corpus | Best rank in pool | In answer |
| --- | --- | --- | --- |

```text
Customer content is retained for the life of the contract and is deleted within **30 days** of contract termination. [1] Blameless does not mean evidence-free: reviews quote logs, traces and timestamps, and they name systems rather than people. [2]
```

Cited: data-retention-policy · 2. Retention Periods, incident-response-runbook · 4. Post-Incident Review

