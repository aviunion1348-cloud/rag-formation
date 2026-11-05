# `lear demo`: terminal demonstration guide

`lear demo` walks through the real Lear agent pipeline in the terminal. It
**observes the existing services**; it does not reimplement them. Every scene
gets its data from the same functions the CLI uses: `verify_docs`,
`indexer.rebuild`/`stats`, `run_doctor`, `LearAgent.ask`, `evalset.validate`,
`evaluation_runs`, `run_bench` and `build_report`
([`ragformation/lear/demo.py`](../../ragformation/lear/demo.py)). Rendering is
kept in a separate module,
[`ragformation/lear/demo_render.py`](../../ragformation/lear/demo_render.py).

The demo has no web UI, GUI, browser view, image path or upload flow. It never
calls Lear's incident-approval endpoints, kubectl, Slack or any deployment
operation.

## Examiner runbook (five commands)

```bash
python3 -m ragformation lear demo replay --plain                       # recorded evidence, no Ollama needed (exit 0)
python3 -m ragformation lear --backend baseline demo examiner --plain \
        --source /path/to/Lear-backend                                 # live baseline run in a scratch dir (exit 0)
python3 -m ragformation lear demo live                                 # real model path; here it stops at the gate (exit 6)
python3 -m ragformation lear demo                                      # director: narrated, interactive on a TTY
python3 -m ragformation lear --backend baseline demo examiner --json   # machine-readable scene records
```

Transcripts of these exact runs are in [`evidence/demo/`](evidence/demo/). The
evidence page explains each one: [demo_evidence.md](demo_evidence.md).

## Modes

| Mode | What it does | Exit codes |
|---|---|---|
| `director` (default) | Narrated, staged scenes with motion on a capable TTY. If the live model path is blocked, each affected scene also shows the recorded bundle's version, under a `RECORDED REPLAY … [LIVE BLOCKED: showing recorded evidence instead]` banner. | 0, 5, 6 |
| `live` | Real `LearAgent.ask` calls. On the default `ollama` backend without Ollama it stops after the model gate (S03), shows `LIVE MODE STOPPED AT THE DEPENDENCY GATE` and offers replay. With `--backend baseline` it runs every scene, labelled `BASELINE / NOT QUALIFYING`. | 0, 5, 6 |
| `replay` | Renders the packaged bundle [`lear_demo_replay_v1.json`](../../ragformation/lear/demo_replay/lear_demo_replay_v1.json). Every scene shows `RECORDED REPLAY - run <id> captured <time> at <commit>`, and all `LIVE` labels become `RECORDED`. | 0 valid, 5 integrity failure |
| `examiner` | Compact. Shows facts with their source function or file, the commands to reproduce, evidence paths, exit codes and limitations. No narration and no motion. | 0, 5, 6 |
| `capture` | Runs the live scenes and writes a sanitized, checksummed replay bundle (`--out`). | 0, 5, 6 |
| `effects` | Lists the motion library: trigger, grammar, timing, purpose, reduced-motion equivalent and data source. | 0 |

What the exit codes mean ([`demo_exit_code`](../../ragformation/lear/demo.py)):

- **0:** every demonstrated behaviour matched its expected status or exit code.
- **5:** a behaviour did not match, a scene errored, or a replay bundle failed integrity.
- **6:** the Ollama/model gate blocked a qualifying (ollama-backend) run.
- **130:** interrupted (Ctrl-C).
- **1:** broken pipe.

The test is `ExitCodeTests` in [`tests/test_lear_demo.py`](../../tests/test_lear_demo.py).

## Flags

| Flag | Effect |
|---|---|
| `--plain` | No colour, no motion, ASCII only. Arrows, dashes and `≤` are transliterated, not dropped. Use it for screen readers, logs and CI. |
| `--no-animate` | Keep colour but disable motion. |
| `--json` | Output is a single JSON object and nothing else. Scene output is collected under `redirect_stdout`, so stray prints cannot corrupt it. |
| `--scene 0,5,S08` | Run only the listed scenes. |
| `--source <Lear checkout>` | Enables full docs verification (11 checks plus coverage), a byte-identical docs-rebuild check, and line-range checks for citation source refs. |
| `--bundle <file>` | Replay or fallback bundle. The default is the packaged one. |
| `--out <file>` | Capture destination. |
| `--speed <x>` | Motion speed multiplier. `0` means instant. |
| `--auto` | Director mode advances without waiting for keys. |
| `--keep-scratch` | Keep the scratch directory for inspection. |

## Keys (director mode on an interactive TTY)

| Key | Action |
|---|---|
| `Enter` | Next scene |
| `Space` | Switch to auto-advance |
| `p` | Previous scene |
| `r` | Replay this scene from the recorded bundle |
| `d` / `e` | Diagnostics or evidence: re-render in examiner detail |
| `m` | Model panel (S03) |
| `?` | Help |
| `q` / Ctrl-C | Quit |

Keys are read in cbreak mode, and the terminal settings are restored in a
`finally` block (`raw_keys` in `demo_render.py`). Without an interactive TTY,
director mode runs straight through, so it works in CI.

## Terminal behaviour

[`detect_caps`](../../ragformation/lear/demo_render.py) decides what the
terminal gets:

| Condition | Result |
|---|---|
| stdout is not a TTY | No colour and no motion |
| `NO_COLOR` | No colour |
| `FORCE_COLOR` | Colour, unless `NO_COLOR`, `--plain` or `--json` is set |
| `CI` | No motion |
| `TERM=dumb` | Neither colour nor motion |
| `LEAR_DEMO_ASCII=1`, or a non-UTF-8 stream encoding | ASCII glyphs |

- **Width** is clamped to 40–120 columns and re-measured at every scene boundary, so a terminal resize takes effect on the next scene.
- **Long lines** wrap without truncation. A long path is broken across lines rather than cut.
- **ANSI reset** is written on every exit path: success, exception, Ctrl-C (exit 130) and broken pipe.

Each of these behaviours is tested in `CapabilityTests` and `ExitCodeTests`.

## Data safety

- **Scratch only.** Live scenes run in a temporary directory created with `dataclasses.replace(settings, data_dir=<scratch>/data, agent_db_path=None)`. The demo never opens `data/lear`. A test asserts that the configured data directory is never created (`test_examiner_json_runs_the_real_pipeline_in_scratch_only`).
- **Isolated users and sessions.** The demo uses `demo-examiner`, `demo-other` and `demo-rl`, each in a scratch SQLite database. It never displays another user's sessions or memory.
- **Injection test.** The hostile page lives in a scratch copy of the corpus only. The committed docs are untouched.
- **Docs rebuild check.** The rebuild writes into the scratch directory, including `agent_docs_dir`, and is then byte-compared with the committed docs.
- **Replay sanitization.** Before a bundle is written, the scratch path becomes `<scratch>`, the home directory becomes `~`, and credential-shaped strings (the `SECRET_PATTERNS` from `secrets.py`) become `[REDACTED]`. The test is `test_sanitize_redacts_secrets_and_scratch_paths`.
- **Loopback only.** The non-loopback refusal in `LearSettings` is not bypassed. S00 shows the endpoint and whether it is loopback.

## Recording a demo

There is no video. These are the supported recordings:

```bash
# plain transcript (what evidence/demo/*.txt are)
python3 -m ragformation lear --backend baseline demo examiner --plain --source <Lear checkout> > transcript.txt

# JSON event log
python3 -m ragformation lear --backend baseline demo examiner --json > demo.json

# exact terminal recording, colour and motion included (util-linux `script`)
TERM=xterm-256color script -qec "python3 -m ragformation lear --backend baseline demo director --auto" demo.typescript
```

## Motion library

The library has **36 effects**, listed by `lear demo effects`
([`evidence/demo/effects_plain.txt`](evidence/demo/effects_plain.txt)).

- **Below the 100 target, on purpose.** The task's target was ≥100 "only if usability allows". Every effect here maps to real data and has a reduced-motion equivalent. Adding effects just to reach the number would have added noise.
- **Teletype is never shown for extractive output.** Text is "typed" only when it came from a real model stream (`teletype` in `EFFECTS`). Baseline answers print at once under `A (BASELINE extractive selection, not a model stream)`.
- **No fake gauges.** Values that were never measured are shown as `not measured` or `not instrumented`.

## Known limitations

- The qualifying path (Ollama embedder, LLM and judge) cannot run in the recorded environment. Live model scenes are therefore blocked (exit 6), or shown as baseline, or shown from the recorded bundle. Nothing here is a qualifying result. See [final_report.md](final_report.md).
- Stages 1–9 and 11–12 of the request lifecycle are not separately timed, and are shown as `not instrumented`.
- The engine does not expose per-channel ranks (dense, BM25, RRF), so they are shown as `UNAVAILABLE`.
- CONFLICTED and PARTIAL statuses cannot be reproduced on the baseline. They are shown as `LIMITATION`, not as passes (see [demo_scenes.md](demo_scenes.md), S08).
