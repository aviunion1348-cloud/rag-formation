# Configuration

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Every variable with its reading site: [configuration_env.md](reference/configuration_env.md).

## Where configuration comes from

- **[implemented]** The CLI reads one credentials file, chosen in this order: the path in `PRASH_ENV`, else `./.env` in the working directory, else `~/.prash/.env`. Evidence: `prash/credentials.py:L28-L36`.
- **[implemented]** `--env-file <path>` on any subcommand sets `PRASH_ENV` before the file is read. Evidence: `prash/cli.py:L1245`, `prash/cli.py:L1346-L1356`.
- **[implemented]** The file is parsed line by line as `KEY=VALUE`, skipping blank lines and `#` comments and stripping surrounding quotes; a missing file yields an empty mapping. Evidence: `prash/credentials.py:L38-L50`.
- **[implemented]** Most CLI code reads settings from that mapping rather than from the process environment. The exceptions are the Kubernetes and model keys, which their libraries read from `os.environ`. Evidence: `prash/cli.py:L120-L128`.
- **[implemented]** For those keys (`KUBECONFIG`, `KUBE_CONTEXT`, `KUBE_NAMESPACE`, the `KIMI_*` and `DEEPSEEK_*` keys and `PRIMARY_MODEL`), the CLI copies non-empty file values into the environment only when the shell has not already set them, so a shell export wins. Evidence: `prash/cli.py:L123-L128`, `prash/cli.py:L132-L156`.
- **[implemented]** The desktop server instead loads the repository-root `.env` with `override=True`, so for the server the file wins over the shell. Evidence: `prash/server.py:L52`, `prash/server.py:L56`.
- **[inferred]** CLI and server therefore resolve the same variable differently when it is set in both places, and they may read different files (`PRASH_ENV` / working directory for the CLI, the repository root for the server).

## Behaviour switches

| Variable | Default | Effect | Label |
|---|---|---|---|
| `PRASH_PERMISSION_MODE` | `ask` | permission mode when `--mode` is absent | **[configured]** `prash/cli.py:L351` |
| `PRASH_ENVIRONMENT` | `staging` | target environment when `--env` is absent | **[configured]** `prash/cli.py:L273` |
| `PRASH_MANIFEST_REPO` | none | GitHub repository for manifest fixes in `prash fix` | **[configured]** `prash/cli.py:L569` |
| `PRASH_CIRCUIT_MAX_ACTIONS` | 5 | executions allowed per resource per window | **[configured]** `prash/circuit_breaker.py:L28`, `prash/circuit_breaker.py:L47-L53` |
| `PRASH_CIRCUIT_WINDOW_SECONDS` | 60 | circuit-breaker window length | **[configured]** `prash/circuit_breaker.py:L29`, `prash/circuit_breaker.py:L47-L53` |
| `PRASH_CIRCUIT_STATE_PATH` | `.prash/circuit.json` | circuit-breaker state file | **[configured]** `prash/circuit_breaker.py:L30` |
| `PRASH_AUDIT_LOG_PATH` | `.prash/audit.log` | audit log location | **[configured]** `prash/audit.py:L28-L29` |
| `PRASH_MEMORY_PATH` | `.prash/memory.json` | brain's local repo memory | **[configured]** `prash/brain/local_memory.py:L18-L20` |
| `PRASH_WATCH_INTERVAL_SECONDS` | 30 | watch poll interval; a non-integer value logs a warning and uses the default | **[configured]** `prash/watcher.py:L38-L48` |
| `PRASH_MAX_DIAGNOSIS_CONCURRENCY` | 3 | parallel sub-diagnoses for multi-job CI failures | **[configured]** `prash/brain/multi_diagnosis.py:L44-L63` |

## Model settings

- **[configured]** `PRIMARY_MODEL` defaults to `deepseek`; `DEEPSEEK_MODEL` defaults to `deepseek-v4-flash` and `KIMI_MODEL` to `kimi-k2.6`. Evidence: `prash/brain/kimi_client.py:L57-L66`.
- **[configured]** `DEEPSEEK_BASE_URL` defaults to `https://api.deepseek.com/v1` and `KIMI_BASE_URL` to `https://api.moonshot.ai/v1`; both clients use a 90-second timeout. Evidence: `prash/brain/kimi_client.py:L130-L135`, `prash/brain/kimi_client.py:L91-L95`.
- **[implemented]** Without `DEEPSEEK_API_KEY` the DeepSeek client is absent and calls fall back to Kimi; a missing `KIMI_API_KEY` raises an error that names the variable. Evidence: `prash/brain/kimi_client.py:L99-L139`, `prash/brain/kimi_client.py:L77-L90`.
- **[configured]** The DeepSeek completion budget is capped at 32000 tokens. Evidence: `prash/brain/kimi_client.py:L448`.
- **[implemented]** The setup wizard also lists `GEMINI_API_KEY` under the AI model group. Evidence: `prash/setup.py:L8-L15`.

## Notification channels

- **[configured]** Slack and Discord use `SLACK_WEBHOOK_URL` and `DISCORD_WEBHOOK_URL`; email uses `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_USER`, `EMAIL_PASSWORD`, `EMAIL_FROM` and `EMAIL_TO`; WhatsApp uses `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `WHATSAPP_FROM_NUMBER` and `WHATSAPP_TO_NUMBERS`. Evidence: `prash/notifications.py:L46-L59`.
- **[implemented]** A channel counts as configured when its key has a non-empty value. Evidence: `prash/notifications.py:L62-L74`.

## Packaging and runtime

- **[configured]** Core dependencies: rich, kubernetes, pydantic, httpx, openai, plyer, textual, boto3, google-auth, google-api-python-client, fastapi, uvicorn, python-dotenv and pyyaml (plus pyobjus on macOS). Evidence: `pyproject.toml:L10-L35`.
- **[configured]** The `desktop` extra repeats fastapi, python-dotenv and uvicorn (with the standard extras), and `dev` adds pytest plus the desktop extra. Evidence: `pyproject.toml:L41-L54`.
- **[implemented]** The comment above the extras says the core CLI never imports the server modules and that the server packages "stay an opt-in extra", yet fastapi, uvicorn, python-dotenv and pyyaml are also core dependencies. Evidence: `pyproject.toml:L37-L40`, `pyproject.toml:L31-L34`.
- **[tested]** Neither the core list nor the extras declare `python-multipart`, which FastAPI needs for the upload and form routes; on a clean install from the declared dependencies, three test modules fail at collection (run of 2026-11-05). Evidence: `pyproject.toml:L10-L54`, `prash/server.py:L3642`.
- **[configured]** pytest uses `testpaths = ["tests"]` and puts the repository root on `pythonpath`. Evidence: `pyproject.toml:L62-L68`.
