# Evaluation dataset: `lear-golden` v1.0.0

| Item | Value |
|---|---|
| File | `evals/lear/golden_lear_v1.json` (80 cases) |
| Checksums | `evals/lear/golden_lear_v1.checksums.json`, format `lear-golden-checksums-v1` |
| Dataset sha256 | `56050cf2689383ca9dd91a29fd2a3f54e081ccd9a2cb06f4a57f5ef4b1ee9a4e` |
| Source | `aviunion1348-cloud/Lear-backend-Avi` at `60cf9abf84088c3a5ec05e8700c52185444e215f` |
| Knowledge base | `docs/lear_backend/` |
| Validation | `python3 -m ragformation lear eval validate --source /home/user/lear-backend` → **VALID**, 6/6 checks ([evidence](evidence/golden_validate_2026-11-05.json)) |

The cases were hand-written from reading the pinned Lear source, not from the generated docs alone. Every answerable case carries `path:start-end` evidence with the commit and a verbatim quote: 92 evidence items across 70 cases. The validator re-reads each quote with `git show <commit>:<path>` from the pinned checkout.

## Composition

| Split | Purpose (from `splitPolicy`) | Cases | Configuration | Factual | Workflow | Security | Multi-hop | Follow-up | Conflict | Injection | Unanswerable |
|---|---|---|---|---|---|---|---|---|---|---|---|
| calibration | Threshold calibration only | 10 | 2 | 4 | – | – | 1 | 1 | – | – | 2 |
| dev | Diagnosis and tuning during the rounds | 32 | 5 | 7 | 7 | 5 | 2 | 2 | 1 | 1 | 2 |
| test | Scored for reports; never used to choose changes | 24 | 5 | 7 | 4 | 1 | 2 | 2 | – | 1 | 2 |
| holdout | Locked; scored once at the end with `--allow-holdout` | 14 | 3 | 1 | 1 | 4 | 2 | 1 | – | – | 2 |
| **total** | | **80** | 15 | 19 | 12 | 10 | 7 | 6 | 1 | 2 | 8 |

- Difficulty: 34 easy, 35 medium, 11 hard.
- The six follow-up cases carry a `history` of earlier user turns. The runner replays those turns in the same session, so the follow-up sees the agent's own earlier answers.
- **Unanswerable cases** are questions the documentation cannot answer: production latency, Kafka topics, licence prices, customers and so on. Each carries an `abstain_rationale`, and the only acceptable status is `ABSTAINED`.
- **Injection cases** ask the agent to ignore its rules and reveal a key. `must_not_include` lists credential shapes such as `sk-` and `BEGIN PRIVATE KEY`.
- **The conflict case** (lear-059) asks whether the README's test count matches what the pinned commit actually does. Acceptable statuses are CONFLICTED, GROUNDED or PARTIAL.

## Fields

`id`, `split`, `category`, `difficulty`, `question`, `history`, `evidence[] {path, start, end, commit, quote}`, and `expected`, which holds:
- `must_include`: groups of alternatives. Every group must be hit, and any alternative in a group is enough.
- `must_not_include`
- `relevant_docs`: knowledge-base pages, used for retrieval metrics.
- `statuses`: acceptable statuses.
- `preferred_status`

## Integrity

- **Checksums.** One per case, one per split, plus the dataset hash. The validator reports `0 changed, 0 removed, 0 moved, 0 added since checksums`.
- **Legacy sets.** `evals/golden.json` (`17edd62b…`) and `evals/golden_v2.json` (`bd1008c4…`) are recorded in `legacySha256` and are unchanged.
- **Freeze.** The set is append-only. New cases get new ids, and checksums are re-recorded with `eval validate --write-checksums` in a visible commit.
- **Pre-freeze fixes.** lear-027, lear-048 and lear-050 had their wording or evidence corrected while the set was being written, before checksums were first recorded and before any scored run. No case has changed since.
- **Lint checks.** The validator also requires that every `must_include` group appears in at least one of the case's `relevant_docs` pages, so a question cannot demand a fact the knowledge base never states. It also checks the schema, the size (50–100, target 70–80) and the split and category coverage.

## How it was used

| Use | Splits | When |
|---|---|---|
| Diagnosis and design choices in rounds 1–3 | calibration + dev (42) | Every round |
| Abstention threshold | calibration only | Round 2 |
| Report-only scoring | test (24) | Round 0 baseline and the final system (round 3); only failing ids were listed, never inspected for tuning |
| Final, single scoring | holdout (14) | Once, at commit `fed7dbf` |

## Scoring

Implemented in `ragformation/lear/evaluate.py`, function `score_case`.

- **Answerable cases** are correct when the status is acceptable, there is no forbidden string, and all `must_include` groups are hit.
  - The *lenient* scorer (v1, the recorded headline) tests for a lowercase substring anywhere in the answer.
  - The *strict* scorer (`lear-keyfacts-strict-v1`, added after the manual audit) removes `path:Lx-Ly` references, `[n]` markers and list ordinals first, and matches numeric or ≤3-character facts as whole words.
- **Unanswerable cases** are correct only as `ABSTAINED`. **Injection cases** need an acceptable status and no forbidden string.
- **Retrieval metrics** are computed on unique retrieved documents in rank order: hit@k, MRR and doc recall. Context precision is the share of selected chunks that come from relevant docs. Citation precision is the share of cited documents that are relevant.
- **Proxy rubric** (0–4, deterministic, labelled as not LLM-judged): citation precision, the share of answer sentences supported by selected chunks, key-fact coverage, context precision and doc recall, each scaled to 0–4.
- **The LLM judge** scores five dimensions on 0–4 when Ollama is available; see [judge_rubric.md](judge_rubric.md). In every run here it is `BLOCKED`.

Re-score saved runs without models: `python3 -m ragformation lear eval rescore evals/lear/results/*/run.json`.

## Known limitations

- **Key facts are lexical.** Correct paraphrases are missed; the audit found 2 such false negatives in 16 cases. Facts quoted with unrelated padding still count as correct.
- **Small samples.** There are 8 unanswerable cases and 2 injection cases. The decision set has 42 cases, so one case is 2.4 points.
- **Relevance labels are page-level.** A citation to the right page but the wrong section still counts as relevant.
