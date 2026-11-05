# Helios Grid — Access Control Policy

> **Synthetic corpus.** Invented for this demonstration; not real policy.

## 1. Authentication

All workforce accounts require multi-factor authentication. Password-only access
is not permitted for any system that holds customer content. Service accounts use
short-lived credentials issued by the internal identity broker.

## 2. Access Reviews

Privileged access is reviewed **quarterly** by the system owner. Non-privileged
access is reviewed annually. Evidence of each review is retained for **3 years**.

## 3. Offboarding

Access for a departing employee must be revoked within **24 hours** of their last
working day. Contractor access expires automatically at the end of the contract
term unless explicitly renewed.

## 4. Least Privilege

Production access follows least privilege: engineers receive time-bound,
just-in-time elevation for specific tasks, and standing production access is
limited to the on-call rotation and two platform administrators.

## 5. Break-glass

Break-glass credentials are sealed, audited on use, and rotated after every use.
Use of a break-glass account pages the security on-call immediately.
