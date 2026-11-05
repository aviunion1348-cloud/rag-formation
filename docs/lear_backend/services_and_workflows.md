# Services and workflows

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Step lists follow the code order; line ranges point at each step.

## `prash run`: one action through the pipeline

1. **[implemented]** Load the credential store from the environment and read its values, then export cluster-related keys into the process environment. Evidence: `prash/cli.py:L348-L350`.
2. **[implemented]** Resolve the permission mode from `--mode`, else `PRASH_PERMISSION_MODE`, else `ask`, and build a dispatcher for it. Evidence: `prash/cli.py:L351-L353`.
3. **[implemented]** Build the `ActionContext`; an unknown action id ends with exit code 2. Evidence: `prash/cli.py:L354-L358`.
4. **[implemented]** Dispatch with the interactive ask helper unless `--noninteractive` was given; a `MissingSecretError` ends with exit code 3. Evidence: `prash/cli.py:L360-L367`.
5. **[implemented]** Render the result and exit 0 only when the run is ok, else 1. Evidence: `prash/cli.py:L369`, `prash/cli.py:L372-L409`.

Inside the dispatcher (`Dispatcher.run`):

- **[implemented]** Order: build the plan, check the circuit breaker, decide, prompt if needed, execute, mark NEEDS_INPUT when a secret is missing, verify a succeeded result, and log. Evidence: `prash/dispatch.py:L94-L175`.
- **[implemented]** Exceptions raised by `execute` become a failed result with outcome `errored`, not a crash. Evidence: `prash/dispatch.py:L155-L164`.
- **[implemented]** Successful executions are recorded with the circuit breaker so the per-resource cap applies to the next call. Evidence: `prash/dispatch.py:L211-L213`.

## `prash fix`: diagnose, then act

- **[implemented]** With `--ci`, the handler picks the provider (GitHub by default, GitLab with `--provider gitlab`), requires the provider token (exit 3 when missing), diagnoses the run and offers the matching apply action through the dispatcher. Evidence: `prash/cli.py:L463-L530`.
- **[implemented]** With `--provider aws`, it diagnoses an EC2 instance and dispatches the recommended action when one maps to a registered action. Evidence: `prash/cli.py:L532-L561`, `prash/fix.py:L596-L627`.
- **[implemented]** Otherwise the target must be `<namespace>/<pod>` (exit 2 otherwise) and the pod is diagnosed from Kubernetes state and logs, optionally with a manifest repository (`--repo` or `PRASH_MANIFEST_REPO`, requiring `GITHUB_TOKEN`). Evidence: `prash/cli.py:L563-L583`, `prash/fix.py:L72-L76`, `prash/fix.py:L87-L175`.
- **[implemented]** When the diagnosis proposes file changes, the handler dispatches `apply-manifest-fix` against the manifest repository. Evidence: `prash/cli.py:L585-L608`.
- **[implemented]** When the diagnosis returns a ranked options menu, non-interactive runs report the menu and take no action; interactive runs let the operator pick, and the pick still goes through risk tiers, the circuit breaker and the audit log. Evidence: `prash/cli.py:L610-L647`.
- **[implemented]** Otherwise `recommended_action_id` maps the brain's choice to an action: restart_pod to `restart-pod`, edit_configmap to `edit-configmap`, mute_monitor to `datadog-mute-monitor`, acknowledge_incident to `pagerduty-acknowledge`, silence_alert to `grafana-silence-alert`; anything else means no automated action. Evidence: `prash/fix.py:L59-L84`, `prash/cli.py:L649-L652`.
- **[implemented]** On the Kubernetes path the target environment defaults to the namespace name when `--env` is not given. Evidence: `prash/cli.py:L631-L636`.
- **[inferred]** Under environment-scoped mode, a SAFE-tier fix is therefore auto-allowed in any namespace not literally named `production`.

### CI diagnosis details

- **[implemented]** `diagnose_ci_run` fetches the failed run's logs and diagnoses them; the proposed edits are then grounded against the real file contents before they are offered. Evidence: `prash/fix.py:L262-L287`, `prash/fix.py:L290-L348`.
- **[implemented]** Logs with several failing jobs are split per job and diagnosed concurrently, with concurrency from `PRASH_MAX_DIAGNOSIS_CONCURRENCY` (default 3, clamped to at least 1). Evidence: `prash/brain/multi_diagnosis.py:L44-L63`, `prash/brain/multi_diagnosis.py:L135-L201`.
- **[implemented]** `reconcile_ci_fix` follows a fix branch's CI run to decide whether the fix worked. Evidence: `prash/fix.py:L396-L491`.
- **[implemented]** The diagnosis call budget gives DeepSeek 240 seconds across all investigation steps and reserves 35 seconds for the Kimi fallback. Evidence: `prash/brain/kimi_client.py:L430-L432`.
- **[implemented]** After the model answers, deterministic guardrails and confidence recalibration adjust the diagnosis, and a second opinion is requested below a low-confidence threshold. Evidence: `prash/brain/diagnosis_agent.py:L1811-L1906`, `prash/brain/diagnosis_agent.py:L2048-L2095`.

## `prash watch` and notifications

- **[implemented]** The Kubernetes watch loop polls pod status every `PRASH_WATCH_INTERVAL_SECONDS` (default 30) until interrupted. Evidence: `prash/watcher.py:L38-L48`, `prash/watcher.py:L160-L188`.
- **[implemented]** Only transitions notify: healthy to problem, or one problem to a different one; repeated polls of the same problem stay silent and recoveries are recorded without a notification. Evidence: `prash/watcher.py:L55-L77`.
- **[implemented]** A notification tries a desktop notification first, then pushes the same text to every configured team channel, and failures are logged without stopping the loop. Evidence: `prash/watcher.py:L80-L99`.
- **[implemented]** Provider-specific loops exist for Datadog, PagerDuty, Grafana, GitHub, GitLab, Terraform, AWS and GCP, plus a generic connector loop. Evidence: `prash/watcher.py:L298-L807`.
- **[implemented]** `send_team_notifications` sends to every configured channel (Slack, Discord, email, WhatsApp) and returns a per-channel success map; it never raises. Evidence: `prash/notifications.py:L224-L246`.
- **[implemented]** `prash notify <message>` posts one message to the configured team channels. Evidence: `prash/cli.py:L1115-L1127`.

## Interactive surfaces

- **[implemented]** The REPL keeps a session, resolves each line, and re-enters the argparse parser so every subcommand behaves as on the command line. Evidence: `prash/repl.py:L68-L120`, `prash/repl.py:L131-L265`.
- **[implemented]** Intent resolution tries a rule-based fast path and falls back to an LLM call that returns a suggestion or a clarifying question. Evidence: `prash/intent.py:L259-L367`, `prash/intent.py:L370-L382`, `prash/intent.py:L687-L726`.
- **[implemented]** `prash tui` opens a Textual application with a command palette and a chat-style interaction object. Evidence: `prash/tui.py:L244-L339`, `prash/tui.py:L363-L743`.
- **[implemented]** `prash setup` walks grouped settings (AI models, GitHub, Kubernetes and others) and rewrites the local `.env`, keeping keys that were already present. Evidence: `prash/setup.py:L8-L20`, `prash/setup.py:L80-L159`.

## Desktop server background work

- **[implemented]** The lifespan hook restores persisted watches and notifications, starts the watch-polling task and the connector health-check task, starts the IMAP listener, and cancels the tasks on shutdown. Evidence: `prash/server.py:L358-L394`.
- **[implemented]** The polling loop drives every active watch and records notifications. Evidence: `prash/server.py:L454-L558`.
- **[implemented]** The health-check loop periodically checks configured connectors. Evidence: `prash/server.py:L341-L354`.
