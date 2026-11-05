# Helios Grid — Backup and Restore Runbook

> **Synthetic corpus.** Invented for this demonstration; not real policy.

## 1. Backup Schedule

Customer content is backed up **every six hours** with a **14-day** retention
window. Configuration and infrastructure state are backed up daily with a
**35-day** retention window. Backups are encrypted with a key that is rotated
annually and stored separately from the backup data.

## 2. Restore Testing

A restore test is performed **quarterly** against a staging environment. The test
must restore a full tenant and verify row counts, index consistency and access
controls. A restore test that is not completed in a quarter is reported to the
security team as a compliance gap.

## 3. Restore Time

The recovery time objective is **four hours** for a single tenant and **24 hours**
for a full region. The recovery point objective is **six hours**, matching the
backup interval.

## 4. Disposal

Expired backups are deleted by the backup service. Deletion of a tenant's data
must also purge that tenant's backups on the next rotation cycle, which is why the
effective deletion window after contract termination is **44 days**.
