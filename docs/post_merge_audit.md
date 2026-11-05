# Post-merge audit and finalization v2

This document records what the merge left behind, what was actually found when
the repository was audited, what this pass changed, and what is still missing. It
is written to be checkable: every claim points at a file, a command or a test.

Audit date: 2026-11-05. Starting commit: `4a6dab0` (merge of PR #2) →
`86e762b` (typed API, event contract) → this pass.

## 1. What was healthy after the merge

- The system ran end to end offline: ingest → chunk → index → hybrid retrieval →
  extractive answer → validated citations (`make test` green at 107 tests).
- Provider honesty was already enforced: the hashing embedder reported
  `semantic: false` everywhere, and the extractive path never called itself a
  model.
- Abstractions were real, not stubs: `EmbeddingProvider`, `GenerationProvider`,
  `Principal`, `RAGEngine`, and a versioned `/api/v1` surface existed with tests.

## 2. Gaps found by the audit

| # | Gap | Why it mattered | Evidence at audit time |
| --- | --- | --- | --- |
| 1 | No genuinely semantic embedding path | hashing is lexical; "semantic search" would have been an overclaim | only `hashing` + an untested Ollama path |
| 2 | One evaluation set (14 cases), no calibration split | threshold could be tuned on the same cases it was scored on | `evals/golden.json` only |
| 3 | Conflict detection was citation-scoped | the extractive generator cites one side, so a real contradiction was invisible | a hand-built 2-citation unit test was the only coverage |
| 4 | No installable package evidence | the wheel did not ship the web assets | `pyproject` packaged only `web/*.js` |
| 5 | No scale measurements | "fast" was asserted, not measured beyond 21 chunks | README said "6 documents / 21 chunks" |
| 6 | No CI | regressions were caught only when someone ran the suite | no `.github/workflows` |
| 7 | No integration kit | callers had to read route code to use the API | console-only examples |
| 8 | The visual walkthrough lived in the legacy `engine/` | the real system had no honest, cinematic explanation | `/` console is a utility, not a film |
| 9 | `GET /api/v1/traces/{id}` was untested | it returned **500** (`NameError: json`) | found by the integration kit on first run |
| 10 | `allowedSources` in the query body was ignored | a documented narrowing filter silently did nothing — a scope leak | quickstart check "scope is enforced server-side" failed |

## 3. What this pass changed

### 3.1 Semantic embeddings without inventing them

- `DistributionalEmbedder` (LSA: TF-IDF → SVD, `lsa` extra) and
  `SpacyEmbedder` (pretrained static vectors, `semantic` extra).
- Dynamic dimension; the index fingerprint now covers provider, model,
  dimension, normalisation, metric, preprocessing and fit id, so two vector
  spaces cannot be mixed silently.
- Corpus-trained providers are fitted once per ingest batch, persisted, and
  reloaded on restart.
- Hashing remains the default and remains labelled `semantic: false`.

### 3.2 Evaluation v2 (46 cases, versioned)

- `evals/golden_v2.json`, revision `golden-v2-2026-11-05`, with `dev`,
  `calibration` and `test` splits and ten categories including paraphrase,
  multihop, exact-id, structured, no-answer, injection, conflict and distractor.
- `ragformation/evaluation_v2.py` measures retrieval (recall, precision, hit,
  MRR, nDCG, duplicate rate, source coverage), generation (fact recall, forbidden
  facts, citation precision, location validity), abstention as a classifier,
  conflict precision/recall, threshold calibration by balanced accuracy, and a
  five-way retrieval ablation matrix.
- The v1 14-case set is untouched and still the frozen regression gate.
- `cli eval` distinguishes measurement from verdict: v2 exits 0 and lists
  failures; `--gate <split> --min-pass-rate` turns it into a CI gate.

Measured (hashing + extractive, 59 chunks): **39/46** cases; recall@6 1.0,
precision@6 0.365, MRR 0.921, nDCG 0.918; answered 33 / needs_review 3 /
abstained 10; fact recall 0.838; citation precision 1.0; abstention recall 0.889
with 0.111 false answers; calibration best threshold 0.24 (configured default now
0.24). Ablations: dense 0.946 recall / 0.885 MRR, lexical 0.649 / 0.325, hybrid
1.0 / 0.921. The remaining 7 failures are enumerated in
`evals/results/golden-v2-hashing-extractive.json` and in `docs/evaluation.md`.

### 3.3 Conflicts that are visible and honest

- Detection runs over the assembled evidence, not only the sentences the
  generator happened to cite; that was the bug that hid a real 5-years vs
  3-years contradiction.
- Subject gating: shared terms ≥ 60 % of the smaller set, numeric unit must
  match, two different sources required, and **named entities must not conflict**
  (`svc-001` vs `svc-005` is not a contradiction — a false positive this pass
  actually produced and fixed).
- Both sides are cited with provenance, the disputed sentence is cited from the
  section that contains it, and the answer is marked `needs_review` with an
  explicit "the system does not pick one" note. It never averages.

### 3.4 A package that installs and serves

- `pyproject` now ships `web/experience/js/*.js` and the fixture; a test asserts
  every web asset matches a package-data pattern, and CI builds a wheel, checks
  its contents, installs it into a clean venv and exercises the API **and**
  `/experience` from site-packages.

### 3.5 The S3 Experience

- `GET /experience`: 16 scenes, WebGL2 particle stage with a WebGL1 and Canvas-2D
  fallback, Director / Live / Explorer modes, reduced motion, adaptive quality, a
  generated transcript, no third-party assets and no build step.
- Live mode follows the real event stream with per-trace sequence tracking,
  event-id de-duplication, contract-version rejection and a polling fallback
  (`GET /api/v1/events/recent`, added in this pass).
- The demo fixture is recorded from a real ingest → query → delete → rebuild by
  `scripts/record_experience_demo.py` and is labelled `demo` with the providers it
  actually used.

### 3.6 Measured scale

`scripts/bench_scale.py` produced `evals/results/scale/scale-{100,500,1000}.json`:

| Chunks | Ingest | Query p50 | Query p95 | Correct lookups |
| --- | --- | --- | --- | --- |
| 91 | 0.05 s | 10.8 ms | 12.3 ms | 12/12 |
| 441 | 0.30 s | 24.5 ms | 27.8 ms | 12/12 |
| 875 | 0.80 s | 42.5 ms | 52.7 ms | 12/12 |

### 3.7 CI, integration kit, docs

- `.github/workflows/ci.yml`: tests on 3.9/3.11/3.13, frozen golden gate,
  calibration-split gate, evaluation artifact, CLI smoke; a packaging job; a Node
  job for the experience contracts and the jsdom smoke test.
- `integrations/`: Python client, Node client, host-principal proxy example and a
  curl quickstart — all executed by `tests/test_integration_kit.py`, including an
  impersonation attempt that must be dropped by the proxy.
- New/updated docs: `events.md`, `integration.md`, `experience.md`,
  `benchmarks.md`, `evaluation.md`, this audit, README.

### 3.8 Bugs found and fixed during this pass

| Bug | Found by | Fix |
| --- | --- | --- |
| `GET /api/v1/traces/{id}` → 500 (`json` not imported) | integration kit | `import json`; new API test asserts stages and no document text |
| `allowedSources` ignored in the query body | curl quickstart | server-side intersection with the principal scope; empty intersection abstains; malformed filters 400 |
| False conflicts between templated documents (`svc-001` vs `svc-005`) | scale benchmark spot-check | named-entity gate in `_detect_conflict` |
| Conflict citation pointed at the wrong section | manual review of the conflict answer | cite the candidate containing the disputed sentence |
| Wheel omitted `web/experience/js/*.js` | packaging check | package-data pattern + test + CI assertion |
| `renderer.js` used `ctx2d` before its declaration | jsdom smoke test | declaration moved above `state` |
| Timeline could fall behind after a long frame | contract test | catch-up loop over elapsed dwells |
| Scene stages used names the engine never publishes (`response_ready`, `ingestion.source_accepted`) | contract test | dotted stage names matched to `make_event` normalisation; alias map kept for old traces |
| `cli eval --json` printed prose onto stdout | CLI test | diagnostics to stderr; gate verdict inside the JSON |
| `/api/v1/events/recent` assumed `history()` returns objects | new test | it returns dicts |

## 4. What is still not done (and is not claimed)

1. **A pretrained semantic embedder is not measured here.** The code path exists
   (`[semantic]` extra, spaCy static vectors) but no weights could be downloaded
   in this environment, so no semantic-quality number is reported.
2. **No live language model was exercised.** The Ollama adapter is tested against
   a stub; nothing here proves behaviour against a real model.
3. **WebGL is statically checked, not demonstrated.** No GPU or browser exists in
   this environment; `docs/experience.md` states exactly what was and was not
   verified.
4. **7 of 46 evaluation v2 cases fail** with hashing embeddings (paraphrase /
   structured lookups and one off-topic false answer). They are listed, not
   hidden, and are the honest next target.
5. **Abstention distributions overlap** (answerable min 0.2437 vs unanswerable max
   0.3289). No single threshold separates them; 0.24 is the balanced-accuracy
   choice, not a clean boundary.
6. **Retrieval is a linear scan.** Comfortable at ~1 000 chunks, not at millions;
   an ANN index is future work, not a current feature.
7. **No multi-node deployment, no auth server, no rate-limit persistence.** The
   rate limiter is in-process.
