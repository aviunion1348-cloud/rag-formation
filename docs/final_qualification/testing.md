# Testing — inventory, gates, and the honest gaps

Every number in this document comes from
`docs/final_qualification/evidence/local_gates-20261105T155552Z.log` (the final
full gate run) or from the report JSONs named beside it.

## 1. How to run everything

```bash
bash scripts/run_local_gates.sh          # all gates, ~2.5 min on 2 vCPU, tees an evidence log
bash scripts/run_local_gates.sh --quick  # skips 5k/10k scale + host fixtures, ~40 s
```

The script sets `RAG_DATA_DIR` to a fresh temp directory,
`RAG_EMBED_PROVIDER=hashing`, `RAG_LLM_PROVIDER=extractive` so results are
reproducible and offline, runs each gate, prints `GATE <name>: PASS|FAIL`, and
writes `docs/final_qualification/evidence/local_gates-<UTC stamp>.log`. Final run:
**16 gates, 0 failed**.

Individual invocations (`ragformation` imports only from the repo root, which is
why the `-s tests` form is required):

```bash
python3 -m unittest discover -s tests -p 'test_*.py'   # 160 tests
(cd engine && python3 -m unittest discover -s tests)    # legacy engine suite
node tests/experience_contract.mjs                      # 40 renderer-independent checks
python3 -m ragformation eval                            # golden v2
python3 -m ragformation eval --dataset evals/holdout_v4.json
```

## 2. Unit and integration inventory (160 tests, OK in 19.4 s)

| File | Tests | What it pins |
| --- | --- | --- |
| `test_engine_e2e.py` | 26 | ingest → ask end to end, abstention, citations, traces, rebuild/rollback, conflict surfacing |
| `test_qualification_fixes.py` | 17 | **new in this run** — one or more tests per defect fixed here (§3) |
| `test_api.py` | 14 | routing, auth outcomes, error envelopes, SSE, static paths |
| `test_experience.py` | 16 | scene contract, transcript, reduced-motion/contrast, demo fixture honesty |
| `test_guardrails.py` | 16 | injection quarantine, credential redaction, citation validation |
| `test_integration_kit.py` | 13 | host integration helpers |
| `test_loaders.py` | 13 | md/txt/html/json/csv/docx/pdf extraction, rejection of binary and oversized input |
| `test_text_and_chunking.py` | 12 | tokenisation, stems, compounds, chunk boundaries, overlap rules |
| `test_evaluation_and_cli.py` | 10 | metric computation, CLI wiring, `--fact-matcher` |
| `test_store.py` | 9 | transactions, revision swap, integrity report, deletion |
| `test_retrieval.py` | 8 | RRF, rerank, MMR, support score, identifier boost |
| `test_ollama_provider.py` | 6 | Ollama adapter against a stub server (**stub only** — no real model) |

Plus: `tests/experience_contract.mjs` (40 checks, Node), the legacy `engine/`
suite, and four shell/Python harnesses that are gates rather than unit tests:
`security_matrix.py`, `recovery_check.py`, `bench_scale.py`,
`bench_semantic_providers.py`.

## 3. What the 17 new tests cover (one per fixed defect)

| Defect | Test(s) |
| --- | --- |
| Compound/identifier/stem matching (fix 3) | `test_compound_query_matches_split_source_phrase`, `test_hyphenated_identifier_exposes_its_parts`, `test_trailing_e_stemming_joins_verb_forms`, `test_unrelated_words_are_not_joined` |
| JSON rendered as attributable records (fix 1) | `test_structured_facts_keep_their_subject`, `test_each_record_gets_its_own_heading_for_citation` |
| Overlap must not cross a heading (fix 2) | `test_overlap_never_crosses_a_heading` |
| Scored extractive selection (fix 4) | `test_quantity_question_prefers_the_sentence_with_a_quantity`, `test_identifier_question_prefers_the_sentence_naming_it`, `test_restatement_of_the_question_is_not_an_answer`, `test_record_lines_are_quoted_whole_with_their_label` |
| Question-aware conflict gate (fix 6) | `test_unrelated_contradiction_is_not_surfaced`, `test_contradiction_about_the_question_is_still_surfaced` |
| Canonical fact matcher (fix 7) | `test_markdown_and_hyphenation_do_not_hide_a_fact`, `test_facts_still_have_to_be_contiguous_and_ordered`, `test_legacy_matcher_keeps_its_old_behaviour`, `test_canonical_tokens_fold_plurals_only` |

Two of these were written to *fail* against the old code on purpose
(`test_overlap_never_crosses_a_heading`, `test_unrelated_contradiction_is_not_surfaced`),
which is how the conflict-gate false-positive on a synthetic corpus was found
before it could hide behind a green suite.

## 4. Evaluation gates

| Gate | Command | Result (final run) |
| --- | --- | --- |
| golden v1 | `python3 -m ragformation eval --v1` | 14/14, recall 1.0, MRR 1.0, nDCG 0.9513, abstain accuracy 1.0 |
| golden v2 (canonical) | `python3 -m ragformation eval` | **45/46**, recall@6 1.0, precision 0.4099, MRR 0.9416, nDCG 0.8964; splits 12/12, 10/10, 23/24 |
| golden v2 (legacy matcher) | `--fact-matcher legacy` | 44/46 (difference is `t15`, disclosed) |
| calibration sweep | `scripts/calibration_sweep.py --margins` | threshold 0.24 chosen on calibration+dev; test-only optimum 0.34 **not** adopted (`evidence/calibration_sweep.json`) |
| holdout v3 | `--dataset evals/holdout_v3.json` | 12/12 (first run 11/12 — history in §5) |
| holdout v4 | `--dataset evals/holdout_v4.json` | 7/8 (`h14`, disclosed) |

The eval commands are *report-only* by default; `--gate all` makes a failure exit
non-zero. The gate runner deliberately leaves them report-only so a known,
documented failure cannot be confused with a broken harness — the numbers are
compared by a human against the acceptance matrix, not silently asserted.

## 5. Non-functional gates

| Gate | Script | Result |
| --- | --- | --- |
| Security matrix | `scripts/security_matrix.py` | **22/22** rows (`security_matrix.json`) |
| Recovery / rollback / crash | `scripts/recovery_check.py` | **14/14** (`evidence/recovery_report.json`) |
| Scale curve | `scripts/bench_scale.py --chunks 5000 10000` | p50 161 ms @ 4,375 chunks; 412 ms @ 8,750; answers 10/10 both |
| Semantic provider bench | `scripts/bench_semantic_providers.py` | 5 configs (`semantic_bench.json`), promotion **no-go** |
| Host fixtures | `scripts/run_host_fixtures.sh` | **5/5** hosts (`evidence/hosts-*.log`) |
| Experience matrix | `scripts/run_experience_matrix.sh` | **33/33** across 4 renderer modes |
| Live provider | `scripts/live_provider_check.py` | **BLOCKED** (exit 2, expected) — no ollama, no key, no GPU |

## 6. Gaps — tests that do not exist and properties nothing here measures

1. **No test ever calls a real model.** `test_ollama_provider.py` uses a stub
   server; the extractive generator is deterministic code. Neural generation
   quality is unverified.
2. **No neural embedding was executed.** The distributional provider is trained
   in-process on 66 chunks; spacy/ollama/openai weights are unreachable from this
   sandbox. Every embedding-quality claim in this package is about `hashing`.
3. **No concurrency or soak test.** `RAG_MAX_CONCURRENT_QUERIES=8` is a config
   value; the scale bench issues sequential queries. Throughput under parallelism,
   SQLite write contention, and memory growth over hours are unmeasured.
4. **No TLS, proxy, or container test.** All host fixtures are loopback HTTP.
5. **No systematic extraction corpus.** Loader tests use small fixtures; no
   per-format accuracy benchmark exists for real-world PDFs/DOCX.
6. **The corpus is 14 synthetic documents.** 46 + 12 + 8 cases on 66 chunks is a
   regression net, not a generalisation study; a 5,000-chunk corpus with real
   questions would be a different measurement.
7. **Coverage is not instrumented.** No line/branch coverage figure is reported
   because no coverage tool was installed here; claiming one would be an estimate.

## 7. Reproduction notes (traps that cost time in this run)

* `python3 -m unittest discover -t .` fails — use `-s tests -p 'test_*.py'`.
* `tests/test_ollama_provider.py` is not importable as `tests.test_ollama_provider`.
* On a 3-document toy corpus, terms appear in most chunks, so the conflict gate's
  document-frequency rule yields an empty "distinctive" set; gate tests must be
  written so the topical pair is separable, and anchors checked through
  `text.matched_terms` rather than raw intersection.
* The eval harness wants `{"title","text"}` for ingest, and `/api/v1/sources`
  replies `{"results":[…]}`; asserting on the wrong shape produces false security
  "passes".
* A jailbreak probe must be graded on `warnings` (`possible_meta_disclosure`), not
  on the presence of a substring in the answer.
* `recovery_check.py` must interrupt ingest at ~0.6 s; later and the child exits 0
  and the "killed mid-flight" assertion tests nothing.
