# Helios Grid — Data Classification Standard

> **Synthetic corpus.** Invented for this demonstration; not real policy.

## 1. Classes

Data is classified as **public**, **internal**, **confidential** or
**restricted**. Customer content is confidential by default. Credentials,
payment data and government identifiers are restricted.

## 2. Handling Requirements

Restricted data must be encrypted at rest with a customer-managed key and must
never appear in logs, traces or analytics events. Confidential data may be logged
only after pseudonymisation. Internal data may be stored in the analytics
warehouse without additional controls.

## 3. Sharing

Sharing restricted data outside the company requires a signed data processing
agreement and security review. Sharing confidential data requires a documented
business justification retained for **two years**.

## 4. Retention Override

A data class can shorten retention but never lengthen it beyond the retention
policy. If a classification conflicts with the retention schedule, the shorter
period wins and the conflict is reported to the data owner.
