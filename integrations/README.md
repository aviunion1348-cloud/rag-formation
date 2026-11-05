# Integration kit

Everything needed to put this engine behind a real application: a zero-dependency
Python client, a zero-dependency Node client, a host-principal proxy example, and
a curl quickstart. All three speak the same versioned surface (`/api/v1`).

The kit is deliberately thin. It does not re-implement retrieval, and it does not
make authorisation decisions — the server owns those. Its job is to make the
correct way to call the service the easy way.

## Contents

| Path | What it is |
| --- | --- |
| `python_client/ragformation_client.py` | stdlib-only client (reads, writes, SSE, structured errors) |
| `node_client/client.mjs` | the same contract for Node 18+ (fetch, AsyncIterator) |
| `host_principal/proxy_example.py` | a worked example of turning *your* session into a server-trusted principal |
| `curl/quickstart.sh` | ingest → ask → trace → delete, with `set -eu` and cleanup |
| `../docs/integration.md` | deployment shapes, auth modes, tenancy, error contract, limits |

## The three identity modes

1. **Local operator (default).** The server binds to loopback, there is no login,
   and every request is the same local principal with all roles. Suitable for one
   person on one machine. `ragformation doctor` prints this posture.
2. **API key.** Set `RAG_API_KEY` and send `x-api-key: …` (or
   `Authorization: Bearer …`). The key is compared in constant time. This
   authenticates *the calling service*, not a user.
3. **Host principal.** `RAG_TRUST_PROXY=1` makes the server trust the
   `x-rag-principal` header (name configurable with `RAG_PRINCIPAL_HEADER`),
   which must contain a JSON object:

   ```json
   {"subjectId": "u-42", "tenantId": "acme", "roles": ["viewer"], "allowedSources": ["src-abc"]}
   ```

   Only a proxy that has already authenticated the user may set that header —
   bind the service to loopback and terminate the proxy in front of it, as the
   example does.

Client-supplied `x-tenant-id`, `x-subject-id` and `x-allowed-sources` *headers*
are ignored by design. A `allowedSources` **body field** may only narrow the
principal's scope; it can never widen it. If you find a path where that is not
true, that is a security bug, not a feature request.

## Quick use (Python)

```python
from ragformation_client import Client

client = Client("http://127.0.0.1:8000")
client.ingest_text("policy.md", "# Retention\n\nAudit logs are retained for 400 days.\n")
answer = client.ask("How long are audit logs retained?")
assert answer["status"] == "answered"
print(answer["answer"], answer["citations"][0]["sourceTitle"])
```

Every failure raises `RagFormationError` with `.code`, `.status`, `.requestId`
and `.hint`, so callers can distinguish "your question was invalid" (400) from
"you are not allowed" (403) from "slow down" (429) without parsing prose.

## Quick use (Node)

```js
import { Client, RagFormationError } from "./node_client/client.mjs";

const client = new Client("http://127.0.0.1:8000");
const answer = await client.ask("How long are audit logs retained?");
console.log(answer.status, answer.citations.length);
```

## Answer contract (what a caller may rely on)

```json
{
  "queryId": "q-…",
  "status": "answered | needs_review | abstained",
  "answer": "… [1]",
  "citations": [{"evidenceId": 1, "chunkId": "src-…", "sourceTitle": "…", "section": "…", "page": 1, "excerpt": "…"}],
  "warnings": ["conflicting_evidence"],
  "metrics": {"totalMs": 12.4, "bestSupport": 0.52},
  "generationMode": "extractive | llm",
  "apiVersion": "1"
}
```

* `abstained` means the system declined; it is a successful response, not an
  error. Render it as an honest "I could not find this", with the reason.
* `needs_review` means citations exist but something needs a human: conflicting
  sources, or an answer with unresolved markers.
* Citation markers in `answer` are only ever resolvable to `citations[]`; the
  server removes markers it cannot bind to a stored chunk.

## Streaming

`GET /api/v1/events` is a server-sent event stream of pipeline stages. The event
contract lives in [docs/events.md](../docs/events.md); `sequence` is **per
trace**, so track it per `traceId` and de-duplicate by `eventId`. Clients without
`EventSource` can poll `GET /api/v1/events/recent`.

## Limits and behaviour you should design for

* `top_k` ≤ 50, question ≤ 2000 characters, upload ≤ `RAG_MAX_SOURCE_BYTES`
  (default 8 MiB), rate limit default 120 requests/minute per key or address.
* Retrieval is brute-force over in-memory vectors: measured behaviour up to
  ~1000 chunks is in [docs/benchmarks.md](../docs/benchmarks.md). Beyond that,
  expect linear growth in query latency.
* Ingestion of the same content is idempotent; changed content replaces the
  revision and removes stale chunks.
* The service never calls a paid API in its own tests or CI.

## Health and readiness

* `GET /api/v1/health` — counts, provider identity, integrity problems, security
  posture. Open without a key so a proxy can probe it.
* `GET /api/v1/ready` — `200` when the index is usable; the body explains *why*
  when it is not.
