# Lear backend agent

A backend-only command-line RAG assistant that answers engineering questions about the Lear backend (`aviunion1348-cloud/Lear-backend-Avi` at `60cf9abf8408`). It answers from evidence-backed documentation and cites Lear source lines.

**Status: BLOCKED for qualification on this machine.** The agent is built to run on local Ollama models only, and Ollama cannot be installed in this sandbox. Everything that does not need a model was run and tested here. The model-dependent parts were tested against a stub and fail with an explicit diagnostic (exit 6) when Ollama is missing. See [final_report.md](final_report.md).

## Local-only guarantee

- **Loopback only.** Model traffic goes only to a loopback Ollama URL (`127.0.0.1`, `localhost` or `[::1]`). Any other URL is a configuration error.
- **No cloud.** There is no cloud provider, no `auto` provider selection, and no hidden fallback. If Ollama or a model is missing, `doctor`, `index rebuild` and `ask` exit with code 6.
- **Labelled baselines.** `--backend baseline` (hashing plus extractive) exists for offline work and testing. Every one of its outputs is labelled `NOT QUALIFYING`.

## Models

| Role | Default | Cap | Environment variable |
|---|---|---|---|
| Embedder | `nomic-embed-text` | ≤200M parameters | `RAG_LEAR_EMBED_MODEL` |
| Answer LLM | `qwen2.5:1.5b` | ≤4B | `RAG_LEAR_LLM_MODEL` |
| Judge, which must differ from the LLM | `llama3.2:1b` | ≤4B | `RAG_LEAR_JUDGE_MODEL` |

`lear doctor` reads each parameter count from Ollama rather than trusting the model name ([model_compliance.md](model_compliance.md)).

## Quick start (on a machine with Ollama)

```bash
ollama serve &                                  # stays on 127.0.0.1
ollama pull nomic-embed-text && ollama pull qwen2.5:1.5b && ollama pull llama3.2:1b
cp .env.example .env                            # optional; every value has a local default

python3 -m ragformation lear doctor                                   # exit 0 when ready
python3 -m ragformation lear docs build --source /path/to/Lear-backend-Avi
python3 -m ragformation lear docs verify --source /path/to/Lear-backend-Avi
python3 -m ragformation lear index rebuild
python3 -m ragformation lear index stats
python3 -m ragformation lear ask "Which permission modes does the permission engine have?"
python3 -m ragformation lear chat --session demo        # one question per line on stdin
python3 -m ragformation lear sessions inspect demo
python3 -m ragformation lear memory list
python3 -m ragformation lear eval run --suite all --judge on
python3 -m ragformation lear report --source /path/to/Lear-backend-Avi
python3 -m ragformation lear bench --users 1,2,5
```

## Offline baseline (what was run here)

Add `--backend baseline` to the commands, for example:

```bash
python3 -m ragformation lear --backend baseline index rebuild
python3 -m ragformation lear --backend baseline ask --debug "What happens to open circuits if the circuit breaker's state file is corrupted?"
```

The full transcript of every command, with exit codes, is in [evidence/cli_walkthrough_2026-11-05.txt](evidence/cli_walkthrough_2026-11-05.txt). The script that produced it is [evidence/cli_walkthrough.sh](evidence/cli_walkthrough.sh).

## Commands

| Command | Purpose |
|---|---|
| `doctor [--json] [--no-generation]` | Probe Ollama, models, parameter caps, embedding, streaming and judge JSON |
| `docs build --source P` / `docs verify [--source P] [--write-coverage]` | Generate the reference docs from the pinned checkout, and verify links, refs, pin, labels, duplicates, quarantine and secrets |
| `index rebuild [--full] [--rechunk]` / `index rollback` / `index stats` | Incremental, idempotent index; staged re-embed; roll back to the previous vectors |
| `ask "Q" [--session S] [--user U] [--debug] [--no-stream] [--json]` | One question. Prints the answer, citations, status, models and TTFT/total/tok/s. |
| `chat --session S` | Multi-turn session from stdin |
| `sessions list\|inspect\|delete` | Session management (user-scoped) |
| `memory list\|add\|delete\|import --source DIR` | Scoped long-term memory; read-only shared-memory import |
| `eval validate\|run\|rescore` | Golden set checks; evaluation runs (`--suite all\|calibration\|dev\|test\|holdout`, `--judge auto\|on\|off`); re-score saved runs |
| `bench --users 1,2,5 [--requests N]` | Dummy-user load through the service layer |
| `report [--source P]` | Doctor, docs, index, every evaluation run and the latest bench |
| `demo [director\|live\|replay\|examiner\|capture\|effects] [--plain] [--json] [--source P]` | Terminal walkthrough of the real pipeline in a scratch directory; every value labelled LIVE, RECORDED, ILLUSTRATIVE, UNAVAILABLE, BLOCKED or NOT QUALIFYING ([demo_guide.md](demo_guide.md), [video](demo_video/README.md)) |

**Answer statuses:** `GROUNDED`, `PARTIAL`, `ABSTAINED`, `CONFLICTED`, `ERROR`.

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Error |
| 2 | Usage or configuration error |
| 3 | Abstained |
| 4 | Partial or conflicted |
| 5 | Gate failed |
| 6 | Dependency unavailable |
| 7 | Rate limited |

## Documents

| Page | Content |
|---|---|
| [final_report.md](final_report.md) | Gates A–G, the readiness decision, limitations |
| [requirements_matrix.md](requirements_matrix.md) | The 25 requirements, each with evidence and a status |
| [audit_environment.md](audit_environment.md) | Machine, network blocks, checkouts, pins and divergences |
| [source_inventory.md](source_inventory.md) · [documentation_coverage.md](documentation_coverage.md) | The Lear source inventory and documentation coverage |
| [architecture.md](architecture.md) | Components, request path, storage |
| [model_compliance.md](model_compliance.md) | Models, caps and enforcement |
| [retrieval_and_chunking.md](retrieval_and_chunking.md) | Chunking, metadata, store lifecycle, retrieval and reranking |
| [sessions_memory_context.md](sessions_memory_context.md) | Sessions, context assembly, compaction, memory, shared memory |
| [guardrails_security.md](guardrails_security.md) | Controls, injection tests, rate limits, Lear security findings |
| [evaluation_dataset.md](evaluation_dataset.md) · [judge_rubric.md](judge_rubric.md) | Golden set, judge rubric, manual audit |
| [round_1_report.md](round_1_report.md) · [round_2_report.md](round_2_report.md) · [round_3_report.md](round_3_report.md) | The improvement rounds |
| [benchmark_report.md](benchmark_report.md) | Hardware, latency, load and rate limits |
| [demo_guide.md](demo_guide.md) · [demo_scenes.md](demo_scenes.md) · [demo_evidence.md](demo_evidence.md) | `lear demo`: modes, keys, terminal behaviour, the scene map with data sources, and the replay bundles and transcripts |
| [demo_runbook.md](demo_runbook.md) · [demo_replay_format.md](demo_replay_format.md) · [demo_effects.md](demo_effects.md) | Operating the demo, the replay bundle format, and the effect registry (generated) |
| [demo_production_audit.md](demo_production_audit.md) · [demo_production_report.md](demo_production_report.md) · [demo_video/](demo_video/README.md) | Audit of the v1 demo, the productionisation rounds with the acceptance matrix and handoff, and the terminal video |
| `evidence/` | Machine-readable outputs, transcripts, red/green regression logs |

The Lear documentation itself is in [`../lear_backend/`](../lear_backend/README.md).
