# ADR 0007 — Ship an operator console, defer the cinematic walkthrough

* Status: accepted
* Date: 2026-11-05

## Context

The handwritten sketch shows a "RAG experience" layer: a guided, cinematic
walkthrough of the pipeline (ingest → chunk → embed → retrieve → answer) intended
to make the mechanism legible, plus KPI-style metrics. The repository already
contains `engine/` — a self-contained demo of that experience contract
(`RagExperienceEvent`, sample documents, 29 passing tests) that predates this work.

The master prompt asks for a working pipeline with an honest acceptance gate. A
walkthrough UI is presentation, not mechanism; it cannot be built on top of an
unverified pipeline.

## Decision

1. **Ship the mechanism first**: CLI + HTTP API + a functional console
   (`ragformation/web/`) that lists sources, shows chunks, asks questions, renders
   the trace (each pipeline stage, timings, support score, citations) and the
   KPI row (mean/p50/p95, TTFT, ITL, retrieval vs generation).
2. **Keep `engine/` untouched and passing** (29 tests). It remains the contract for
   the cinematic experience; nothing in the new pipeline depends on it.
3. **Do not fake the walkthrough.** No staged animations over real data, no
   progress bars driven by timers.

## Consequences

Good: the deliverable is verifiable end-to-end today, and the trace view already
gives an operator the stage-by-stage visibility the sketch wanted.

Costs: the "experience" layer from the sketch is not delivered as a separate
product; `engine/` and `ragformation/` duplicate a little vocabulary (sources,
events) without sharing code.

## Revisit when

the user wants the walkthrough: drive it from `queries.trace` (already persisted
per query, including per-stage timings and candidate lists), so the animation
reflects what actually happened rather than invented steps.

---

## Amendment (2026-11-05): the walkthrough now ships

The deferred deliverable was built, and the constraint that mattered was kept:
it is driven by real state, not by staged animation over invented data.

- `ragformation/web/experience/` implements the 16-scene film with three modes
  (Director over a recorded run, Live over the event stream, Explorer over the
  live index). The recording is produced by `scripts/record_experience_demo.py`
  from a real ingest → query → delete → rebuild, and is labelled `demo` with the
  providers it actually used.
- The event contract in `docs/events.md` is what the film consumes; when the
  stream says a stage was skipped, the film shows it as skipped.
- What this amendment does **not** change: no fabricated capability. The film
  says hashing is lexical, says extractive is not a model, and says "server
  unreachable" instead of inventing a value. WebGL rendering is statically
  checked, not demonstrated — see `docs/experience.md` §"What is verified".

The original decision's second option ("do not fake the walkthrough") is the one
that was taken; the "defer" part is now historical.
