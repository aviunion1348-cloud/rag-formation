# Session recording toolkit

Test instrumentation that records the **existing** Lear agent (`python3 -m ragformation lear …`) in nine
pre-registered scenarios. It changes no application file: it runs the real CLI, copies its output unmodified into a
terminal recording, exports the engine's own persisted traces read-only, and judges each scenario with checks
written before execution. The published package is `artifacts/agent_session_recordings/` (start with its
`MANAGER_HANDOFF.md`).

| file | role |
|---|---|
| `scenario_plan.json` | the plan: questions (golden cases chosen by fixed rules, or CONTROLLED inputs written in full), commands, checks, status policy. Registered before execution (commit 81c28c8). |
| `preflight.py` | read-only environment, Ollama, health and tooling checks → `PREFLIGHT_REPORT.md`, `evidence/preflight.json`, `evidence/env/` |
| `harness.py` | runs one scenario inside asciinema; each CLI step in its own pseudo-terminal; writes `traces/NN/` and `evidence/results/NN.json` |
| `record_all.sh` | one `asciinema rec` per scenario (real timing, no idle limit); refuses to overwrite an existing cast |
| `render_cast.py` | asciicast → 1920×1080 H.264: real-time part, then a paced replay of the same recording (labelled holds); also renders editorial cards |
| `render_all.sh` | renders every cast (and preserved failed attempts) with identical encoder settings |
| `make_complete.py` | `complete_agent_session.mp4`: genuine recordings plus labelled chapter cards, joined losslessly (`-c copy`) |
| `verify_package.py` | video/cast/trace integrity, consistency, security scan (`verify`, `scan`), then `checksums` (run last) |
| `build_docs.py` | writes `session_metadata.json`, `session_index.md`, `recording_verification.md`, `MANAGER_HANDOFF.md`, the folder READMEs |

## Reproduce

```bash
# recording tools (not dependencies of RAG Formation)
python3 -m venv /tmp/recvenv && /tmp/recvenv/bin/pip install asciinema==2.4.0 pyte Pillow imageio-ffmpeg

STAMP=$(date -u +%Y%m%dT%H%MZ); RUN=lear-rec-$STAMP
python3 scripts/session_recordings/preflight.py --root /tmp/$RUN --run-id $RUN
scripts/session_recordings/record_all.sh $RUN $STAMP            # all nine scenarios, in order (S01 builds the index)
scripts/session_recordings/render_all.sh $RUN
/tmp/recvenv/bin/python scripts/session_recordings/make_complete.py --run-id $RUN
/tmp/recvenv/bin/python scripts/session_recordings/verify_package.py verify
python3 scripts/session_recordings/build_docs.py --run-id $RUN
/tmp/recvenv/bin/python scripts/session_recordings/verify_package.py scan
python3 scripts/session_recordings/build_docs.py --run-id $RUN
python3 scripts/session_recordings/verify_package.py checksums  # always last
```

All agent data lives under `/tmp/$RUN/` (index, sessions, memory, eval and bench outputs); nothing is written to the
repository's `data/`. A re-recording needs a new run ID: existing casts are never overwritten, and failed attempts
belong under `evidence/attempts/`.

## Re-running where Ollama works

This plan records the environment it was registered for, where Ollama was unavailable. Re-running it unchanged on a
machine with Ollama would **not** produce LLM recordings:

- every answer-producing step (22 of the 26 `ask`/`chat` calls, plus the S09 `eval run` and `bench`) is pinned to
  `--backend baseline`; only S01 `a0` and S08 `e1`–`e3` use Ollama, to record the failure path;
- the no-LLM status caps are fixed in the plan and the harness always applies them;
- three checks (S01 `c1`, S08 `c1`, S08 `c2`) expect the Ollama dependency failure, so they would fail.

A qualifying recording needs a new plan version, committed before execution: answer steps on `--backend ollama`,
caps removed where the capability is now present, and the S08 failure path driven by a labelled injected fault (for
example an unused loopback port) instead of the missing server.
