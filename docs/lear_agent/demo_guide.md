# `lear demo`: terminal demonstration guide

`lear demo` walks through the real Lear agent pipeline in the terminal. It
**observes the existing services**; it does not reimplement them. Every scene
gets its data from the same functions the CLI uses: `verify_docs`,
`indexer.rebuild`/`stats`, `run_doctor`, `LearAgent.ask`, `evalset.validate`,
`evaluation_runs`, `run_bench` and `build_report`
([`ragformation/lear/demo.py`](../../ragformation/lear/demo.py)). Rendering
lives in [`demo_render.py`](../../ragformation/lear/demo_render.py) and the
effect registry in [`demo_effects.py`](../../ragformation/lear/demo_effects.py).

The demo has no web UI, GUI, browser view, image path or upload flow. It never
calls Lear's incident-approval endpoints, kubectl, Slack or any deployment
operation.

**Watch it first:** [terminal recording (MP4, 2 min 14 s)](demo_video/lear_demo_terminal.mp4?raw=true).
What it shows and how it was made: [demo_video/README.md](demo_video/README.md).
Operating notes: [demo_runbook.md](demo_runbook.md).

## Examiner runbook (five commands)

```bash
python3 -m ragformation lear demo replay --plain                       # recorded evidence, no Ollama needed (exit 0)
python3 -m ragformation lear --backend baseline demo examiner --plain \
        --source /path/to/Lear-backend                                 # live baseline run in a scratch dir (exit 0)
python3 -m ragformation lear demo live                                 # real model path; here it stops at the gate (exit 6)
python3 -m ragformation lear demo                                      # director: narrated, interactive on a TTY
python3 -m ragformation lear --backend baseline demo examiner --json   # machine-readable scene records
```

Transcripts of these runs are in [`evidence/demo/`](evidence/demo/); see
[demo_evidence.md](demo_evidence.md).

## Modes

| Mode | What it does | Exit codes |
|---|---|---|
| `director` (default) | Narrated, staged scenes with motion on a capable TTY. If the live model path is blocked, each affected scene also shows the recorded bundle's version under a `RECORDED REPLAY … [LIVE BLOCKED: showing recorded evidence instead]` banner. | 0, 5, 6 |
| `live` | Real `LearAgent.ask` calls. On the default `ollama` backend without Ollama it stops after the model gate (S03), shows `LIVE MODE STOPPED AT THE DEPENDENCY GATE` and offers replay. With `--backend baseline` it runs every scene, labelled `NOT QUALIFYING`. | 0, 5, 6 |
| `replay` | Renders a recorded bundle: the full v2 capture in a source checkout, the redacted packaged one in a wheel ([format](demo_replay_format.md)). Every scene shows `RECORDED REPLAY - run <id> captured <time> at <commit>`, and every `LIVE` label becomes `RECORDED`. | 0 valid, 5 integrity failure |
| `examiner` | Compact. Facts with their source function or file, the commands to reproduce, evidence paths, exit codes and limitations. No narration and no motion. | 0, 5, 6 |
| `capture` | Runs the live scenes and writes a checksummed bundle atomically (`--out`). Evaluation questions and answers are redacted unless `--full` is given. | 0, 5, 6 |
| `effects` | Lists the effect registry (`--json` for every field). `--preview <id>[,<id>]` or `--preview all` renders recipes with labelled example data. | 0, 2 unknown id |

Exit codes ([`demo_exit_code`](../../ragformation/lear/demo.py)):

- **0:** every demonstrated behaviour matched its expected status or exit code.
- **2:** usage error, e.g. an unknown effect id.
- **5:** a behaviour did not match, a scene errored, or a replay bundle failed integrity.
- **6:** the Ollama/model gate blocked a qualifying (ollama-backend) run.
- **130:** interrupted (Ctrl-C).
- **1:** broken pipe.

The tests are `ExitCodeTests` in [`tests/test_lear_demo.py`](../../tests/test_lear_demo.py).

## Flags

| Flag | Effect |
|---|---|
| `--plain` | No colour, no motion, ASCII only. Arrows, dashes and `≤` are transliterated, not dropped. Use it for screen readers, logs and CI. |
| `--no-animate` | Keep colour but disable motion. |
| `--json` | Output is a single JSON object and nothing else. Scene output is collected under `redirect_stdout`, so stray prints cannot corrupt it. |
| `--scene 0,5,S08` | Run only the listed scenes. |
| `--source <Lear checkout>` | Enables full docs verification (11 checks plus coverage), a byte-identical docs-rebuild check, and line-range checks for citation source refs. The checkout must be on a branch ([runbook §6](demo_runbook.md)). |
| `--bundle <file>` | Replay or fallback bundle. |
| `--out <file>` | Capture destination. |
| `--full` | Capture: keep question and answer text (repository use only). |
| `--preview <ids>` | Effects: render recipes with example data. |
| `--speed <x>` | Motion and auto-dwell multiplier. `0` means instant. |
| `--auto` | Director advances by itself (`6 s × --speed` per scene; `p` pauses). |
| `--keep-scratch` | Keep the scratch directory for inspection. |

## Keys (director mode on an interactive TTY)

| Key | Action |
|---|---|
| `Enter` / `Space` | Next scene |
| `p` | Pause or resume automatic advance |
| `b` / `←` | Previous scene |
| `n` / `→` | Next scene |
| `0`–`9` | Jump to a scene; two digits typed quickly reach S10 and S11 |
| `r` | Show the recorded version of this scene |
| `d` / `e` | Re-show this scene in examiner detail (commands, sources, evidence) |
| `m` | Model gate (S03) |
| `?` | Help |
| `q` / Ctrl-C | Quit (Ctrl-C exits 130) |

Keys are read in cbreak mode with `os.read`; arrow escape sequences are parsed.
The terminal settings are restored in a `finally` block. Without an interactive
TTY no key is needed, so the demo works in CI. `PtyKeyTests` drives the real CLI
through a pseudo-terminal.

**Changed from v1.** `p` used to mean "previous" and `Space` switched to auto.

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

- **Width** is clamped to 40–120 columns and re-measured at every scene boundary.
- **Long lines** wrap without truncation.
- **ANSI reset** is written on every exit path: success, exception, Ctrl-C (exit 130) and broken pipe.
- **Cursor:** never hidden.

## Data safety

- **Scratch only.** Live scenes run in `tempfile.mkdtemp(prefix="lear-demo-")` via `dataclasses.replace(settings, data_dir=<scratch>/data, agent_db_path=None)`. The demo never opens `data/lear`.
- **Guarded removal.** `safe_scratch` deletes only a real `lear-demo-*` directory directly under the system temp directory, and never a symlink (`test_scratch_deletion_refuses_anything_but_lear_demo_temp_dirs`).
- **Permanent data untouched.** A test hashes a permanent data directory, including a SQLite file, before and after a live run.
- **Isolated users and sessions.** `demo-examiner`, `demo-other` and `demo-rl`, in a scratch SQLite database.
- **Injection test.** The hostile page exists only in a scratch copy of the corpus.
- **Replay sanitisation and redaction.** Scratch paths become `<scratch>`, home becomes `~`, and credential-shaped strings become `[REDACTED]`. Evaluation question and answer text is replaced by tagged placeholders unless `--full` is given.
- **Loopback only.** The non-loopback refusal in `LearSettings` is not bypassed.

## Effects

The registry `lear-demo-effects-v2` has **118 recipes in 40 families** (31
layouts × 11 motions), listed with every field in [demo_effects.md](demo_effects.md).

- **Distinct:** each recipe shows a different real value.
- **Static equivalent:** every recipe's static form is its final frame.
- **No typing of extractive output.** Text is typed only from a real model stream.
- **No fake gauges.** Values that were never measured are shown as `UNAVAILABLE`.

The previous guide's "36 effects" was an overclaim (20 drawing methods); see
[demo_production_audit.md](demo_production_audit.md).

## Recording

- **The committed video:** [demo_video/](demo_video/README.md) holds a real pty recording (`.cast`) and its MP4 rendering.
- **Re-record:** `python3 scripts/record_demo_video.py --source ../lear-backend`, then `scripts/render_demo_video.py` (optional tooling).
- **Plain transcript:** `… demo examiner --plain > transcript.txt`.
- **JSON log:** `… demo examiner --json > demo.json`.

## Known limitations

- **No qualifying path.** The qualifying path (Ollama embedder, LLM and judge) cannot run in the recorded environment. Live model scenes are blocked (exit 6), shown as baseline, or shown from the recorded bundle. Nothing here is a qualifying result ([final_report.md](final_report.md)).
- **Partial core timing.** Lifecycle spans are wall-clock intervals of one request. Inside the core, only retrieval and generation are timed; `core.other` is derived.
- **No per-channel ranks.** The engine does not expose dense, BM25 or RRF ranks, so they are `UNAVAILABLE — CORE ENGINE DOES NOT EXPOSE THIS STAGE`.
- **Baseline gaps.** On the baseline, CONFLICTED is not detected for lear-059 and PARTIAL is never produced. Both stay listed in S08 and S11.
