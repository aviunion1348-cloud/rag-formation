#!/usr/bin/env python3
"""Reference host-principal proxy: your session in front of the RAG service.

The shape this example demonstrates is the one that avoids the classic mistake:
**the browser never talks to the RAG service directly.** Your application (which
already knows who the user is) authenticates the request, then forwards a
server-trusted principal header to a loopback-bound engine.

What it enforces:

* the engine is reached on loopback only;
* the proxy strips any client-supplied principal headers before adding its own,
  so a malicious browser cannot claim another tenant;
* the API key lives in the proxy's environment, never in the page;
* the tenant and source allow-list come from your session store, not the request.

Run it (development only):

    # 1. engine, loopback, trusting the proxy header, key required
    RAG_API_KEY=dev-secret RAG_TRUST_PROXY=1 \\
        ragformation serve --host 127.0.0.1 --port 8000

    # 2. this proxy
    RAG_API_KEY=dev-secret RAG_UPSTREAM=http://127.0.0.1:8000 \\
        python integrations/host_principal/proxy_example.py --port 8080

Then, in another terminal, play the role of a logged-in user:

    curl -s -X POST http://127.0.0.1:8080/ask \\
        -H 'cookie: session=alice' -H 'content-type: application/json' \\
        -d '{"question": "How long are audit logs retained?"}' | head -20

    # and prove the impersonation attempt is ignored:
    curl -s -X POST http://127.0.0.1:8080/ask \\
        -H 'cookie: session=alice' -H 'x-rag-principal: {"subjectId":"root","tenantId":"other","roles":["admin"]}' \\
        -H 'content-type: application/json' -d '{"question": "audit logs"}' | head -20

This file is an example, not a product: it has no TLS, no CSRF protection and no
session storage. Use it to copy the *trust boundary*, then implement the session
part in your own framework.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

FORWARDED_HEADERS = {"accept", "content-type"}


def session_to_principal(cookie_header: str) -> Optional[Dict[str, Any]]:
    """Stand-in for your real session lookup.

    Returns the principal your host is willing to vouch for, or ``None`` when the
    request is unauthenticated. In a real app this is a signed-cookie or
    session-table lookup.
    """

    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "session" and value:
            return {
                "subjectId": value,
                "tenantId": "acme",
                "roles": ["viewer"],
                "issuer": "example-proxy",
            }
    return None


class ProxyHandler(BaseHTTPRequestHandler):
    upstream = ""
    api_key = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # no request bodies
        sys.stderr.write("[proxy] " + (fmt % args) + "\n")

    def _forward(self, method: str) -> None:
        principal = session_to_principal(self.headers.get("cookie") or "")
        if principal is None:
            self._json(401, {"code": "unauthenticated", "message": "sign in first"})
            return

        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else None

        # Only allow the client to choose *which* question, never *who* it is.
        if body:
            try:
                parsed = json.loads(body)
            except ValueError:
                self._json(400, {"code": "invalid_json", "message": "body must be JSON"})
                return
            parsed.pop("tenantId", None)
            parsed.pop("principal", None)
            body = json.dumps(parsed).encode()

        headers = {name: self.headers[name] for name in FORWARDED_HEADERS if self.headers.get(name)}
        headers["x-api-key"] = self.api_key
        # Deliberately set *after* stripping anything the browser sent.
        headers["x-rag-principal"] = json.dumps(principal, separators=(",", ":"))

        request = urllib.request.Request(
            f"{self.upstream}{self.path}", data=body, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                status = response.status
                content_type = response.headers.get("content-type", "application/json")
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            content_type = exc.headers.get("content-type", "application/json")
        except urllib.error.URLError as exc:
            self._json(502, {"code": "upstream_unavailable", "message": str(exc.reason)})
            return

        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(payload)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._forward("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._forward("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._forward("DELETE")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    upstream = os.environ.get("RAG_UPSTREAM", "http://127.0.0.1:8000").rstrip("/")
    api_key = os.environ.get("RAG_API_KEY", "")
    if not api_key:
        print("RAG_API_KEY must be set: the proxy authenticates to the engine", file=sys.stderr)
        return 2

    ProxyHandler.upstream = upstream
    ProxyHandler.api_key = api_key
    server = ThreadingHTTPServer((args.host, args.port), ProxyHandler)
    print(f"proxy on http://{args.host}:{args.port} -> {upstream} (session cookie: session=<user>)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
