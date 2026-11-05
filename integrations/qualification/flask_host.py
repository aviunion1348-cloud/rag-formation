#!/usr/bin/env python3
"""Host fixture B — a Flask application that owns identity in front of the engine.

The shape is the documented production one: the browser talks to *this* app, the
app resolves the session, and the app calls a loopback-bound engine with an API
key plus a server-signed principal header. The engine never sees a browser and
never trusts a client-supplied tenant.

    RAG_BASE_URL=http://127.0.0.1:8787 RAG_API_KEY=... python flask_host.py --port 8081

Routes:

* ``GET  /``        — tiny HTML page with one question box,
* ``POST /ask``     — session cookie -> principal -> engine ``/api/v1/ask``,
* ``GET  /healthz`` — host liveness plus engine reachability.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

from flask import Flask, jsonify, request

app = Flask(__name__)

ENGINE = os.environ.get("RAG_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
API_KEY = os.environ.get("RAG_API_KEY", "")
SESSIONS = {
    # Two demo sessions with different scopes. The host decides who may see what.
    "session=analyst": {"subjectId": "analyst", "tenantId": "local", "roles": ["viewer"]},
    "session=ops": {"subjectId": "ops", "tenantId": "local", "roles": ["operator"]},
}


def principal_for(cookie_header: str) -> dict | None:
    """Map a session cookie to a principal. Unknown sessions get nothing."""

    return SESSIONS.get((cookie_header or "").strip())


def engine_request(path: str, principal: dict, payload: dict | None = None) -> tuple[int, dict]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-api-key": API_KEY,
        "x-rag-principal": json.dumps(principal, separators=(",", ":")),
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_ = urllib.request.Request(f"{ENGINE}{path}", data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request_, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


@app.get("/")
def index():
    return (
        "<!doctype html><meta charset=utf-8><title>Flask host</title>"
        "<h1>Flask host in front of ragformation</h1>"
        "<form method=post action=/ask>"
        "<input name=question value='How long are audit logs retained?' size=50>"
        "<button>Ask</button></form>"
        "<p>Set the cookie <code>session=analyst</code> to be authorised.</p>"
    )


@app.post("/ask")
def ask_route():
    principal = principal_for(request.headers.get("Cookie", ""))
    if principal is None:
        return jsonify({"error": "not signed in"}), 401
    question = (request.form.get("question") or (request.get_json(silent=True) or {}).get("question") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    status, body = engine_request("/api/v1/ask", principal, {"question": question, "topK": 6})
    # The host re-shapes the payload: the browser never receives evidence text.
    return (
        jsonify(
            {
                "question": question,
                "status": body.get("status"),
                "answer": body.get("answer"),
                "citations": [
                    {"title": c.get("sourceTitle"), "section": c.get("section")}
                    for c in body.get("citations") or []
                ],
                "engineStatus": status,
            }
        ),
        status,
    )


@app.get("/healthz")
def health():
    status, body = engine_request("/healthz", {"subjectId": "flask-host", "tenantId": "local", "roles": ["viewer"]})
    return jsonify({"host": "ok", "engine": body.get("status"), "engineHttp": status})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
