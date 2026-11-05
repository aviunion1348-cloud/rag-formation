# Integrations

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Method-level detail per connector: [reference/connectors.md](reference/connectors.md).

## Provider connectors

- **[implemented]** All connectors subclass `Connector` and declare `name`, `read_capabilities` and `write_capabilities`; the optional methods default to `NotImplementedError`. Evidence: `prash/connectors/base.py:L75-L113`.
- **[implemented]** The connector package docstring states that connectors hold no credentials of their own. Evidence: `prash/connectors/__init__.py:L1-L17`.

| Connector | Class | Reads | Writes | Label |
|---|---|---|---|---|
| kubernetes | `KubernetesConnector` | pod status, logs, events, watch, stats | restart, rollback, scale | **[implemented]** `prash/connectors/kubernetes.py:L128-L296` |
| github | `GitHubConnector` | workflow logs, repo, check runs, watch, stats | open PR, re-run job, apply fix, alert | **[implemented]** `prash/connectors/github.py:L56-L352` |
| gitlab | `GitLabConnector` | pipeline logs, repo, jobs, watch, stats | open MR, apply fix, alert | **[implemented]** `prash/connectors/gitlab.py:L64-L305` |
| aws | `AWSConnector` | instance status, logs, watch, stats | execute | **[implemented]** `prash/connectors/aws.py:L63-L525` |
| gcp | `GCPConnector` | instance status, logs, stats, watch | execute | **[implemented]** `prash/connectors/gcp.py:L31-L385` |
| azure | `AzureConnector` | instance status, logs | execute | **[implemented]** `prash/connectors/azure.py:L29-L325` |
| datadog | `DatadogConnector` | monitor state, logs, watch, stats | mute monitor, alert | **[implemented]** `prash/connectors/datadog.py:L179-L660` |
| grafana | `GrafanaConnector` | alert state, annotations, watch, stats | silence alert | **[implemented]** `prash/connectors/grafana.py:L196-L547` |
| pagerduty | `PagerDutyConnector` | incident state, watch, stats, on-calls, service dependencies | acknowledge, resolve, trigger event, page on-call | **[implemented]** `prash/connectors/pagerduty.py:L209-L784` |
| terraform | `TerraformConnector` | state, drift | apply, init | **[implemented]** `prash/connectors/terraform.py:L15-L154` |
| vercel | `VercelConnector` | build logs, deploy state | redeploy, rollback | **[implemented]** `prash/connectors/vercel.py:L28-L136` |
| snyk | `SnykConnector` | project issue state | ignore issue | **[implemented]** `prash/connectors/snyk.py:L71-L193` |
| gitleaks | `GitleaksConnector` | secret scan | none | **[implemented]** `prash/connectors/gitleaks.py:L45-L129` |

- **[implemented]** The CLI instantiates the 12 non-Kubernetes connectors for every invocation from the credentials mapping. Evidence: `prash/cli.py:L89-L102`, `prash/cli.py:L116-L117`.
- **[implemented]** The desktop server builds connectors through the registry instead, which caches instances and lists the credential fields each one needs. Evidence: `prash/connector_registry.py:L880-L946`.
- **[implemented]** The AWS and GCP connectors run in mock mode unless `LEAR_MOCK_SERVICES` is set to a false value or credentials are missing; AWS also falls back to mock data when authentication raises. Evidence: `prash/connectors/aws.py:L75`, `prash/connectors/aws.py:L97-L126`, `prash/connectors/gcp.py:L44-L55`.
- **[inferred]** With the default settings, AWS and GCP results shown by the CLI or the desktop app can be demo data rather than the operator's real cloud state; testing.md lists the 16 test failures this default causes.

## Model providers

- **[implemented]** Diagnosis and intent fallback use OpenAI-compatible clients for DeepSeek (primary by default) and Kimi (Moonshot), selected by `PRIMARY_MODEL`. Evidence: `prash/brain/kimi_client.py:L57-L139`.
- **[implemented]** Diagnosis runs a bounded investigation loop: the model may call read-only investigation tools (fetch file, list directory, search code) for up to `max_steps` rounds before the final structured call. Evidence: `prash/brain/kimi_client.py:L720-L869`, `prash/brain/diagnosis_agent.py:L2156-L2184`.
- **[implemented]** Investigation paths are validated before files are fetched. Evidence: `prash/brain/diagnosis_agent.py:L2187-L2206`.
- **[implemented]** The desktop widget generator asks DeepSeek or Kimi for a widget layout, also accepts `OPENAI_API_KEY`, and falls back to a template layout when the model output fails validation. Evidence: `prash/widget_generator.py:L357-L429`, `prash/widget_generator.py:L373-L381`, `prash/widget_generator.py:L251-L354`.
- **[inferred]** Every model call goes to a hosted API; nothing in the pinned code runs a local model.

## Team notifications

- **[implemented]** Four channels share one interface: Slack and Discord webhooks, SMTP email and WhatsApp through Twilio. Evidence: `prash/notifications.py:L104-L221`.
- **[implemented]** Webhook posts use a 15-second timeout, log the HTTP error body (first 300 characters) on rejection, and return false instead of raising. Evidence: `prash/notifications.py:L80-L101`.
- **[implemented]** The desktop server's Slack service posts Block Kit alerts and chat replies to `SLACK_WEBHOOK_URL`. Evidence: `prash/slack_service.py:L21-L285`.
- **[implemented]** The email service renders HTML incident reports and copilot replies and sends them over SMTP using the `EMAIL_*` settings. Evidence: `prash/email_service.py:L324-L441`, `prash/email_service.py:L565-L648`.

## Inbound conversations (desktop server)

- **[implemented]** The conversation router polls a Gmail inbox over IMAP (`imap.gmail.com`) and routes inbound email and Slack messages to incident chat. Evidence: `prash/conversation_router.py:L1-L7`, `prash/conversation_router.py:L506-L592`.
- **[implemented]** The listener is started and stopped by the server lifespan hook. Evidence: `prash/conversation_router.py:L610-L623`, `prash/server.py:L373-L384`.
- **[implemented]** When `EMAIL_USER` or `EMAIL_TO` is unset, the router falls back to a hard-coded personal Gmail address (not reproduced here). Evidence: `prash/conversation_router.py:L466`, `prash/conversation_router.py:L509`, `prash/conversation_router.py:L548`.
- **[implemented]** The router loads the repository-root `.env` with `override=False`, unlike the server module, which overrides. Evidence: `prash/conversation_router.py:L43-L49`, `prash/server.py:L56`.
