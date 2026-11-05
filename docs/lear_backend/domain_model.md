# Domain model

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Types are Python dataclasses, enums, TypedDicts and pydantic models; there are no database tables.

## Action contract (`prash/actions/contract.py`)

- **[implemented]** `RiskTier` has three values: `safe` (may run without asking in permissive modes), `approval` (always needs an explicit per-action grant, even in bypass mode) and `never` (unconditionally refused). Evidence: `prash/actions/contract.py:L25-L39`.
- **[implemented]** `Decision` is the permission verdict: `allow`, `prompt` or `refuse`. Evidence: `prash/actions/contract.py:L42-L47`.
- **[implemented]** `ActionResultStatus` is one of `succeeded`, `failed`, `needs_approval`, `needs_input` or `skipped`. Evidence: `prash/actions/contract.py:L50-L55`.
- **[implemented]** `ActionSpec` is a frozen dataclass with `id`, `summary`, `risk_tier`, `reversible`, `capabilities` and `approval_hint`; its `always_asks` property is true for the approval and never tiers. Evidence: `prash/actions/contract.py:L58-L71`.
- **[implemented]** `Target` names the resource and its environment (default `staging`) plus free-form labels; the environment drives environment-scoped permission mode. Evidence: `prash/actions/contract.py:L74-L84`.
- **[implemented]** `ActionContext` carries the target, the credentials mapping, the `PRASH_SECRET_*` secrets, the `dry_run` and `grant` flags and an `extra` dict (the CLI puts connectors, the credential store, a GitHub runner and per-action options there). Evidence: `prash/actions/contract.py:L87-L96`, `prash/cli.py:L271-L304`.
- **[implemented]** `Plan` lists `PlanStep`s (description, impact, dry-run-only flag) with the action id, reversibility and risk tier, and `describe()` renders the steps for the approval prompt. Evidence: `prash/actions/contract.py:L99-L116`.
- **[implemented]** `ActionResult` holds a status, a summary, an optional `VerificationResult` (`ok`, `detail`) and a detail dict. Evidence: `prash/actions/contract.py:L119-L130`.
- **[implemented]** `Action` is the abstract base: `plan`, `execute` and `verify` are abstract; `dry_run` re-plans with `dry_run=True`; `ask_for_secret` raises `MissingSecretError` so an action can request a value instead of dead-ending. Evidence: `prash/actions/contract.py:L133-L170`.

## Dispatch results (`prash/dispatch.py`)

- **[implemented]** `ExecutionOutcome` distinguishes `executed`, `refused`, `skipped`, `needs_input`, `errored` and `circuit_open`. Evidence: `prash/dispatch.py:L37-L43`.
- **[implemented]** `RunResult` bundles the outcome, the decision, the action result, the plan, whether a grant was used and the audit entry id; `ok` is true only for an executed action whose status is `succeeded`. Evidence: `prash/dispatch.py:L46-L60`.
- **[implemented]** `AskFn.ask(action, plan, ctx)` is the interface the dispatcher uses to ask a human; returning true means an explicit per-action grant. Evidence: `prash/dispatch.py:L63-L68`.

## Connector types (`prash/connectors/base.py`)

- **[implemented]** `ConnectorState` values: unknown, healthy, degraded, failed, crash-looping, deploying, stable, not-found. Evidence: `prash/connectors/base.py:L17-L25`.
- **[implemented]** `ResourceState` is a resource name, a `ConnectorState`, a detail dict and an optional timestamp. Evidence: `prash/connectors/base.py:L28-L33`.
- **[implemented]** `ConnectorEvent` is the normalised timeline entry used for cross-connector correlation: UTC `timestamp`, `connector`, `event_type`, a one-line `summary` and the untouched `raw` payload. Evidence: `prash/connectors/base.py:L36-L43`.
- **[implemented]** `WatchHandle` is polled by the shared watcher loop; `poll()` returns only events since the previous poll. Evidence: `prash/connectors/base.py:L46-L72`.

## Diagnosis schema (`prash/brain/schemas.py`)

- **[implemented]** `Diagnosis` requires `problem_summary` (10–500 chars), `root_cause` and `fix_description` (20–2000 chars each), `fix_type`, `confidence` between 0 and 1, and a `category`. Evidence: `prash/brain/schemas.py:L347-L355`.
- **[implemented]** `fix_type` is one of `safe_auto_apply`, `review_recommended` or `manual_required`. Evidence: `prash/brain/schemas.py:L351`.
- **[implemented]** `category` is one of code, workflow_config, dependency, environment, flaky_test, runtime, infra_as_code, monitoring or unknown; a validator maps aliases such as `k8s`, `terraform` or `pagerduty` onto these. Evidence: `prash/brain/schemas.py:L355`, `prash/brain/schemas.py:L25-L39`.
- **[implemented]** Optional fields include `files_changed`, `is_flaky_test`, `logs_truncated_warning`, `speculative` and `required_secrets`. Evidence: `prash/brain/schemas.py:L353-L369`.
- **[implemented]** `recommended_action` is restart_pod, rollback, scale, edit_configmap, terraform_init, terraform_apply, mute_monitor, acknowledge_incident, silence_alert or null. Evidence: `prash/brain/schemas.py:L370-L384`.
- **[implemented]** `config_patch` and `config_patch_target` describe a ConfigMap key/value fix. Evidence: `prash/brain/schemas.py:L385-L403`.
- **[implemented]** `options` is a ranked menu of `DiagnosisOption`s for ambiguous runtime cases; the validator requires at least two entries with exactly one default and derives `recommended_action` from the default when it is unset. Evidence: `prash/brain/schemas.py:L318-L344`, `prash/brain/schemas.py:L404-L414`, `prash/brain/schemas.py:L445-L463`.
- **[implemented]** `FileChange` names a repo-relative `path` with either targeted `edits` (`FileEdit`), full `new_content`, or `create_empty`, plus an `explanation`; edits are applied with whitespace-tolerant matching. Evidence: `prash/brain/schemas.py:L225-L315`, `prash/brain/schemas.py:L42-L86`, `prash/brain/schemas.py:L198-L222`.
- **[implemented]** `MultiFailureResult` keeps one diagnosis per failing CI job and counts a job as fixed when its diagnosis changes files, so partial success ("fixed 3 of 4") is reportable. Evidence: `prash/brain/multi_diagnosis.py:L95-L132`.

## Correlation and memory

- **[implemented]** `CorrelatedIncident` is a cluster of events that coincide in time; `is_multi_source` is true when two or more connectors are involved. Evidence: `prash/brain/correlation.py:L44-L64`.
- **[implemented]** `correlate` merges events from any connectors onto one timeline and starts a new incident when the gap exceeds the window, 120 seconds by default. Evidence: `prash/brain/correlation.py:L76-L103`, `prash/brain/correlation.py:L31`.
- **[implemented]** `RepoMemory` holds similar fixes, repeated error signatures, flaky tests, per-category outcomes, known-good files and dependency patterns for one repository id. Evidence: `prash/brain/repo_memory.py:L22-L29`.

## Desktop connector registry (`prash/connector_registry.py`)

- **[implemented]** A `ConnectorRegistryEntry` describes one connector for the desktop app: id, name, category, icon, colour, connector class path, `AuthField`s, `WidgetTemplate`s and flags for watch, stats and execute support. Evidence: `prash/connector_registry.py:L49-L70`.
- **[implemented]** An `AuthField` is one credential input (environment key, label, input type, required flag, default, placeholder, help text, options). Evidence: `prash/connector_registry.py:L26-L34`.
- **[implemented]** `CONNECTOR_REGISTRY` maps connector ids to entries and is the server's source of truth for connector configuration. Evidence: `prash/connector_registry.py:L73-L870`.
