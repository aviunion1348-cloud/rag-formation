# Persistence and storage

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. This page replaces a database schema page: Lear has no database.

## No database by design

- **[implemented]** The model client's port notes say the earlier hosted backend wrote agent calls to a Supabase table, and that v2 "has no database by design"; those calls became local logger calls. Evidence: `prash/brain/kimi_client.py:L11-L14`.
- **[implemented]** The evaluation harness repeats the point: the case-seeding script that read the old Supabase database was not ported because v2 has no database. Evidence: `evals/README.md:L47-L53`.
- **[inferred]** No module imports an SQL driver or ORM; all state is kept in local files described below.

## CLI state files

| File | Written by | Format and behaviour | Label |
|---|---|---|---|
| `.prash/audit.log` (override `PRASH_AUDIT_LOG_PATH`) | `AuditLog.append` | JSON object per line, opened in append mode, never rewritten | **[implemented]** `prash/audit.py:L28-L29`, `prash/audit.py:L36-L65` |
| `.prash/circuit.json` (override `PRASH_CIRCUIT_STATE_PATH`) | `CircuitBreaker.record` / `reset` | `{"resources": {resource: [timestamps]}}`, rewritten on each change | **[implemented]** `prash/circuit_breaker.py:L28-L30`, `prash/circuit_breaker.py:L58-L71` |
| `.prash/memory.json` (override `PRASH_MEMORY_PATH`) | `save_fix` in the brain's local memory | similar fixes, error signatures, flaky tests, category outcomes, known-good files, dependency patterns | **[implemented]** `prash/brain/local_memory.py:L18-L20`, `prash/brain/local_memory.py:L49-L141` |
| credentials file (`PRASH_ENV`, else `./.env`, else `~/.prash/.env`) | `CredentialStore.set_secret` | `KEY=VALUE` lines; secrets are appended as `PRASH_SECRET_<name>`, never rewritten | **[implemented]** `prash/credentials.py:L28-L36`, `prash/credentials.py:L61-L66` |

- **[implemented]** Audit sequence numbers are computed by reading the last non-empty line of the log before each append. Evidence: `prash/audit.py:L67-L80`.
- **[inferred]** Because the sequence is read and then appended without a lock, two processes appending at the same moment can write the same `seq`; the log stays append-only but the sequence is not guaranteed unique under concurrency.
- **[implemented]** The circuit-breaker state is written with a plain `write_text` (no temporary file and rename). Evidence: `prash/circuit_breaker.py:L68-L71`.
- **[inferred]** A crash during that write could leave a truncated JSON file; `_load` then treats unreadable state as empty, which silently closes every open circuit. Evidence for the fallback: `prash/circuit_breaker.py:L58-L66`.

## Desktop server state files

- **[implemented]** The server keeps its credentials in the repository-root `.env` (`ENV_PATH`), watch definitions in `prash.yaml` (`YAML_PATH`) and notifications in `.prash/notifications.json`. Evidence: `prash/server.py:L52-L54`.
- **[implemented]** At import time the server loads that `.env` into the process environment with `override=True`, so file values replace variables already exported in the shell. Evidence: `prash/server.py:L56`.
- **[implemented]** Credential updates copy `.env` to a temporary file in the same directory, apply `set_key` / `unset_key`, then atomically replace the original with `os.replace`. Evidence: `prash/server.py:L271-L290`, `prash/server.py:L293-L307`.
- **[implemented]** Started watches are persisted to `prash.yaml` and restored by the lifespan hook on the next start. Evidence: `prash/server.py:L107-L124`, `prash/server.py:L358-L363`.
- **[implemented]** Persisted notifications are reloaded on startup when the file exists. Evidence: `prash/server.py:L59-L72`, `prash/server.py:L364-L369`.

## Demo and messaging modules

- **[implemented]** Chat sessions are JSON files under `.prash/chat_sessions/` with a `sessions_index.json`; the directory is created when the module is imported. Evidence: `prash/chat_manager.py:L27-L29`, `prash/chat_manager.py:L57-L58`.
- **[implemented]** Incidents are kept in memory and mirrored to `.prash/incidents/incidents.json`; that directory is also created at import time. Evidence: `prash/incident_manager.py:L22-L27`, `prash/incident_manager.py:L42`.
- **[inferred]** Importing `prash.chat_manager` or `prash.incident_manager` therefore creates directories next to the package, which matters for read-only installs.
