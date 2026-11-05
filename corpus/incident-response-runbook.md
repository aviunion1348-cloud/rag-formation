# Helios Grid — Incident Response Runbook

> **Synthetic corpus.** Helios Grid is a fictional company. Every policy, number
> and name in `corpus/` was invented for this RAG demonstration. Nothing here is
> real company policy.

## 1. Severity Classification

An incident is **SEV-1** when a customer-facing service is unavailable for more
than five minutes, or when data integrity is at risk for any tenant. All other
availability losses are **SEV-2** unless no workaround exists, in which case the
incident manager may raise severity to SEV-1.

Severity must be recorded within **five minutes** of the first automated alert.
The on-call engineer owns classification until an incident commander is paged.

## 2. Escalation Window

For a SEV-1 incident the incident commander must be paged within **10 minutes**
of classification. Escalation to the engineering director is required if the
incident is still unresolved after **45 minutes**.

A SEV-2 incident requires commander paging within **30 minutes**, and director
escalation after four hours of continuous customer impact.

## 3. Communication Cadence

During a SEV-1 the incident commander publishes a status update every
**20 minutes** in the `#ops-live` channel. Updates must state customer impact,
current hypothesis, and the next checkpoint. Speculation is permitted only when
it is explicitly labelled as a hypothesis.

| Severity | Paging target | First update | Escalation trigger |
|---|---|---|---|
| SEV-1 | incident commander | 20 minutes | unresolved after 45 minutes |
| SEV-2 | incident commander | 60 minutes | unresolved after 4 hours |
| SEV-3 | team lead | next business day | backlog review |

## 4. Post-Incident Review

A post-incident review is mandatory for every SEV-1 and is due within
**five business days**. The review must contain a timeline, contributing
factors, blast radius, and at least one owned corrective action with a due date.

Blameless does not mean evidence-free: reviews quote logs, traces and
timestamps, and they name systems rather than people.
