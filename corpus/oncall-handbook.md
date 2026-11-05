# Helios Grid — On-Call Handbook

> **Synthetic corpus.** Invented for this demonstration; not real policy.

## 1. Rotation

On-call rotations run for **one week**, Monday 10:00 to Monday 10:00 local time.
Handover is a written summary posted in the operations channel before 11:00 on the
Monday of the switch. A handover without open incidents, in-flight changes and
known risks is rejected by the incoming engineer.

## 2. Acknowledgement

The primary on-call must acknowledge a page within **five minutes**. If a page is
not acknowledged within **ten minutes**, the secondary is paged automatically and
the primary's manager is notified.

## 3. Escalation Path

Escalation runs primary, secondary, incident commander, engineering director.
Escalating directly to the director is reserved for SEV-1 incidents with customer
data exposure.

## 4. Fatigue

After a night of more than three production pages, the engineer is entitled to a
**half day** of recovery time. On-call shifts may not be worked back-to-back
without an explicit exception from the engineering director.

## 5. Runbook Discipline

Every tier-1 service must have a runbook with a rollback step and a verification
step. A paging alert without a linked runbook is treated as a defect in the
alerting configuration.
