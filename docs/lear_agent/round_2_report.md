# Round 2: grounding, citations and abstention

Status: done. All runs use the labelled offline baseline (hashing plus extractive, `NOT QUALIFYING`), and the judge is `BLOCKED` because there is no Ollama. Decisions were made on calibration+dev only. The official run is `eval run --suite calibration,dev --label round-2 --out data/lear/eval_runs/round-2` at commit `38bea8c` (clean tree), with results in `evals/lear/results/round-2/`.

## Diagnosis of round 1 (decision set)

| Problem | Cases / measurement |
|---|---|
| Unanswerable questions answered anyway | 2 of 4: lear-068 asks for Kafka topics, which Lear has none of; lear-073 asks which customers use Lear |
| Answerable questions abstained | 2 of 37: lear-077 (support .2376 against a .24 threshold) and lear-064 |
| Useless citation line ranges | 141 of 355 chunks spanned more than 40 lines (p90 422, max 573). A `modules.md` citation read "lines 11-577", i.e. the whole page |
| Document-level citation precision | .505: about half the cited passages come from pages outside the case's relevant set |

Why the line ranges were wrong:
- A chunk's span was computed from the first to the last document line whose text appears in the chunk.
- Some lines repeat across a generated page, for example `- Registered in the CLI dispatcher: yes.` in every module entry.
- Any chunk that contains such a line therefore "covered" the whole page.

## Changes (commit `38bea8c`)

1. **Scope guard for unknown named terms** (`ragformation/lear/agent.py`: `salient_terms`, `unknown_terms`, `LearAgent.vocabulary`).
   - What counts as a name-like token: a capitalised word that does not start a sentence, an ALL-CAPS word, or a word that mixes letters and digits. Possessive `'s` is removed, and backticked literals (the user's own commands or pod names) are ignored.
   - If such a token never occurs in the indexed documentation vocabulary (5,498 terms, including the parts of `-`/`_` identifiers, matched with prefix tolerance), the question is declined **before retrieval**. The result is `abstainReason=unknown_term`, the term is named in the answer, and the exit code is 3.
   - Raw out-of-vocabulary rates were rejected as a signal. Generic verbs such as "provide" or "moves" are absent from the docs, so the noise is too high.
   - On the 42 decision-set questions the guard fires on lear-067 (`p99`) and lear-068 (`Kafka`) and on **no answerable question**.
   - `RAG_LEAR_SCOPE_GUARD=0` disables it.
2. **Abstention threshold per backend** (`ragformation/lear/settings.py`, `ABSTAIN_DEFAULTS`).
   - The baseline default is **0.215**, calibrated on the **calibration split only**.
   - Ollama stays at the engine default of 0.24. This is marked *uncalibrated*, because the support score mixes lexical coverage with the embedding cosine, so the distribution changes with the embedder.
   - An explicit `RAG_LEAR_ABSTAIN_THRESHOLD` still overrides both, and `settings` reports `effective_abstain_threshold` and its source.
3. **Line spans** (`ragformation/lear/indexer.py`, `locate_lines`).
   - Covered lines are grouped into runs that no foreign substantive line interrupts.
   - The run with the most *distinctive* coverage wins; a line repeated n times on a page counts 1/n.
   - Result: p90 span 422 → 14 lines, max 573 → 31, with 354 of 355 chunks at line precision.
   - A known limitation: an identical repeated line next to the run can extend the span by one entry's worth of lines.
4. **Evaluation records.** `run.json` now stores `bestSupport` and `warnings` for each case, plus citation-span statistics (`spanLines`, `within40Lines`).

### Threshold calibration (calibration split, best selected support)

| Case | Kind | Support |
|---|---|---|
| lear-067 | unanswerable | .1927 |
| lear-077 | answerable | .2376 |
| lear-073 | unanswerable | .2619 |
| lear-061 | answerable | .3602 (lowest of the rest) |

No single threshold separates these three points. Thresholds in (.1927, .2376] and in (.2619, .3602] each leave one error. The lower interval was chosen, using its midpoint 0.215, because a false abstention there costs an answerable question, while lear-073 is left to the generator. lear-067 is caught by the scope guard either way.

## Experiments (decision set)

| Run | Change | Correct (lenient) | Correct (strict) | Unanswerable abstained | False abstention | Cite precision | Notes |
|---|---|---|---|---|---|---|---|
| e8 | Round 1 | .500 | .429 | .500 | .054 | .505 | – |
| e9 | Guard + threshold 0.215 | .524 | .452 | .750 | .054 | .505 | lear-068 is now ABSTAINED (unknown term `Kafka`) |
| e9b | e9 without the guard | .500 | .429 | .500 | .054 | .505 | The guard is the whole gain |
| e9c | Guard + threshold 0.24 | .524 | .452 | .750 | .054 | .505 | The threshold has no effect on this set |
| e10 | e9 + line spans | .524 | .452 | .750 | .054 | .505 | Citation spans: max 567 → 13 lines, p85 13 → 7, ≤40 lines .899 → 1.000 |

The threshold change shows no measurable effect with the extractive generator:
- lear-077 now passes the support gate, but the extractive generator still declines because no sentence overlaps the question enough.
- lear-064 (support .254) is declined by the generator too.

The threshold therefore matters only for the LLM path, and it **must be recalibrated with Ollama** (`lear eval run --suite calibration` with `--backend ollama`) before anyone relies on it.

## Results

| Metric | Round 1 | Round 2 |
|---|---|---|
| Overall correct, lenient / strict | .500 / .429 | **.524 / .452** |
| Answerable correct | .486 | .486 |
| Acceptable status | .946 | .946 |
| Key-fact coverage | .559 | .559 |
| False abstention | .054 | .054 |
| Unanswerable abstained | .500 | **.750** |
| Injection resisted | 1.000 | 1.000 |
| hit@k / MRR / doc recall | .973 / .621 / .860 | .973 / .621 / .860 |
| Context precision | .289 | .289 |
| Citation precision (documents) | .505 | .505 |
| Citations with line ranges | 1.000 | 1.000 |
| Citation span ≤ 40 lines | .899 | **1.000** |
| Citation span max (lines) | 567 | 13 |
| Statuses | 38 GROUNDED, 4 ABSTAINED | 37 GROUNDED, 5 ABSTAINED |

Pipeline latency was p50 25.1 ms, p85 28.7 ms and p99 51.5 ms.

## Not achieved

- **Document-level citation precision stayed at .505.** The extractive answerer quotes the best-overlapping sentences from every selected chunk, including chunks from other pages. Nothing generator-agnostic in the Lear layer moved it without core changes. An LLM that follows the prompt rule "cite every factual sentence" would be measured separately.
- **lear-073** ("which customer companies…") is still answered. None of its terms is unknown, and its support of .2619 is above every threshold the calibration split allows.

## Caveats

- The scope guard depends on spelling. A product that the docs name differently would be declined with a clear message rather than answered.
- The guard and the threshold are both measured on the baseline only.
