# Event contract (v1)

Every ingest, query and index operation publishes a stream of stage events. The
S3 Experience draws its Live mode from this stream, and operators use it to see
where time went. Because a UI and an operator dashboard both consume it, the
contract is versioned and small.

- Envelope version: `schemaVersion` (currently `1.0`), reported by
  `GET /api/v1/health` as `eventSchemaVersion`.
- Contract name: `ragformation.events`.
- Transports: server-sent events at `GET /api/v1/events`; polling at
  `GET /api/v1/events/recent`.

## Envelope

```json
{
  "schemaVersion": "1.0",
  "eventId": "evt-56a51c43a499",
  "traceId": "job-c80cb7de7787",
  "sequence": 1,
  "timestamp": "2026-11-05T14:27:52.886Z",
  "operationId": "job-c80cb7de7787",
  "stage": "ingestion.source.accepted",
  "status": "started",
  "durationMs": null,
  "counts": {"sourceId": "src-…", "mediaType": "text/markdown", "bytes": 1173, "tenantId": "local"},
  "metrics": {"bytes": 1173},
  "references": [{"type": "source", "id": "src-…"}],
  "safeSummary": "ingestion.source.accepted started",
  "mode": "live",
  "warningCodes": []
}
```

| Field | Meaning |
| --- | --- |
| `schemaVersion` | major.minor of the envelope; a client must reject a higher **major** |
| `eventId` | unique per event; de-duplicate on this |
| `traceId` | one ingest job or one query |
| `sequence` | **per trace**, starting at 1 — not global |
| `stage` | dotted stage name (see below) |
| `status` | `started`, `completed`, `skipped`, `failed` |
| `durationMs` | set on completion; `null` for notes and starts |
| `counts` | small integers/ids summarising the stage |
| `metrics` | numeric subset of `counts` |
| `references` | typed pointers (`source`, `chunk`, `query`) |
| `safeSummary` | human text, truncated, never document content |
| `mode` | `live` for provider-backed generation, `extractive` otherwise |
| `warningCodes` | codes such as `conflicting_evidence`, `quarantined_instruction_like_content` |

### What is never in an event

No document text, no prompt, no answer, no API key, no session token, no email
address. Details are whitelisted scalars and truncated to 120 characters; longer
strings are cut with `…`. Anything else is stringified and truncated, or dropped
by type. If you find payload content in a trace, that is a bug — see
`ragformation/engine.py::_safe_detail` and its tests.

## Stages

Ingestion (`traceId` = job id):

```
ingestion.source.accepted → ingestion.extracted → ingestion.chunking
  → ingestion.embedding → ingestion.index.committed
ingestion.duplicate   (content unchanged; nothing was re-embedded)
```

Query (`traceId` = query id):

```
query.received → query.embedding
  → retrieval.semantic | retrieval.lexical   (a channel that did not run says "skipped" and why)
  → retrieval.fusion → retrieval.rerank → retrieval.mmr
  → context.assembly → guardrail.scan
  → generation (+ generation.first_token in LLM mode)
  → citation.validation → [abstention]
  → response.ready
```

Maintenance: `source.delete`, `index.rebuild`.

`skipped` is a first-class status: a stage that did not run is reported with a
reason, never omitted and never faked as completed. In extractive mode there is
no `generation.first_token` event and no token timings, because no model ran.

## Consuming the stream

Server-sent events:

```bash
curl -N -H "x-api-key: $RAG_API_KEY" "http://127.0.0.1:8000/api/v1/events?trace_id=q-…"
```

Rules that matter for a client:

1. **Track `sequence` per `traceId`.** A missing number inside one trace means
   events were dropped; do not treat two traces as one sequence.
2. **De-duplicate by `eventId`.** Reconnects and polling overlap.
3. **Reject unknown majors.** `event_version`/`schemaVersion` `2.x` must not be
   interpreted with a 1.x client.
4. **Reconnect with backoff.** The server sends `retry: 2000` and a heartbeat
   comment every 10 s; the stream closes after 5 minutes by design.
5. **Fall back to polling** when `EventSource` is unavailable or blocked by a
   proxy: `GET /api/v1/events/recent?since=<sequence>` returns up to 64 events.

`reference` JavaScript clients live in
`ragformation/web/experience/js/events.js` (SSE + polling, gap detection) and
`integrations/python_client/ragformation_client.py` (`stream_events()`).

## Compatibility policy

- Additive fields may appear within a major version; clients must ignore what
  they do not know.
- Removing a field, renaming a stage, or changing a status value requires a
  major bump.
- The event stream is diagnostic, not transactional. Durable state lives in
  SQLite (`GET /api/v1/traces/{queryId}` returns the stored trace for a query).
