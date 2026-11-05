# Helios Grid — Release Process

> **Synthetic corpus.** Invented for this demonstration; not real policy.

## 1. Canary Rollout

Releases go to a canary cohort of **5 percent** of traffic for **30 minutes**
before full rollout. A canary is aborted automatically if the error rate exceeds
**2 percent** or if p95 latency degrades by more than **20 percent** against the
previous hour.

## 2. Rollback Window

A release can be rolled back to the previous known-good build within **15
minutes**. Rollback requires no schema change: database migrations must be
backwards compatible for at least two releases, so a rollback never strands data.

## 3. Migration Order

Migrations run in expand-migrate-contract order: add the new column, dual-write,
backfill, then remove the old column in a later release. A destructive migration
in the same release as a code change is rejected by the deploy checker.

## 4. Change Freeze

No production change is deployed during the end-of-quarter freeze, which covers
the last **two business days** of each quarter. Emergency fixes during a freeze
need director approval and a post-incident review.
