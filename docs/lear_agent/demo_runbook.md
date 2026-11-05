# `lear demo` runbook

How to run, check, re-record and recover the terminal demo. The user-facing
guide is [demo_guide.md](demo_guide.md); this page is for the person
operating it in front of an examiner.

## 0. Get the project

- **One click:** download the
  [project ZIP](https://github.com/aviunion1348-cloud/rag-formation/archive/refs/heads/arena/227b3998-rag-formation.zip)
  (this branch) and unzip it.
- **Or clone it:** `git clone https://github.com/aviunion1348-cloud/rag-formation.git`
- **Requirements:** Python 3.9 or later; nothing else for replay and the
  baseline. The Lear source checkout is optional; it enables the full docs
  checks.

```bash
cd rag-formation
python3 -m pip install -e .            # optional: puts `ragformation` on PATH
```

## 1. Pre-flight (2 minutes)

| Check | Command | Expected |
|---|---|---|
| Tests | `python3 -m unittest discover -s tests` | `Ran 256 tests … OK` (about 40 s) |
| Replay integrity | `python3 -m ragformation lear demo replay --plain --scene 0` | `bundle sha256 verified`, exit 0 |
| Terminal | `echo $TERM; tput cols` | `xterm-256color` (or similar), at least 100 columns |
| Lear checkout (optional) | `git -C ../lear-backend rev-parse HEAD` and `git -C ../lear-backend branch --show-current` | `60cf9abf…`, and `main`, **not** a detached HEAD (see section 6) |

## 2. The 5-minute examiner path

```bash
python3 -m ragformation lear demo live                                   # 1. real model path: stops at the gate, exit 6
python3 -m ragformation lear demo replay                                 # 2. recorded run, any machine, exit 0
python3 -m ragformation lear --backend baseline demo director \
        --source ../lear-backend                                         # 3. live offline pipeline, keys below, exit 0
python3 -m ragformation lear --backend baseline demo examiner --plain    # 4. compact facts + commands + sources
python3 -m ragformation lear demo effects --preview chunk_token_strip    # 5. any effect, with labelled example data
```

**Keys in director mode.** `Enter`/`Space` next · `p` pause/resume auto ·
`b`/`←` back · `n`/`→` next · `0`–`9` jump (`1` then `1` = S11) · `r` recorded
version · `d`/`e` examiner detail · `m` model gate · `?` help · `q` quit.
Without a TTY no key is needed.

**Hands-free.** `--auto` waits `6 s × --speed` per scene; press `p` to hold a
scene.

## 3. If something goes wrong

| Symptom | Cause | Action |
|---|---|---|
| `demo live` exits 6 | No loopback Ollama, or models missing | Expected here. Show `demo replay`, or run the baseline with `--backend baseline` |
| `replay` exits 5, `checksum mismatch` | The bundle file was edited | `git checkout -- <bundle>`, or re-capture (section 5) |
| `replay` warns `evidence missing here` | Installed wheel, no `docs/` | Harmless: the scenes still render. Use a source checkout for the evidence files |
| S01 `docs build … 8/10 identical`, exit 5 | Lear checkout on a detached HEAD | `git -C ../lear-backend checkout main` (section 6) |
| S09 `EVALUATION DATA UNAVAILABLE` | Installed wheel, no `evals/` | Expected: the golden set is not distributed. Run from a source checkout |
| Colours or boxes look wrong | `TERM=dumb`, `NO_COLOR` or a non-UTF-8 locale | Use `--plain` (ASCII, no motion), or `LEAR_DEMO_ASCII=1` |
| The terminal looks odd after a crash | Not expected (the reset runs on every exit) | `reset`; please report it with the command |
| Ctrl-C | You pressed it | Exit 130 after an ANSI reset; scratch data is removed |

## 4. What the demo touches

- **Writes** only into a fresh `tempfile.mkdtemp(prefix="lear-demo-")` directory, which is removed on exit unless you pass `--keep-scratch`. Removal refuses anything that is not a real `lear-demo-*` directory directly under the system temp directory, and refuses symlinks (`safe_scratch`).
- **Never** opens `data/lear`, your index, your sessions or your memory. A test hashes a permanent data directory before and after a live run (`test_permanent_data_directory_is_byte_identical_after_a_live_demo`).
- **Never** calls Lear's incident-approval endpoints, kubectl, Slack or any deployment operation. Model traffic goes only to a loopback Ollama.

## 5. Re-capture the replay bundles

Capture from a clean tracked tree, so that the bundle records an exact commit:

```bash
git status --porcelain --untracked-files=no          # must print nothing
python3 -m ragformation lear --backend baseline demo capture --source ../lear-backend \
    --out ragformation/lear/demo_replay/lear_demo_replay_v2.json           # redacted: ships in the wheel
python3 -m ragformation lear --backend baseline demo capture --full --source ../lear-backend \
    --out docs/lear_agent/evidence/demo/lear_demo_replay_v2_full.json      # full text: repository only
```

Then regenerate the transcripts listed in [demo_evidence.md](demo_evidence.md).
Write them to `/tmp` first: redirecting into a tracked file marks the tree
dirty before the demo reads `git status`.

## 6. Lear checkout on a branch, not a detached HEAD

The S01 rebuild check regenerates the docs from your Lear checkout and
byte-compares them with the committed ones. The generated `SOURCE_PIN.json` and
`source_inventory.md` record the checkout's branch name. On a detached HEAD
that name is `HEAD`, so 2 of the 10 files differ and S01 reports a mismatch
(exit 5). Fix: `git -C ../lear-backend checkout main` (`main` is
`60cf9abf` at the pin).

## 7. Re-record the video

See [demo_video/README.md](demo_video/README.md). Recording takes about 2.5
minutes and rendering about 25 seconds; the render is deterministic.

## 8. Renderer cost

From `python3 scripts/measure_demo_render.py`
([evidence](evidence/demo/render_overhead_2026-11-06.json)), rendering all 12
recorded scenes on 2 vCPU takes:

| Mode | Cost |
|---|---|
| Static (motion off) | 6.3 ms median |
| Generating every animation frame (560 carriage-return frames) | 11.6 ms median |
| Deliberate pacing at `--speed 1` | 21.0 s in total |

`--speed 0`, `--no-animate`, `--plain`, `CI` and non-TTY output skip the
pacing.
