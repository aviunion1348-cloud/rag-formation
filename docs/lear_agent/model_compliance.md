# Model compliance

**Status: BLOCKED.** The configuration and the enforcement code are in place and tested against a stub Ollama. But no real model has been pulled or run on this machine, so every parameter count below is **UNVERIFIED**.
- Ollama is not installed. Its release assets (on `objects.githubusercontent.com`), `ollama.com` and `registry.ollama.ai` are all unreachable from this sandbox.
- The live doctor exits 6 with `ollama.version: FAIL` and every model check `SKIP` ([evidence/doctor_live_final_2026-11-05.json](evidence/doctor_live_final_2026-11-05.json)).

## Requirements and configuration

| Role | Requirement | Configured default (`ragformation/lear/settings.py`) | Size (public model card, not verified here) | Verified count |
|---|---|---|---|---|
| Embedder | Local Ollama, ≤200M parameters | `nomic-embed-text` (`RAG_LEAR_EMBED_MODEL`) | about 137M | **UNVERIFIED** |
| Answering LLM | Local Ollama, ≤4B parameters | `qwen2.5:1.5b` (`RAG_LEAR_LLM_MODEL`) | about 1.5B | **UNVERIFIED** |
| Judge | Local Ollama, ≤4B parameters, distinct from the LLM | `llama3.2:1b` (`RAG_LEAR_JUDGE_MODEL`) | about 1.2B, a different model family | **UNVERIFIED** |

The caps are `RAG_LEAR_EMBED_MAX_PARAMS=200000000` and `RAG_LEAR_MODEL_MAX_PARAMS=4000000000`. Raising them is possible, but `doctor` prints the caps it checked against.

## How compliance is enforced

| Rule | Mechanism | Tested by |
|---|---|---|
| Local only | `LearSettings.validate` rejects any `RAG_LEAR_OLLAMA_URL` whose host is not `127.0.0.1`, `localhost` or `[::1]`. The Lear Ollama client refuses non-loopback URLs as well. | `tests/test_lear_core.py` (loopback tests) |
| No cloud provider, no `auto` | `engine_settings()` pins the engine's providers: `embed_provider="ollama"`, `llm_provider="ollama"` and `require_semantic=True` on the `ollama` backend. Cloud API keys in the environment are never read; `doctor` lists any it sees as "present but ignored". | Core and doctor tests |
| No silent hashing for the embedder | `require_semantic=True` makes the engine fail instead of falling back to hashing. A query against an index built with a different embedding fingerprint (model, dimension, normalisation) is refused with `ERROR embedding_mismatch` until a `--full` rebuild. | `test_stub_embedding_dimension_change_is_refused_until_full_rebuild`; red/green in [evidence/embedding_mismatch_red_green.txt](evidence/embedding_mismatch_red_green.txt) |
| No silent extractive answer | If the LLM fails, the core engine falls back to extractive. On the `ollama` backend the Lear agent turns that into `ERROR generation_unavailable` (exit 6) unless `RAG_LEAR_ALLOW_EXTRACTIVE_FALLBACK=1`, in which case the answer is labelled `fallbackUsed: true, qualifying: false`. | `test_llm_failure_is_an_error_not_a_silent_extractive_answer`; red/green in [evidence/llm_failure_no_silent_fallback_red_green.txt](evidence/llm_failure_no_silent_fallback_red_green.txt) |
| Parameter counts read, not assumed | `doctor` calls `/api/show` for each model. It prefers `model_info["general.parameter_count"]` (exact), then `details.parameter_size` (rounded, labelled as such). If neither is present the model is `UNVERIFIED`, which is a required failure. | `test_stub_doctor_ready_caps_and_missing_models` (stub reports counts; missing models fail) |
| Judge distinct from LLM | `doctor` check `models.judge_distinct`; `LearSettings.validate` refuses an identical judge and LLM | `test_judge_must_differ_from_answer_model` |
| Real probes | `doctor` embeds a probe sentence (it records the dimension and fails on an empty vector), streams a short generation (first token and stream) and asks the judge for schema-constrained JSON. Each one is a required check. `--no-generation` skips the last two and marks them not required. | Stub doctor test; live run blocked |
| Labelled baselines | `--backend baseline` (hashing + extractive) prints `NOT QUALIFYING` on every answer, evaluation, bench and report, and sets `qualifying: false` in JSON | CLI and pipeline tests |

## Live doctor output on this machine

`python3 -m ragformation lear doctor --json` returned exit code **6**:

| Check | Status |
|---|---|
| `config.loopback_only` | PASS |
| `config.backend` | PASS |
| `config.no_cloud_providers` | PASS |
| `ollama.version` | **FAIL**: connection refused at `http://127.0.0.1:11434` |
| `models.installed` | SKIP |
| `models.embedder` | SKIP |
| `models.llm` | SKIP |
| `models.judge` | SKIP |
| `probe.embedding` | SKIP |
| `probe.generation_stream` | SKIP |
| `probe.judge_json` | SKIP |

## To reach PASS on a capable machine

Download roughly 2.5–3 GB of models (approximate public sizes, not measured here). Check the free disk space first; this sandbox has about 20 GB free and 3.8 GiB RAM.

```bash
ollama serve                    # loopback only (the default)
ollama pull nomic-embed-text
ollama pull qwen2.5:1.5b
ollama pull llama3.2:1b
python3 -m ragformation lear doctor --json     # expect exit 0 and exact parameter counts
python3 -m ragformation lear index rebuild --full
```

If `doctor` reports a count above a cap, swap the model through the `RAG_LEAR_*_MODEL` variables rather than raising the cap.
