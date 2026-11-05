# Lear backend — engineering documentation

> Source: `aviunion1348-cloud/Lear-backend-Avi`, branch `main`, commit `60cf9abf8408` (2026-10-01). Full id in [SOURCE_PIN.json](SOURCE_PIN.json).

These pages describe what the Lear backend code does at the pinned commit. They are the knowledge base of the
`python -m ragformation lear` assistant, so every factual statement points at the code that supports it.

## What Lear is

- **[implemented]** Lear is packaged as the Python distribution `prash-v2` version 0.2.0, requires Python 3.10 or newer, and installs one console script, `prash`, mapped to `prash.cli:main`. Evidence: `pyproject.toml:L5-L9`, `pyproject.toml:L56-L57`.
- **[implemented]** The package docstring calls it "Lear — the AI DevOps agent", and the CLI spine describes itself as the entry point for the local agent that loads local credentials, builds the action registry and connectors, and drives the dispatch pipeline. Evidence: `prash/__init__.py:L1-L8`, `prash/cli.py:L1-L19`.
- **[implemented]** The core idea is a permission-gated action pipeline: actions declare a risk tier, a permission engine decides allow / prompt / refuse, and every decision lands in an append-only audit log. Evidence: `prash/dispatch.py:L1-L15`, `prash/permissions.py:L1-L17`, `prash/audit.py:L1-L8`.
- **[implemented]** A second surface, a FastAPI "Prash Desktop API" server, bridges a desktop app to the same connectors and CLI. Evidence: `prash/server.py:L1-L7`, `prash/server.py:L397`.

## How to read these pages

Each claim carries one evidence label:

| Label | Meaning |
|---|---|
| implemented | read directly from code at the pinned commit |
| configured | a default, environment variable, packaging or launcher setting |
| tested | exercised by a test in the Lear repository (and, where stated, run on 2026-11-05) |
| inferred | a conclusion drawn from reading the code; no single line proves it |

Evidence is written `path:Lstart-Lend`, relative to the Lear repository root, at commit `60cf9abf8408`.
`python -m ragformation lear docs verify --source <checkout>` re-checks every reference against that commit.

<!-- lear-index: skip -->
<!-- The page list below is navigation only; it is kept out of the assistant's index. -->
## Pages

Narrative pages (written from reading the code):

- [Architecture](architecture.md) — components and how a request flows through them
- [API reference](api_reference.md) — the `prash` CLI and the desktop HTTP/WebSocket API
- [Domain model](domain_model.md) — actions, decisions, connectors, diagnoses
- [Persistence and storage](database.md) — there is no database; what is stored where
- [Authentication and authorization](authentication_authorization.md) — permission modes, risk tiers, grants, circuit breaker, audit, API exposure
- [Services and workflows](services_and_workflows.md) — `run`, `fix`, `watch`, `notify`, REPL, TUI, setup, server loops
- [Configuration](configuration.md) — credential file resolution, environment variables, defaults
- [Integrations](integrations.md) — provider connectors, model providers, notification channels
- [Errors and operations](errors_and_operations.md) — failure handling, exit codes, launchers, CI
- [Testing](testing.md) — test suite layout, CI, and the suite run against the pinned commit
- [Unknowns and gaps](unknowns_and_gaps.md) — what could not be verified, and code/doc drift

Reference pages (generated from the code with `ast`, one entry per item):

- [CLI reference](reference/cli.md) · [HTTP API reference](reference/http_api.md) · [Environment variables](reference/configuration_env.md)
- [Action catalogue](reference/actions.md) · [Connector reference](reference/connectors.md) · [Module index](reference/modules.md) · [Test suite reference](reference/tests.md)
- [Source map](source_map.md) — topic to file index

<!-- lear-index: end -->

## Scope

- **[configured]** In scope: the Python package `prash/`, `tests/`, `evals/`, packaging and launcher files at the repository root. Evidence: `pyproject.toml:L59-L60`.
- **[inferred]** Out of scope: the `desktop/` frontend (a UI, not backend behaviour) and `tasks/` planning notes. Project prose such as `README.md` and `PRASH_V2.md` is treated as a claim to check against code, not as evidence.
