# Testing

> Pinned source: `aviunion1348-cloud/Lear-backend-Avi` @ `60cf9abf8408`. Per-file test list: [reference/tests.md](reference/tests.md).

## Layout

- **[configured]** pytest collects from `tests/` with the repository root on `pythonpath`. Evidence: `pyproject.toml:L62-L68`.
- **[implemented]** At the pinned commit `tests/` holds 42 Python test files with 843 statically defined `test_*` functions (counted with `ast`; parametrisation adds collected items). Evidence: `tests/test_permissions.py:L1-L53`, `tests/test_desktop_api.py:L690-L749`.
- **[configured]** Live Kubernetes tests are skipped unless `PRASH_LIVE_K8S_TESTS=1` points them at a real cluster, and the Grafana integration tests are skipped unless WireMock is configured. Evidence: `tests/test_kubernetes_connector_live.py:L49`, `tests/integration/test_grafana_mock_service.py:L66`.

## Continuous integration

- **[configured]** The CI workflow runs on pushes to `main` across Ubuntu, Windows and macOS with Python 3.12. Evidence: `.github/workflows/ci.yml:L3-L25`.
- **[configured]** Lint (`ruff check .`) only warns; the test step runs `pytest -q`. Evidence: `.github/workflows/ci.yml:L43-L57`.
- **[configured]** A separate job deploys a broken-pod fixture to a cluster and runs the live connector tests with `PRASH_LIVE_K8S_TESTS=1`; another starts a Grafana mock for integration tests. Evidence: `.github/workflows/ci.yml:L76-L133`.
- **[configured]** `always-broken.yml` is a deliberately failing workflow that only triggers on pushes to the `prash-broken-ci` branch, so `prash fix --ci` has a stable failing run to diagnose. Evidence: `.github/workflows/always-broken.yml:L1-L20`.
- **[tested]** GitHub Actions reports the CI workflow run for the pinned commit as failed (run created 2026-10-03; metadata retrieved read-only on 2026-11-05). Evidence: `.github/workflows/ci.yml:L47-L57`.

## Suite run against the pinned commit (2026-11-05)

The suite was run from a `git archive` copy of the pinned commit in a fresh virtual environment (Python 3.11.2, not the
3.12 used by CI), with `DEEPSEEK_API_KEY` and `KIMI_API_KEY` unset. Logs are kept in the RAG Formation repository under
`docs/lear_agent/evidence/lear_pytest_run*.txt`.

| Run | Setup | Result | Label |
|---|---|---|---|
| 1 | `pip install -e ".[dev]"` only (declared dependencies) | 3 collection errors, pytest exit 2: `python-multipart` missing | **[tested]** `pyproject.toml:L10-L54`, `prash/server.py:L3642` |
| 2 | run 1 plus `python-multipart` | 826 passed, 28 failed, 10 skipped | **[tested]** `tests/test_aws_connector.py:L38`, `tests/test_gcp_connector.py:L81` |
| 3 | run 2 plus `LEAR_MOCK_SERVICES=false` | 842 passed, 12 failed, 10 skipped | **[tested]** `prash/connectors/aws.py:L75`, `prash/connectors/gcp.py:L44` |

What the failures show:

- **[implemented]** The AWS and GCP connectors start in mock mode unless `LEAR_MOCK_SERVICES` is set to something other than true/1/yes, because the variable defaults to `"true"`. Evidence: `prash/connectors/aws.py:L75`, `prash/connectors/gcp.py:L44`.
- **[tested]** That default explains 16 of the 28 failures in run 2: the connector tests expect real-mode behaviour and receive `{"mode": "mock"}` results. Evidence: `tests/test_aws_connector.py:L38`, `tests/test_gcp_connector.py:L81`.
- **[implemented]** The AWS connector also switches itself to mock mode when authentication raises, "for demo stability", so a credential failure can look like success. Evidence: `prash/connectors/aws.py:L97-L126`.
- **[tested]** The negative authentication tests for AWS and GCP still fail with mocks disabled (they expect `False` and get `True`). Evidence: `tests/test_aws_connector.py:L48`, `tests/test_aws_connector.py:L61`, `tests/test_gcp_connector.py:L97-L107`.
- **[tested]** The Grafana and PagerDuty connectors fail on HTTP 401 handling with `'str' object has no attribute 'decode'` raised from the error-message formatting. Evidence: `prash/connectors/grafana.py:L243`, `prash/connectors/pagerduty.py:L268`.
- **[tested]** Two desktop chat tests fail: the telemetry injection assertion and the SSE stream, which returns `Bridge Error: 'cpu_pct'`. Evidence: `tests/test_desktop_api.py:L370`, `tests/test_desktop_api.py:L785`.
- **[tested]** One intent test fails: the fast path asks a Grafana clarifying question where the test expects it to return nothing. Evidence: `tests/test_intent.py:L160`.
- **[implemented]** The project README still says the suite should report 457 passed and 7 skipped. Evidence: `README.md:L57-L61`.
- **[inferred]** That figure is stale: the pinned commit collects 864 items (826 + 28 + 10 in run 2).

## Behaviour that is well covered

- **[tested]** Permission decisions for every mode and tier. Evidence: `tests/test_permissions.py:L8-L53`.
- **[tested]** Circuit-breaker cap, per-resource isolation, window expiry, reset and persistence. Evidence: `tests/test_circuit_breaker.py:L10-L54`.
- **[tested]** Append-only audit log and sequence numbers. Evidence: `tests/test_audit.py:L12-L45`.
- **[tested]** Dry-run semantics and the audit record for refused runs. Evidence: `tests/test_actions.py:L187-L217`, `tests/test_actions.py:L818-L824`.
- **[tested]** Chat-driven execution through the desktop API, including the auto-approval of approval-tier actions. Evidence: `tests/test_desktop_api.py:L690-L749`.

## Evaluation harness

- **[implemented]** `evals/` holds a port of the earlier diagnosis evaluation harness with golden cases. Evidence: `evals/README.md:L7-L25`.
- **[implemented]** Its README says `run_eval.py` is "not runnable yet" because the brain modules do not exist. Evidence: `evals/README.md:L40-L45`.
- **[inferred]** That note is stale at the pinned commit: `prash/brain/diagnosis_agent.py` and `prash/brain/kimi_client.py` both exist. A real evaluation run would still need DeepSeek or Kimi credentials.
