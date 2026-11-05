"""Lear backend engineering assistant.

A backend-only, strict-local CLI agent built on the ragformation engine. It
answers questions about the Lear backend repository from generated,
evidence-backed documentation (``docs/lear_backend/``) and keeps every model
call on a loopback Ollama endpoint:

* ``settings``      – strict-local configuration and model-size caps
* ``ollama``        – a small HTTP client used for real probes (no fakes)
* ``doctor``        – live readiness checks (version, tags, show, embed, stream, JSON)
* ``inventory``     – mechanical source inventory of a pinned Lear checkout
* ``docgen``        – deterministic reference docs with ``path:line`` evidence
* ``docverify``     – link / source-path / commit / secret / duplicate checks
* ``indexer``       – Lear documentation index (separate namespace) + chunk manifest
* ``sessions``      – SQLite chat sessions (isolation, expiry, delete)
* ``compaction``    – deterministic, versioned conversation compaction
* ``memory``        – scoped long-term memory with provenance and a secret guard
* ``shared_memory`` – read-only shared-memory connector (local directory)
* ``ratelimit``     – per-user / per-session windows and a concurrency cap
* ``prompts``       – the versioned Lear system prompt
* ``agent``         – the service layer: context assembly, status mapping, timing
* ``evalset`` / ``judge`` / ``evaluate`` – golden set, local judge, eval runner
* ``bench``         – dummy-user load harness with TTFT / ITL percentiles
* ``cli``           – ``python -m ragformation lear ...``

Nothing in this package changes the existing engine defaults, HTTP API or UI.
"""

LEAR_AGENT_VERSION = "0.1.0"
