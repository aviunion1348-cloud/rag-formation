# API reference

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Full per-item tables: [CLI](reference/cli.md), [HTTP](reference/http_api.md).

Lear exposes two programmatic surfaces: the `prash` command-line interface and the desktop HTTP/WebSocket API in
`prash/server.py`. This page summarises both; the generated reference pages list every argument and every route.

## The `prash` CLI

- **[implemented]** One global option, `--env-file`, overrides the local credentials file; `main` stores it in `PRASH_ENV` before any subcommand reads credentials. Evidence: `prash/cli.py:L1245`, `prash/cli.py:L1346-L1356`.

| Subcommand | Purpose | Handler | Label |
|---|---|---|---|
| `prash run <action> <resource>` | send one action through the permission pipeline | `cmd_run` `prash/cli.py:L347-L369` | **[implemented]** |
| `prash fix <target>` | diagnose a pod (or a CI run with `--ci`) and dispatch the recommended action | `cmd_fix` `prash/cli.py:L443-L671` | **[implemented]** |
| `prash investigate <resource>` | read-only connector probe (`--provider`, `--dependabot` for GitHub) | `cmd_investigate` `prash/cli.py:L696-L765` | **[implemented]** |
| `prash stats <resource>` | recent event timeline from a connector's `get_stats` | `cmd_stats` `prash/cli.py:L778-L818` | **[implemented]** |
| `prash logs <namespace>/<pod>` | read or follow pod logs (`--follow`, `--tail`) | `cmd_logs` `prash/cli.py:L1130-L1170` | **[implemented]** |
| `prash actions` | list registered actions and their risk tiers | `cmd_actions` `prash/cli.py:L840-L855` | **[implemented]** |
| `prash audit` | display the audit log (`--tail`, default 50) | `cmd_audit` `prash/cli.py:L858-L877` | **[implemented]** |
| `prash config` | list the credentials file path and the key names present | `cmd_config` `prash/cli.py:L880-L894` | **[implemented]** |
| `prash circuit status/reset [resource]` | inspect or reset the circuit breaker | `cmd_circuit` `prash/cli.py:L1198-L1218` | **[implemented]** |
| `prash watch` | poll a provider and notify on state changes | `cmd_watch` `prash/cli.py:L897-L1112` | **[implemented]** |
| `prash notify <message>` | post a message to every configured team channel | `cmd_notify` `prash/cli.py:L1115-L1127` | **[implemented]** |
| `prash tui` | open the Textual terminal app | `cmd_tui` `prash/cli.py:L1173-L1186` | **[implemented]** |
| `prash repl` | persistent interactive session | `cmd_repl` `prash/cli.py:L1189-L1195` | **[implemented]** |
| `prash setup` | interactive configuration wizard | `cmd_setup` `prash/cli.py:L1221-L1226` | **[implemented]** |

Common options on the acting subcommands:

- **[implemented]** `--mode` selects the permission mode (read-only, ask, auto-safe, environment-scoped, bypass); when omitted, `run` and `fix` read `PRASH_PERMISSION_MODE` from the credentials file and fall back to `ask`. Evidence: `prash/cli.py:L1251`, `prash/cli.py:L351`, `prash/cli.py:L460`.
- **[implemented]** `--env` sets the target environment; the context falls back to `PRASH_ENVIRONMENT` and then `staging`. Evidence: `prash/cli.py:L1252`, `prash/cli.py:L273`.
- **[implemented]** `--dry-run` plans without touching infrastructure, `--grant` pre-grants a single action, and `--noninteractive` never prompts (a missing secret then returns NEEDS_INPUT). Evidence: `prash/cli.py:L1253-L1255`.
- **[implemented]** `prash fix --ci` diagnoses a CI run; `--provider` is github (default) or gitlab and `--run-id` names the run or pipeline. Evidence: `prash/cli.py:L1277-L1296`, `prash/cli.py:L106`.

## The desktop HTTP/WebSocket API

- **[implemented]** The FastAPI app adds `CORSMiddleware` with every origin, method and header allowed and credentials allowed. Evidence: `prash/server.py:L399-L405`.
- **[implemented]** The server file registers 87 method/path routes through 85 route decorators; two `@app.api_route` decorators accept both GET and POST. Evidence: `prash/server.py:L397-L3868`, `prash/server.py:L3511`, `prash/server.py:L3536`.
- **[inferred]** None of the 85 route decorators or handler signatures declares an authentication dependency, and no authentication middleware is added, so every route is reachable by any client that can connect to the bind address.

Route groups (counts from the generated reference):

| Group | Routes | What it covers | Label |
|---|---:|---|---|
| `/api/connectors` | 18 | list, inspect, connect, disconnect, validate, check, status, metrics, resources, watch control, widgets | **[implemented]** `prash/server.py:L566-L1246`, `prash/server.py:L2836-L2985` |
| `/api/chat` | 9 | chat, streaming chat, greeting, approved action invocation, chat sessions, file upload | **[implemented]** `prash/server.py:L2145-L2781`, `prash/server.py:L3553-L3660` |
| `/api/demo` | 16 | demo storefront and failure-injection scenarios | **[implemented]** `prash/server.py:L3050-L3473` |
| `/api/projects` | 6 | project records and status | **[implemented]** `prash/server.py:L1668-L1907` |
| `/api/slack`, `/api/email` | 7 | inbound Slack events and commands, email chat and polling | **[implemented]** `prash/server.py:L3687-L3868` |
| `/api/incident`, `/api/incidents` | 9 | incident records, incident chat, one-click approve and deny (GET or POST) | **[implemented]** `prash/server.py:L3489-L3549`, `prash/server.py:L3663-L3684` |
| `/api/config`, `/api/settings` | 4 | read and write configuration and settings | **[implemented]** `prash/server.py:L1914-L2136` |
| `/api/notifications` | 3 | list, mark read, clear | **[implemented]** `prash/server.py:L2788-L2829` |
| `/ws/events` | 1 | WebSocket event stream | **[implemented]** `prash/server.py:L1601-L1660` |

### Selected endpoints

- **[implemented]** `POST /api/connectors/{connector_id}/connect` (`connect_connector`) authenticates the submitted credentials against the provider first and persists them to the server's `.env` only on success (HTTP 401 `CONNECTOR_AUTH_FAILED` otherwise). Evidence: `prash/server.py:L704-L722`.
- **[implemented]** `POST /api/chat/execute` (`execute_chat_action`) turns a JSON `command` list (or an action id) into CLI arguments, prefixing `run` when the first word is not a known subcommand, and re-enters `cli.build_parser()` in the same process. Evidence: `prash/server.py:L2641-L2700`.
- **[implemented]** For the duration of that call the handler swaps `cli.CliAsk` for an ask object that always answers yes, so prompts raised by the permission engine are approved automatically. Evidence: `prash/server.py:L2705-L2726`.
- **[implemented]** After the call the handler appends an audit entry with risk tier SAFE and mode ASK regardless of the action's real tier. Evidence: `prash/server.py:L2732-L2745`.
- **[implemented]** `GET /api/settings` reports the permission mode from saved settings, then `PRASH_PERMISSION_MODE`, then `ask`. Evidence: `prash/server.py:L2019-L2059`, `prash/server.py:L2029`.
- **[implemented]** `/ws/events` (`websocket_events_endpoint`) is the push channel for the desktop app. Evidence: `prash/server.py:L1601-L1660`.
- **[implemented]** `GET` or `POST /api/incident/{incident_id}/approve` (`approve_incident_endpoint`) approves the incident's remediation, emails a resolved notice, and for GET returns an HTML page saying the fix was applied; `/deny` is the matching denial. Evidence: `prash/server.py:L3511-L3533`, `prash/server.py:L3536-L3549`.
- **[tested]** The Lear suite covers chat-driven execution, including that an approval-tier action is not silently declined. Evidence: `tests/test_desktop_api.py:L690-L724`.
