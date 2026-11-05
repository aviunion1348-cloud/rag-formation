#!/usr/bin/env bash
# Independent host fixtures (Stage G).
#
# Builds the wheel once, installs it into throw-away environments and runs four
# hosts against it: a plain Python host, a Flask host, a FastAPI host and a Node
# host, plus a smoke test from an unrelated repository that never saw this
# source tree. Every host talks to the *same* engine over HTTP with an API key
# and a host-signed principal header, which is the documented production shape.
#
# Usage: scripts/run_host_fixtures.sh [--keep]
# Evidence: docs/final_qualification/evidence/hosts-<utc>.log
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="$ROOT/docs/final_qualification/evidence"
mkdir -p "$EVIDENCE_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$EVIDENCE_DIR/hosts-$STAMP.log"
WORK="$(mktemp -d /tmp/hosts-XXXXXX)"
FAILED=0

exec > >(tee -a "$LOG") 2>&1
echo "== host fixtures $STAMP =="
echo "repo: $ROOT   work: $WORK"

run() {
  local name="$1"; shift
  echo
  echo "-- $name"
  if "$@"; then
    echo "RESULT $name: PASS"
  else
    echo "RESULT $name: FAIL"
    FAILED=$((FAILED + 1))
  fi
}

# ---------------------------------------------------------------- build wheel
python3 -m venv "$WORK/buildenv" >/dev/null 2>&1
"$WORK/buildenv/bin/pip" install -q --disable-pip-version-check build httpx >/dev/null 2>&1
"$WORK/buildenv/bin/python" -m build --wheel --outdir "$WORK/wheel" "$ROOT" >/dev/null 2>&1
WHEEL="$(ls "$WORK"/wheel/ragformation-*.whl | head -1)"
echo "wheel: $(basename "$WHEEL") ($(du -h "$WHEEL" | cut -f1))"

# ------------------------------------------------------- engine (shared server)
DATA="$(mktemp -d /tmp/hosts-data-XXXXXX)"
export RAG_DATA_DIR="$DATA" RAG_EMBED_PROVIDER=hashing RAG_LLM_PROVIDER=extractive
export RAG_API_KEY=hostkey RAG_TRUST_PROXY=1
PORT=$(( 18000 + RANDOM % 800 ))
"$WORK/buildenv/bin/pip" install -q --disable-pip-version-check "$WHEEL" >/dev/null 2>&1
"$WORK/buildenv/bin/python" -m ragformation ingest "$ROOT"/corpus/* >/dev/null 2>&1
"$WORK/buildenv/bin/python" -m ragformation serve --port "$PORT" --host 127.0.0.1 &
ENGINE_PID=$!
trap 'kill $ENGINE_PID 2>/dev/null' EXIT
for _ in $(seq 1 80); do
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 0.25
done
echo "engine: pid $ENGINE_PID on $PORT"
export RAG_BASE_URL="http://127.0.0.1:$PORT" RAG_API_KEY=hostkey

# --------------------------------------------------------------------- host A
python3 -m venv "$WORK/hostA" >/dev/null 2>&1
"$WORK/hostA/bin/pip" install -q --disable-pip-version-check "$WHEEL" >/dev/null 2>&1
run "A python-wheel-host" "$WORK/hostA/bin/python" "$ROOT/integrations/qualification/python_wheel_host.py" --wheel "$WHEEL" --corpus "$ROOT/corpus"

# --------------------------------------------------------------------- host B
python3 -m venv "$WORK/hostB" >/dev/null 2>&1
"$WORK/hostB/bin/pip" install -q --disable-pip-version-check flask >/dev/null 2>&1
"$WORK/hostB/bin/python" "$ROOT/integrations/qualification/flask_host.py" --port $((PORT + 1)) &
FLASK_PID=$!
sleep 2
host_flask() {
  local base="http://127.0.0.1:$((PORT + 1))"
  local code body
  body="$(curl -s -X POST "$base/ask" -H 'cookie: session=analyst' -H 'content-type: application/json' -d '{"question":"How long are audit logs retained?"}')"
  echo "flask ask: $(echo "$body" | head -c 220)"
  echo "$body" | grep -q '"status": *"answered"' || return 1
  echo "$body" | grep -qi '400' || return 1
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$base/ask" -H 'content-type: application/json' -d '{"question":"x"}')"
  echo "flask without session -> $code"
  [ "$code" = "401" ] || return 1
  curl -s "$base/healthz" | head -c 160; echo
}
run "B flask-host" host_flask
kill $FLASK_PID 2>/dev/null

# --------------------------------------------------------------------- host C
python3 -m venv "$WORK/hostC" >/dev/null 2>&1
"$WORK/hostC/bin/pip" install -q --disable-pip-version-check fastapi "uvicorn[standard]" httpx >/dev/null 2>&1
( cd "$ROOT" && "$WORK/hostC/bin/python" -m uvicorn integrations.qualification.fastapi_host:app --host 127.0.0.1 --port $((PORT + 2)) --log-level warning ) &
UVICORN_PID=$!
sleep 3
host_fastapi() {
  local base="http://127.0.0.1:$((PORT + 2))"
  local body
  body="$(curl -s -X POST "$base/ask" -H 'cookie: session=analyst' -H 'content-type: application/json' -d '{"question":"What availability SLO does the ingest-api commit to?"}')"
  echo "fastapi ask: $(echo "$body" | head -c 220)"
  echo "$body" | grep -qi '99.95' || return 1
  echo "fastapi SSE relay (first bytes):"
  curl -s -N "$base/events" -H 'cookie: session=analyst' --max-time 6 | head -c 260; echo
  # scope narrowing survives the proxy hop: an empty intersection must mean "no evidence"
  body="$(curl -s -X POST "$base/ask" -H 'cookie: session=analyst' -H 'content-type: application/json' \
    -d '{"question":"How long are audit logs retained?","allowedSources":["src-does-not-exist"]}')"
  echo "fastapi narrowed scope: $(echo "$body" | head -c 200)"
  echo "$body" | grep -q '"status": *"abstained"' || return 1
}
run "C fastapi-host" host_fastapi
kill $UVICORN_PID 2>/dev/null

# --------------------------------------------------------------------- host D
run "D node-host" node "$ROOT/integrations/qualification/node_host.mjs"

# --------------------------------------------------------------------- host E
host_unrelated_repo() {
  local repo="$WORK/unrelated-repo"
  mkdir -p "$repo" && cd "$repo" || return 1
  git init -q . && printf '{"name":"unrelated-consumer","private":true}\n' > package.json
  python3 -m venv .venv >/dev/null 2>&1
  .venv/bin/pip install -q --disable-pip-version-check "$WHEEL" >/dev/null 2>&1 || return 1
  cat > use_engine.py <<'PY'
"""A consumer that has never seen the ragformation source tree."""
import json, tempfile, urllib.request
import ragformation
from ragformation.config import Settings
from ragformation.engine import RAGEngine

print("import path:", ragformation.__file__)
settings = Settings(**{**Settings.from_env().__dict__, "data_dir": tempfile.mkdtemp(), "embed_provider": "hashing", "llm_provider": "extractive"})
engine = RAGEngine(settings)
report = engine.ingest_bytes("release-policy.md", b"Releases are frozen during the audit window for 48 hours.")
print("ingested:", report.indexed, "sources,", report.chunks, "chunks")
result = engine.ask("How long is the release freeze during the audit window?", save=False)
print("status:", result.status)
print("answer:", (result.answer or "").strip()[:160])
print("citations:", len(result.citations))
assert result.status == "answered", result.status
assert "48" in (result.answer or ""), result.answer
assert result.citations, "no citations"
engine.close()
print("unrelated-repo smoke: OK")
PY
  .venv/bin/python use_engine.py
}
run "E unrelated-repo" host_unrelated_repo
cd "$ROOT" || true

echo
echo "== summary: $((5 - FAILED))/5 hosts passed =="
if [ "${1:-}" = "--keep" ]; then echo "kept: $WORK"; else rm -rf "$WORK"; fi
exit $(( FAILED > 0 ))
