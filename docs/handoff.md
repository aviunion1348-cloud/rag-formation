# Handoff — post-merge finalization v2

Readiness label: **READY-INTERNAL-BETA** — runnable and verified as a
single-tenant, single-process deployment up to roughly a thousand chunks with
lexical embeddings and extractive answers; **not** ready for public
multi-tenant use, not validated for semantic-retrieval quality, and its WebGL
path has not been seen on a GPU.

Date: 2026-11-05 · Branch: `arena/56047441-rag-formation` · Base: `4a6dab0` (PR #2)
· Commits: `86e762b`, `82807be`, `67b4bfe`, `7114048`.

---

## 1. Executive summary

The merged repository already worked end to end. This pass closed the gaps that
made it look broader than it was, and added the layer that had been deferred:

- **semantic embeddings without overclaiming** — LSA (corpus-trained) and spaCy
  static-vector providers exist; hashing remains the default and stays labelled
  `semantic: false` everywhere;
- **evaluation that can disagree with us** — a 46-case versioned set with
  dev/calibration/test splits, ten categories, calibration sweep, five-way
  retrieval ablation, and 7 failures reported in full;
- **conflicts that surface** — detection now runs over assembled evidence, cites
  both sides with the disputed sentence, and refuses to call differently-named
  entities a contradiction;
- **a package that installs** — the wheel ships the experience and is tested from
  a clean venv;
- **measured scale** — 91/441/875 chunks with ingest and query timings;
- **CI** — tests on three Pythons, frozen + calibration gates, packaging job, Node
  job;
- **the S3 Experience** — 16 scenes, Director/Live/Explorer, offline, accessible,
  honestly labelled;
- **an integration kit** — Python and Node clients, a host-principal proxy, a curl
  quickstart, all executed as tests.

Three real defects were found by that new work and fixed: `GET /api/v1/traces/{id}`
returned 500; the query body's `allowedSources` was silently ignored (a scope
leak); and templated documents produced false conflict reports.

## 2. Delivered against the brief

| Brief item | Status | Where |
| --- | --- | --- |
| Audit the merged repo, preserve all merged work | done | `docs/post_merge_audit.md`; no history rewritten, no force-push; `engine/` untouched (29 tests pass) |
| Genuine semantic embeddings | providers implemented, quality **not measured** | `embeddings.py` (`DistributionalEmbedder`, `SpacyEmbedder`), extras `[lsa]`, `[semantic]` |
| Real LLM path | adapter verified against a stub only | `generation.py`, `tests/test_ollama_provider.py` |
| Expanded evaluation + recalibration | done | `evals/golden_v2.json`, `evaluation_v2.py`, `docs/evaluation.md` §8; threshold 0.22 → 0.24 on calibration evidence |
| Installable package, stable API, versioned `/api/v1` | done | `pyproject.toml`, `api.py`, wheel install test, CI packaging job |
| Universal integration kit with host-principal auth | done | `integrations/`, `docs/integration.md` |
| S3 Experience: Director/Live/Explorer, 16 scenes, honest labelling, WebGL + fallbacks | done (headless-verified) | `ragformation/web/experience/`, `docs/experience.md` |
| Versioned event contract | done | `docs/events.md`, `events.py`, `/api/v1/events/recent` |
| Stronger conflict detection | done | `engine._detect_conflict`, `_conflict_from_evidence`, `_merge_conflict_citations` |
| Measured benchmarks | done | `scripts/bench_scale.py`, `evals/results/scale/`, `docs/benchmarks.md` |
| CI | done (local execution verified; first GitHub run pending push) | `.github/workflows/ci.yml` |
| Rewritten docs | done | README + `events`, `integration`, `experience`, `benchmarks`, `post_merge_audit`, evaluation §8, ADR 0007 amendment |
| No new parallel implementation | honoured | everything extended `ragformation/`; no `ragformation2/` |

## 3. Evidence ledger (every line was run in this session)

| Check | Command | Result |
| --- | --- | --- |
| Full suite | `python -m unittest discover -s tests -p 'test_*.py'` | **143 tests OK**, ~20 s |
| Legacy suite | `cd engine && python -m unittest discover -s tests` | 29 tests OK |
| Frozen golden gate | `ragformation eval --v1 --json` | 14/14, recall 1.0, MRR 1.0, nDCG 0.952, abstain accuracy 1.0 |
| Evaluation v2 | `ragformation eval --out …` | 39/46, recall@6 1.0, MRR 0.921, citation precision 1.0, abstention recall 0.889 |
| Calibration gate | `ragformation eval --gate calibration --min-pass-rate 1.0` | held, 12/12, exit 0 |
| Conflict case | `ragformation ask "How long is access review evidence retained?"` | `needs_review`, 5 vs 3 years, both cited |
| Scale | `python scripts/bench_scale.py --chunks 100 500 1000` | 91/441/875 chunks; p50 10.8 / 24.5 / 42.5 ms; 12/12 lookups |
| Experience contracts | `node tests/experience_contract.mjs` | 39 checks passed |
| Experience smoke (jsdom) | `node scripts/experience_smoke.mjs --jsdom …` | 23 checks passed |
| Integration kit | `python -m unittest … test_integration_kit.py` | 13 tests OK (Python client, Node client, proxy, impersonation dropped) |
| Quickstart | `bash integrations/curl/quickstart.sh` | all steps passed against a keyed server |
| Wheel | build → install into clean venv | `/experience`, `/api/v1/openapi.json`, `/api/v1/events/recent` served; conflict answer correct from site-packages |
| CI workflow | YAML parsed; every command executed by hand | green locally; GitHub run pending |

## 4. Map of the system

```
ragformation/
  cli.py            init · ingest · ask · serve · eval · doctor · rebuild · delete · export
  engine.py         RAGEngine: ingest → retrieve → assemble → generate → validate → decide
  retrieval.py      dense + BM25 + RRF + rerank + MMR (brute force over in-memory vectors)
  store.py          SQLite: sources, chunks, vectors, FTS5, queries, jobs, backups
  embeddings.py     hashing · LSA · spaCy · Ollama · OpenAI, with fingerprints
  generation.py     Ollama · OpenAI · extractive, streaming and timing
  guardrails.py     untrusted-text handling, citation stripping, redaction
  auth.py           principals, roles, tenant + source scope
  events.py         versioned event bus (SSE + polling history)
  api.py            legacy `/api` + versioned `/api/v1` + static console & experience
  evaluation.py     frozen v1 runner (14 cases)
  evaluation_v2.py  v2 runner: splits, calibration, ablations, conflicts
  web/              console at `/`, experience at `/experience`
corpus/             14 synthetic fixtures (one deliberate contradiction)
evals/              golden v1 + v2, paraphrase set, committed results
integrations/       python client · node client · proxy example · curl quickstart
scripts/            demo recorder · scale bench · paraphrase bench · experience smoke
docs/               architecture, retrieval, ingestion, evaluation, benchmarks, events,
                    integration, experience, post-merge audit, decisions/, operations
```

## 5. Running it

```bash
python3 -m ragformation ingest corpus           # 14 sources → 59 chunks
python3 -m ragformation ask "How long are audit logs retained?"
python3 -m ragformation serve --port 8787       # console /, film /experience
python3 -m unittest discover -s tests -p 'test_*.py'
```

The experience also works with no server: director mode reads a fixture recorded
by `scripts/record_experience_demo.py`.

## 6. The S3 Experience, precisely

- 16 scenes; WebGL2 renderer with ~2 600 particles per scene, WebGL1 shader pair,
  Canvas-2D fallback, and a transcript-only path when no canvas exists.
- Director (recorded), Live (event stream), Explorer (real index + real queries).
- Accessibility: reduced-motion honoured, keyboard control, `aria-live` captions,
  skip link, generated transcript, print stylesheet.
- Honesty: mode badge, embedding and generation badges come from the server; a
  missing key or unreachable server is displayed as such, not guessed.
- Verified headlessly (62 checks total). **Not verified:** shader compilation on a
  real GPU and visual quality. Stated in `docs/experience.md`, `docs/handoff.md` §9.

## 7. Numbers you may quote

| Metric | Value | Caveat |
| --- | --- | --- |
| Tests | 143 (+29 legacy) | offline, no paid API |
| v1 golden | 14/14, recall 1.0, MRR 1.0, nDCG 0.952 | small synthetic set |
| v2 | 39/46, citation precision 1.0 | lexical embeddings; 7 failures listed |
| Abstention | recall 0.889, false answers 0.111 | support ranges overlap (gap −0.085) |
| Latency | 59 chunks: mean 10.8 ms, p95 14.6 ms; 875 chunks: p50 42.5 ms | linear scan, one process |
| Ingest | 875 chunks in 0.80 s | hashing embedder, not a transformer |
| Wheel | 165 KB, installs clean, serves the film | no PyPI publication |

## 8. Security posture

- Loopback by default; a non-loopback bind without authentication is refused
  unless `RAG_ALLOW_INSECURE_PUBLIC=1`, which is logged and reported by `doctor`
  and `/health`.
- Identity is server-side only: API key (constant-time compare) or host principal
  header behind `RAG_TRUST_PROXY=1`. Client-supplied tenant/subject **headers** are
  ignored; the `allowedSources` **body** field can only narrow scope (fixed this
  pass — it was silently ignored).
- Traces and events never carry document text, prompts, answers, keys or tokens;
  tested.
- Uploaded content is data: instruction-like text is quarantined and reported, not
  executed.
- Rate limiting is in-process and not shared across workers (documented).

## 9. Known gaps (with severity)

| # | Gap | Severity | Why it is not "done" |
| --- | --- | --- | --- |
| 1 | No measured semantic-retrieval quality | high for the feature's promise | no pretrained weights obtainable here; providers exist but their quality is unmeasured |
| 2 | No live LLM execution | high | no Ollama/API key; adapter tested against a stub only |
| 3 | WebGL unverified on hardware | medium | no browser/GPU in this environment |
| 4 | 7/46 v2 failures | medium | lexical embedder + sentence-selection generator; listed individually |
| 5 | Abstention classes overlap | medium | calibrated, not solved; 0.24 is a balanced choice |
| 6 | Linear vector scan | medium | an ANN index is future work; measured envelope documented |
| 7 | Single-process, in-memory rate limit | low | deployment shape, documented |
| 8 | CI not yet executed on GitHub | low | workflow authored and every command run locally; first run happens on push |
| 9 | No licence file (pyproject declares MIT metadata) | owner decision | not changed here |

## 10. Release and deployment checklist

1. `ragformation doctor` — posture, providers, integrity.
2. `GET /api/v1/ready` — `ready: true`, no integrity problems.
3. `python -m unittest discover -s tests -p 'test_*.py'` and `ragformation eval --v1`.
4. `ragformation eval --gate calibration --min-pass-rate 1.0` in CI.
5. `python scripts/bench_scale.py --chunks <expected size>` — compare against your
   latency budget.
6. Key set, loopback bind, proxy in front (`integrations/host_principal/`).
7. Run `bash integrations/curl/quickstart.sh` against the deployed instance.

## 11. Data safety and rollback

- Ingestion is additive and idempotent; unchanged content is a no-op.
- Rebuild stages vectors in `vectors_staged`, validates, swaps in one transaction,
  and keeps `vectors_backup` for `POST /api/v1/index/rollback`.
- Changing the embedding provider is detected (`409` fingerprint mismatch) rather
  than silently mixing vector spaces; the remedy is `rebuild --reembed`.
- The demo recorder and every benchmark run in `tempfile` directories and never
  touch `data/`; no user database was modified in this session.

## 12. Next steps, in priority order

1. **Measure a real semantic embedder.** `pip install "ragformation[lsa]"` is
   already wired; run `ragformation rebuild --reembed` then `eval` and record the
   delta on the 7 failing cases. Use `[semantic]` with `en_core_web_md` where
   weights are available.
2. **Run one live LLM session** (Ollama `llama3.2:3b`) and re-measure fact recall,
   hallucination and TTFT/ITL. Nothing in this repo proves model behaviour yet.
3. **Open `/experience` in a real browser** on a GPU machine and look at it. Fix
   anything that only a GPU can reveal; then remove the "unverified" caveat from
   `docs/experience.md`.
4. **Decide the v2 test-split policy** (18/24 today). Either fix the failures or
   set an explicit, documented threshold; do not lower it silently.
5. **Push the branch and let CI run**; fix whatever the first run finds (3.9 and
   3.13 have not executed here).
6. **Add an ANN index** when a real corpus exceeds a few thousand chunks, and
   re-run `bench_scale.py` to prove the improvement.
7. Optional: OpenAPI snapshot in `integrations/`, a `docker-compose` example, and a
   second tenant isolation integration test.

---

### What to trust, and what to check yourself

Trust the test suite, the golden gate, the benchmarks, the wheel install and the
integration kit: they were executed here and their output is quoted verbatim.
Check the film, the GPU path, a live model and semantic quality yourself: those
are the four things this environment could not exercise, and no document in this
repository claims otherwise.
