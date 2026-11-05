# Documentation coverage — docs/lear_backend

Produced by `python -m ragformation lear docs verify --source <lear checkout> --write-coverage` against `aviunion1348-cloud/Lear-backend-Avi` at commit `60cf9abf8408`. Re-run it after any docs change; this file is overwritten by that command.

## Verification checks

| Check | Status | Result |
|---|---|---|
| pin | PASS | pinned aviunion1348-cloud/Lear-backend-Avi @ 60cf9abf8408; checkout HEAD matches |
| required_pages | PASS | 13/13 present; 0 below 120 words |
| links | PASS | 26 relative links, 0 broken |
| source_refs | PASS | 1237 references, 0 invalid |
| stale_commits | PASS | 0 foreign commit ids; 0 narrative pages without the pinned id |
| claim_labels | PASS | 340 claim lines; 340 labelled; 340 with evidence (inferred claims may cite none) |
| duplicates | PASS | 0 repeated paragraphs; 0 identical chunks of 378 |
| guardrails | PASS | 0 of 378 chunks would be quarantined by the injection guard |
| secrets | PASS | 0 credential-pattern or personal email hits in markdown (SOURCE_PIN.json may hold the full commit id) |
| api_auth_claim | PASS | 87 method/path routes from 85 route decorators in prash/server.py; 0 declare Depends/Security/dependencies; middleware: CORSMiddleware; custom @app.middleware: 0; documented 'no authentication dependency' claim holds |
| route_count | PASS | reference/http_api.md documents 87 routes; the AST of prash/server.py has 87 |

Overall: **PASS**.

## Evidence labels on narrative claims

| Label | Claims |
|---|---:|
| implemented | 255 |
| configured | 32 |
| tested | 23 |
| inferred | 30 |

## Source coverage

- Python modules in `prash/` (excluding `__init__.py`): 79.
- Cited by at least one narrative page: 43 (54%).
- Cited anywhere, including the generated reference pages: 79 (100%).
- Test files referenced: 11 of 42.

Modules with no narrative citation are still listed, with line spans, in `reference/modules.md`:

`prash/actions/alert_base.py`, `prash/actions/apply_ci_fix.py`, `prash/actions/apply_gitlab_ci_fix.py`, `prash/actions/aws_alert.py`, `prash/actions/datadog_alert.py`, `prash/actions/datadog_mute.py`, `prash/actions/edit_config.py`, `prash/actions/exec_command.py`, `prash/actions/execute_aws.py`, `prash/actions/execute_azure.py`, `prash/actions/execute_gcp.py`, `prash/actions/gcp_alert.py`, `prash/actions/github_alert.py`, `prash/actions/gitlab_alert.py`, `prash/actions/gitleaks_escalate.py`, `prash/actions/grafana_silence.py`, `prash/actions/missing_secret.py`, `prash/actions/open_pr.py`, `prash/actions/pagerduty_incident.py`, `prash/actions/pagerduty_page.py`, `prash/actions/restart_pod.py`, `prash/actions/rollback.py`, `prash/actions/scale.py`, `prash/actions/snyk_ignore.py`, `prash/actions/terraform_apply.py`, `prash/actions/terraform_init.py`, `prash/actions/vercel_deploy.py`, `prash/admin_panel.py`, `prash/brain/edit_repair.py`, `prash/brain/gitlab_log_fetcher.py`, `prash/brain/log_fetcher.py`, `prash/customer_store.py`, `prash/demo_page.py`, `prash/incident_page.py`, `prash/mock_service.py`, `prash/ui.py`

## Most-cited files

| File | References |
|---|---:|
| `prash/server.py` | 186 |
| `prash/cli.py` | 139 |
| `.env.example` | 56 |
| `prash/watcher.py` | 34 |
| `prash/connectors/kubernetes.py` | 32 |
| `prash/email_service.py` | 32 |
| `prash/brain/kimi_client.py` | 29 |
| `prash/actions/contract.py` | 26 |
| `prash/fix.py` | 25 |
| `prash/conversation_router.py` | 25 |
| `prash/connectors/aws.py` | 24 |
| `prash/dispatch.py` | 23 |
| `prash/widget_generator.py` | 22 |
| `prash/circuit_breaker.py` | 19 |
| `prash/brain/diagnosis_agent.py` | 19 |
| `prash/brain/schemas.py` | 18 |
| `prash/connectors/gcp.py` | 18 |
| `prash/connector_registry.py` | 17 |
| `pyproject.toml` | 16 |
| `prash/connectors/datadog.py` | 16 |
| `prash/connectors/pagerduty.py` | 16 |
| `prash/incident_manager.py` | 15 |
| `prash/connectors/grafana.py` | 15 |
| `prash/permissions.py` | 14 |
| `prash/connectors/base.py` | 14 |
| `prash/notifications.py` | 14 |
| `prash/intent.py` | 12 |
| `prash/credentials.py` | 12 |
| `prash/connectors/github.py` | 12 |
| `prash/connectors/gitlab.py` | 12 |
