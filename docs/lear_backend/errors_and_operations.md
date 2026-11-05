# Errors and operations

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Covers failure handling, exit codes, launch paths and day-two tasks.

## Exit codes of the CLI

| Situation | Exit code | Label |
|---|---|---|
| `prash run`: action executed and succeeded | 0 | **[implemented]** `prash/cli.py:L369`, `prash/cli.py:L409` |
| `prash run`: refused, skipped, needs approval, errored or failed | 1 | **[implemented]** `prash/cli.py:L372-L409` |
| `prash run`: unknown action id | 2 | **[implemented]** `prash/cli.py:L356-L358` |
| `prash run`: an action needs a secret that was not supplied | 3 | **[implemented]** `prash/cli.py:L365-L367` |
| `prash fix`: bad target shape or diagnosis error | 2 | **[implemented]** `prash/cli.py:L563-L583` |
| `prash fix --ci`: provider token missing | 3 | **[implemented]** `prash/cli.py:L478-L483` |
| `prash fix`: no automated action applies | 0 | **[implemented]** `prash/cli.py:L649-L652` |
| any subcommand interrupted with Ctrl+C | 130 | **[implemented]** `prash/cli.py:L1352-L1356` |

## Failure handling in the pipeline

- **[implemented]** An exception raised inside an action's `execute` is caught by the dispatcher and recorded as a failed result with outcome `errored`; the audit entry is still written. Evidence: `prash/dispatch.py:L155-L164`.
- **[implemented]** A `MissingSecretError` from `execute` becomes a `needs_input` result instead of a crash. Evidence: `prash/dispatch.py:L166-L168`, `prash/actions/contract.py:L164-L170`.
- **[implemented]** Verification runs only after a succeeded execution; its result is attached to the action result. Evidence: `prash/dispatch.py:L170-L175`.
- **[implemented]** When the circuit is open the action is skipped with a message to escalate to a human. Evidence: `prash/dispatch.py:L109-L119`.

## Failure handling around models and providers

- **[implemented]** `DiagnosisValidationError` signals that the model could not produce a valid structured diagnosis. Evidence: `prash/brain/kimi_client.py:L142-L143`.
- **[implemented]** Errors whose text mentions 402, 429, 503, insufficient balance, rate limits, timeouts or connection problems are treated as recoverable and trigger the cross-provider fallback. Evidence: `prash/brain/kimi_client.py:L148-L163`.
- **[implemented]** A separate check classifies timeouts, disconnections and HTTP 502/503/504/529 as transient and worth retrying. Evidence: `prash/brain/kimi_client.py:L243-L252`.
- **[implemented]** When a model returns prose instead of a tool call, the client tries to extract a JSON object from the text before failing. Evidence: `prash/brain/kimi_client.py:L165-L192`.
- **[implemented]** Notification channels and the watch loop log failures and continue; a dead webhook never stops the watcher. Evidence: `prash/notifications.py:L80-L101`, `prash/watcher.py:L80-L99`.

## Desktop server errors

- **[implemented]** `APIBridgeException` carries a machine-readable `code`, a message, an HTTP status and extra detail, and its handler returns `{"error": true, "code", "message", "detail"}`. Evidence: `prash/server.py:L408-L427`.
- **[implemented]** A second handler formats ordinary `HTTPException`s. Evidence: `prash/server.py:L429-L440`.
- **[tested]** In the 2026-11-05 run the streaming chat endpoint answered with `Bridge Error: 'cpu_pct'` in one test, showing that some internal errors surface to clients as raw exception text. Evidence: `tests/test_desktop_api.py:L785`.

## Launching and running

- **[configured]** `python -m prash.server` serves on `127.0.0.1:8000` with auto-reload enabled. Evidence: `prash/server.py:L3871-L3873`.
- **[configured]** `lear.bat` and `lear.ps1` start uvicorn on `127.0.0.1:8000` and wait for `/api/system/version` to answer before continuing. Evidence: `lear.bat:L79-L93`, `lear.ps1:L77-L86`.
- **[configured]** `npm run dev:server` starts uvicorn on `0.0.0.0:8000` with `--reload`. Evidence: `package.json:L9`.
- **[inferred]** Auto-reload is a development setting; none of the launch paths in the repository describes a hardened deployment.

## Day-two operations

- **[implemented]** `prash audit --tail N` lists the most recent audit entries (50 by default). Evidence: `prash/cli.py:L858-L877`.
- **[implemented]** `prash circuit status` lists open circuits and `prash circuit reset [resource]` clears them. Evidence: `prash/cli.py:L1198-L1218`.
- **[implemented]** `prash config` confirms which credentials file is in use and which keys it defines. Evidence: `prash/cli.py:L880-L894`.
- **[implemented]** `prash actions` lists the registered actions with their risk tiers. Evidence: `prash/cli.py:L840-L855`.
- **[implemented]** Model calls are logged locally with token usage and an estimated cost from a per-million-token price table. Evidence: `prash/brain/kimi_client.py:L667-L710`.
