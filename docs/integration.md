# Integrating this engine into a product

This page is about the boundary, not the internals. It answers: where does this
run, who is the caller, what may the caller see, and what does failure look like.
Code that implements it lives in [`integrations/`](../integrations/README.md).

## 1. Deployment shapes

| Shape | Bind | Identity | Use when |
| --- | --- | --- | --- |
| Local operator | `127.0.0.1` | none (single principal) | a person on a laptop, or a CLI pipeline |
| Service behind your API | `127.0.0.1` | API key + host principal header | the normal production shape |
| Public bind | `0.0.0.0` | API key **required** | you accept the risk explicitly |

The server refuses to bind a non-loopback interface without authentication. The
override (`RAG_ALLOW_INSECURE_PUBLIC=1`) exists so an operator can make that
decision deliberately; it is logged at startup and reported by `doctor` and
`/api/v1/health` as `insecure-public`.

## 2. Identity and authorisation

```
browser ──session cookie──► your app/proxy ──x-rag-principal + x-api-key──► ragformation (loopback)
```

* **The browser never talks to the engine directly.** The proxy example in
  `integrations/host_principal/proxy_example.py` shows the trust boundary: the
  session is resolved server-side, then serialised into the principal header.
* **Claimed identity in a request body is ignored.** `tenantId`, `subjectId` and
  `allowedSources` **headers** are not read. A `allowedSources` **body field** may
  only narrow the principal's scope (intersection), and an empty intersection
  means "no evidence", not "no filter".
* **Roles are coarse and deliberate:** `viewer`, `writer`, `operator`, `admin`.
  Reads need `viewer`; ingest/delete need `writer`; traces and stats need
  `operator`; rebuild needs `admin`.
* **Host mode requires the header.** With `RAG_TRUST_PROXY=1` and an API key, a
  request *without* the principal header is treated as a service-to-service call
  with the `service` principal (tenant `local`), never as an anonymous user and
  never as a caller-chosen tenant. Unknown roles in that header are rejected
  with `401`.

### Why the header must only come from the proxy

If the engine port is reachable by a client that can set `x-rag-principal`, that
client can choose its tenant. Bind to loopback, keep the key in the proxy, and
strip inbound copies of the header — the example does the last part explicitly,
and `tests/test_integration_kit.py` proves an impersonation attempt is dropped.

## 3. Surface

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | public | counts, providers, integrity, security posture |
| `GET` | `/api/v1/ready` | public | readiness with reasons |
| `GET` | `/api/v1/config` | viewer | public configuration (no secrets) |
| `GET` | `/api/v1/openapi.json` | public | machine-readable contract |
| `GET` | `/api/v1/sources` | viewer | list sources in scope |
| `POST` | `/api/v1/sources` | writer | ingest text (`{filename,text}`) or multipart file |
| `GET` | `/api/v1/sources/{id}` | viewer | one source, with revision and warnings |
| `GET` | `/api/v1/sources/{id}/chunks` | viewer | chunks with provenance |
| `DELETE` | `/api/v1/sources/{id}` | writer | remove source, chunks, vectors, lexical rows |
| `POST` | `/api/v1/query` (alias `/ask`) | viewer | grounded answer with citations |
| `GET` | `/api/v1/traces` | operator | recent query summaries |
| `GET` | `/api/v1/traces/{queryId}` | operator | stored stage trace (no document text) |
| `GET` | `/api/v1/index/stats` | operator | index and latency statistics |
| `POST` | `/api/v1/index/rebuild` | admin | staged rebuild with rollback |
| `POST` | `/api/v1/index/rollback` | admin | restore pre-rebuild vectors |
| `GET` | `/api/v1/events` | viewer | SSE stage stream |
| `GET` | `/api/v1/events/recent` | viewer | polling fallback |

Legacy `/api/...` routes remain for the bundled console; new integrations should
use `/api/v1`.

## 4. Answer contract

`status` is the contract, not a suggestion:

| status | meaning | what your UI must do |
| --- | --- | --- |
| `answered` | grounded, cited | show the answer with its sources |
| `needs_review` | cited but contested or partially uncited | show it, flag it, list warnings |
| `abstained` | insufficient or filtered-out evidence | say so honestly; do not retry with a bigger `top_k` and present the result as fact |

`warnings` include `conflicting_evidence`, `quarantined_instruction_like_content`,
`citation_removed_unresolvable` and `retrieval_failed`. Treat unknown codes as
displayable strings.

## 5. Error contract

Every error is JSON with `error`, `code`, `message`, `requestId` (and `hint` when
useful):

```json
{"error": "validation_error", "code": "validation_error",
 "message": "question is required", "requestId": "req-1f0c…"}
```

| HTTP | When | Client behaviour |
| --- | --- | --- |
| 400 | bad input (empty question, bad `topK`, malformed `allowedSources`) | fix the request; do not retry |
| 401 | missing/incorrect key or principal header | re-authenticate |
| 403 | role not permitted | do not retry; show a permission message |
| 404 | unknown source/query/trace | treat as gone |
| 409 | conflicting state (e.g. embedding fingerprint mismatch) | run the documented remedy (`rebuild --reembed`) |
| 413 | upload too large | reject early in your UI |
| 429 | rate limit / concurrency limit | retry with backoff; `Retry-After` |
| 5xx | internal failure; the message never contains data | retry idempotent reads; alert |

Both shipped clients implement exactly this table, including "writes are not
retried blindly".

## 6. Tenancy

- Every source, chunk and query row carries a `tenant_id`.
- A principal's tenant is set server-side; queries, listings and traces are
  filtered by it.
- The `local` tenant is the single-user default and is only reachable through a
  local principal.
- Tenant isolation is enforced in SQL predicates, not in the UI. If you add a
  second tenant, add a test that tenant A cannot see tenant B's chunks — there is
  one in `tests/test_api.py` to copy.

## 7. Operations checklist before exposing the service

1. `ragformation doctor` — posture, providers, integrity.
2. `GET /api/v1/ready` — must be `ready: true` with no integrity problems.
3. API key set, engine bound to loopback, proxy in front.
4. `python scripts/bench_scale.py --chunks <your size>` — check latency against
   your budget (`docs/benchmarks.md` explains the linear scan).
5. `ragformation eval --v1 --gate` (or `--gate calibration`) in CI, so retrieval
   quality regressions block merges.
6. Decide what `abstained` looks like in your UI. It is the feature, not the bug.
