#!/usr/bin/env bash
# End-to-end smoke of the HTTP surface with nothing but curl + python3.
#
#   bash integrations/curl/quickstart.sh                    # local loopback server
#   RAG_BASE=http://127.0.0.1:8000 RAG_API_KEY=secret bash integrations/curl/quickstart.sh
#
# It starts nothing and stops nothing: point it at a running server. Every step
# prints what it proved. Exits non-zero on the first unmet expectation.

set -euo pipefail

BASE="${RAG_BASE:-http://127.0.0.1:8000}"
AUTH=()
if [[ -n "${RAG_API_KEY:-}" ]]; then
  AUTH=(-H "x-api-key: ${RAG_API_KEY}")
fi

say() { printf '\n== %s\n' "$1"; }
expect() { # expect <json> <python-expression> <message>
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
expression, message = sys.argv[2], sys.argv[3]
if not eval(expression, {"payload": payload, "len": len}):  # noqa: S307 - test harness
    sys.exit(f"FAILED: {message}\npayload={json.dumps(payload)[:600]}")
print(f"ok: {message}")
PY
}

say "health"
HEALTH=$(curl -fsS "${BASE}/api/v1/health")
expect "$HEALTH" 'payload["chunks"] >= 0' "service answers /api/v1/health"

say "ready"
READY=$(curl -fsS "${BASE}/api/v1/ready")
expect "$READY" '"ready" in payload' "readiness reports a state (with reasons when false)"

say "ingest text"
INGEST=$(curl -fsS -X POST "${BASE}/api/v1/sources" "${AUTH[@]}" \
  -H 'content-type: application/json' \
  -d '{"filename":"quickstart-policy.md","text":"# Retention\n\nQuickstart logs are retained for 400 days.\n"}')
expect "$INGEST" 'payload.get("indexed", 0) >= 1 or payload.get("unchanged", 0) >= 1' "document accepted and indexed"

say "ask"
ANSWER=$(curl -fsS -X POST "${BASE}/api/v1/query" "${AUTH[@]}" \
  -H 'content-type: application/json' \
  -d '{"question":"How long are quickstart logs retained?"}')
expect "$ANSWER" 'payload["status"] in {"answered","needs_review"} and payload["citations"]' \
  "answer is grounded and cited"

say "trace"
QUERY_ID=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["queryId"])' "$ANSWER")
TRACE=$(curl -fsS "${BASE}/api/v1/traces/${QUERY_ID}" "${AUTH[@]}")
expect "$TRACE" 'payload["trace"]["stages"]' "every answer has an inspectable trace (no document text)"

say "scope is enforced server-side"
SCOPED=$(curl -fsS -X POST "${BASE}/api/v1/query" "${AUTH[@]}" \
  -H 'content-type: application/json' \
  -d '{"question":"How long are quickstart logs retained?","allowedSources":["src-does-not-exist"]}')
expect "$SCOPED" 'payload["status"] == "abstained"' "a narrower source filter cannot leak evidence"

say "error contract"
STATUS=$(curl -s -o /tmp/rag-error.json -w '%{http_code}' -X POST "${BASE}/api/v1/query" "${AUTH[@]}" \
  -H 'content-type: application/json' -d '{"question":""}')
expect "$(cat /tmp/rag-error.json)" 'payload.get("code") and payload.get("requestId")' \
  "validation failures carry a code and a request id (HTTP $STATUS)"

say "delete the fixture"
SOURCE_ID=$(python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
results = payload.get("results") or payload.get("sources") or []
print((results[0] or {}).get("sourceId", "") if results else payload.get("primary", {}).get("sourceId", ""))
' "$INGEST")
if [[ -n "$SOURCE_ID" ]]; then
  DELETED=$(curl -fsS -X DELETE "${BASE}/api/v1/sources/${SOURCE_ID}" "${AUTH[@]}")
  expect "$DELETED" 'payload.get("existed") is True or payload.get("deleted") is not None' \
    "fixture removed"
else
  echo "skip: could not determine the source id from the ingest response"
fi

say "done"
echo "quickstart passed against ${BASE}"
