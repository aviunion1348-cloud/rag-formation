# ADR 0003 — Embedding providers with an honest lexical fallback

* Status: accepted
* Date: 2026-11-05

## Context

The sketch names a local model server (Ollama) for embeddings. No model server was
reachable in this environment, and a RAG demo with a silently stubbed embedder is
a lie. Yet the pipeline must still be exercisable end-to-end.

## Decision

One interface, three implementations, selected by `RAG_EMBED_PROVIDER`
(`auto` by default):

| Provider | Behaviour | `semantic` flag |
|---|---|---|
| `ollama` | `POST /api/embeddings` with `nomic-embed-text`; batched, retried, timeout-bounded | `true` |
| `openai` | `POST /v1/embeddings` with `text-embedding-3-small`; key required, never logged | `true` |
| `hashing` | Deterministic 512-dim signed feature hash over tokens and character trigrams | `false` |

Rules:

1. **The flag travels with the data.** `EmbedderInfo.semantic` is stored in the
   index fingerprint, exposed by `/api/health` and `doctor`, and printed by
   `stats`. The CLI says "lexical-only embeddings" instead of "semantic search".
2. **`auto` may fall back; `ollama` may not.** With an explicit provider, an
   unreachable server is a hard error (`RagError` with the command to run), so a
   misconfigured production deployment cannot quietly degrade.
3. **Model identity is part of the index.** Vectors record `model` and `dim`;
   ingesting with a different model is refused (`embedding_mismatch`) rather than
   mixing incompatible spaces. `rebuild --force` re-embeds intentionally.
4. **Cache is model-scoped.** `CachedEmbedder` keys on `(model, sha256(text))`, so
   switching models cannot serve stale vectors.

## Consequences

Good: the full path (chunk → embed → store → cosine → RRF → answer) is always real;
the fallback is disclosed everywhere; provider swaps are one env var.

Costs: hashing embeddings are lexical, so paraphrase recall is not demonstrated
(`docs/evaluation.md` §6). Real quality numbers require an Ollama or OpenAI run.

## Revisit when

a live embedder is available: re-run `rebuild --force`, then re-calibrate the
abstention threshold — the 0.212/0.468 gap in `docs/evaluation.md` §4 is specific
to hashing.
