# Round 3: sessions, reliability and performance (plus final test and holdout scoring)

Status: done. Everything is measured on the labelled offline baseline (`NOT QUALIFYING`), and the judge is `BLOCKED` because there is no Ollama.

| Run | Command (all with `--backend baseline`) | Commit | Results |
|---|---|---|---|
| Round 3 | `eval run --suite calibration,dev --label round-3` | `fed7dbf` | `evals/lear/results/round-3/` |
| Final test | `eval run --suite test --label round-3-test` | `fed7dbf` | `evals/lear/results/round-3-test/` |
| Holdout, once | `eval run --suite holdout --allow-holdout --label final-holdout` | `fed7dbf` | `evals/lear/results/final-holdout/` |

## Diagnosis (decision set and reliability probes)

**Follow-ups.** The decision set has three follow-up cases. lear-061 (facts 0.5) and lear-064 (false abstention) failed.
- The retrieval expansion appended identifiers from the previous answer. Those included its *evidence references* (`prash/server.py:L397`, `prash/permissions.py:L37-L64`), which name files rather than the thing "it" refers to.
- The extractive answerer saw only the user's words ("What happens if that variable is not set?"). It had no way to resolve "that variable".

**LLM failure.** With the stub Ollama answering `/api/chat` with HTTP 500, the core engine quietly substituted its extractive generator.
- The Lear agent reported `GROUNDED` with exit code 0.
- The answer was labelled in JSON (`fallbackUsed: true`, `qualifying: false`), but the status and exit code said success.
- The task forbids exactly this kind of substitution. This is defect 3.

## Changes (commit `fed7dbf`)

1. **No silent extractive answer on the Ollama backend.**
   - If the engine fell back from the LLM, the Lear agent now returns `ERROR` with `error.code=generation_unavailable` and exit code 6. The answer and citations are empty, and the warning `extractive_fallback_discarded` is added.
   - `RAG_LEAR_ALLOW_EXTRACTIVE_FALLBACK=1` returns the labelled fallback instead, with `qualifying: false`.
   - In streaming mode, the CLI says that any text already streamed came from the discarded fallback.
   - The core engine is unchanged; the Lear layer decides.
   - Red/green evidence: [evidence/llm_failure_no_silent_fallback_red_green.txt](evidence/llm_failure_no_silent_fallback_red_green.txt). The test is `test_llm_failure_is_an_error_not_a_silent_extractive_answer`.
2. **Cleaner follow-up expansion.**
   - Before identifiers are extracted from the previous answer, evidence references and citation markers are removed.
   - Identifiers from an ABSTAINED or ERROR answer are not used.
   - The test is `test_follow_up_expansion_skips_evidence_refs_and_abstained_answers`. It fails on `38bea8c`, and that red output is in the same evidence file.
3. **Baseline only: the extractive answerer picks sentences with `question (follow-up to: previous question)`.**
   - Identifiers are left out here, because they pulled unrelated sentences into the answer.
   - An LLM still receives the user's own words plus the conversation history.

### Experiments (decision set)

| Run | Change | Correct (lenient / strict) | Facts | False abstention | MRR / recall | Cite precision |
|---|---|---|---|---|---|---|
| e10 | Round 2 | .524 / .452 | .559 | .054 | .621 / .860 | .505 |
| e11 | Expansion cleanup | .524 / .452 | .559 | .054 | .626 / .847 | .500 |
| e12 | e11 + extractive answerer uses the full expansion | .548 / .476 | .573 | .027 | .626 / .847 | .468 |
| **e13** | **e11 + extractive answerer uses the expansion without identifiers (adopted)** | **.548 / .476** | **.587** | **.027** | .626 / .847 | .491 |

- **e11 is neutral within noise**: one document of recall was lost and a little MRR gained. It was kept because the expansion no longer carries file references.
- **e13 beats e12** on facts and citation precision for the same correctness.
- **lear-064 is now correct.** The answer quotes "the path in `PRASH_ENV`, else `./.env` in the working directory, else `~/.prash/.env`". An unrelated sentence is still glued on, so it is correct with noise.

## Manual audit and strict scoring

Sixteen calibration+dev answers were read by hand, one correct and one incorrect per category where possible. The full table is in [judge_rubric.md](judge_rubric.md#manual-audit). The automatic verdict agreed with the human one in 12 of 16 cases.

- **Two false positives**, lear-005 and lear-010. A numeric fact matched inside an evidence reference (`L585-L608` contains "5" and "60") or a list ordinal ("- 3."). The same class explains lear-026: "kimi" matched inside `prash/brain/kimi_client.py`.
- **Two false negatives**, lear-036 and lear-054. The answer was right but paraphrased ("an ask object that always returns true"), which substring matching cannot see. Fixing those is the judge's job.

Response: a **strict key-fact scorer** (`lear-keyfacts-strict-v1`) in `ragformation/lear/evaluate.py`.
- It drops code references, citation markers and list ordinals, then matches short or numeric facts as whole words.
- The first version also dropped a sentence-final number ("exit code 2."). Checking the cases whose verdict changed caught this; one of them was a test-split case, looked at only to validate the scorer, and the system was not changed. The ordinal rule was fixed before any table here was produced.
- The lenient verdict stays the recorded headline for continuity, and both are reported.
- `lear eval rescore` re-scores saved runs without calling a model. It reproduces every recorded lenient number exactly ([evidence](evidence/rescore_strict_keyfacts_2026-11-05.json)).

## Results

| Metric | Round 2 cal+dev | **Round 3 cal+dev** | Round 0 test | **Round 3 test** | **Holdout (once)** |
|---|---|---|---|---|---|
| Overall correct, lenient | .524 | **.548** (23/42) | .417 (10/24) | **.500** (12/24) | **.643** (9/14) |
| Overall correct, strict | .452 | **.476** (20/42) | .417 | **.500** | **.643** |
| Answerable correct | .486 | .513 | .429 | .476 | .583 |
| Acceptable status | .946 | .973 | 1.000 | 1.000 | 1.000 |
| Key-fact coverage | .559 | .587 | .559 | .607 | .653 |
| False abstention | .054 | **.027** | 0 | 0 | 0 |
| Unanswerable abstained | .750 | .750 | 0 | .500 | 1.000 |
| Injection resisted | 1.000 | 1.000 | 1.000 | 1.000 | – (none in split) |
| hit@k / MRR | .973 / .621 | .973 / .626 | 1.000 / .733 | 1.000 / .812 | 1.000 / .736 |
| Doc recall | .860 | .847 | .952 | .929 | .917 |
| Context precision | .289 | .290 | .248 | .255 | .249 |
| Citation precision | .505 | .491 | .389 | .452 | .305 |
| Citation span ≤ 40 lines | 1.000 | 1.000 | – | 1.000 | 1.000 |

Correctness changes between rounds. The same verdict holds under both scorers unless the row says otherwise.

| From → to | Cases fixed | Cases broken |
|---|---|---|
| Round 0 → round 1 | lear-028 (lear-005 under the lenient scorer only) | none |
| Round 1 → round 2 | lear-068 | none |
| Round 2 → round 3 | lear-064 | none |
| Round 0 test → round 3 test | lear-065 (follow-up), lear-071 (unanswerable, now ABSTAINED) | none |

Under the strict scorer, the decision set went from 17/42 (round 0) to 20/42 (round 3), and the test split from 10/24 to 12/24. The holdout scored 9/14 on its only run.

Proxy rubric scores (0–4, deterministic, **not** judged by an LLM):

| Split | Citation | Groundedness | Relevance | Context precision | Context recall |
|---|---|---|---|---|---|
| Round 3 cal+dev | 1.91 | 3.90 | 2.40 | 1.16 | 3.39 |
| Test | 1.81 | 4.00 | 2.38 | 1.02 | 3.71 |
| Holdout | 1.22 | 4.00 | 2.81 | 1.00 | 3.67 |

Groundedness near 4 is expected from an extractive answerer, which only quotes.

## Sessions and reliability

Sessions, memory and compaction are covered by unit tests and are unchanged in this round: isolation per user, TTL expiry with a fake clock, deletion, persistence across agent restarts, versioned and idempotent compaction that keeps identifiers and negations, and memory that cannot be cited. See [sessions_memory_context.md](sessions_memory_context.md).

Under load:
- The rate limiter rejected 10 of 20 requests at 5 users when the per-session limit was 2 per minute. The rejected requests appear in the results as `RATE_LIMITED`, and there were 0 errors.
- With no limits hit, 1, 2, 5 and 10 users completed 3, 6, 15 and 30 requests with 0 errors.
- Concurrency is capped at 2, and the queueing shows in the tail: p99 is 810 ms at 10 users against 36 ms at 1 user.

All of this is pipeline-only timing. See [benchmark_report.md](benchmark_report.md).

## Performance

- Pipeline latency on the decision set was p50 27.1 ms, p85 31.2 ms and p99 54.7 ms; retrieval p50 was 10.0 ms.
- The scope guard builds its vocabulary once per index state. It is cached on chunk count plus `last_ingest_at`.
- No LLM time-to-first-token or decode rate was measured, because there is no Ollama.

## Caveats

- Every gain here is a baseline gain. The follow-up change for the extractive answerer has, by design, no effect on the LLM path.
- Test and holdout were scored with the final system only. Nothing was tuned on them.
- 42 decision cases is a small sample: one case is 2.4 points.
