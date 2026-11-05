"""Zero-dependency HTTP API and web console.

Runs on the standard library's threaded server, so there is nothing to install
and nothing to build:

    python3 -m ragformation serve --port 8787

Security posture (documented in SECURITY.md):

* Binds ``0.0.0.0`` so it works behind a container/proxy, but the deployment is
  intended for local/trusted use. Set ``RAG_API_KEY`` to require an
  ``x-api-key`` header on every ``/api`` route except ``/api/health``.
* Request bodies are size-limited and must be JSON objects.
* Uploaded bytes are decoded from base64 and parsed in memory; filenames are
  sanitised and never used as filesystem paths.
* Static file serving is restricted to the packaged ``web/`` directory, with
  path traversal rejected.
* Unexpected exceptions return a generic 500; details go to the server log only.
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import posixpath
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from urllib.parse import unquote, urlparse

from . import __version__
from .config import Settings
from .engine import RAGEngine
from .errors import AuthError, NotFound, RagError, ValidationError

MAX_BODY_BYTES = 12 * 1024 * 1024
WEB_ROOT = Path(__file__).resolve().parent / "web"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class Api:
    """Route table over a shared engine instance."""

    def __init__(self, engine: RAGEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings
        self.routes: Dict[Tuple[str, str], Callable[..., Any]] = {
            ("GET", "/api/health"): self.health,
            ("GET", "/api/config"): self.config,
            ("GET", "/api/sources"): self.sources,
            ("GET", "/api/stats"): self.stats,
            ("GET", "/api/doctor"): self.doctor,
            ("POST", "/api/ask"): self.ask,
            ("POST", "/api/sources/upload"): self.upload,
            ("POST", "/api/sources/text"): self.add_text,
            ("POST", "/api/ingest"): self.ingest_server_path,
            ("POST", "/api/rebuild"): self.rebuild,
            ("POST", "/api/feedback"): self.feedback,
        }

    # ------------------------------------------------------------------ routes
    def health(self, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.engine.health()

    def config(self, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        public = self.settings.to_public_dict()
        return {
            "config": public,
            "providers": self.engine.health()["embedding"],
            "generation": self.engine.health()["generation"],
            "version": __version__,
        }

    def sources(self, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"sources": self.engine.list_sources()}

    def stats(self, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.engine.stats()

    def doctor(self, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.engine.doctor()

    def ask(self, body: Dict[str, Any]) -> Dict[str, Any]:
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
        result = self.engine.ask(
            question, top_k=top_k, mode=mode, temperature=temperature, top_p=top_p
        )
        return result.to_dict()

    def upload(self, body: Dict[str, Any]) -> Dict[str, Any]:
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
        )

    def add_text(self, body: Dict[str, Any]) -> Dict[str, Any]:
        title = body.get("title")
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("text is required")
        if len(text.encode("utf-8")) > self.settings.max_source_bytes:
            raise ValidationError("text is too large")
        name = (title or "pasted-note").strip() or "pasted-note"
        safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_.").strip() or "pasted-note"
        return self.engine.ingest_bytes(f"{safe}.md", text.encode("utf-8"), title=name)

    def ingest_server_path(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.settings.allow_path_ingest:
            raise ValidationError(
                "server-side path ingestion is disabled",
                hint="set RAG_ALLOW_PATH_INGEST=1 to enable it, or upload files instead",
            )
        path = body.get("path") or "corpus"
        if not isinstance(path, str):
            raise ValidationError("path must be a string")
        return self.engine.ingest_path([path])

    def rebuild(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.engine.rebuild(force=bool(body.get("force")))

    def feedback(self, body: Dict[str, Any]) -> Dict[str, Any]:
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

    # -------------------------------------------------------------- dispatcher
    def dispatch(self, method: str, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        route = self.routes.get((method, path))
        if route:
            return 200, route(body)

        # Parameterised routes.
        if method == "GET" and path.startswith("/api/sources/") and path.endswith("/chunks"):
            source_id = unquote(path[len("/api/sources/") : -len("/chunks")])
            return 200, {"chunks": self.engine.source_chunks(source_id)}

        if method == "DELETE" and path.startswith("/api/sources/"):
            source_id = unquote(path[len("/api/sources/") :])
            return 200, self.engine.delete_source(source_id)

        raise NotFound("no such endpoint", path=path)


def make_handler(api: Api, settings: Settings):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ragformation/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter, no payloads
            sys.stderr.write("[ragformation] " + (fmt % args) + "\n")

        # ---------------------------------------------------------- plumbing
        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", settings.cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Api-Key")

        def _send(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self._cors()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload, default=str).encode("utf-8"), "application/json; charset=utf-8")

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
                raise RagError(
                    "request body too large", code="payload_too_large", limit=MAX_BODY_BYTES
                )
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError("body must be valid JSON") from exc
            if not isinstance(data, dict):
                raise ValidationError("body must be a JSON object")
            return data

        def _authorised(self, path: str) -> bool:
            if not settings.api_key:
                return True
            if path in {"/api/health", "/healthz"}:
                return True
            return self.headers.get("X-Api-Key") == settings.api_key

        # ------------------------------------------------------------ routes
        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                self._handle_api("GET", path)
                return
            if path in ("/healthz", "/api/health"):
                self._handle_api("GET", "/api/health")
                return
            self._serve_static(path)

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            self._handle_api("DELETE", path)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            self._handle_api("POST", path)

        def _handle_api(self, method: str, path: str) -> None:
            if not self._authorised(path):
                self._json(401, AuthError("missing or invalid x-api-key").to_dict())
                return
            try:
                body = self._read_body() if method in {"POST", "PUT", "PATCH"} else {}
                status, payload = api.dispatch(method, path, body)
                self._json(status, payload)
            except RagError as exc:
                self._json(exc.http_status, exc.to_dict())
            except Exception:  # pragma: no cover - surfaced in logs only
                traceback.print_exc(file=sys.stderr)
                self._json(
                    500,
                    {"error": "internal_error", "message": "the request failed; see server logs"},
                )

        def _serve_static(self, path: str) -> None:
            if path in ("/", ""):
                path = "/index.html"
            relative = posixpath.normpath(unquote(path)).lstrip("/")
            if relative.startswith("..") or relative.startswith("/"):
                self._json(400, {"error": "bad_path"})
                return
            target = (WEB_ROOT / relative).resolve()
            try:
                target.relative_to(WEB_ROOT.resolve())
            except ValueError:
                self._json(400, {"error": "bad_path"})
                return
            if not target.is_file():
                self._json(404, {"error": "not_found", "path": path})
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


def build_server(settings: Optional[Settings] = None) -> Tuple[ThreadingHTTPServer, RAGEngine]:
    settings = settings or Settings.from_env()
    engine = RAGEngine(settings)
    api = Api(engine, settings)
    httpd = ThreadingHTTPServer((settings.host, settings.port), make_handler(api, settings))
    httpd.daemon_threads = True
    return httpd, engine


def serve(settings: Optional[Settings] = None, host: Optional[str] = None, port: Optional[int] = None) -> None:
    settings = settings or Settings.from_env()
    if host:
        settings = type(settings)(**{**settings.__dict__, "host": host})
    if port:
        settings = type(settings)(**{**settings.__dict__, "port": port})
    httpd, engine = build_server(settings)
    health = engine.health()
    print(
        f"ragformation {__version__} serving on http://{settings.host}:{settings.port}\n"
        f"  embedding: {health['embedding']['provider']}/{health['embedding']['model']} "
        f"(semantic={health['embedding']['semantic']})\n"
        f"  generation: {health['generation']['provider']}/{health['generation']['model']}\n"
        f"  index: {health['chunks']} chunks from {health['sources']} sources",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        httpd.server_close()
        engine.close()

