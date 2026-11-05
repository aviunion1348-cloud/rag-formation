# ADR 0006 — Server-side citation validation and thresholded abstention

* Status: accepted
* Date: 2026-11-05

## Context

Two failure modes matter more than answer polish: an answer that cites something
that does not support it, and an answer where the corpus contained nothing.
Model output cannot be trusted for either, so both must be enforced in code.

## Decision

**Citations.** The generator emits `[n]` markers referring to evidence blocks. The
engine then:

1. maps each marker back to a chunk id via the assembly record (never trusting the
   model's text about sources);
2. validates the marker is in range and the chunk is in the assembled context and
   passed the access filter;
3. extracts the claim (the sentence carrying the marker) and checks term overlap
   between claim and cited chunk — a marker attached to a sentence with no support
   is dropped;
4. strips invalid markers (`guardrails.strip_invalid_citations`) and re-checks
   coverage: an answer with zero valid citations is downgraded to
   `needs_review`, never returned as `answered`.

**Abstention.** Before generating, the engine compares the best `support_score`
against `RAG_ABSTAIN_THRESHOLD` (default 0.22) and also honours the refusal
sentinel in model output. Abstained answers have `status: "abstained"`, a reason,
no claims, and the citations that were considered (so an operator can see what was
*almost* enough). CLI exit code 3.

**Conflict surfacing.** When two different sources give different numbers for the
same unit/metric, the answer is `needs_review` with both values and both citations
(`test_conflicting_numbers_are_surfaced_not_merged`). The engine never averages or
picks silently.

## Consequences

Good: every returned claim has a resolvable citation checked server-side;
`citationPrecision` is 1.0 on the golden set; unanswerable questions abstain
(0.107–0.212 vs threshold 0.22); contradictions are visible.

Costs: the claim/chunk overlap check is heuristic and can drop a legitimate
citation phrased with synonyms. Conflict detection is limited to same-unit numeric
disagreement — prose contradictions pass through. The threshold is calibrated on a
small corpus and its margin is thin for one case (q11 at 0.212).

## Revisit when

a live model is used: measure how often it emits invalid markers — that rate is
the real justification for step 4. Re-calibrate on more unanswerable questions
before lowering the threshold.
