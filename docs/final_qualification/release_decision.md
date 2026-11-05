# Release decision

Decision taken 2026-11-05 on branch `arena/5f16d127-rag-formation`, from the final
gate run recorded in
[`evidence/local_gates-20261105T155552Z.log`](evidence/local_gates-20261105T155552Z.log)
(16 gates, 0 failed).

## 1. The decision

**GO — for the envelope stated in §3. NO-GO — for anything outside it.**

Concretely:

| Claim | Verdict |
| --- | --- |
| Ship as a **single-tenant, loopback, local-first** RAG service with deterministic providers (`hashing` embeddings + `extractive` generation) | **GO** |
| Ship as a service that ingests, cites, abstains, surfaces conflicts, survives restart/crash, and can be rolled back | **GO** — each is exercised in `evidence/` |
| Ship into a third-party host (Python, Node, Flask, unrelated repo) | **GO** — 5/5 host fixtures |
| Support interactive use up to **5,000 chunks** on 2 vCPU | **GO** — measured p50 146.7 ms (worst of two samples 173.7 ms) |
| Support interactive use at 10,000 chunks | **NO-GO** — measured p50 412–444 ms against a 250 ms gate; no ANN index |
| Claim neural/semantic answer quality | **NO-GO** — never executed here; neural paths unverified |
| Claim hosted-LLM behaviour (streaming TTFT, prompt-following, refusal) | **NO-GO** — blocked; see [`live_llm_validation.md`](live_llm_validation.md) |
| Expose on a public network (TLS, proxy, abuse controls) | **NO-GO** — out of scope, untested |
| Claim multi-tenant isolation as an audited boundary | **NO-GO** — role checks only |
| Claim GPU rendering of the experience | **NO-GO** — no GPU in this environment |

This is a *qualified* release: the code is released together with the list of
things it has not been shown to do, and the list is binding on how it may be
described.

## 2. Decision inputs

| Input | Value | Source |
| --- | --- | --- |
| Unit/integration tests | 160 OK (19.4 s) | gate log lines 76–78 |
| Legacy engine suite | OK | gate log |
| golden v1 | 14/14, recall 1.0, MRR 1.0, nDCG 0.9513 | gate log |
| golden v2 (canonical matcher) | 45/46; recall@6 1.0; precision 0.4099; MRR 0.9416; nDCG 0.8964 | `golden_v2_report.json` |
| golden v2 (legacy matcher) | 44/46 | gate log |
| holdout v3 | 12/12 (first run 11/12) | `holdout_v3_report.json` |
| holdout v4 | 7/8 (only `h14`) | `holdout_v4_report.json` |
| Security matrix | 22/22 | `security_matrix.json` |
| Recovery/rollback/SIGKILL | 14/14 | `evidence/recovery_report.json` |
| Host fixtures | 5/5 | `evidence/hosts-20261105T155115Z.log` |
| Experience matrix | 33/33 (4 renderer modes) | `evidence/experience-20261105T154937Z.log` |
| Scale | 4,375 chunks → p50 146.7 ms; 8,750 → p50 414.8 ms | `evidence/scale/` |
| Semantic provider promotion | no-go, 6-clause rule | [`semantic_provider_benchmark.md`](semantic_provider_benchmark.md) |
| Live LLM | BLOCKED (exit 2) | `live_provider.json` |

Release bar applied before looking at the numbers: every listed gate must pass,
no test may be skipped to make a gate pass, and any surviving failure must be
characterised with measured evidence rather than described as an edge case. All
three conditions hold.

## 3. Supported envelope (write this on the box)

* One process, one SQLite data directory, loopback or a trusted reverse proxy.
* `hashing` embeddings (`semantic=False` in the health output) and `extractive`
  generation, unless the operator verifies their own provider.
* Corpus up to ~5,000 chunks (≈ 625 documents at this corpus's 7-chunks-per-doc
  average); beyond that, measure first.
* Single tenant, or tenants separated by `principal.tenant_id` **without** an
  independent isolation audit.
* Backup via file copy while the engine is open; recovery verified.
* English-language, heading-structured documents.

Outside the envelope: neural embeddings, hosted LLMs, GPUs, public exposure,
horizontal scale, concurrency soak, non-English corpora, and scanned PDFs.

## 4. Why the abstention threshold is 0.24 (and stays 0.24)

The threshold was frozen before the holdouts were written. The sweep below
(`scripts/calibration_sweep.py`, output
[`evidence/calibration_sweep.json`](evidence/calibration_sweep.json)) is a
post-hoc audit of that choice, not a search for a better number:

| Subset used to choose | Best threshold by balanced accuracy | Balanced accuracy | Separation (min answerable − max unanswerable) |
| --- | --- | --- | --- |
| calibration only (12 cases) | **0.24** | 1.0000 | +0.1092 |
| calibration + dev (22 cases) | **0.24** | 1.0000 | +0.1092 |
| test only (24 cases) | 0.34 | 0.9286 | −0.0892 |
| all 46 | 0.36 | 0.9459 | −0.0892 |

At the frozen 0.24 the split results are: calibration 1.0000 balanced accuracy,
calibration+dev 1.0000, test 0.8333, all 0.9444.

The rule adopted: **choose on calibration+dev, report test, never choose on
test.** Under that rule the answer is 0.24 — the same value that was frozen — and
the test-only optimum (0.34) is *not* adopted, because fitting the threshold to the
split that measures generalisation is exactly the failure mode this package exists
to avoid. The honest consequence is a known false answer, not a tuned one:

| Case | Answerable? | Support | Would abstain at | Cost of that threshold |
| --- | --- | --- | --- | --- |
| `t23-off-scope` | no | 0.3329 | > 0.3329 | loses `t01` (0.2437), `t02` (0.2628), `t03` (0.3069) |
| `c06-open-actions` | yes | 0.3398 | — | lost only at the test-tuned 0.34 |
| `t24-off-scope-competitor` | no | 0.2398 | already abstains at 0.24 | — |
| next-highest unanswerable | no | 0.2306 | already abstains | — |

So the trade is explicit: at 0.24 the build answers one off-scope question
wrongly and keeps 21/21 answerable test cases; at 0.34 it abstains that question
and loses four answerable ones. The first trade is the one a grounded-answer
service should take, and it is disclosed rather than hidden. `t23` is listed as a
known defect in [`acceptance_matrix.md`](acceptance_matrix.md) (D1).

## 5. Residual risk register (what a reader must know before deploying)

| Risk | Likelihood | Impact | Mitigation in this build |
| --- | --- | --- | --- |
| Off-scope question answered plausibly (D1) | low–medium | medium — a wrong answer with real citations | Threshold audit in §4; citations always point at real passages so a human can check |
| Synonym paraphrase misses (D4, `h14`) | medium | low — an incomplete answer, not a fabricated one | Documented; would need a neural/synonym layer |
| Neural stack never benchmarked | certain | unknown | Providers labelled unverified; promoted only under the 6-clause rule |
| Scale beyond 5,000 chunks degrades | high past the boundary | medium — latency, not correctness | Gate says "measure first"; ANN addition scoped but not done |
| Concurrency/soak unmeasured | unknown | unknown | Conservative defaults (`max_concurrent_queries=8`), rate limit 600/min |
| Multi-tenant leak | low | high if wrong | Server-side `allowedSources` intersection, tenant-scoped queries, 2 matrix rows; **not** an audited boundary |
| Operator exposes it publicly without TLS/proxy | possible | high | README/`technical_spec.md` §9 state the envelope; the server is HTTP-only |

## 6. What would change the decision

* A reachable model host (Ollama endpoint or `OPENAI_API_KEY`) → run
  `scripts/live_provider_check.py`, then the six graded checks in
  [`live_llm_validation.md`](live_llm_validation.md); a pass would let the claim
  move from "unverified" to "verified for that provider".
* Reachable embedding weights → rerun `scripts/bench_semantic_providers.py`; the
  promotion rule (a)–(f) in `semantic_provider_benchmark.md` is already written and
  would decide it without new judgement calls.
* A 10k-chunk requirement → add an ANN index, then re-run
  `scripts/bench_scale.py` against the same 250 ms p50 gate.
* A public-deployment requirement → TLS/proxy/container work plus a load and
  abuse-resistance test; neither exists today.
* A retraction of D1 or D4 → only via a genuine improvement measured on a *new*
  frozen holdout, not by editing `t23`/`h14` or re-tuning on v3/v4.

## 7. Sign-off statement

Every number in this package is traceable to a command in
`docs/final_qualification/evidence/` or to a named section of a document in this
directory. Where the environment could not execute a property, the property is
labelled unverified in the document that would otherwise have claimed it
(`live_llm_validation.md`, `semantic_provider_benchmark.md`,
`experience_validation.md`, `technical_spec.md` §9). Two holdout cases were found
failing and are still failing, and the run history that produced the conflict-gate
fix is disclosed in `evaluation_v2_failure_analysis.md` §6 rather than smoothed
over.

What nobody should read into this decision: that the system is production-ready in
the operational sense, that its answers are better than the 66 curated cases
suggest, or that the neural paths work. Those are precisely the claims this
document refuses to make.
