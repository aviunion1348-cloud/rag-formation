# Round 1: retrieval and data (and the round 0 baseline)

Status: done. Every number here comes from the **labelled offline baseline**: hashing embeddings (d512) plus extractive answers, backend `baseline`. Runs print `NOT QUALIFYING`, and the local judge is `BLOCKED` because no Ollama runtime exists on this machine (see [audit_environment.md](audit_environment.md)). These numbers tell us nothing about the qualifying Ollama path.

## Protocol

| Item | Value |
|---|---|
| Decision set | calibration (10) + dev (32) = 42 cases from `evals/lear/golden_lear_v1.json` (sha256 `56050cf2689383ca…`) |
| Test split | Scored at round 0 and again only at the end of round 3. Only the failing ids were looked at, never their content. |
| Holdout | Scored once, at the end (`--allow-holdout`) |
| Round 0 run | `python3 -m ragformation lear --backend baseline eval run --suite calibration,dev --label round-0 --out data/lear/eval_runs/round-0` at commit `6c438d1` |
| Round 1 run | Same command with `--label round-1 --out data/lear/eval_runs/round-1`. It was run on the uncommitted round-1 tree, so `run.json` records `6c438d1` with `dirty: true`; the tree was committed as `e70bec9` 33 seconds later. Re-running on the committed `e70bec9` tree reproduces it exactly: 0 verdict, answer or citation differences ([evidence/round1_reproduced_at_e70bec9.txt](evidence/round1_reproduced_at_e70bec9.txt)). |
| Results | `evals/lear/results/round-0/`, `round-0-test/` and `round-1/`, each with `run.json` and `summary.md` |

**Scorer.** "Correct" means three things: the status is acceptable, no forbidden string appears, and every `must_include` group is hit.
- The **lenient** scorer is v1, the recorded headline. It tests for a lowercase substring anywhere in the answer.
- The **strict** scorer was added in round 3 after a manual audit. It ignores code references, citation markers and list ordinals, and it matches short or numeric facts as whole words.

Both are shown. Strict values come from re-scoring the saved answers with `lear eval rescore` ([evidence](evidence/rescore_strict_keyfacts_2026-11-05.json)).

## Round 0 diagnosis (decision set only)

- **Navigation pages crowded out content.** `README.md` (its page list) was retrieved in 19 of 42 cases, and `source_map.md`, a path-to-page table, in 15 of 42. Neither one holds an answer.
- **Claim labels leaked into the evidence.** The loader strips a leading list marker. As a result `- **[implemented]** text` reached the index as `[implemented]** text`, and those stray asterisks ended up inside quoted sentences.
- **Most wrong answers came from the right page but the wrong sentence.**
  - The extractive answerer joins list lines.
  - The core sentence splitter only splits before an uppercase letter.
  - So neighbouring list items merge, and the answerer quotes the wrong one.
  - This is a property of the baseline answerer. It does not affect an LLM.

## Experiments (decision set; a fresh index per run)

Each run rebuilt an index in a scratch directory (`--data-dir`) with the change applied, then ran `eval run --suite calibration,dev --judge off`. The scratch runs are not committed. The official runs above are.

| Run | Change | Correct (lenient) | Correct (strict) | Facts | hit@k | MRR | Doc recall | Ctx precision | Cite precision | False abstention |
|---|---|---|---|---|---|---|---|---|---|---|
| e0 | Round 0 configuration (reproduces round 0) | .452 | .405 | .514 | .946 | .587 | .820 | .271 | .472 | .027 |
| e1 | README page list kept out of the index | .476 | .405 | .541 | .946 | .591 | .833 | .276 | .477 | .027 |
| e2 | e1 + `source_map.md` excluded as a page | .476 | .405 | .541 | .946 | .605 | .820 | .281 | .486 | .027 |
| e3 | e2 + overlap 0 | .357 | .309 | .440 | .973 | .614 | .869 | .277 | .481 | .054 |
| e4 | e2 + overlap 0, target/max 260/400 | .357 | .309 | .450 | .973 | .636 | .860 | .291 | .395 | .054 |
| e5 | e2 + overlap 0, target/max 120/220 | .286 | .262 | .413 | .946 | .649 | .883 | .293 | .386 | .054 |
| e6 | e2 + overlap 20 | .500 | .429 | .559 | .973 | .614 | .860 | .287 | .505 | .054 |
| e7 | e2 + claim-label normalisation | .476 | .405 | .541 | .946 | .618 | .833 | .285 | .486 | .027 |
| **e8** | **e7 + overlap 20 (adopted)** | **.500** | **.429** | .559 | .973 | .621 | .860 | .289 | .505 | .054 |

What we rejected and why:
- **Overlap 0, with or without bigger or smaller windows (e3–e5).** Doc recall and MRR went up, but correctness dropped 4–7 cases: answers lost the sentence that straddles a chunk boundary.
- **Overlap 40 → 20 (e6, e8).** This is the only change with a strict gain. It is one case (lear-028), which is weak evidence.
- **Navigation exclusion and label normalisation (e1, e2, e7).** These have no strict effect on correctness. They were kept because MRR, context precision and citation precision improved, and because quoting navigation tables or `]**` fragments is a defect in its own right.

## What changed (commit `e70bec9`)

- **Index markers** (`ragformation/lear/indexer.py`, `prepare_for_index`):
  - `<!-- lear-index: skip-page -->` keeps a whole page out of the index; the generated `source_map.md` carries it.
  - `<!-- lear-index: skip -->` … `<!-- lear-index: end -->` drops a region, such as the README page list.
  - `docs verify` uses the same function, so the quarantine checks see exactly what gets indexed.
- **Claim labels.** `**[label]**` becomes `[label]` at index time. The source docs keep the bold form, because `docs verify` requires it.
- **Chunking.** `RAG_LEAR_CHUNK_TARGET_TOKENS` 180, `RAG_LEAR_CHUNK_MAX_TOKENS` 300, `RAG_LEAR_CHUNK_OVERLAP_TOKENS` 40 → **20**.
- **Defect fixed: stale chunks after a chunking change.**
  - Changing the overlap and running an incremental `index rebuild` re-chunked only 12 of 19 documents. The engine keeps one global chunking fingerprint, and the first re-ingested file updates it, so the other 7 files kept their old chunks.
  - The indexer now compares the stored fingerprint with the configured one, re-chunks every document when they differ, and reports `rechunked`. `index rebuild --rechunk` forces this.
  - Red/green evidence: [evidence/rechunk_after_chunking_change_red_green.txt](evidence/rechunk_after_chunking_change_red_green.txt). The test is `test_chunking_change_rechunks_every_document`.
  - The repaired index (355 chunks) is identical to a fresh build.
  - Gap reported, not changed: the legacy `ragformation ingest` path in the core engine has the same behaviour.

## Results

| Metric | Round 0 calibration+dev | Round 0 test | Round 1 calibration+dev |
|---|---|---|---|
| Overall correct, lenient (recorded) | .452 (19/42) | .417 (10/24) | .500 (21/42) |
| Overall correct, strict | .405 (17/42) | .417 | .429 (18/42) |
| Answerable correct | .432 | .429 | .486 |
| Acceptable status | .973 | 1.000 | .946 |
| Key-fact coverage (lenient / strict) | .514 / .441 | .559 / .536 | .559 / .460 |
| False abstention | .027 | 0 | .054 |
| Unanswerable abstained | .500 | 0 | .500 |
| hit@k / MRR | .946 / .587 | 1.000 / .733 | .973 / .621 |
| Doc recall | .820 | .952 | .860 |
| Context precision | .271 | .248 | .289 |
| Citation precision | .472 | .389 | .505 |
| Statuses | 39 GROUNDED, 3 ABSTAINED | 24 GROUNDED | 38 GROUNDED, 4 ABSTAINED |
| Index | 378 chunks | 378 | 355 chunks (source_map excluded) |

Total latency p50/p85/p99 at round 0 was 27.7/33.0/36.0 ms on calibration+dev. This is pipeline only: there is no model decode.

## Per-case mechanisms

- **lear-028 became correct under both scorers.** The answer sentence used to sit on a chunk boundary; overlap 20 keeps it whole.
- **lear-005 became "correct" under the lenient scorer only.** The answer never states the defaults (5 actions per 60 s). The digits "5" and "60" matched inside the evidence reference `prash/cli.py:L585-L608`. This was found in the round 3 audit. An earlier note credited navigation exclusion with this gain; that was wrong.
- **lear-064 changed from GROUNDED to ABSTAINED** and is wrong both times. It is fixed in round 3.

## Caveats

- All of this is the offline baseline. The gains from overlap and navigation exclusion should carry over to Ollama embeddings, because they change what gets indexed, but that has not been measured.
- The strict gain is one case out of 42.
- The test split was not re-scored in this round.
