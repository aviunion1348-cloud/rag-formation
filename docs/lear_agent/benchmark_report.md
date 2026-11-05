# Benchmark report

**Headline: the qualifying benchmark is BLOCKED.** No Ollama runtime is installed, and it cannot be installed here: Ollama's release assets and model registry are not reachable from this sandbox (see [audit_environment.md](audit_environment.md)). So nothing below is a time-to-first-token (TTFT), inter-token latency (ITL) or tokens-per-second figure for a model.

What was measured is the **service-layer pipeline** on the labelled offline baseline: hashing embeddings plus extractive answers. That covers rate limiting, the concurrency gate, sessions, retrieval, context assembly, extractive "generation", citation mapping and streaming callbacks. The bench prints `PIPELINE-ONLY timings (offline baseline: no LLM decode; not model latency)` and `qualifying: false` on every run.

## Machine and setup

| Item | Value |
|---|---|
| Machine | 2 vCPU, 3.8 GiB RAM, no GPU; Linux 6.1 x86_64; Python 3.11.2 |
| Index | `data/lear/index-baseline`: 355 chunks from 19 docs, fingerprint `hashing:hashing-lexical-v1:d512:…` |
| Questions | 12 dev-split questions without history, excluding unanswerable and injection cases (`ragformation/lear/bench.py`, `default_questions`) |
| Users | Dummy users `bench-u1`…`bench-uN`, one thread and one session each, all requests through `LearAgent.ask` with real rate limits |
| Warm-up | One untimed request before measuring (`warmup: ok`) |
| Limits | 30 requests per user per minute, 20 per session per minute, max 2 concurrent, queue timeout 30 s |
| Timing source | TTFT and ITL come from per-token callback timestamps; stage timings come from the engine's per-query metrics |

Commands (run on 2026-11-05 at about 18:55 UTC):

```bash
python3 -m ragformation lear --backend baseline bench --users 1,2,5,10 --requests 3 \
  --out data/lear/bench/official-2026-11-06
RAG_LEAR_RATE_SESSION_PER_MIN=2 python3 -m ragformation lear --backend baseline bench \
  --users 5 --requests 4 --out data/lear/bench/ratelimit-2026-11-06.json
```

Raw JSON, with one record per request including failures: [evidence/bench_baseline_users_1_2_5_10_2026-11-05.json](evidence/bench_baseline_users_1_2_5_10_2026-11-05.json) and [evidence/bench_baseline_ratelimit_session2_2026-11-05.json](evidence/bench_baseline_ratelimit_session2_2026-11-05.json). The output file names under `data/` carry the IST date (2026-11-06); the evidence copies carry the UTC date.

## Results: pipeline only, milliseconds

| Users | Requests | OK | 429 | Errors | Throughput (req/s) | First-token mean / p50 / p85 / p99 | Total mean / p50 / p85 / p99 | Retrieval p50 / p99 | Extractive generation p50 / p99 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 3 | 0 | 0 | 27.0 | 29.9 / 30.9 / 31.4 / 31.4 | 33.7 / 34.6 / 36.1 / 36.1 | 13.0 / 13.8 | 3.9 / 5.0 |
| 2 | 6 | 6 | 0 | 0 | 26.2 | 56.1 / 50.5 / 80.4 / 80.4 | 70.6 / 67.6 / 85.6 / 85.6 | 29.4 / 40.9 | 9.0 / 28.9 |
| 5 | 15 | 15 | 0 | 0 | 30.3 | 94.4 / 53.2 / 226.3 / 417.6 | 105.2 / 62.0 / 236.4 / 420.8 | 19.8 / 27.2 | 6.5 / 25.3 |
| 10 | 30 | 30 | 0 | 0 | 32.0 | 169.5 / 47.4 / 422.3 / 806.6 | 179.8 / 63.3 / 428.7 / 810.3 | 19.9 / 36.2 | 5.4 / 25.0 |

How to read this:
- **Small samples.** With 3–30 requests per level, p85 and p99 are close to or equal to the maximum. Treat them as indicative only.
- **Concurrency.** The gate admits 2 requests at a time. From 5 users up, the median stays near 50–65 ms while the tail grows with queue depth: p99 is 0.42 s at 5 users and 0.81 s at 10. That is the limiter working as configured, not slower retrieval, whose p99 stays at or below 41 ms at every level.
- **Inter-token gaps** (ITL p50 0.08–0.09 ms) and the "tokens/s" values in the JSON (about 10⁴ per second) measure how fast the extractive answerer hands words to the callback. They say nothing about model decode speed, and the CLI prints `tok/s n/a` for this backend.

## Rate limits under load

Run with 5 users, 4 requests each, and the per-session limit lowered to 2 per minute:

| Requests | OK | Rate limited | Errors | Detail |
|---|---|---|---|---|
| 20 | 10 | 10 | 0 | Every user got exactly 2 answers, then 2 refusals: `per-session request limit reached`, `retryAfterS` ≈ 59.9 |

- Rejected requests stay in the results (`statusCounts: GROUNDED 10, RATE_LIMITED 10`). Rejection is decided before retrieval and takes about 0.05 ms.
- Latency of the accepted requests: p50 54.9 ms, p99 278.6 ms.
- Unit tests check the same limiter with a fake clock (`tests/test_lear_core.py`), including the window, concurrency and the input caps.

## What is needed for the qualifying benchmark

On a machine with Ollama on loopback and the three models pulled (`nomic-embed-text`, `qwen2.5:1.5b`, `llama3.2:1b`):

```bash
python3 -m ragformation lear doctor                      # must exit 0
python3 -m ragformation lear index rebuild --full        # re-embed with nomic-embed-text
python3 -m ragformation lear bench --users 1,2,5,10 --requests 5
```

The same JSON then carries:
- real TTFT, and ITL from token timestamps;
- tokens per second, computed as Ollama's `eval_count` divided by the measured generation time (which includes prompt processing), plus a decode rate from stream-event timestamps;
- per-stage timings;
- the label `qualifying: true`. A CPU-only machine like this one would likely need a lower `RAG_LEAR_MAX_CONCURRENT`; that has not been measured.
