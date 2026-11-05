# Retrieval and chunking

The Lear agent reuses the rag-formation engine (`ragformation/engine.py`, `retrieval.py`, `chunking.py`, `store.py`), which is unchanged by this work. It adds a Lear layer in `ragformation/lear/indexer.py` and `agent.py`. Settings are pinned by `LearSettings.engine_settings()`.

## Knowledge base

| Item | Value |
|---|---|
| Source | `docs/lear_backend/`, generated and hand-written from Lear at `60cf9abf8408` (see `SOURCE_PIN.json`) |
| Indexed | 19 pages, 355 chunks (baseline index) |
| Excluded pages | `source_map.md`, a path-to-page lookup table carrying `<!-- lear-index: skip-page -->` |
| Excluded regions | The README page list, between `<!-- lear-index: skip -->` and `<!-- lear-index: end -->` |
| Namespace | `lear-docs`. Source origins are `lear-docs:<relative path>`, tagged `namespace:lear-docs` and `commit:<sha12>`. |
| Storage | `data/lear/index/` (ollama) or `data/lear/index-baseline/` (baseline): `ragformation.db` (SQLite with chunks, FTS5, vectors, backup vectors, meta) plus `chunk_manifest.json` |

**Namespaces are kept apart.** The Lear docs, the agent state and the test fixtures each live somewhere different:
- The Lear docs index is the only vector index the agent queries, and it holds nothing else.
- Sessions and memory, including imported shared memory, live in `data/lear/agent.db`. They never enter the vector index and can never be cited.
- Test fixtures build their own corpora in temporary directories.

## Index-time preparation (`prepare_for_index`)

1. A page with the skip-page marker is excluded and reported in `excludedPages`.
2. Skip/end regions are removed.
3. Claim labels `**[implemented]**` become `[implemented]`. The loader strips a leading list marker, so the bold form used to leak `]**` into quoted sentences. `docs verify` uses the same function, so its quarantine scan sees exactly what is indexed.

## Chunking (core heading-aware chunker)

| Setting | Value | Environment variable |
|---|---|---|
| Target tokens | 180 | `RAG_LEAR_CHUNK_TARGET_TOKENS` |
| Maximum tokens | 300 | `RAG_LEAR_CHUNK_MAX_TOKENS` |
| Overlap tokens | 20 (was 40; round 1) | `RAG_LEAR_CHUNK_OVERLAP_TOKENS` |

How the chunker behaves:
- Chunks follow Markdown headings. Each chunk keeps its heading path, for example `Authentication and authorisation > Permission modes`, which is also prepended to the chunk text.
- Fenced code blocks and tables are not split mid-row; this is tested by `test_table_rows_stay_together`.
- The chunker is deterministic: the same text and settings give the same chunk ids and content hashes. An unchanged rebuild reports `unchanged` for every page.
- The configuration is validated: overlap must be below the target, and the maximum must be at least the target.
- **Changing any chunking setting re-chunks every page** on the next `index rebuild`. This fixes the stale-chunk defect from round 1, where the engine's single global chunking fingerprint left 7 of 19 pages with chunks from the old settings. `--rechunk` forces this.

### Chunk metadata (`chunk_manifest.json`, `lear-chunk-manifest-v1`)

Every chunk records:

| Field | Content |
|---|---|
| `chunk_id`, `chunk_index` | Identity and position |
| `source_id`, `source_revision` | The source document and its revision |
| `source_path`, `document_title` | The page |
| `source_commit` | The Lear commit the page describes |
| `heading_path` | The chunk's heading path |
| `start_line`, `end_line`, `line_precision` | Line span: `lines`, `section` or `document` |
| `content_hash`, `token_count` | Content checks |
| `source_refs` | The `path:Lx-Ly` Lear references inside the chunk |
| `embedding_model`, `embedding_fingerprint`, `embedding_dimension` | Embedding identity |
| `created_at`, `namespace` | Provenance |

**Line spans.** These are computed by `locate_lines`, which was fixed in round 2.
- It picks the densest run of covered lines, and a line repeated n times on the page counts 1/n.
- On the current index: median 6 lines, p90 14, max 31, and 354 of 355 chunks at line precision. Before the fix it was p90 422 and max 573, because one repeated line could span a whole page.

## Embeddings

| Backend | Embedder | Fingerprint |
|---|---|---|
| `ollama` (qualifying) | `nomic-embed-text` via local `/api/embed`, `require_semantic=True` (no hashing fallback) | `ollama:nomic-embed-text:d<N>:norm=l2:metric=cosine:pre=lower-token-v1:fpv=2` |
| `baseline` (labelled) | Hashing, lexical, d512 | `hashing:hashing-lexical-v1:d512:norm=l2:metric=cosine:pre=lower-token-v1:fpv=2` |

**Fingerprint checks happen at query time**, in `check_index_fingerprint`.
- If the stored index fingerprint differs from the configured embedder (model, dimension, normalisation or preprocessing), every ask fails with `ERROR embedding_mismatch` and a hint to run `index rebuild --full`.
- Before this check, the core cosine returned 0.0 for mismatched lengths, so retrieval degraded silently while answers still said GROUNDED (red/green evidence in `evidence/embedding_mismatch_red_green.txt`).

## Store lifecycle

| Operation | Command / behaviour | Tested by |
|---|---|---|
| Incremental rebuild | `lear index rebuild`: new, changed (content hash), unchanged and deleted pages, each counted | `test_incremental_update_delete_and_restart_persistence` |
| Idempotent | A second rebuild reports every page `unchanged` with the same chunk count | Same test, and the live index (355 → 355) |
| Staged replace | A changed page's chunks are replaced in one transaction (`BEGIN IMMEDIATE` in `store.py`); a full re-embed is staged and swapped atomically | Core `tests/test_store.py` |
| Delete | Pages removed from `docs/lear_backend` are deleted from the index | Lear incremental test |
| Rollback | `lear index rollback` restores the vectors that were live before the last `--full` rebuild (`RAGEngine.rollback_rebuild`). It reports restored-vs-configured fingerprints and integrity problems. | `test_full_rebuild_can_be_rolled_back_to_the_previous_vectors` |
| Restart persistence | The index and manifest are reopened by a new process or agent | Incremental test (new agent instance) |
| Dimension checks | Query-time fingerprint check; a full rebuild is required to change the embedder | `test_stub_embedding_dimension_change_is_refused_until_full_rebuild` |
| Re-chunk on settings change | Automatic, or `--rechunk` | `test_chunking_change_rechunks_every_document` |

## Retrieval (core, per query)

1. **Candidates.** Cosine semantic search plus SQLite FTS5 BM25, each fetching `top_k × candidate_multiplier` = 6 × 4 = 24.
2. **Fusion.** Reciprocal Rank Fusion with k = 60. This needs no score calibration across the two channels.
3. **Basic reranker** `heuristic-coverage-v1`. It is a transparent blend of query-term coverage, phrase matches and cosine. It is not a cross-encoder and is labelled as such.
4. **MMR diversification** with λ = 0.72.
5. **Context assembly** up to `RAG_LEAR_MAX_CONTEXT_TOKENS` (1,800). It records what was selected and what was excluded, and why (budget, outside top-k, quarantined).
6. **Abstention gate.** The best selected support score is `0.65·(0.75·coverage + 0.25·phrase) + 0.35·(cos+0.15)/1.15`. Below the threshold, the agent abstains: 0.215 on the baseline (calibrated on the calibration split), 0.24 on Ollama (uncalibrated).
7. **Scope guard** (Lear layer, before retrieval). A question that names a term absent from the whole knowledge base is declined, with that term named in the reply.

**Diagnostics.** These are in every answer's `retrieval` block, and `lear ask --debug` shows them:
- the correlation id and the index fingerprint;
- every candidate with its document, heading, fused score, support, channel and reason;
- the selected and the excluded chunks, with reasons; quarantined chunks with the rules they tripped;
- context tokens, the best support, and per-stage latency (`semanticMs`, `lexicalMs`, `fusionMs`, `rerankMs`, `mmrMs` and generation).

## Measured quality (baseline only)

| Split | hit@k | MRR | Doc recall | Context precision |
|---|---|---|---|---|
| calibration+dev (round 3) | .973 | .626 | .847 | .290 |
| test (round 3) | 1.000 | .812 | .929 | .255 |
| holdout | 1.000 | .736 | .917 | .249 |

Context precision is low because the 6 selected chunks usually include neighbouring pages. The rounds tried shrinking the windows and removing overlap (round 1, e3–e5): recall went up and answer correctness went down, so this was not changed. Retrieval with `nomic-embed-text` has **not** been measured.
