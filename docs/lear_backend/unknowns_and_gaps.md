# Unknowns and gaps

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Things these docs cannot confirm, and places where the code, tests and project prose disagree.

## Not verifiable from this environment

- **[inferred]** No provider credentials were available, so no connector was exercised against a real Kubernetes cluster, cloud account, CI system or monitoring tool; connector behaviour is documented from code and unit tests only.
- **[inferred]** No DeepSeek or Kimi key was available, so no diagnosis, intent fallback or widget generation call was made; the model flow is documented from code.
- **[inferred]** The desktop server was not started and the `desktop/` frontend was not reviewed; API behaviour comes from the route code and the desktop API tests.
- **[inferred]** No shared-memory source for Lear was found among the repositories visible to this account, so nothing here draws on shared team memory.
- **[inferred]** The pinned repository is a fork of `ananttheacharya/Lear-Backend`; GitHub metadata on 2026-11-05 showed the fork one commit behind the upstream head `5ea73a140e28` and none ahead. That upstream commit is not covered.

## Code, tests and prose disagree

- **[implemented]** The README promises 457 passed and 7 skipped tests, but the pinned commit collects 864 items, and the run of 2026-11-05 ended with 28 failures (12 with mock services disabled). Evidence: `README.md:L57-L61`, `prash/connectors/aws.py:L75`.
- **[implemented]** The GitHub Actions CI run for the pinned commit concluded with a failure. Evidence: `.github/workflows/ci.yml:L47-L57`.
- **[implemented]** `python-multipart` is required by the upload and form routes but is not declared, so a clean install from `pyproject.toml` cannot import the server. Evidence: `prash/server.py:L3642`, `pyproject.toml:L10-L54`.
- **[implemented]** The packaging comment calls the server dependencies an opt-in extra while listing them as core dependencies. Evidence: `pyproject.toml:L31-L40`.
- **[implemented]** The evaluation README says the harness cannot run because the brain modules are missing, yet those modules exist at the pinned commit. Evidence: `evals/README.md:L40-L45`, `prash/brain/diagnosis_agent.py:L1523-L1545`.
- **[implemented]** The `/api/chat/execute` comment says the ask swap is "never module-global", but it assigns the `cli.CliAsk` module attribute. Evidence: `prash/server.py:L2702-L2710`.

## Security-relevant observations

- **[inferred]** The desktop API has no authentication on any of its 87 method/path routes and allows every origin with credentials. Evidence for CORS: `prash/server.py:L399-L405`.
- **[configured]** The `dev:server` script binds that API to all interfaces. Evidence: `package.json:L9`.
- **[implemented]** Actions started from the chat endpoint are auto-approved, and their audit entries are labelled SAFE / ASK whatever the real tier was. Evidence: `prash/server.py:L2705-L2745`.
- **[implemented]** The Slack interactivity endpoint approves incident remediations without checking a Slack signature. The server also offers ngrok tunnel URLs for these endpoints. Evidence: `prash/server.py:L3713-L3735`, `prash/server.py:L3819-L3868`.
- **[implemented]** Incident approval also works through a one-click GET link with no token. Evidence: `prash/server.py:L3511-L3533`.
- **[implemented]** Incident remediation patches a ConfigMap and deletes pods with `kubectl` outside the dispatcher, so the permission engine, circuit breaker and dispatcher audit log do not see it. Evidence: `prash/incident_manager.py:L173-L259`.
- **[implemented]** The conversation router falls back to hard-coded personal email addresses when `EMAIL_USER` or `EMAIL_TO` is unset. Evidence: `prash/conversation_router.py:L509`, `prash/conversation_router.py:L548`.
- **[implemented]** AWS and GCP connectors default to mock data, and AWS reports mock success after an authentication exception, which can hide misconfiguration. Evidence: `prash/connectors/aws.py:L75`, `prash/connectors/aws.py:L118-L126`.

## Robustness observations

- **[inferred]** Audit sequence numbers can repeat under concurrent writers because the next number is read from the file without a lock. Evidence for the read: `prash/audit.py:L67-L80`.
- **[inferred]** The circuit-breaker state file is rewritten in place; a torn write is read back as empty state, which closes open circuits. Evidence: `prash/circuit_breaker.py:L58-L71`.
- **[implemented]** Configuration precedence differs by entry point: the CLI lets shell exports win, the server lets `.env` win, and the conversation router lets the shell win again. Evidence: `prash/cli.py:L132-L156`, `prash/server.py:L56`, `prash/conversation_router.py:L43-L49`.
- **[implemented]** Importing the chat and incident managers creates `.prash/` directories next to the package. Evidence: `prash/chat_manager.py:L27-L28`, `prash/incident_manager.py:L22-L23`.

## Deliberately not covered

- **[inferred]** The diagnosis system prompt (about 960 lines) is cited by location but not summarised rule by rule; questions about specific prompt rules need the source.
- **[inferred]** Demo-only modules (the mock service, customer store, demo page, admin panel, incident page) appear only in the module index, with line spans.
- **[inferred]** How Lear is deployed for real users, and whether the earlier hosted version is still running, cannot be determined from the repository.
