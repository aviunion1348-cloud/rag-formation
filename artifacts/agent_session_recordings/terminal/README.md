# Terminal recordings (asciicast v2)

Genuine recordings made with asciinema 2.4.0 at real timing (no idle-time limit). Each file holds one scenario: the recording harness prints labelled lines (▌REC, ▌STEP, ▌USER, ▌VIEW, ▌TRACE, ▌CHECK, ▌RESULT) and the exact `$ command` it runs, and everything else is the unmodified output of that command, which ran in its own pseudo-terminal. Input events are not recorded (the harness types nothing).

| file | recorded duration | events | bytes | sha256 (full in checksums.sha256) |
|---|---|---|---|---|
| 01_successful_rag_session.cast | 1.322 s | 37 | 36255 | `4b5a1b67a92ae984…` |
| 02_multiturn_memory_session.cast | 1.221 s | 94 | 82910 | `ab7ee766cb2cb625…` |
| 03_multidocument_retrieval.cast | 0.369 s | 48 | 30465 | `5bb1add82f10c03b…` |
| 04_abstention_session.cast | 0.62 s | 86 | 39417 | `c3f50565bfe1f73e…` |
| 05_guardrail_security_session.cast | 0.764 s | 80 | 79850 | `e38ff56244f81739…` |
| 06_conflict_detection_session.cast | 0.33 s | 32 | 28565 | `bd93fc2e9659f2a3…` |
| 07_context_compaction_session.cast | 2.115 s | 147 | 193667 | `33869a8ed519362c…` |
| 08_error_recovery_session.cast | 63.381 s | 254 | 40266 | `24bcd681dce78fee…` |
| 09_evaluation_benchmark_session.cast | 3.983 s | 16 | 12311 | `44a52f0240268fa5…` |

## Replay

```bash
python3 -m pip install asciinema==2.4.0
asciinema play terminal/01_successful_rag_session.cast            # real timing
asciinema play -s 0.25 terminal/01_successful_rag_session.cast    # quarter speed for reading
asciinema cat terminal/01_successful_rag_session.cast | less -R   # whole output, scrollable
```

Recordings were made at 158×40; use a terminal at least that size. Any asciicast v2 player works (for example asciinema-player in a browser). The S08 attempt-1 recording (aborted by a harness bug) is kept at `../evidence/attempts/attempt1_08_error_recovery_session.cast`.
