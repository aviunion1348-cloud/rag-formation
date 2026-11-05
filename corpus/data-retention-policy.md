# Helios Grid — Data Retention Policy

> Synthetic corpus. Invented for this demonstration.

## 1. Scope

This policy covers application logs, audit logs, customer content, and internal
analytics. It applies to every environment that handles customer data.

## 2. Retention Periods

Application logs are retained for **90 days** in hot storage and then archived
for a further **275 days**. Audit logs are retained for **400 days** total,
because the security team needs a full annual review window plus a buffer.

Customer content is retained for the life of the contract and is deleted within
**30 days** of contract termination. Backups that contain customer content are
rotated every **14 days**, so the effective deletion window after termination is
at most 44 days.

Internal analytics derived from customer content is retained for **18 months**
in aggregated form. Aggregated data may not be re-identified.

## 3. Deletion Requests

A verified deletion request from a customer is executed within **7 days**.
Deletion covers primary storage, derived search indexes, and cached embeddings.
Vectors derived from deleted content must be removed from the vector index in the
same operation; leaving stale embeddings behind is treated as a security defect.

## 4. Exceptions

Legal hold overrides this policy. A hold must be requested in writing by the
legal team and is reviewed every **90 days**.
