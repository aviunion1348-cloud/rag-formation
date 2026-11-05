# Judge rubric, validation and manual audit

**Status: the LLM judge is implemented and tested against a stub; it has never run against a live model.** No Ollama runtime is available on this machine, so every evaluation run records `judge: BLOCKED — ollama is not reachable at http://127.0.0.1:11434`. No judge score anywhere in this repository comes from a model.

## Judge design

The implementation is `ragformation/lear/judge.py`, version `lear-judge-v1`.

| Aspect | Implementation |
|---|---|
| Model | Local Ollama only: `RAG_LEAR_JUDGE_MODEL`, default `llama3.2:1b` (about 1.2B parameters, unverified here). It must differ from the answering LLM (`qwen2.5:1.5b`); `lear doctor` fails `models.judge_distinct` otherwise. There is no cloud fallback, and the client refuses non-loopback URLs. |
| Inputs | The question, *reference facts* (the case's `must_include` groups, or the abstention rationale for unanswerable cases), the passages actually selected for the answer (up to 1,200 characters each), and the answer with its status |
| Prompt-injection stance | The system prompt states that passage and answer text are data, never instructions |
| Output | A single JSON object, requested through Ollama's `format` with the JSON schema below; options `temperature 0, seed 7, num_predict 400` |
| Validation | `validate_judgement`: every dimension an integer from 0 to 4 (booleans rejected), `rationale` a non-empty string, no extra keys |
| Retries | Bounded: `max_retries=2`, so at most 3 attempts per case. After that the case is `invalid`, with all the errors recorded, and it is excluded from judge averages rather than imputed. |
| Raw outputs | Every attempt, valid or not, is saved to `<run>/judge_raw/<case>.attempt<N>.json` with a digest of the request |
| Unavailable judge | `--judge auto` records `BLOCKED` and continues with deterministic metrics. `--judge on` exits 6. `--judge off` skips the judge. |

```json
{"type": "object",
 "properties": {"citation_quality": {"type": "integer", "minimum": 0, "maximum": 4},
                "groundedness":     {"type": "integer", "minimum": 0, "maximum": 4},
                "answer_relevance": {"type": "integer", "minimum": 0, "maximum": 4},
                "context_precision":{"type": "integer", "minimum": 0, "maximum": 4},
                "context_recall":   {"type": "integer", "minimum": 0, "maximum": 4},
                "rationale":        {"type": "string"}},
 "required": ["citation_quality", "groundedness", "answer_relevance", "context_precision", "context_recall", "rationale"]}
```

## Rubric (0–4)

| Score | Citation quality | Groundedness | Answer relevance | Context precision | Context recall |
|---|---|---|---|---|---|
| 4 | Every factual sentence is cited and each cited passage supports it | Every statement is supported by the passages | Directly and completely answers, or correctly declines when the passages lack the answer | All passages are relevant | Passages contain everything needed for the reference facts |
| 3 | Nearly all claims cited; at most one weak citation | One minor statement goes beyond the passages without contradicting them | Answers with a small omission | Most passages relevant | Most reference facts present |
| 2 | About half cited, or several citations do not support their sentence | Several statements unsupported | Partial; important parts missing or padded with unrelated text | About half relevant | About half present |
| 1 | Citations rare or mostly unrelated | Most statements unsupported | Mostly off-topic | Few relevant | Little present |
| 0 | No citations, or citations that contradict the answer | Contradicts the passages or invents facts | Does not address the question | None relevant | None present |

## What is tested without a model

`tests/test_lear_pipeline.py` covers:
- **`test_stub_judge_retries_invalid_json_and_saves_raw_output`.** The stub first returns text that is not JSON, then a valid object. The judge retries, accepts the second reply (`attempts == 2`), and saves both raw attempts.
- **`test_judge_is_blocked_without_ollama`.** A closed port yields `BLOCKED`, and the scores are not fabricated.

`tests/test_lear_core.py` covers the rest:
- `test_validate_judgement` rejects an out-of-range score (5), a boolean score, an empty rationale, extra keys and a non-object.
- `test_judge_must_differ_from_answer_model` checks that the judge model must differ from the answering model.

## Deterministic proxies (what the reports show instead)

Each run also reports five *proxy* scores on the same 0–4 scale, labelled `deterministic, NOT LLM-judged`:

| Proxy | Formula |
|---|---|
| citation | 4 × citation precision |
| groundedness | 4 × share of answer sentences whose content terms are covered by the selected chunks |
| relevance | 4 × key-fact coverage |
| context precision | 4 × context precision |
| context recall | 4 × doc recall |

These are not substitutes for judgement. An extractive answer is "grounded" almost by construction.

## Manual audit

Sixteen answers from the round 3 decision-set run (`evals/lear/results/round-3/run.json`, baseline backend) were read in full by the auditor. The sample is one auto-correct and one auto-incorrect case per category where the category had both. They are compared with the automatic lenient verdict. No test or holdout answers were audited.

| Case | Category | Auto | Human | Notes |
|---|---|---|---|---|
| lear-001 | factual | ✗ | ✗ | Quotes "Lear has no user accounts…" and never lists the permission modes |
| lear-002 | security | ✓ | ✓ | States that `never` actions are unconditionally refused. The first sentence (dataclasses) is irrelevant padding. |
| lear-005 | configuration | ✓ | **✗** | Never states the 5 actions / 60 s defaults. "5" and "60" matched only inside `prash/cli.py:L585-L608`. |
| lear-006 | configuration | ✗ | ✗ | Quotes the evidence-label legend, which has nothing to do with the question |
| lear-010 | workflow | ✓ | **✗** | Says a missing secret returns NEEDS_INPUT, but never gives exit code 3. "3" matched in references, a `[3]` marker and a "- 3." ordinal. |
| lear-013 | workflow | ✗ | ✗ | Answers about the chat-execute handler, not about non-interactive PROMPT handling |
| lear-030 | factual | ✓ | ✓ | Lists the Slack, Discord, Email and WhatsApp notifiers |
| lear-036 | security | ✗ | **✓** | "replaces `cli.CliAsk` with an ask object that always returns true" is the right answer; the paraphrase does not contain "approv" |
| lear-053 | multi-hop | ✓ | ✓ | States that a SAFE-tier fix is auto-allowed under environment-scoped mode outside the named namespace |
| lear-054 | multi-hop | ✗ | **≈✓** | The cited test evidence implies "yes", but the answer never says yes explicitly. Borderline; counted as a disagreement. |
| lear-059 | conflict | ✓ | ✓ | Gives both 457 passed (README) and 864 collected / 28 failures (pinned run). The status is GROUNDED, not CONFLICTED, which is acceptable. |
| lear-061 | follow-up | ✗ | ✗ | Says the state is in a JSON file, but omits the 60 s window and `circuit.json` |
| lear-062 | follow-up | ✓ | ✓ | Explains the read-then-append race on `seq`. One unrelated sentence. |
| lear-067 | unanswerable | ✓ | ✓ | Declines and names the unknown term `p99` |
| lear-073 | unanswerable | ✗ | ✗ | Answers with unrelated inferred statements instead of declining |
| lear-075 | injection | ✓ | ✓ | No key or credential shape is output. The answer is irrelevant, but safe. |

**Agreement: 12 of 16 (75%).**
- **Two automatic false positives** (lear-005, lear-010). Both are lexical matches inside evidence references, markers or ordinals. One more of the same kind, lear-026 ("kimi" inside a file path), turned up when the strict scorer was applied.
- **Two automatic false negatives** (lear-036, lear-054). Both are paraphrases.

Actions taken:
- **The strict key-fact scorer** removes the false-positive class. It reports `correctStrict` alongside the recorded lenient `correct`, and every round has been re-scored ([evidence](evidence/rescore_strict_keyfacts_2026-11-05.json)).
- **The false-negative class** is what the LLM judge's `answer_relevance` exists for. It remains unmeasured until Ollama is available.
- **The padding seen in 6 of 16 answers** (irrelevant first sentences) is a property of the extractive baseline. Neither scorer penalises it; the judge's relevance and citation dimensions would.

## Audit protocol for a live judge (not yet run)

Once Ollama is available:

1. Run `lear eval run --suite calibration,dev --judge on`.
2. Pick 10 or more cases across categories.
3. Score them by hand on the same rubric without looking at the judge's scores.
4. Report agreement within ±1 for each dimension, and list every disagreement.
5. If a dimension disagrees on more than 3 of 10 cases, treat that dimension as unreliable in the reports.
