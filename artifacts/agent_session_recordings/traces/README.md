# Traces

One folder per scenario (`01/` … `09/`). Everything is produced by real execution; nothing here is hand-edited.

| file | content |
|---|---|
| `<step>.out` | raw bytes printed by the CLI for that step (also byte-identical inside the asciicast); sha256 in steps.jsonl |
| `<step>.answer.json` | the answer JSON the CLI printed (`lear ask --json`), parsed and saved unchanged |
| `engine_trace_<id>.json` | read-only export of the engine's own persisted trace row (`queries.trace_json` in SQLite): stages, durations, counts; no document text |
| `steps.jsonl` | per step: argv, exit code, UTC start/end, wall ms, output size and sha256, question and its source |
| `events.jsonl` | normalised trace events (event_name, timestamps, session_id, trace_id, stage, status, duration_ms, component, model, provider, …); fields the agent does not expose are null |
| `<step>.scan.json`, `<step>.compare.json` | outputs of read-only helper steps (S05 corpus injection scan, S09 round-3 comparison) |
| `09/eval_run.json`, `09/eval_summary.md`, `09/bench.json` | the evaluation run and benchmark written by the agent's own `lear eval run` and `lear bench` |

Sanitisation: child commands ran with RAG_*/OLLAMA_* variables removed (defaults apply) except those a step sets explicitly; no environment values, credentials or prompts are written. See `../recording_verification.md` §5 for the scan.
