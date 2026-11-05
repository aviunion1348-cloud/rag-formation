# Helios Grid — Post-Incident Review Standard

> **Synthetic corpus.** Invented for this demonstration; not real policy.

## 1. Review Deadline

Every SEV-1 and SEV-2 incident requires a written post-incident review. The review
must be published within **five business days** of the incident being resolved. A
review that slips past ten business days requires director sign-off and is logged
as a process failure.

## 2. Required Content

A review records the timeline, the customer impact, the detection path, the
contributing causes, and the corrective actions with named owners and due dates.
Corrective actions without an owner are rejected by the review board.

## 3. Blameless Rule

Reviews are blameless: they describe what the system allowed to happen, not who to
blame. Naming an individual as the cause is grounds for rejecting the review.

## 4. Follow-up Tracking

Corrective actions are tracked in the reliability backlog. Any action still open
after **60 days** is escalated to the engineering director in the weekly
reliability review.

## 5. Related Records

The incident timeline itself lives in the incident record, not the review. Reviews
link to the incident identifier and to the change that introduced the fault.
