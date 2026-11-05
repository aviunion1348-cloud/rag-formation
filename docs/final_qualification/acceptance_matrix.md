# Acceptance matrix — criterion, evidence, verdict

Adjudication for the qualification run. Each row states a criterion, the artifact
that demonstrates it, and a verdict from one of three values: **MET**, **MET WITH
DISCLOSED DEVIATION**, or **NOT MET / UNVERIFIED**. Nothing is marked MET on the
strength of an intention; every MET names an executed command or a file produced
by one.

Evidence root: `docs/final_qualification/`. Final gate log:
`evidence/local_gates-20261105T155552Z.log` (16 gates, 0 failed).

## 1. Functional and quality criteria

| # | Criterion | Evidence | Verdict |
| --- | --- | --- | --- |
| A1 | Baseline captured before any change, from executed commands | `baseline.md`; commit `986ba55`; 143 tests OK; v2 39/46 | MET |
| A2 | Every golden-v2 failure diagnosed to a root cause, with the per-case dossier | `evaluation_v2_failure_analysis.md` §1–2 + `evidence/failure_dossier_baseline.md` (7 cases, 4-class taxonomy) | MET |
| A3 | Fixes are general rules, not case-specific carve-outs; no eval or test was weakened | `evaluation_v2_failure_analysis.md` §2 (7 fixes, all keyed to code paths) + `tests/test_qualification_fixes.py`; `evals/golden_v2.json` unmodified (`git status`) | MET |
| A4 | golden v2 improvement is real and attributable | 39/46 → **45/46**; legacy matcher 44/46 kept for comparison; splits 12/12, 10/10, 23/24 | MET |
| A5 | Residual golden-v2 failure is characterised, not hidden | `t23-off-scope` analysed with support/margin/coverage numbers, declared non-separable in §4 | MET WITH DISCLOSED DEVIATION (1/46 case remains a false answer) |
| A6 | golden v1 does not regress | 14/14, recall 1.0, MRR 1.0, nDCG 0.9513 | MET |
| A7 | Frozen holdout exists, is executed, and its result is reported as-is | `evals/holdout_v3.json` (12 cases), `evals/holdout_v4.json` (8 cases); v3 12/12, v4 7/8 | MET WITH DISCLOSED DEVIATION — see §3 |
| A8 | Semantic embedding options were benchmarked, not assumed | `semantic_provider_benchmark.md` + `semantic_bench.json` (5 configurations, pre-registered 6-clause promotion rule, **no-go**) | MET |
| A9 | Promotion of a new embedding path is a decision, not a vibe | Same doc: each clause (a)–(f) with its own measured verdict; decision = do not promote | MET |
| A10 | Host independence: the wheel runs inside foreign hosts | `universal_integration_report.md`; 5/5 hosts (python-wheel, cli, flask, node, unrelated repo venv) | MET |
| A11 | The experience layer degrades honestly across renderer capabilities | `experience_validation.md`; 33/33 checks across webgl2 / webgl1 / canvas2d / none | MET |
| A12 | Accessibility claims are structural, not asserted | Same doc: transcript is an `<ol>` inside a labelled section, canvas `aria-hidden`, reduced-motion, forced-colors, focus ring, no third-party assets | MET |
| A13 | Security posture probed with adversarial cases | `security_matrix.json`; 22/22 rows incl. authz per role, tenant isolation, traversal, oversized body, jailbreak, header hardening | MET |
| A14 | Recovery/rollback/crash-safety demonstrated | `data_recovery_validation.md` + `evidence/recovery_report.json`; 14/14 incl. SIGKILL mid-ingest → `integrity_check=ok` | MET |
| A15 | Performance envelope measured and a support boundary stated | `performance_and_scale.md` + `evidence/scale/scale-*.json`; 56 → 8,750 chunks; supported ≤5,000, 10k measured-not-supported | MET |
| A16 | The whole package is reproducible with one command | `scripts/run_local_gates.sh`; final log 15 gates / 0 failed | MET |

## 2. Labelling criteria (the ones that protect the user from over-claiming)

| # | Criterion | Evidence | Verdict |
| --- | --- | --- | --- |
| B1 | Neural embedding performance is labelled unverified, never implied | `semantic_provider_benchmark.md` §2 (blocked-host table with the exact transport errors); `architecture.md` §5 provider matrix | MET |
| B2 | Hosted/local LLM quality is labelled unverified | `live_llm_validation.md` — BLOCKED (exit 2), with the json verdict, the six graded checks that would run, and two unblock recipes; stub coverage stated as stub | MET |
| B3 | GPU rendering is labelled unverified | `experience_validation.md`; `experience-*.log`: "physical GPU: NOT verified in this sandbox" | MET |
| B4 | Multi-tenancy is not described as an audited boundary | `architecture.md` §7.2; `technical_spec.md` §9 | MET |
| B5 | Public deployment (TLS, proxy, container) is out of scope, stated | `technical_spec.md` §9; `universal_integration_report.md` "what this does not prove" | MET |
| B6 | Every number names its source | Conventions enforced across all nine documents: each table names the log/report JSON and the run stamp | MET |
| B7 | No estimate presented as a measurement | `performance_and_scale.md` gives measured p50/p95 and no extrapolated capacity claim; `testing.md` §6 declines to state a coverage percentage | MET |

## 3. Deviation register (the honest part)

| ID | Deviation | Why it happened | Residual risk | Where it is disclosed |
| --- | --- | --- | --- | --- |
| D1 | `t23-off-scope` is answered instead of abstained | Its support (0.333) sits inside the answerable range (0.244–0.307); no threshold separates the classes | A user asking an off-topic question can receive a plausible, sourced, wrong-scope answer | `evaluation_v2_failure_analysis.md` §4; `release_decision.md` §4 |
| D2 | Holdout v3 was executed more than once | First run 11/12 (`h03` → quantity defect), then a later conflict-gate change regressed `h09`, then a general stem/plural fix restored 12/12 | v3's 12/12 is a **post-fix** number, not a clean generalisation estimate | `evaluation_v2_failure_analysis.md` §6 (run-by-run table); the dataset's own `notice` says so and was corrected in this run |
| D3 | Holdout v4 was re-scored after the gate change | To confirm the gate change did not alter its result (it did not: 7/8, same case) | Minor: v4 is a validation set, not pristine single-shot for that dimension; no rule was shaped by a v4 case | v4 `notice`; `acceptance_matrix.md` §3 |
| D4 | `h14` (`keeping` ↔ `retention`) is left failing | It is a synonym, not a morphological variant; measured with distributional embeddings too, so it is a genuine semantic gap, not a lexical bug | One holdout case fails on a paraphrase the pipeline cannot bridge without a neural/synonym layer | `evaluation_v2_failure_analysis.md` §5; `semantic_provider_benchmark.md` |
| D5 | Live LLM validation is blocked | No ollama, no provider key, no GPU, no model host reachable | Model-backed answer quality, streaming TTFT, and prompt-following remain unverified | `live_llm_validation.md` |
| D6 | Neural embeddings were never executed | `huggingface.co` and GitHub release assets are blocked; spaCy wheel download fails mid-transfer | Semantic retrieval quality is unknown; only hashing and in-corpus LSA were measured | `semantic_provider_benchmark.md` §1–2 |
| D7 | 10,000-chunk interactive performance is not supported | p50 412–444 ms at 8,750 chunks against a 250 ms gate | Deployments above ~5,000 chunks need an ANN index (not added here) | `performance_and_scale.md` |
| D8 | Conflict metrics are degenerate on a split with no positive conflict case | v4's only annotated case is `expectConflict: false`, so `max(1,0)` denominators print 0.0 | A reader could misread `precision 0.0` on v4 as a failure; the meaningful value is `detected: 0` | `evaluation_v2_failure_analysis.md` §6 |

## 4. What this matrix deliberately does **not** certify

* That the service is production-ready in the operational sense (on-call,
  upgrades, migrations at scale, backup retention policy, incident response).
* That answer quality generalises beyond 66 synthetic chunks and 66 curated cases.
* That the security matrix is a penetration test. It is 22 targeted assertions
  against a local loopback process; it is not a threat-model sign-off.
* That any claim here transfers to a GPU/neural deployment. The provider matrix in
  `architecture.md` §5 is the boundary.
