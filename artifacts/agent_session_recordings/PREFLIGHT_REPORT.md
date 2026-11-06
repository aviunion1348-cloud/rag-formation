# Preflight report: Lear agent session recordings

Run `lear-rec-20261106T1219Z` · generated 2026-11-06T12:20:03Z by `scripts/session_recordings/preflight.py` (read-only diagnostics). Raw outputs: `evidence/env/`. Machine-readable: `evidence/preflight.json`.

**Result:** BLOCKED 11 · NOT_APPLICABLE 1 · NOT_IMPLEMENTED 2 · PARTIAL 1 · PASS 28. The pipeline, index, stores, tests and recording tools are healthy. **The configured local LLM (Ollama) is BLOCKED**: it is not installed and its download and model hosts are unreachable from this sandbox, so no real model inference can be recorded.

Status values: PASS, FAIL, PARTIAL, BLOCKED. 3 checks (E17, H10, H11) use NOT_IMPLEMENTED or NOT_APPLICABLE because none of the four would be truthful.

_Re-rendered 2026-11-06T12:45:24Z from the unchanged `evidence/preflight.json` to correct a wording error in this summary (it said "Two checks"); no check was re-run and no result changed._

## Ollama and model states

| State | Observed |
|---|---|
| Installed | NO |
| Configured | YES (qwen2.5:1.5b, nomic-embed-text, judge llama3.2:1b) |
| Available (server reachable) | NO |
| Loaded | NO |
| Successfully invoked | NO |
| Returned a real inference response | NO |

## Checks

| ID | Area | Check | Status | Detail | Evidence |
|---|---|---|---|---|---|
| E01 | environment | Operating system and architecture | **PASS** | Debian GNU/Linux 12 (bookworm) · kernel 6.1.158+ · x86_64 · 2 vCPU · 3939 MiB RAM · DISPLAY unset |  |
| E02 | environment | Working directory and repository root | **PASS** | cwd /home/user/rag-formation · git root /home/user/rag-formation | `evidence/env/git_toplevel.txt` |
| E03 | environment | Git branch | **PASS** | arena/c2d86cd7-rag-formation | `evidence/env/git_branch.txt` |
| E04 | environment | Commit SHA | **PASS** | 81c28c828be166fa51b66bd7cc8dadb0cd163c70 2026-11-06T12:19:19+00:00 recordings: pre-register the Lear agent session recording plan and harness | `evidence/env/git_head.txt` |
| E05 | environment | Uncommitted changes | **PASS** | tracked changes: 0; untracked files: 3 (top-level: artifacts) | `evidence/env/git_status.txt` |
| E06 | environment | Runtime and language versions | **PASS** | Python 3.11.2 · node v22.22.3 · SQLite 3.40.1 |  |
| E07 | environment | Dependency and package-manager versions | **PASS** | pip: pip 23.0.1 from /usr/lib/python3/dist-pa · npm 10.9.8 · RAG Formation is standard-library Python (pyproject declares no runtime dependencies) |  |
| E08 | environment | Project scripts | **PASS** | console script ragformation = ragformation.cli:main · Makefile targets: help, init, ingest, ask, serve, test, eval, doctor, stats, rebuild, clean, check |  |
| E09 | environment | Application and agent entry points | **PASS** | python3 -m ragformation lear {doctor,docs,index,ask,chat,sessions,memory,eval,bench,demo,report}; agent class ragformation.lear.agent.LearAgent.ask | `evidence/env/lear_help.txt` |
| E10 | environment | Configuration files | **PASS** | pyproject.toml, Makefile, ragformation/lear/settings.py, ragformation/config.py, evals/lear/golden_lear_v1.json · .env present: False (existence only; never read) |  |
| E11 | environment | Environment variable names (values never read) | **PASS** | 31 RAG_LEAR_* names read by settings; RAG_*/OLLAMA_* names set here: none (all defaults apply) |  |
| E12 | environment | Vector database implementation | **PASS** | SQLite file ragformation.db: table vectors (L2-normalised; exact brute-force cosine scan in Python) + FTS5 virtual table chunks_fts (BM25) |  |
| E13 | environment | Embedding provider | **PARTIAL** | configured: ollama:nomic-embed-text (BLOCKED here, see O-checks) · available: hashing:hashing-lexical-v1 d512 (offline baseline, lexical, NOT semantic) |  |
| E14 | environment | Reranking provider | **PASS** | heuristic-coverage-v1: a transparent blend of the retrieval signals (support score); not a cross-encoder, no model |  |
| E15 | environment | LLM provider | **BLOCKED** | configured: ollama:qwen2.5:1.5b at http://127.0.0.1:11434 (loopback only); judge llama3.2:1b · available here: extractive:extractive-synthesis-v1 (no LLM, labelled NOT QUALIFYING) |  |
| E16 | environment | Memory implementation | **PASS** | SQLite agent.db: sessions, messages, compactions (summaries), memories (scoped long-term memory); read-only shared-memory import from an approved directory |  |
| E17 | environment | Tool registry | **NOT_IMPLEMENTED** | no tool registry, tool calling, subprocess or shell execution in the Lear agent path (static scan of agent, sessions, memory, compaction, ollama, shared_memory) |  |
| E18 | environment | Logging and observability | **PASS** | engine events (ragformation/events.py, schema 1.0, sanitised) · per-query trace persisted to SQLite queries.trace_json · agent lifecycle spans lear-lifecycle-v1 in the answer JSON · no OpenTelemetry exporter |  |
| E19 | environment | Testing and evaluation frameworks | **PASS** | unittest suites (tests/, engine/tests/) · lear eval validate\|run\|rescore (golden set, score_case, judge) · lear bench (dummy users) |  |
| E20 | environment | Recording and media utilities | **PASS** | asciinema asciinema 2.4.0 · script from util-linux 2.38.1 · ffmpeg version n9.0.2-17-g2a571b6068-202 · pyte 0.8.2 Pillow 13.0.0 · OBS missing · no display server (native screen capture unavailable; terminal recording used) |  |
| O01 | ollama | Ollama installed | **BLOCKED** | ollama binary on PATH: not found |  |
| O02 | ollama | Server reachable (TCP 127.0.0.1:11434) | **BLOCKED** | ConnectionRefusedError: [Errno 111] Connection refused |  |
| O03 | ollama | HTTP /api/version | **BLOCKED** | URLError: <urlopen error [Errno 111] Connection refused> |  |
| O04 | ollama | Configured models | **PASS** | configured: embedder nomic-embed-text (~137M), llm qwen2.5:1.5b (~1.5B), judge llama3.2:1b (~1.2B) - names from ragformation/lear/settings.py defaults |  |
| O05 | ollama | Models installed / metadata / size / quantisation / context window | **BLOCKED** | cannot be read: /api/tags and /api/show need a running Ollama server |  |
| O06 | ollama | Embedding model available | **BLOCKED** | nomic-embed-text cannot be listed or probed without the server |  |
| O07 | ollama | Inference invoked / real response returned | **BLOCKED** | no generation request can be sent; no model response exists in this package |  |
| O08 | ollama | GPU or CPU execution | **PASS** | GPU devices: none · nvidia-smi: not found · CPU only (2 vCPU) |  |
| O09 | ollama | lear doctor --no-generation (qualifying backend) | **BLOCKED** | exit 6 · [FAIL      ] ollama.version: error=ollama is not reachable at http://127.0.0.1:11434 \| NOT READY: ollama.version, models.installed, models.embedder, models.llm, models.judge, probe.embedding, probe.generation_stream, probe.judge_json | `evidence/env/lear_doctor_ollama.txt` |
| O10 | ollama | Install path for Ollama (release assets) | **BLOCKED** | latest release via api.github.com: v0.40.15 ollama-linux-amd64-mlx.tar.zst 1214961197 https://github.com/ollama/ollama/releases/download/v0.40.15/ollama-linux-amd64-mlx.tar.zst \| ollama-linux-amd64-rocm.tar.zst 1053019798 https://github.com/ollama/ollama/ · asset hosts: objects.githubusercontent.com → URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>; release-assets.githubusercontent.com → URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>; ollama.com → URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)> | `evidence/env/ollama_release_api.txt` |
| O11 | ollama | Model registries reachable | **BLOCKED** | registry.ollama.ai → URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)>; huggingface.co → URLError: <urlopen error TLS/SSL connection has been closed (EOF) (_ssl.c:992)> (sandbox egress allows only github.com, api.github.com, codeload.github.com, registry.npmjs.org, pypi.org, files.pythonhosted.org) | `evidence/env/model_hosts_reachability.json` |
| H01 | health | Unit and integration tests (tests/) | **PASS** | ran 256 · OK skipped=2 · 33.0 s | `evidence/env/unit_tests.txt` |
| H02 | health | Legacy engine tests (engine/tests) | **PASS** | ran 29 · exit 0 | `evidence/env/legacy_tests.txt` |
| H03 | health | Golden set validation | **PASS** | exit 0 · Golden set lear-golden v1.0.0 — 80 cases (/home/user/rag-formation/evals/lear/golden_lear_v1.json) [PASS] schema: 80 cases, 0 schema problems [PASS] size: 80 cases (required 50-100, target 70-80) [PAS | `evidence/env/eval_validate.txt` |
| H04 | health | Application startup + document indexing (baseline, scratch dir) | **PASS** | namespace=lear-docs files=20 chunks=355 counts={'new': 19, 'updated': 0, 'unchanged': 0, 'failed': 0, 'deleted': 0} embedding=hashing:hashing-lexical-v1 dim=512 [NOT QUALIFYING: baseline hashing embeddings] | `evidence/env/index_rebuild_scratch.txt` |
| H05 | health | Document-index availability | **PASS** | documents 19 · chunks 355 · tokens 48676 · fingerprint hashing:hashing-lexical-v1:d512:norm=l2:metric=cosine:pre=lower-token-v1:fpv=2 · source pin 60cf9abf8408 | `evidence/env/index_stats_scratch.txt` |
| H06 | health | Vector-store and database connectivity | **PASS** | read-only open OK · chunks 355 · vectors 355 · sources 19 · FTS5 MATCH 'permission' → 40 rows |  |
| H07 | health | Memory and session store initialisation | **PASS** | lear sessions list exit 0 · agent.db tables: compactions, memories, messages, meta, sessions, sqlite_sequence | `evidence/env/sessions_list_scratch.txt` |
| H08 | health | Indexed corpus vs pinned Lear source (lear docs verify) | **PASS** | Lear-backend-Avi HEAD 60cf9abf84088c3a5ec05e8700c52185444e215f 2026-10-01T19:21:30 · exit 0 · [PASS] pin: pinned aviunion1348-cloud/Lear-backend-Avi @ 60cf9abf8408; checkout HEAD matches | `evidence/env/lear_docs_verify.txt` |
| H09 | health | Model and embedding connectivity (qualifying) | **BLOCKED** | see O02, O03, O06, O09: no Ollama server |  |
| H10 | health | Tool registration | **NOT_IMPLEMENTED** | the Lear agent has no tools to register (see E17) |  |
| H11 | health | API availability | **NOT_APPLICABLE** | the Lear agent's interface is the CLI (the take-home brief requires a CLI); the core HTTP API of RAG Formation is a different surface and is not part of the Lear agent |  |
| H12 | health | Relevant runtime processes | **PASS** | relevant processes: none (nothing is started in the background for these recordings) | `evidence/env/processes.txt` |

## Baseline

- Repository `aviunion1348-cloud/rag-formation`, branch `arena/c2d86cd7-rag-formation`, commit `81c28c828be166fa51b66bd7cc8dadb0cd163c70`.
- Tracked changes at preflight: 0; untracked files: 3.
- Application version (pyproject): 0.2.0.
- Index (scratch build): 19 documents, 355 chunks, fingerprint `hashing:hashing-lexical-v1:d512:norm=l2:metric=cosine:pre=lower-token-v1:fpv=2`, source pin `60cf9abf84088c3a5ec05e8700c52185444e215f`.
- Environment variable names read by the agent: 31 (`RAG_LEAR_*`); set in this environment: none. Values are never read or stored.

No environment-level correction was applied, and no application file was changed for these recordings.
