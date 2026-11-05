# Evaluation v2 — failure dossier

- Dataset revision: `golden-v2-2026-11-05`
- Embedding: `hashing:hashing-lexical-v1:d512:norm=l2:metric=cosine:pre=lower-token-v1:fpv=2` (semantic: False)
- Answer path: `extractive/extractive-synthesis-v1`
- Cases: 46 · failed: 7

Each entry states whether the expected fact exists in the corpus, whether a chunk
containing it reached the retrieval pool (and at what rank), and whether the generated
answer quoted it. That separates a retrieval failure from an answer-selection failure
from a bad label.

## d07-rollback-window — What is the rollback window for a release?

- Split: `dev` · category: `direct` · status: **answered** · support 0.6998
- Expected sources: release-process, deployment-standard
- Retrieved sources: backup-restore-runbook, data-retention-policy, deployment-standard, incident-response-runbook, oncall-handbook, release-process
- Recorded failure(s): missing facts: ['15 minutes']
- Warnings: —
- **Classified cause:** fact_retrieved_but_not_quoted (answer selection)

| Expected fact | In corpus | Best rank in pool | In answer |
| --- | --- | --- | --- |
| `15 minutes` | yes | 1 | no |

```text
Rollback requires no schema change: database migrations must be backwards compatible for at least two releases, so a rollback never strands data. [1]
```

Cited: release-process · 2. Rollback Window

## t06-exact-billing-macro — How long do refunds take under MACRO-BILLING-01?

- Split: `test` · category: `exact-id` · status: **answered** · support 0.2945
- Expected sources: support-macros
- Retrieved sources: Vendor Onboarding Requirements, access-control-policy, backup-restore-runbook, data-classification, incident-response-runbook, incident-response-standard, oncall-handbook, release-process
- Recorded failure(s): missing facts: ['5 business days']
- Warnings: quarantined_instruction_like_content
- **Classified cause:** fact_retrieved_but_not_quoted (answer selection)

| Expected fact | In corpus | Best rank in pool | In answer |
| --- | --- | --- | --- |
| `5 business days` | yes | 1 | no |

```text
Support macros invented for this demonstration. - id: MACRO-BILLING-01; title: Duplicate charge investigation; steps: Confirm the duplicate authorisation in the payments ledger, refund the second charge, and send the customer a reference number. [1]
```

Cited: support-macros · support-macros

## t08-structured-slo — What availability SLO does the ingest-api commit to?

- Split: `test` · category: `structured` · status: **abstained** · support 0.2437
- Expected sources: service-catalog
- Retrieved sources: Vendor Onboarding Requirements, backup-restore-runbook, data-retention-policy, deployment-standard, incident-response-runbook, incident-response-standard, release-process, service-catalog
- Recorded failure(s): abstained on an answerable question; missing facts: ['99.95']
- Warnings: quarantined_instruction_like_content
- **Classified cause:** fact_retrieved_but_not_quoted (answer selection)

| Expected fact | In corpus | Best rank in pool | In answer |
| --- | --- | --- | --- |
| `99.95` | yes | 1 | no |

```text
The retrieved evidence does not contain enough information to answer this question, so the system abstained instead of guessing.
```

## t09-owner — Which team owns the ingest-api service?

- Split: `test` · category: `structured` · status: **abstained** · support 0.3136
- Expected sources: service-catalog
- Retrieved sources: Vendor Onboarding Requirements, access-control-policy, backup-restore-runbook, data-retention-policy, deployment-standard, evidence-retention-memo, incident-response-runbook, oncall-handbook
- Recorded failure(s): abstained on an answerable question; missing facts: ['platform-team']
- Warnings: —
- **Classified cause:** fact_retrieved_but_not_quoted (answer selection)

| Expected fact | In corpus | Best rank in pool | In answer |
| --- | --- | --- | --- |
| `platform-team` | yes | 1 | no |

```text
The retrieved evidence does not contain enough information to answer this question, so the system abstained instead of guessing.
```

## t15-config-backup — How long is configuration state retained in backups?

- Split: `test` · category: `direct` · status: **needs_review** · support 0.3641
- Expected sources: backup-restore-runbook
- Retrieved sources: Vendor Onboarding Requirements, access-control-policy, backup-restore-runbook, data-classification, data-retention-policy, evidence-retention-memo, incident-response-runbook, oncall-handbook
- Recorded failure(s): missing facts: ['35 days']
- Warnings: conflicting_evidence, quarantined_instruction_like_content
- **Classified cause:** expected_fact_not_in_corpus (label or fixture problem)

| Expected fact | In corpus | Best rank in pool | In answer |
| --- | --- | --- | --- |
| `35 days` | NO | not retrieved | no |

```text
Configuration and infrastructure state are backed up daily with a **35-day** retention window. [1]

Sources disagree: **3 years** according to access-control-policy; **5 years** according to evidence-retention-memo. Both statements are cited with provenance; this answer is marked for review instead of choosing one.
```

Cited: backup-restore-runbook · 1. Backup Schedule, access-control-policy · 2. Access Reviews, evidence-retention-memo · 2. Retention

## t18-deploy-promotion — When is a deploy promoted under the blue/green model?

- Split: `test` · category: `direct` · status: **answered** · support 0.6552
- Expected sources: deployment-standard
- Retrieved sources: Vendor Onboarding Requirements, access-control-policy, data-retention-policy, deployment-standard, evidence-retention-memo, incident-response-runbook, incident-response-standard, oncall-handbook
- Recorded failure(s): missing facts: ['10 minutes']
- Warnings: —
- **Classified cause:** fact_retrieved_but_not_quoted (answer selection)

| Expected fact | In corpus | Best rank in pool | In answer |
| --- | --- | --- | --- |
| `10 minutes` | yes | 1 | no |

```text
Production deploys use the blue/green model. [1]
```

Cited: deployment-standard · deployment-standard

## t23-off-scope — What is the meaning of life?

- Split: `test` · category: `no-answer` · status: **answered** · support 0.3289
- Expected sources: —
- Retrieved sources: Vendor Onboarding Requirements, access-control-policy, backup-restore-runbook, data-classification, data-retention-policy, evidence-retention-memo, incident-response-runbook, incident-response-standard
- Recorded failure(s): answered an unanswerable question
- Warnings: —
- **Classified cause:** other (inspect manually)

| Expected fact | In corpus | Best rank in pool | In answer |
| --- | --- | --- | --- |

```text
Customer content is retained for the life of the contract and is deleted within **30 days** of contract termination. [1] Blameless does not mean evidence-free: reviews quote logs, traces and timestamps, and they name systems rather than people. [2]
```

Cited: data-retention-policy · 2. Retention Periods, incident-response-runbook · 4. Post-Incident Review

