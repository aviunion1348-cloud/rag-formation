# rag-formation — final qualification report

Date 2026-11-05 · base commit `986ba55` (merge of PR #3) · branch
`arena/5f16d127-rag-formation` · environment: Debian 12, 2 vCPU, 3.9 GiB,
Python 3.11.2, Node 22.22.3, **no GPU, no Ollama, no provider key**.

Everything below is a result of an executed command, and every number names where
its evidence lives. The full document set is in this directory; §7 tells you which
file to open for which question.

## 1. The answer in one paragraph

The service works, and its honesty about what it cannot do is now part of the
deliverable. All 16 release gates pass in a single command. Golden v2 went from
**39/46 to 45/46** with root-cause fixes rather than loosened expectations; the one
remaining failure is measured and documented as not separable from answerable
questions. Two frozen holdouts were written after the rules were frozen: v3 scored
**11/12** on first run, v4 **7/8** on first run, and both results stay on the record
— nothing was re-tuned to make a holdout look better, and the one holdout case that
still fails (`h14`, "keeping" vs "retention") is a real synonym gap. The system is
qualified for **single-tenant, loopback, deterministic-provider use up to ~5,000
chunks**. It is *not* qualified for neural embeddings, hosted LLMs, GPU rendering,
public exposure, or multi-tenant isolation as an audited boundary — those are
labelled unverified, not glossed.

## 2. What was measured

| Area | Result | Evidence |
| --- | --- | --- |
| Unit + integration tests | **160 OK** (19.5 s), of which 17 are new regression tests for the defects found here | `evidence/local_gates-20261105T155552Z.log` |
| Legacy `engine/` suite | OK (29 tests) | same log |
| Node experience contracts | 40/40 checks | same log |
| golden v1 | 14/14, recall 1.0, MRR 1.0, nDCG 0.9513 | same log |
| golden v2 | **45/46** (canonical matcher; 44/46 under the legacy matcher), recall@6 1.0, precision 0.4099, MRR 0.9416, nDCG 0.8964 | `golden_v2_report.json` |
| holdout v3 | **12/12** (first run 11/12 — disclosed) | `holdout_v3_report.json` |
| holdout v4 | **7/8**, only `h14` | `holdout_v4_report.json` |
| Semantic provider bench | 5 configurations; best remains the shipped `hybrid hashing` at 45/46; LSA promotion **no-go** on 5 of 6 pre-registered clauses | `semantic_bench.json`, `semantic_provider_benchmark.md` |
| Security matrix | **22/22** adversarial rows | `security_matrix.json` |
| Recovery / rollback / SIGKILL | **14/14**, `integrity_check=ok` after a hard kill mid-ingest | `evidence/recovery_report.json` |
| Host independence | **5/5** hosts (python wheel, CLI, Flask, Node, unrelated repo in a clean venv) | `evidence/hosts-20261105T155755Z.log` |
| Experience / accessibility | **33/33** checks across WebGL2, WebGL1, canvas2d and no-renderer modes | `evidence/experience-20261105T155618Z.log` |
| Scale | 4,375 chunks → p50 146.7 ms; 8,750 → p50 414.8 ms; ingest 2,590 → 247 chunks/s | `evidence/scale/` |
| Abstention threshold | 0.24 chosen on calibration+dev (balanced accuracy 1.0000); test-only optimum 0.34 **not** adopted | `evidence/calibration_sweep.json` |
| Live LLM | **BLOCKED** (exit 2) — no endpoint, no key, no GPU | `live_provider.json`, `live_llm_validation.md` |

## 3. What was broken, and how it was found

Seven defects were diagnosed against the failing goldens and fixed at the root, not
at the expectation. Two more were found later, and one of those was found *by* a
holdout:

| # | Defect | Symptom it caused |
| --- | --- | --- |
| 1 | JSON ingested as loose values | structured facts (`99.95%`, `platform-team`) lost their subject and could not be quoted |
| 2 | Chunk overlap crossed heading boundaries | a citation could attribute one record's tail to another |
| 3 | No compound/stem matching | "rollback" never matched "rolled back"; "team" never matched `platform-team` |
| 4 | Extractive selection scored by surface overlap | the sentence restating the question beat the sentence answering it |
| 5 | Exact identifiers worth almost nothing | `svc-003` questions answered with `svc-297` at 10k chunks |
| 6 | Conflict gate ignored the question | an access-review contradiction surfaced as the answer to a backup-retention question |
| 7 | Fact matcher compared raw characters | "**35-day** retention" failed a label of "35 days" |
| 8 | A bare digit satisfied the temporal answer shape | `SEV-1` was accepted as a time expression (found by holdout v3, `h03`) |
| 9 | Conflict gate's anchor test used raw tokens | a genuine contradiction was dismissed over a plural (found by holdout v3, `h09`, when #6 was tightened) |

Defect 9 is the uncomfortable one and it is documented in full: tightening the
conflict gate regressed a holdout case, and the fix that restored it was a general
stem/plural rule. That means **holdout v3's 12/12 is a post-fix number**, not a
clean generalisation estimate. The run-by-run table is in
`evaluation_v2_failure_analysis.md` §6, and `acceptance_matrix.md` D2 records it as
a deviation rather than hiding it.

## 4. What still fails, on purpose

| Case | What happens | Why it stays |
| --- | --- | --- |
| `t23-off-scope` ("What is the meaning of life?") | answered with a sourced retention sentence | support 0.3329 sits **between** answerable cases `t03` (0.3069) and `c06` (0.3398); margin 0.017 is wider than `t03`'s 0.0023; coverage 0.50 equals three answerable cases. Any threshold that abstains it also abstains `t01`, `t02`, `t03` |
| `h14` ("…about keeping data?") | answers a different sentence; misses "retention limits" | "keeping" → "retention" is a synonym, not morphology. Reproduced with the distributional embedder too, so it is a genuine semantic gap, not a lexical bug. Fixing it means a neural or synonym layer, which is unverified here |

Both are listed in the acceptance matrix as defects with residual risk, not as
acceptable behaviour.

## 5. What is not verified (and may not be described otherwise)

| Property | Status | Why |
| --- | --- | --- |
| Neural embedding quality | **unverified** | spaCy/GloVe/fastText weight hosts are blocked (`huggingface.co` connection failure, GitHub release assets reset mid-transfer); Ollama and OpenAI are unreachable |
| Hosted / local LLM answer quality, streaming TTFT, prompt-following | **unverified** | no endpoint, no key, no model weights. Only a **stub** server exercises the Ollama adapter (6 tests) |
| GPU rendering of the experience | **unverified** | no `/dev/dri`, no `nvidia-smi`; renderer modes were validated by simulation |
| Multi-tenant isolation as an audited boundary | **unverified** | tenancy columns and server-side `allowedSources` exist and 2 matrix rows pass, but this is a single-process role check |
| Public deployment (TLS, proxy, container, abuse resistance) | **out of scope** | the server speaks HTTP; all fixtures are loopback |
| Concurrency and long-run stability | **unverified** | sequential measurements only; no soak or load test |
| Interactive use above ~5,000 chunks | **not supported** | 8,750 chunks measured at p50 414.8 ms against a 250 ms gate; no ANN index added |

## 6. Reproducing all of it

```bash
git checkout arena/5f16d127-rag-formation
bash scripts/run_local_gates.sh          # ~2.5 min, 16 gates, writes evidence/local_gates-<stamp>.log
bash scripts/run_local_gates.sh --quick  # ~40 s, skips 5k/10k scale + host fixtures
```

The runner uses a throw-away data directory and the deterministic providers
(`hashing` + `extractive`), so it needs no network and no model. Individual gates
and their exact commands are listed in `testing.md` §1.

## 7. Where to read what

| Question | Document |
| --- | --- |
| What state did this start from? | [`baseline.md`](baseline.md) |
| How is the system built, and what was actually exercised? | [`architecture.md`](architecture.md) |
| What is the caller-facing contract (routes, auth, errors, events, config)? | [`technical_spec.md`](technical_spec.md) |
| Why did the golden cases fail, and what changed? | [`evaluation_v2_failure_analysis.md`](evaluation_v2_failure_analysis.md) |
| What did the semantic options cost and why were they rejected? | [`semantic_provider_benchmark.md`](semantic_provider_benchmark.md) |
| Does it work outside this repo? | [`universal_integration_report.md`](universal_integration_report.md) |
| Is the 3-D experience honest and accessible? | [`experience_validation.md`](experience_validation.md) |
| How fast is it, and where does it stop? | [`performance_and_scale.md`](performance_and_scale.md) |
| Can I back it up and roll it back? | [`data_recovery_validation.md`](data_recovery_validation.md) |
| Why is the LLM path "unverified"? | [`live_llm_validation.md`](live_llm_validation.md) |
| What tests exist, and what is untested? | [`testing.md`](testing.md) |
| Which criteria pass, which deviate? | [`acceptance_matrix.md`](acceptance_matrix.md) |
| Ship or don't ship, and why? | [`release_decision.md`](release_decision.md) |
| Raw proof | [`evidence/`](evidence/) (`local_gates-*.log`, `hosts-*.log`, `experience-*.log`, `scale/`, `recovery_report.json`, `calibration_sweep.json`, `failure_dossier_*.md`) |

## 8. Bottom line

**GO** for a single-tenant, local, deterministic-provider deployment up to ~5,000
chunks, with recovery, rollback, citations and abstention that were each exercised
rather than assumed. **NO-GO** for any claim about neural retrieval, LLM answering,
GPU rendering, public exposure or audited multi-tenancy — those were not measured
here, and this package says so in every place a reader might otherwise assume them.
