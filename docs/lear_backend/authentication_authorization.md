# Authentication and authorization

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Lear has no user accounts; authorization means the permission engine that gates actions.

## Identity and credentials

- **[implemented]** Lear has no login or user model; the operator is whoever runs the CLI, and provider access comes from credentials in a local file. Evidence: `prash/credentials.py:L1-L10`, `prash/credentials.py:L28-L36`.
- **[implemented]** Connectors authenticate to their provider with credentials handed over by the local store; `authenticate()` returns a boolean and must not raise for bad credentials. Evidence: `prash/connectors/base.py:L75-L90`.
- **[implemented]** Secrets requested at runtime are stored as `PRASH_SECRET_<name>` entries and exposed to actions through `ActionContext.secrets`. Evidence: `prash/credentials.py:L21`, `prash/credentials.py:L52-L66`.
- **[implemented]** `prash config` lists the path of the credentials file and the key names it contains, never the values. Evidence: `prash/cli.py:L880-L894`.
- **[implemented]** The setup wizard masks values whose key looks secret when it displays current settings. Evidence: `prash/setup.py:L69-L78`.

## Permission engine

- **[implemented]** Five permission modes exist: `read-only`, `ask`, `auto-safe`, `environment-scoped` and `bypass`. Evidence: `prash/permissions.py:L26-L31`.
- **[implemented]** `decide` refuses NEVER-tier actions in every mode, refuses everything in read-only mode, and prompts for everything in ask mode. Evidence: `prash/permissions.py:L37-L53`.
- **[implemented]** For SAFE-tier actions, auto-safe and bypass allow; environment-scoped allows except in production, where it prompts; other modes prompt. Evidence: `prash/permissions.py:L55-L60`.
- **[implemented]** For APPROVAL-tier actions the verdict is ALLOW only with an explicit per-action grant and PROMPT otherwise, in bypass mode too. Evidence: `prash/permissions.py:L62-L64`.

Decision table (what `decide` returns):

| Mode | SAFE tier | APPROVAL tier | NEVER tier | Label |
|---|---|---|---|---|
| read-only | refuse | refuse | refuse | **[implemented]** `prash/permissions.py:L49-L53` |
| ask | prompt | prompt (allow with grant) | refuse | **[implemented]** `prash/permissions.py:L53-L64` |
| auto-safe | allow | prompt (allow with grant) | refuse | **[implemented]** `prash/permissions.py:L55-L64` |
| environment-scoped | allow; prompt in production | prompt (allow with grant) | refuse | **[implemented]** `prash/permissions.py:L57-L64` |
| bypass | allow | prompt (allow with grant) | refuse | **[implemented]** `prash/permissions.py:L55-L64` |

- **[tested]** Each cell of that table has a unit test, including NEVER refused even with a grant in bypass mode and approval-tier prompting in bypass mode without a grant. Evidence: `tests/test_permissions.py:L8-L53`.
- **[implemented]** Every action spec at the pinned commit is SAFE (12) or APPROVAL (19); none uses the NEVER tier (per-spec evidence in [the action catalogue](reference/actions.md)). The CLI registers 30 of the 31 specs. Evidence: `prash/cli.py:L307-L344`.

## How a prompt becomes a grant

- **[implemented]** When the decision is PROMPT and no ask function is supplied (non-interactive use), the dispatcher returns NEEDS_APPROVAL without executing. Evidence: `prash/dispatch.py:L136-L144`.
- **[implemented]** When the human declines, the run is recorded as SKIPPED; when they accept, the action executes with the grant recorded as used. Evidence: `prash/dispatch.py:L145-L153`.
- **[implemented]** The CLI prompt shows the target, environment and plan, defaults to "n", and treats end-of-input or Ctrl+C as a decline. Evidence: `prash/cli.py:L193-L219`.
- **[implemented]** The TUI and REPL can install their own interaction object per thread, which the prompt helper uses instead of stdin. Evidence: `prash/cli.py:L178-L190`, `prash/cli.py:L193-L196`.
- **[implemented]** `--grant` sets `ActionContext.grant`, which lets an APPROVAL-tier action through without a prompt. Evidence: `prash/cli.py:L1254`, `prash/cli.py:L277`.

## Dry runs

- **[implemented]** A dry run consults the permission engine but never calls `execute`; a refusal is reported as refused instead of showing a plan. Evidence: `prash/dispatch.py:L177-L209`.
- **[tested]** Dry runs under read-only mode are refused, dry runs under ask mode still show the plan, and dry runs do not count toward the circuit breaker. Evidence: `tests/test_actions.py:L187-L217`, `tests/test_actions.py:L854-L861`.

## Circuit breaker

- **[configured]** Defaults: at most 5 executions per resource in a 60-second window, overridable through `PRASH_CIRCUIT_MAX_ACTIONS`, `PRASH_CIRCUIT_WINDOW_SECONDS` and `PRASH_CIRCUIT_STATE_PATH`. Evidence: `prash/circuit_breaker.py:L28-L30`, `prash/circuit_breaker.py:L47-L53`.
- **[implemented]** When the circuit is open for the target resource, the dispatcher skips the action, tells the operator to escalate to a human, and writes an audit entry. Evidence: `prash/dispatch.py:L109-L119`.
- **[implemented]** `prash circuit reset [resource]` clears one resource or all of them. Evidence: `prash/cli.py:L1198-L1218`, `prash/circuit_breaker.py:L90-L96`.
- **[tested]** Tests cover the cap, per-resource isolation, window expiry, reset and persistence across instances. Evidence: `tests/test_circuit_breaker.py:L10-L54`.

## Audit trail

- **[implemented]** The dispatcher logs refused, skipped, needs-approval, errored and executed outcomes with action id, risk tier, mode, decision, result, environment and whether a grant was used. Evidence: `prash/dispatch.py:L215-L227`, `prash/audit.py:L36-L65`.
- **[tested]** Audit tests check entry creation, sequence increments, the read limit and that the file holds append-only JSON lines. Evidence: `tests/test_audit.py:L12-L45`.
- **[tested]** A refused read-only run still produces an audit record. Evidence: `tests/test_actions.py:L818-L824`.

## Desktop API exposure

- **[implemented]** The FastAPI app allows every origin with credentials. Evidence: `prash/server.py:L399-L405`.
- **[inferred]** No route declares an authentication dependency and no authentication middleware is installed (re-checked across all 85 route decorators by `lear docs verify`), so any client that reaches the port can drive the API.
- **[configured]** Running the module directly binds `127.0.0.1:8000`, and the Windows launchers also bind 127.0.0.1. Evidence: `prash/server.py:L3871-L3873`, `lear.bat:L80`, `lear.ps1:L78`.
- **[configured]** The `dev:server` npm script binds `0.0.0.0:8000` with auto-reload, which exposes the unauthenticated API to the local network. Evidence: `package.json:L9`.
- **[implemented]** While validating new connector credentials, the server exposes only the candidate values to the connector's environment under a lock and restores the previous values afterwards. Evidence: `prash/server.py:L188-L209`.

### Chat-initiated actions are pre-approved

- **[implemented]** `POST /api/chat/execute` replaces `cli.CliAsk` with an ask object that always returns true for the duration of the call. Evidence: `prash/server.py:L2705-L2726`.
- **[implemented]** The code comment records this as a product decision of 2026-09-17: the chat UI click is treated as the human approval. Evidence: `prash/server.py:L2696-L2704`.
- **[tested]** A desktop API test asserts that an approval-tier action sent through this endpoint is not reported as declined or as needing approval. Evidence: `tests/test_desktop_api.py:L724-L749`.
- **[implemented]** The endpoint's own audit entry is written with risk tier SAFE and mode ASK whatever the real action was. Evidence: `prash/server.py:L2732-L2745`.
- **[inferred]** `cli.CliAsk` is a module attribute, so the swap is visible to every thread for the duration of the call; a concurrent CLI-style prompt in the same server process would also be auto-approved. The permission engine itself is unchanged: NEVER-tier and read-only refusals still apply.

### Demo incident approvals

- **[implemented]** `GET /api/slack/status` looks up a public ngrok tunnel on `127.0.0.1:4040` and returns the public Slack events, interactivity and slash-command URLs built on it. Evidence: `prash/server.py:L3713-L3735`.
- **[implemented]** `POST /api/slack/interactivity` parses the form `payload` and, for an action id containing "approve", calls `approve_incident` and replies that the fix was approved and applied. Evidence: `prash/server.py:L3819-L3868`.
- **[implemented]** `/api/incident/{incident_id}/approve` accepts a plain GET (the one-click link used in incident emails) and approves the remediation without any token in the link. Evidence: `prash/server.py:L3511-L3533`.
- **[inferred]** Because approval happens on GET, anything that fetches the link (a mail scanner, a link preview, a prefetching browser) can trigger the remediation.
- **[inferred]** No module under `prash/` checks a Slack request signature (no `X-Slack-Signature`, signing secret, `hmac` or `compare_digest` use), so the interactivity endpoint trusts any caller that can reach it.
- **[implemented]** Incident remediation patches the demo service's ConfigMap and also attempts the same patch, followed by a pod deletion, with `kubectl` against a live cluster when one is reachable. Evidence: `prash/incident_manager.py:L173-L259`, `prash/incident_manager.py:L218-L226`.
- **[inferred]** That remediation path calls `kubectl` through `subprocess` directly, outside the dispatcher, so it is not gated by the permission engine, the circuit breaker or the dispatcher's audit log.
