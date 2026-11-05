# Operations

## 1. Run

```bash
python3 -m ragformation init                 # create data/ and the database
python3 -m ragformation ingest corpus        # index documents
python3 -m ragformation ask "…"              # one question
python3 -m ragformation serve --port 8787    # API + console (0.0.0.0 by default)
python3 -m ragformation doctor               # what is actually active?
```

Everything lives in `RAG_DATA_DIR` (default `data/`): `ragformation.db` plus WAL
files. Deleting that directory resets the system; re-ingest to rebuild. Nothing
else on the machine is written.

## 2. Configuration

All configuration is environment variables, documented in `.env.example`. The ones
that change behaviour most:

| Variable | Default | Why you would change it |
|---|---|---|
| `RAG_EMBED_PROVIDER` | `auto` | Pin to `ollama` to fail loudly instead of falling back; `hashing` for offline demos |
| `RAG_LLM_PROVIDER` | `auto` | Pin to `ollama`; `extractive` to disable generation entirely |
| `RAG_TOP_K` | 6 | More evidence vs smaller context |
| `RAG_MAX_CONTEXT_TOKENS` | 1400 | Model context budget |
| `RAG_ABSTAIN_THRESHOLD` | 0.22 | Strictness of "I don't know" (re-calibrate, see `docs/evaluation.md` §4) |
| `RAG_API_KEY` | unset | Require `x-api-key` on `/api` |
| `RAG_HOST` | `0.0.0.0` | Set `127.0.0.1` for a strictly local server |
| `RAG_MAX_SOURCE_BYTES` | 8 MiB | Upload cap |

`GET /api/config` and `ragformation doctor` show the effective values (never
secrets, never absolute paths).

## 3. Health and monitoring

```bash
curl -s localhost:8787/api/health | python3 -m json.tool
curl -s localhost:8787/api/stats  | python3 -m json.tool
python3 -m ragformation doctor
```

`/api/health` returns:

* `ok` — false when the index has integrity problems;
* `sources`, `chunks`, `vectors`, `lexicalRows`, `queries`;
* `embedding` / `generation`: provider, model, and whether embeddings are actually
  **semantic**; plus the provider's own reachability check;
* `integrity.problems` — a list of `{code, …}` entries: `chunks_without_vectors`,
  `orphan_vectors`, `chunks_missing_from_lexical_index`, `dimension_mismatch`,
  `mixed_embedding_models`.

`/api/stats` returns count/mean/p50/p95/min/max for end-to-end latency, retrieval,
generation, TTFT and ITL, plus status counts, citations per answer, citation
precision and abstention rate over the recorded window.

## 4. Failure playbook

| Symptom | Check | Fix |
|---|---|---|
| `doctor` → embedding WARN (lexical-only) | `ollama list` | `ollama pull nomic-embed-text`, then `rebuild` with `RAG_EMBED_PROVIDER=ollama` |
| `doctor` → generation WARN | model list from `/api/health` | `ollama pull llama3.2:3b`, or accept extractive answers |
| `embedding_mismatch` on ingest | `health.indexFingerprint` vs configured model | `ragformation rebuild --force` (re-embeds stored chunks) or delete `data/` and re-ingest |
| `health.ok=false` with `chunks_without_vectors` | DB corruption or interrupted write | `ragformation rebuild` |
| Answers say "could not find enough evidence" for everything | corpus empty, wrong tenant filter, or threshold too high | `sources`, then `INGEST`; try `--mode lexical`; lower `RAG_ABSTAIN_THRESHOLD` only after reading §4 of the evaluation doc |
| Provider timeouts on generation | `/api/health` generation health | The engine falls back automatically and records `fallback_provider_used`; investigate the provider separately |
| Jobs stuck in `running` | `recent_jobs` via the API or SQLite | Restart the engine: stale `running` jobs are marked `failed/interrupted` |
| Console shows `offline` chip | `curl /api/health` | Server not running, or `RAG_API_KEY` set without the header (the console does not send one) |

## 5. Backup and restore

* **Back up**: `data/ragformation.db` (stop the server or use SQLite's online
  backup), and the original documents if you still have them.
* **Restore**: stop the service, put the `.db` back, start it. `doctor` verifies
  integrity.
* **Rebuild instead of restoring**: if you kept the originals, `delete data/` and
  re-ingest. The vector index is derived data — this is the cheapest recovery
  path and it is what `rebuild` automates for stored chunks.

```bash
# consistent copy while running
python3 - <<'PY'
import sqlite3
src = sqlite3.connect("data/ragformation.db")
dst = sqlite3.connect("backup-ragformation.db")
src.backup(dst)
dst.close(); src.close()
PY
```

## 6. Upgrades

1. Read `docs/decisions/` for the behaviour that is guaranteed.
2. Run `make test` and `make eval` before and after.
3. If the embedding model changed, run `ragformation rebuild --force` and
   re-calibrate the abstention threshold (`docs/evaluation.md` §4).
4. If chunking changed, re-ingest the originals — `rebuild` cannot re-chunk.
5. Keep `data/` out of version control (`.gitignore` already does).

## 7. Retention and deletion

* `delete <source_id>` removes chunks, vectors and lexical rows in one
  transaction, and marks the source row `deleted`. Retrieval can no longer return
  it (tested).
* `queries` rows contain the question text and trace, not document text. Clear
  them with `DELETE FROM queries;` if question text is sensitive, or set
  `RAG_PERSIST_TRACES=0` to stop recording.
* No original files are stored, so deleting a source also removes the only copy of
  its text held by the system.

## 8. Resource envelope

Measured on 2 vCPU / 4 GB: 21 chunks → 1.6 MB database, ~6 ms per query, 165 q/s
sequential, ~35 MB RSS for the CLI. With Ollama, add the model's memory
(~2 GB for a 3B model at Q4). No connection pool, no background workers, one
process.
