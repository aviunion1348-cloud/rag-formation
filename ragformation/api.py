"""Zero-dependency HTTP service: versioned JSON API, SSE events, static console.

Route surface
-------------

``/api/v1/...``
    The stable, versioned contract (documented in ``ragformation/openapi.py`` and
    served at ``/api/v1/openapi.json``).

``/api/...``
    The original 0.1 routes, kept as compatibility aliases for existing clients.

``/``, ``/experience``
    The operator console and the S3 Experience (static files from the package).

Security posture
----------------

* Loopback bind by default. A non-loopback bind without authentication is
  refused by :meth:`Settings.validate` unless the operator explicitly accepts the
  risk with ``RAG_ALLOW_INSECURE_PUBLIC=1``.
* Every API route resolves a :class:`~ragformation.auth.Principal` server-side.
  Client-supplied tenant/subject headers are never trusted.
* Request bodies are size-limited, JSON-object-only, and file content is parsed in
  memory — uploads never touch the filesystem.
* Static serving is confined to the packaged ``web/`` directory; traversal and
  symlink escapes are rejected.
* Unexpected exceptions return a generic 500. Details stay in the server log.
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import posixpath
import re
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .auth import Principal, check_deployment, resolve_principal
from .config import API_VERSION, Settings
from .engine import RAGEngine
from .errors import AuthError, NotFound, RagError, RateLimited, ValidationError
from .events import EventBus, sse_frame
from .openapi import document as openapi_document

MAX_BODY_BYTES = 12 * 1024 * 1024
WEB_ROOT = Path(__file__).resolve().parent / "web"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

#: Routes that never require a principal (probes and the documents themselves).
PUBLIC_PATHS = {
    "/api/health",
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/openapi.json",
    "/api/openapi.json",
    "/healthz",
    "/readyz",
}


@dataclass
class RequestContext:
    """Per-request, thread-local state. Never stored on the shared :class:`Api`."""

    principal: Principal = field(default_factory=lambda: Principal())
    request_id: str = field(default_factory=lambda: "req-" + uuid.uuid4().hex[:12])
    headers: Dict[str, str] = field(default_factory=dict)
    query: Dict[str, str] = field(default_factory=dict)


class TokenBucket:
    """Small per-key rate limiter (thread-safe, no background thread)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(1, int(per_minute))
        self._lock = threading.Lock()
        self._buckets: Dict[str, Tuple[float, float]] = {}

    def allow(self, key: str, cost: float = 1.0) -> Tuple[bool, float]:
        now = time.monotonic()
        capacity = float(self.per_minute)
        refill = capacity / 60.0
        with self._lock:
            tokens, last = self._buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * refill)
            if tokens >= cost:
                self._buckets[key] = (tokens - cost, now)
                if len(self._buckets) > 4096:  # bound memory
                    self._buckets = dict(list(self._buckets.items())[-1024:])
                return True, 0.0
            self._buckets[key] = (tokens, now)
            return False, round((cost - tokens) / refill, 2)


class Api:
    """Route table over a shared engine instance.

    Handlers take ``(ctx, body)`` and return a JSON-ready mapping. All
    authorisation decisions happen here, never in the browser.
    """

    def __init__(self, engine: RAGEngine, settings: Settings, *, event_bus: Optional[EventBus] = None) -> None:
        self.engine = engine
        self.settings = settings
        self.events: EventBus = event_bus or engine.events
        self.limiter = TokenBucket(settings.rate_limit_per_minute)
        self._query_slots = threading.BoundedSemaphore(max(1, settings.max_concurrent_queries))
        self.started_at = time.time()

        self.routes: Dict[Tuple[str, str], Callable[[RequestContext, Dict[str, Any]], Any]] = {
            ("GET", "/api/health"): self.health,
            ("GET", "/api/config"): self.config,
            ("GET", "/api/sources"): self.sources,
            ("GET", "/api/stats"): self.stats,
            ("GET", "/api/doctor"): self.doctor,
            ("GET", "/api/jobs"): self.jobs,
            ("POST", "/api/ask"): self.ask,
            ("POST", "/api/sources/upload"): self.upload,
            ("POST", "/api/sources/text"): self.add_text,
            ("POST", "/api/ingest"): self.ingest_server_path,
            ("POST", "/api/rebuild"): self.rebuild,
            ("POST", "/api/feedback"): self.feedback,
        }

        #: (method, regex, handler) for the versioned surface.
        self.v1_routes: List[Tuple[str, "re.Pattern[str]", Callable[[RequestContext, Dict[str, Any], "re.Match[str]"], Any]]] = [
            ("GET", re.compile(r"^/api/v1/health$"), lambda ctx, body, m: self.health(ctx, body)),
            ("GET", re.compile(r"^/api/v1/ready$"), lambda ctx, body, m: self.ready(ctx, body)),
            ("GET", re.compile(r"^/api/v1/config$"), lambda ctx, body, m: self.config(ctx, body)),
            ("GET", re.compile(r"^/api/v1/sources$"), lambda ctx, body, m: self.sources(ctx, body)),
            ("POST", re.compile(r"^/api/v1/sources$"), lambda ctx, body, m: self.ingest_or_upload(ctx, body)),
            ("GET", re.compile(r"^/api/v1/sources/(?P<source_id>[^/]+)$"), self.v1_source),
            ("DELETE", re.compile(r"^/api/v1/sources/(?P<source_id>[^/]+)$"), self.v1_delete_source),
            ("GET", re.compile(r"^/api/v1/sources/(?P<source_id>[^/]+)/chunks$"), self.v1_chunks),
            ("GET", re.compile(r"^/api/v1/ingestions/(?P<job_id>[^/]+)$"), self.v1_ingestion),
            ("POST", re.compile(r"^/api/v1/query$"), lambda ctx, body, m: self.ask(ctx, body)),
            ("POST", re.compile(r"^/api/v1/ask$"), lambda ctx, body, m: self.ask(ctx, body)),
            ("GET", re.compile(r"^/api/v1/traces$"), lambda ctx, body, m: self.v1_traces(ctx, body)),
            ("GET", re.compile(r"^/api/v1/traces/(?P<query_id>[^/]+)$"), self.v1_trace),
            ("GET", re.compile(r"^/api/v1/index/stats$"), lambda ctx, body, m: self.stats(ctx, body)),
            ("POST", re.compile(r"^/api/v1/index/rebuild$"), lambda ctx, body, m: self.rebuild(ctx, body)),
            ("POST", re.compile(r"^/api/v1/index/rollback$"), lambda ctx, body, m: self.rollback(ctx, body)),
            ("GET", re.compile(r"^/api/v1/events$"), lambda ctx, body, m: self.v1_events_info(ctx, body)),
            ("GET", re.compile(r"^/api/v1/events/recent$"), lambda ctx, body, m: self.v1_events_recent(ctx, body)),
            ("GET", re.compile(r"^/api/v1/openapi.json$"), lambda ctx, body, m: openapi_document(self.settings)),
        ]

    # ------------------------------------------------------------------ helpers
    def principal_for(self, headers: Dict[str, str], path: str) -> Principal:
        if path in PUBLIC_PATHS:
            # Probes stay open, but still carry a principal for consistent logging.
            try:
                return resolve_principal(headers, self.settings, require_auth=False)
            except AuthError:
                return Principal(subject_id="anonymous", tenant_id="local", roles=frozenset({"viewer"}), issuer="anonymous")
        return resolve_principal(headers, self.settings)

    def _limit(self, ctx: RequestContext, default: int = 50, maximum: int = 500) -> int:
        raw = ctx.query.get("limit")
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValidationError("limit must be an integer") from exc
        if value < 1:
            raise ValidationError("limit must be >= 1")
        return min(value, maximum)

    # ------------------------------------------------------------- legacy routes
    def health(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.engine.health()

    def ready(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        health = self.engine.health()
        problems = health["integrity"]["problems"]
        ready = health["chunks"] > 0 and not problems
        return {
            "ready": ready,
            "chunks": health["chunks"],
            "sources": health["sources"],
            "integrity": problems,
            "embedding": {
                "provider": health["embedding"]["provider"],
                "semantic": health["embedding"]["semantic"],
            },
            "generationMode": health["generation"]["mode"],
            "reasons": [] if ready else (
                ["index is empty"] if health["chunks"] == 0 else ["index integrity problems"]
            ),
        }

    def config(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        health = self.engine.health()
        public = self.settings.to_public_dict()
        return {
            "config": public,
            "providers": health["embedding"],
            "generation": health["generation"],
            "security": health["security"],
            "version": __version__,
            "apiVersion": API_VERSION,
            "eventSchemaVersion": health["eventSchemaVersion"],
        }

    def sources(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        limit = self._limit(ctx)
        cursor = ctx.query.get("cursor")
        items = self.engine.list_sources(principal=ctx.principal)
        if cursor:
            ids = [item["sourceId"] for item in items]
            if cursor in ids:
                items = items[ids.index(cursor) + 1 :]
        page = items[:limit]
        next_cursor = page[-1]["sourceId"] if len(items) > limit and page else None
        return {
            "sources": page,
            "count": len(page),
            "total": len(items),
            "nextCursor": next_cursor,
        }

    def stats(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.engine.stats(principal=ctx.principal)

    def doctor(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ctx.principal.require("stats")
        return self.engine.doctor()

    def jobs(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ctx.principal.require("stats")
        return {"jobs": self.engine.store.recent_jobs(limit=self._limit(ctx, 50, 200))}

    def ask(self, ctx: RequestContext, body: Dict[str, Any]) -> Dict[str, Any]:
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValidationError("question is required")
        top_k = body.get("topK")
        if top_k is not None:
            try:
                top_k = int(top_k)
            except (TypeError, ValueError) as exc:
                raise ValidationError("topK must be an integer") from exc
        mode = body.get("mode")
        if mode is not None and mode not in {"hybrid", "semantic", "lexical"}:
            raise ValidationError("mode must be hybrid, semantic or lexical")
        temperature = body.get("temperature")
        if temperature is not None:
            try:
                temperature = float(temperature)
            except (TypeError, ValueError) as exc:
                raise ValidationError("temperature must be a number") from exc
            if not 0.0 <= temperature <= 2.0:
                raise ValidationError("temperature must be between 0 and 2")
        top_p = body.get("topP")
        if top_p is not None:
            try:
                top_p = float(top_p)
            except (TypeError, ValueError) as exc:
                raise ValidationError("topP must be a number") from exc

        if not self._query_slots.acquire(blocking=False):
            raise RateLimited(
                "the service is at its concurrency limit",
                hint="retry shortly or raise RAG_MAX_CONCURRENT_QUERIES",
            )
        try:
            result = self.engine.ask(
                question,
                top_k=top_k,
                mode=mode,
                temperature=temperature,
                top_p=top_p,
                principal=ctx.principal,
            )
        finally:
            self._query_slots.release()
        payload = result.to_dict()
        payload["requestId"] = ctx.request_id
        return payload

    def upload(self, ctx: RequestContext, body: Dict[str, Any]) -> Dict[str, Any]:
        ctx.principal.require("ingest")
        filename = body.get("filename")
        payload = body.get("contentBase64")
        if not isinstance(filename, str) or not filename.strip():
            raise ValidationError("filename is required")
        if not isinstance(payload, str) or not payload:
            raise ValidationError("contentBase64 is required")
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationError("contentBase64 is not valid base64") from exc
        if len(data) > self.settings.max_source_bytes:
            raise ValidationError(
                "uploaded source is too large",
                hint=f"limit is {self.settings.max_source_bytes // 1024} KiB",
            )
        return self.engine.ingest_bytes(
            filename,
            data,
            title=body.get("title"),
            origin=f"upload:{filename}",
            tenant_id=ctx.principal.tenant_id,
        )

    def add_text(self, ctx: RequestContext, body: Dict[str, Any]) -> Dict[str, Any]:
        ctx.principal.require("ingest")
        title = body.get("title")
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("text is required")
        if len(text.encode("utf-8")) > self.settings.max_source_bytes:
            raise ValidationError("text is too large")
        name = (title or "pasted-note").strip() or "pasted-note"
        safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_.").strip() or "pasted-note"
        return self.engine.ingest_bytes(
            f"{safe}.md", text.encode("utf-8"), title=name, tenant_id=ctx.principal.tenant_id
        )

    def ingest_server_path(self, ctx: RequestContext, body: Dict[str, Any]) -> Dict[str, Any]:
        ctx.principal.require("ingest")
        if not self.settings.allow_path_ingest:
            raise ValidationError(
                "server-side path ingestion is disabled",
                hint="set RAG_ALLOW_PATH_INGEST=1 to enable it, or upload files instead",
            )
        path = body.get("path") or "corpus"
        if not isinstance(path, str):
            raise ValidationError("path must be a string")
        return self.engine.ingest_path([path], tenant_id=ctx.principal.tenant_id)

    def ingest_or_upload(self, ctx: RequestContext, body: Dict[str, Any]) -> Dict[str, Any]:
        """``POST /api/v1/sources`` accepts text, base64 upload or a server path."""

        if body.get("contentBase64"):
            return self.upload(ctx, body)
        if body.get("text"):
            return self.add_text(ctx, body)
        if body.get("path"):
            return self.ingest_server_path(ctx, body)
        raise ValidationError(
            "provide one of: text, contentBase64+filename, or path",
            hint="see /api/v1/openapi.json",
        )

    def rebuild(self, ctx: RequestContext, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.engine.rebuild(
            force=bool((body or {}).get("force")), principal=ctx.principal
        )

    def rollback(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.engine.rollback_rebuild(principal=ctx.principal)

    def feedback(self, ctx: RequestContext, body: Dict[str, Any]) -> Dict[str, Any]:
        query_id = body.get("queryId")
        rating = body.get("rating")
        if not isinstance(query_id, str) or not query_id:
            raise ValidationError("queryId is required")
        if rating not in (-1, 1, 0):
            raise ValidationError("rating must be -1, 0 or 1")
        feedback_id = self.engine.store.record_feedback(
            query_id, int(rating), str(body.get("note") or "")[:500]
        )
        return {"feedbackId": feedback_id}

    # --------------------------------------------------------------- v1 routes
    def v1_source(self, ctx: RequestContext, body: Dict[str, Any], match: "re.Match[str]") -> Dict[str, Any]:
        source_id = unquote(match.group("source_id"))
        return {"source": self.engine.get_source(source_id, principal=ctx.principal)}

    def v1_delete_source(self, ctx: RequestContext, body: Dict[str, Any], match: "re.Match[str]") -> Dict[str, Any]:
        source_id = unquote(match.group("source_id"))
        return self.engine.delete_source(source_id, principal=ctx.principal)

    def v1_chunks(self, ctx: RequestContext, body: Dict[str, Any], match: "re.Match[str]") -> Dict[str, Any]:
        source_id = unquote(match.group("source_id"))
        return {"chunks": self.engine.source_chunks(source_id, principal=ctx.principal)}

    def v1_ingestion(self, ctx: RequestContext, body: Dict[str, Any], match: "re.Match[str]") -> Dict[str, Any]:
        ctx.principal.require("stats")
        job = self.engine.store.get_job(unquote(match.group("job_id")))
        if job is None:
            raise NotFound("ingestion job not found")
        return {"job": job}

    def v1_traces(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ctx.principal.require("traces")
        limit = self._limit(ctx, 50, 200)
        rows = self.engine.store.query_rows(
            limit=limit,
            tenant_id=None if ctx.principal.is_local else ctx.principal.tenant_id,
        )
        return {
            "traces": [
                {
                    "queryId": row["query_id"],
                    "question": row["question"][:200],
                    "status": row["status"],
                    "latencyMs": row["latency_ms"],
                    "createdAt": row["created_at"],
                }
                for row in rows
            ],
            "count": len(rows),
        }

    def v1_trace(self, ctx: RequestContext, body: Dict[str, Any], match: "re.Match[str]") -> Dict[str, Any]:
        return {"trace": self.engine.get_trace(unquote(match.group("query_id")), principal=ctx.principal)}

    def v1_events_info(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Metadata for the SSE stream (the stream itself is handled by the server)."""

        return {
            "stream": "/api/v1/events",
            "transport": "text/event-stream",
            "schemaVersion": self.engine.health()["eventSchemaVersion"],
            "subscribers": self.events.subscriber_count,
            "history": len(self.events.history(limit=1)),
            "hint": "connect with EventSource; pass ?trace_id=… to follow one trace",
        }

    def v1_events_recent(self, ctx: RequestContext, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Polling fallback for clients without EventSource (or behind a proxy)."""

        try:
            since = int(ctx.query.get("since") or 0)
        except (TypeError, ValueError):
            since = 0
        trace_id = ctx.query.get("trace_id") or None
        events = list(self.events.history(trace_id=trace_id, since=since, limit=64))
        return {
            "events": events,
            "count": len(events),
            "contract": "ragformation.events",
            "eventVersion": self.engine.health()["eventSchemaVersion"],
            "stream": "/api/v1/events",
            "transport": "sse-with-polling-fallback",
        }

    # -------------------------------------------------------------- dispatcher
    def dispatch(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        *,
        ctx: Optional[RequestContext] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        body = body or {}
        ctx = ctx or RequestContext()
        route = self.routes.get((method, path))
        if route is not None:
            return 200, route(ctx, body)

        for verb, pattern, handler in self.v1_routes:
            match = pattern.match(path)
            if match and verb == method:
                return 200, handler(ctx, body, match)

        # Legacy parameterised routes.
        if method == "GET" and path.startswith("/api/sources/") and path.endswith("/chunks"):
            source_id = unquote(path[len("/api/sources/") : -len("/chunks")])
            return 200, {"chunks": self.engine.source_chunks(source_id, principal=ctx.principal)}

        if method == "GET" and path.startswith("/api/sources/") and path.count("/") == 3:
            source_id = unquote(path[len("/api/sources/") :])
            return 200, {"source": self.engine.get_source(source_id, principal=ctx.principal)}

        if method == "DELETE" and path.startswith("/api/sources/"):
            source_id = unquote(path[len("/api/sources/") :])
            return 200, self.engine.delete_source(source_id, principal=ctx.principal)

        raise NotFound("no such endpoint", path=path)


def make_handler(api: Api, settings: Settings):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ragformation/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter, no payloads
            sys.stderr.write("[ragformation] " + (fmt % args) + "\n")

        # ---------------------------------------------------------- plumbing
        def _headers_lower(self) -> Dict[str, str]:
            return {key.lower(): value for key, value in self.headers.items()}

        def _cors(self) -> None:
            if not settings.cors_origin:
                return  # same-origin only: no cross-origin grant
            self.send_header("Access-Control-Allow-Origin", settings.cors_origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Api-Key, Authorization, X-Request-Id")

        def _send(self, status: int, payload: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Request-Id", getattr(self, "request_id", "-"))
            self.send_header("X-Content-Type-Options", SECURITY_HEADERS["X-Content-Type-Options"])
            for key, value in SECURITY_HEADERS.items():
                if key in {"X-Content-Type-Options", "Cache-Control"}:
                    continue
                self.send_header(key, value)
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self._cors()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _json(self, status: int, payload: Any, extra: Optional[Dict[str, str]] = None) -> None:
            self._send(status, json.dumps(payload, default=str).encode("utf-8"), "application/json; charset=utf-8", extra)

        def _read_body(self) -> Dict[str, Any]:
            length_header = self.headers.get("Content-Length")
            if not length_header:
                return {}
            try:
                length = int(length_header)
            except ValueError as exc:
                raise ValidationError("invalid Content-Length") from exc
            if length <= 0:
                return {}
            if length > MAX_BODY_BYTES:
                raise RagError("request body too large", code="payload_too_large", limit=MAX_BODY_BYTES)
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError("body must be valid JSON") from exc
            if not isinstance(data, dict):
                raise ValidationError("body must be a JSON object")
            return data

        # ------------------------------------------------------------- routes
        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/healthz", "/readyz"):
                path = "/api/v1/health" if path == "/healthz" else "/api/v1/ready"
            if path == "/api/v1/events":
                self._stream_events(parse_qs(parsed.query))
                return
            if path.startswith("/api/"):
                self._handle_api("GET", path, parse_qs(parsed.query))
                return
            self._serve_static(path)

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            self._handle_api("DELETE", parsed.path, parse_qs(parsed.query))

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            self._handle_api("POST", parsed.path, parse_qs(parsed.query))

        def _context(self, path: str, query: Optional[Dict[str, List[str]]] = None) -> RequestContext:
            headers = self._headers_lower()
            self.request_id = headers.get("x-request-id") or ("req-" + uuid.uuid4().hex[:12])
            if len(self.request_id) > 80:
                self.request_id = self.request_id[:80]
            errors = []
            principal: Principal
            try:
                principal = api.principal_for(headers, path)
            except RagError as exc:
                errors.append(exc)
                principal = Principal(subject_id="anonymous", tenant_id="local", roles=frozenset(), issuer="anonymous")
            flat_query = {key: values[-1] for key, values in (query or {}).items() if values}
            ctx = RequestContext(principal=principal, request_id=self.request_id, headers=headers, query=flat_query)
            if errors:
                raise errors[0]
            return ctx

        def _rate_limit_key(self, ctx: RequestContext) -> str:
            key = ctx.headers.get("x-api-key") or ""
            if not key:
                key = self.client_address[0] if self.client_address else "unknown"
            return "k:" + base64.b64encode(key.encode()[:48]).decode()

        def _handle_api(self, method: str, path: str, query: Optional[Dict[str, List[str]]] = None) -> None:
            try:
                ctx = self._context(path, query)
            except RagError as exc:
                self._error(exc, path)
                return
            if path not in PUBLIC_PATHS:
                allowed, retry_after = api.limiter.allow(self._rate_limit_key(ctx))
                if not allowed:
                    self._error(
                        RateLimited("rate limit exceeded", hint=f"retry in {retry_after:.1f}s"),
                        path,
                        extra={"Retry-After": str(max(1, int(retry_after)))},
                    )
                    return
            try:
                body = self._read_body() if method in {"POST", "PUT", "PATCH"} else {}
                status, payload = api.dispatch(method, path, body, ctx=ctx)
                if isinstance(payload, dict):
                    payload.setdefault("requestId", ctx.request_id)
                self._json(status, payload)
            except RagError as exc:
                self._error(exc, path)
            except Exception:  # pragma: no cover - surfaced in logs only
                traceback.print_exc(file=sys.stderr)
                self._json(
                    500,
                    {
                        "error": "internal_error",
                        "code": "internal_error",
                        "message": "the request failed; see server logs",
                        "requestId": getattr(self, "request_id", "-"),
                    },
                )

        def _error(self, exc: RagError, path: str, extra: Optional[Dict[str, str]] = None) -> None:
            payload = exc.to_dict()
            payload["code"] = payload.get("error")
            payload["requestId"] = getattr(self, "request_id", "-")
            if path.startswith("/api/v1/"):
                payload["apiVersion"] = API_VERSION
            self._json(exc.http_status, payload, extra)

        # ------------------------------------------------------ event streaming
        def _stream_events(self, query: Dict[str, List[str]]) -> None:
            try:
                ctx = self._context("/api/v1/events", query)
            except RagError as exc:
                self._error(exc, "/api/v1/events")
                return
            try:
                ctx.principal.require("read")
            except RagError as exc:
                self._error(exc, "/api/v1/events")
                return
            trace_id = (query.get("trace_id") or [""])[-1] or None
            try:
                since = int((query.get("since") or ["0"])[-1])
            except ValueError:
                since = 0
            subscription = api.events.subscribe(trace_id)
            self.close_connection = True
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self._cors()
                self.end_headers()
                self.wfile.write(b"retry: 2000\n\n")
                for event in api.events.history(trace_id=trace_id, since=since, limit=64):
                    self.wfile.write(sse_frame(event))
                self.wfile.flush()
                deadline = time.monotonic() + 300.0
                while time.monotonic() < deadline:
                    event = subscription.get(timeout=10.0)
                    if event is None:
                        self.wfile.write(b": heartbeat\n\n")
                    else:
                        self.wfile.write(sse_frame(event.to_dict()))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                subscription.close()
                api.events.unsubscribe(subscription)

        # ------------------------------------------------------------- static
        def _serve_static(self, path: str) -> None:
            if path in ("/", ""):
                path = "/index.html"
            if path in ("/experience", "/experience/"):
                path = "/experience/index.html"
            relative = posixpath.normpath(unquote(path)).lstrip("/")
            if relative.startswith("..") or relative.startswith("/"):
                self._json(400, {"error": "bad_path", "message": "invalid path"})
                return
            target = (WEB_ROOT / relative).resolve()
            try:
                target.relative_to(WEB_ROOT.resolve())
            except ValueError:
                self._json(400, {"error": "bad_path", "message": "invalid path"})
                return
            if not target.is_file():
                self._json(404, {"error": "not_found", "message": "no such asset", "path": path})
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {
                "application/javascript",
                "application/json",
                "image/svg+xml",
            }:
                content_type += "; charset=utf-8"
            self._send(200, target.read_bytes(), content_type)

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

    return Handler


def build_server(
    settings: Optional[Settings] = None,
    *,
    engine: Optional[RAGEngine] = None,
    event_bus: Optional[EventBus] = None,
) -> Tuple[ThreadingHTTPServer, RAGEngine]:
    settings = settings or Settings.from_env()
    settings.validate()
    engine = engine or RAGEngine(settings)
    api = Api(engine, settings, event_bus=event_bus)
    httpd = ThreadingHTTPServer((settings.host, settings.port), make_handler(api, settings))
    httpd.daemon_threads = True
    httpd.api = api  # type: ignore[attr-defined]  # handy for tests/tools
    return httpd, engine


def serve(settings: Optional[Settings] = None, host: Optional[str] = None, port: Optional[int] = None) -> None:
    settings = settings or Settings.from_env()
    if host or port:
        from dataclasses import replace

        settings = replace(settings, host=host or settings.host, port=port or settings.port)
        settings.validate()
    httpd, engine = build_server(settings)
    health = engine.health()
    deployment = check_deployment(settings)
    print(
        f"ragformation {__version__} serving on http://{settings.host}:{settings.port}\n"
        f"  embedding: {health['embedding']['provider']}/{health['embedding']['model']} "
        f"(semantic={health['embedding']['semantic']}, d={health['embedding']['dimension']})\n"
        f"  generation: {health['generation']['provider']}/{health['generation']['model']} "
        f"(mode={health['generation']['mode']})\n"
        f"  index: {health['chunks']} chunks from {health['sources']} sources\n"
        f"  security: {deployment['securityMode']} — {deployment['detail']}\n"
        f"  console: http://{settings.host}:{settings.port}/  ·  experience: /experience\n"
        f"  api: /api/v1 (contract at /api/v1/openapi.json)",
        flush=True,
    )
    if deployment["securityMode"] == "insecure-public":
        print(
            "  WARNING: unauthenticated non-loopback bind (RAG_ALLOW_INSECURE_PUBLIC=1). "
            "Anyone who can reach this port can read the corpus.",
            flush=True,
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        httpd.server_close()
        engine.close()
