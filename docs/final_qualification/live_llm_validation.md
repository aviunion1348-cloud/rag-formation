# Live LLM validation (Stage D) — **BLOCKED**, with a runnable gate

## 1. Verdict

**No live language model was exercised in this qualification.** Nothing in this
release should be read as evidence about neural generation quality, hallucination
rate, TTFT with a real model, or prompt-injection resistance with a real model.

The gate that decides this is `scripts/live_provider_check.py`. It ran in this
environment and returned exit code **2** (blocked), writing
[`live_provider.json`](live_provider.json):

```json
{
  "status": "blocked",
  "ollama": {"host": "http://127.0.0.1:11434", "reachable": false,
             "error": "URLError: <urlopen error [Errno 111] Connection refused>"},
  "openai": {"keyPresent": false},
  "blockedReasons": [
    "no Ollama endpoint at http://127.0.0.1:11434 (URLerror: connection refused)",
    "no OPENAI_API_KEY in the environment",
    "no GPU and no local weights: a model cannot be installed here (no /dev/dri, no model host reachable)"
  ]
}
```

Environment facts behind that: no GPU (`nvidia-smi` absent, no `/dev/dri`), 3.9 GiB
RAM shared with 2 vCPUs, no model runtime listening, no provider key, and no
reachable model host (see [`environment.txt`](environment.txt)).

## 2. What *is* verified about the provider layer

| Item | Evidence |
| --- | --- |
| Ollama request/stream contract is exercised against a stubbed HTTP server: streaming frames, token accounting, `done_reason`, error propagation, unavailability | `tests/test_ollama_provider.py` — **6 tests, OK** (`Ran 6 tests … OK`) |
| Provider routing: `auto` prefers Ollama, falls back to the extractive provider, and a provider that is configured explicitly and then fails raises instead of silently degrading | `tests/test_engine_e2e.py`, `tests/test_guardrails.py` |
| The answer path is labelled honestly: every report, health payload and CLI output says `extractive/extractive-synthesis-v1` when that is what produced the answer | `python3 -m ragformation eval` header, `/healthz`, `doctor` |
| Token streaming plumbing (chunk callbacks, TTFT/ITL measurement) works end-to-end | `tests/test_api.py` SSE, `node integrations/qualification/node_host.mjs` event stream |

This is **stub_verified**, not live-verified. The distinction is the point.

## 3. The gate, and what it will prove when a provider exists

```
python3 scripts/live_provider_check.py --json /tmp/live.json
# exit 0 verified · 2 blocked · 3 provider unusable · 4 grading failed
```

With a reachable model it performs one real session and grades six checks:

1. the answer is non-empty and the status is not `abstained`;
2. every citation resolves to evidence actually delivered to the model;
3. the expected fact is present (a grounded answer, not a plausible one);
4. the stream produced more than one token chunk, with measurable TTFT/ITL;
5. an off-corpus question still abstains (the guardrail does not disappear when a
   neural generator is plugged in);
6. the quarantined injection fixture never turns into an answer.

To unblock, an operator needs **one** of:

```bash
# A. local model
curl -fsSL https://ollama.com/install.sh | sh      # needs a reachable model host
ollama pull llama3.2:3b
python3 scripts/live_provider_check.py

# B. hosted, OpenAI-compatible
export OPENAI_API_KEY=...                          # or a compatible base URL
export OPENAI_BASE_URL=https://api.openai.com/v1
python3 scripts/live_provider_check.py
```

Whatever answers, the rest of the qualification numbers stay attached to the
provider that produced them: every evaluation report records
`"generation": "extractive/extractive-synthesis-v1"` and a fingerprint, so a live
run is a new measurement, not an upgrade of an old one.
