# Architecture

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Labels: implemented, configured, tested, inferred.

Lear is one Python package, `prash`, with two front doors that share the same internals: the `prash` command-line
interface and a FastAPI server used by the desktop app.

## Component map

```text
               prash CLI (prash/cli.py)                 Desktop API (prash/server.py, FastAPI)
                 |  argparse subcommands                  |  REST + /ws/events, CORS, background loops
                 v                                        v
   +----------------------------+          +-------------------------------+
   | Dispatcher (dispatch.py)   |<---------| /api/chat/execute re-enters   |
   |  plan -> circuit check ->  |          | the CLI parser in-process     |
   |  permission decision ->    |          +-------------------------------+
   |  prompt -> execute ->      |
   |  verify -> audit           |
   +----------------------------+
      |            |            |
      v            v            v
 permissions.py  circuit_breaker.py  audit.py        credentials.py (local .env, PRASH_SECRET_*)
      |
      v
 actions/* (ActionSpec + plan/execute/verify)  --->  connectors/* (authenticate, locate, logs, state, watch, stats)
      ^
      |
 fix.py  <---  brain/ (diagnosis_agent, kimi_client, schemas, multi_diagnosis, correlation, local_memory)
 watcher.py ---> notifications.py (Slack, Discord, email, WhatsApp)
 repl.py / intent.py / tui.py  ---> re-invoke the same argparse parser
```

## Layers

### Entry points

- **[configured]** The `prash` console script points at `prash.cli:main`. Evidence: `pyproject.toml:L56-L57`.
- **[implemented]** `main` builds the argparse parser, prints the masthead, honours `--env-file` by setting `PRASH_ENV`, and dispatches to the subcommand handler stored in `args.func`. Evidence: `prash/cli.py:L1346-L1356`.
- **[implemented]** The parser defines 14 subcommands: run, fix, investigate, stats, logs, actions, audit, config, circuit, watch, notify, tui, repl and setup. Evidence: `prash/cli.py:L1237-L1343`.
- **[implemented]** The desktop server is a FastAPI application titled "Prash Desktop API", version 2.0.0, with a lifespan hook. Evidence: `prash/server.py:L397`.

### Orchestration: the dispatcher

- **[implemented]** `Dispatcher.run` resolves the action, builds its plan, checks the circuit breaker, asks the permission engine for a decision, prompts when needed, executes, verifies successful results and appends an audit entry for every outcome. Evidence: `prash/dispatch.py:L94-L175`.
- **[implemented]** The CLI builds one dispatcher per invocation with the persisted circuit breaker and registers 30 action classes. Evidence: `prash/cli.py:L307-L344`.
- **[implemented]** Dry runs never call `execute`; they plan, consult the permission engine, and report refused or a prepared plan. Evidence: `prash/dispatch.py:L177-L209`.

### Policy: permissions, circuit breaker, audit

- **[implemented]** `decide(mode, risk_tier, environment, grant)` returns ALLOW, PROMPT or REFUSE; the NEVER tier is always refused and the APPROVAL tier needs an explicit per-action grant. Evidence: `prash/permissions.py:L37-L64`.
- **[implemented]** The circuit breaker opens for a resource after `max_actions` executions inside `window_seconds` and keeps its state in a JSON file so the cap survives restarts. Evidence: `prash/circuit_breaker.py:L40-L100`.
- **[implemented]** The audit log appends one JSON object per line and is never rewritten in place. Evidence: `prash/audit.py:L27-L65`.

### Actions and connectors

- **[implemented]** Every action implements the `Action` contract: a static `ActionSpec` (id, summary, risk tier, reversibility) plus `plan`, `execute`, `verify` and a default `dry_run`. Evidence: `prash/actions/contract.py:L58-L71`, `prash/actions/contract.py:L133-L161`.
- **[implemented]** Connectors share one interface: `authenticate` and `locate` are required; `fetch_logs`, `poll_state`, `watch` and `get_stats` are optional and raise `NotImplementedError` by default. Evidence: `prash/connectors/base.py:L75-L113`.
- **[implemented]** The CLI maps 12 provider names to connector classes in `PROVIDERS` (github, gitlab, vercel, aws, azure, gcp, datadog, grafana, pagerduty, snyk, gitleaks, terraform); Kubernetes is handled by its own connector module outside this map. Evidence: `prash/cli.py:L89-L102`, `prash/connectors/kubernetes.py:L128`.

### Diagnosis brain

- **[implemented]** The brain was ported from the earlier hosted backend: diagnosis agent, model client, schemas and repo memory. Evidence: `prash/brain/__init__.py:L1-L11`.
- **[implemented]** `diagnose_failure` produces a validated `Diagnosis` from CI logs through the configured primary model and raises `DiagnosisValidationError` when no valid structured output can be produced. Evidence: `prash/brain/diagnosis_agent.py:L1523-L1545`.
- **[implemented]** Model access goes through OpenAI-compatible clients for DeepSeek and Kimi, built lazily from environment variables, with a cross-provider fallback. Evidence: `prash/brain/kimi_client.py:L1-L22`, `prash/brain/kimi_client.py:L69-L139`.
- **[implemented]** The diagnosis system prompt is a single large constant (lines 300 to 1262) and the structured answer is requested through a tool schema named in `DIAGNOSIS_TOOL`. Evidence: `prash/brain/diagnosis_agent.py:L300-L1262`, `prash/brain/diagnosis_agent.py:L41-L295`.
- **[implemented]** `fix.py` is the seam between a diagnosis and the dispatcher: it maps the brain's `recommended_action` to a registered action id. Evidence: `prash/fix.py:L1-L12`, `prash/fix.py:L59-L84`.

### Interactive and background surfaces

- **[implemented]** The REPL re-invokes the existing argparse parser for each line instead of defining a new command set. Evidence: `prash/repl.py:L1-L20`.
- **[implemented]** Free text is resolved by a deterministic fast path first and an LLM fallback second. Evidence: `prash/intent.py:L370-L382`.
- **[implemented]** `prash watch` is a foreground poll loop that notifies only on new or changed problems. Evidence: `prash/watcher.py:L1-L15`, `prash/watcher.py:L160-L188`.
- **[implemented]** On startup the desktop server restores persisted watches and notifications, starts a watch-polling task and a connector health-check task, and starts an IMAP listener. Evidence: `prash/server.py:L358-L394`.

## Trust model

- **[implemented]** Credentials live in a local `.env`-style file under the user's control; the loader docstring states that Drufiy's servers never receive them. Evidence: `prash/credentials.py:L1-L10`.
- **[implemented]** Connectors hold no credentials of their own and receive them from the local `CredentialStore`. Evidence: `prash/connectors/base.py:L1-L9`.
- **[inferred]** The local-first trust model covers the CLI. The desktop server widens the surface: it accepts any origin and declares no request authentication (see authentication_authorization.md), so its exposure depends on the bind address chosen at launch.
