# Universal integration report (Stage G) — 5/5 hosts

The question this stage answers: **can somebody who has never seen this
repository install it and use it, without the checkout?** Yes — verified from
five independent hosts, all installing the built wheel, all talking to one live
engine over HTTP with an API key and a host-signed principal header.

Reproduce with `bash scripts/run_host_fixtures.sh`. Raw log:
[`evidence/hosts-20261105T153435Z.log`](evidence/hosts-20261105T153435Z.log)
(the script writes a new timestamped log on each run).

```
host A: 13/13 checks passed          RESULT A python-wheel-host: PASS
RESULT B flask-host: PASS            RESULT C fastapi-host: PASS
host D: all checks passed            RESULT D node-host: PASS
RESULT E unrelated-repo: PASS
== summary: 5/5 hosts passed ==
```

## 1. Host A — plain Python, installed wheel

`integrations/qualification/python_wheel_host.py`, run by a venv that contains
only the wheel:

| Check | Observed |
| --- | --- |
| import resolves to the wheel, not the checkout | `/tmp/hosts-…/hostA/lib/python3.11/site-packages/ragformation/__init__.py` |
| package version | `0.2.0` |
| ingest through the installed package | 14 sources, 66 chunks |
| grounded answer + citations | status `answered`, 1 citation naming `data-retention-policy` |
| answer states the fact | "Audit logs are retained for **400 days** total…" |
| off-corpus question abstains | `abstained` |
| installed server starts and answers HTTP | `/healthz` 200, `/api/v1/ask` answered with 12 evidence chunks |

## 2. Host B — Flask, identity in the host

`integrations/qualification/flask_host.py`. The browser talks to Flask; Flask
resolves the session and calls the engine with `x-api-key` + `x-rag-principal`.

* signed-in session → `answered` with a citation to `data-retention-policy` § *2. Retention Periods*;
* no session cookie → **401** (the host refuses before the engine is involved);
* `/healthz` reports host + engine status.

## 3. Host C — FastAPI, including SSE and scope narrowing

`integrations/qualification/fastapi_host.py` (uvicorn), same trust boundary:

* `POST /ask` → `{"answer": "ingest-api — availability (slo): 99.95% [2]", "citations": [{"title": "service-catalog", "section": "services: ingest-api"}]}`;
* `GET /events` relays the engine's real `text/event-stream` (no invented second
  streaming protocol);
* `POST /ask` with `allowedSources: ["src-does-not-exist"]` → `abstained` with
  *"retrieval returned no passages at all"* — an empty intersection means **no
  evidence**, not *no filter*. This is the scope-creep failure mode that matters
  most in a host-proxy design, and it is checked through the proxy hop.

## 4. Host D — Node 22, no Python in the request path

`integrations/qualification/node_host.mjs`:

* `/healthz`, `POST /api/v1/ask` → grounded answer, "400 days" present;
* every citation's `chunkId` exists in the delivered `evidence` array;
* `GET /api/v1/events` opens as `text/event-stream` and delivers frames;
* off-corpus question → `abstained`;
* request without an API key → **401**;
* `/healthz` without a principal still answers (liveness probe stays open by design).

## 5. Host E — an unrelated repository

A fresh `git init` outside the project, a `package.json`, a venv, the wheel, and
a 20-line consumer that ingests its own document and asks a question:

```
import path: …/unrelated-repo/.venv/lib/python3.11/site-packages/ragformation/__init__.py
ingested: 1 sources, 1 chunks
status: answered
answer: Releases are frozen during the audit window for 48 hours. [1]
citations: 1
unrelated-repo smoke: OK
```

This is the "universal integration" claim in its smallest honest form: an
unrelated consumer with no access to the source produced a grounded, cited answer
from its own document.

## 6. What this does **not** prove

* No public endpoint, no TLS termination, no reverse proxy, no container image,
  no Kubernetes manifest was exercised. Those are deployment choices, not
  package properties.
* The hosts above are fixtures written for this qualification; they are small on
  purpose so that a reviewer can read them in a minute.
* All five ran single-process on 2 vCPUs. Nothing here measures behaviour under
  concurrency, which remains unverified.
