# Architecture

The Lear agent is a backend-only command-line RAG assistant that answers questions about the Lear backend. It is a layer, `ragformation/lear/` (22 modules, about 6,300 lines), on top of the unchanged rag-formation engine.

**Changes to existing code:**
- `ragformation/cli.py` registers the `lear` subcommand (`add_lear_parser`, `run_lear`).
- The engine fixes from the M1 audit are separate and listed in `docs/evaluation.md` §8.5.
- No UI, web or demo file was changed.

## Component map

```text
python -m ragformation lear ...            ragformation/cli.py  ->  lear/cli.py (parser, exit codes, streaming, --json)
 |
 |-- doctor ......................... lear/doctor.py    -> lear/ollama.py (loopback HTTP: /api/version, tags, show, embed, chat)
 |-- docs build / verify ............ lear/docgen.py    (pinned Lear checkout -> docs/lear_backend/reference, source_map, SOURCE_PIN)
 |                                    lear/docverify.py (pin, pages, links, refs, stale SHAs, labels, duplicates, quarantine, secrets)
 |-- index rebuild / rollback / stats lear/indexer.py   -> core RAGEngine.ingest / rebuild / rollback_rebuild -> data/lear/index*/
 |-- ask / chat ..................... lear/agent.py     (service layer, below)
 |-- sessions / memory .............. lear/sessions.py (AgentDB, SessionStore), lear/memory.py, lear/shared_memory.py
 |-- eval validate / run / rescore .. lear/evalset.py, lear/evaluate.py, lear/judge.py
 |-- bench .......................... lear/bench.py     (threads of dummy users -> LearAgent.ask)
 `-- report ......................... lear/report.py    (latest doctor, docs, index, eval and bench artifacts)

shared: lear/settings.py (LearSettings, RAG_LEAR_*), lear/prompts.py (lear-assistant-v1), lear/compaction.py,
        lear/ratelimit.py, lear/secrets.py, lear/stats.py
```

## Request path (`LearAgent.ask`)

1. **Validate input.** The question must be non-empty and at most 2,000 characters.
2. **Admit** through `LearRateLimiter.acquire`: the per-user and per-session sliding windows, then the concurrency slot.
3. **Session.** Open or create the `(session_id, user_id)` session, load its messages, and compact them when they exceed the history budget (`lear-compaction-v1`, recording before and after tokens).
4. **Memory.** Pick up to 3 relevant, unexpired items visible to this user and session.
5. **Scope guard.** If the question names a term absent from the whole knowledge base, return ABSTAINED and name the term.
6. **Follow-up rewrite.** An anaphoric or short follow-up gets the previous question and identifiers added, for retrieval only.
7. **Check the index fingerprint** against the configured embedder. A mismatch is `ERROR embedding_mismatch`.
8. **Run the engine** (`RAGEngine.ask` with the Lear system prompt):
   - hybrid retrieval: cosine + BM25, then RRF, the heuristic reranker and MMR;
   - quarantine scan, context budget, abstention gate;
   - generation: Ollama streaming, or the labelled extractive baseline;
   - output guardrails: citation-marker validation, redaction.
9. **No-silent-fallback check.** On the `ollama` backend, an engine answer produced by its extractive fallback becomes `ERROR generation_unavailable`, unless explicitly allowed.
10. **Map the status:** ERROR, ABSTAINED, CONFLICTED, PARTIAL (uncited claims, removed markers, partial-coverage wording) or GROUNDED.
11. **Attach evidence.** Citations are resolved through `chunk_manifest.json` to the document, heading, Lear `path:Lx-Ly` references and the line span.
12. **Persist and return.** Save the user and assistant turns. Return the answer JSON:
    - `status`, `answer`, `citations`, `qualifying`, `backend`;
    - `models`: generation (configured, used, `fallbackUsed`) and embedding (provider, model, dimension, fingerprint);
    - `promptVersion`, `session`, `memoryUsed`;
    - `retrieval` (candidates, selected, excluded, quarantined, `contextTokens`, stages);
    - `timings` (TTFT, total, tokens/s, per-stage);
    - `correlationId`, `warnings`, `sourceCommit`, `error`.

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Error |
| 2 | Usage or configuration error |
| 3 | Abstained |
| 4 | Partial or conflicted |
| 5 | Gate failed |
| 6 | Dependency unavailable (Ollama or a model) |
| 7 | Rate limited |

## Storage layout (all under `data/`, which is gitignored)

| Path | Content |
|---|---|
| `data/lear/index/` | Qualifying index (Ollama embeddings); not built on this machine |
| `data/lear/index-baseline/` | Baseline index: `ragformation.db` and `chunk_manifest.json` (355 chunks, 19 docs) |
| `data/lear/agent.db` | Sessions, messages, compactions and memories |
| `data/lear/eval_runs/<label>/` | `run.json`, `summary.md`, `cases.jsonl`, `eval_agent.db`; plus `judge_raw/` when the judge runs |
| `data/lear/bench/*.json` | Benchmark runs |

Committed copies of the evaluation summaries are in `evals/lear/results/<label>/` (`run.json` and `summary.md`), and the evidence files are in `docs/lear_agent/evidence/`.

## Backends

| Backend | Embedder | Generator | Judge | Qualifying |
|---|---|---|---|---|
| `ollama` (default) | `nomic-embed-text` via loopback Ollama, no fallback | `qwen2.5:1.5b`, streaming | `llama3.2:1b` | Yes, if `doctor` passes |
| `baseline` (`--backend baseline`) | Hashing, d512 | Extractive sentence selection | Unavailable (BLOCKED) | **No**: every output is labelled `NOT QUALIFYING` |

## Design decisions

- **The engine stays unchanged.** Defects found in the core, such as the silent provider fallback or stale chunks after a chunking change, were fixed or guarded in the Lear layer with regression tests, and are listed as core gaps in [final_report.md](final_report.md). Approval is required before changing core behaviour.
- **Evidence over trust.** Lear facts come from the pinned checkout, through generated references and claim-labelled narrative pages. `docs verify` re-checks every `path:Lx-Ly` reference against that checkout.
- **Everything is local.** The settings refuse non-loopback model URLs, and there is no cloud SDK and no `auto` provider.
- **Failures are visible.** Missing dependencies give exit 6 with a diagnostic. Baselines are labelled. Rate-limited and failed requests stay in the benchmark results.
