"""A dependency-free client for the RagFormation HTTP API.

Why a client at all when the API is plain JSON? Because three things are easy to
get wrong and the library makes them explicit:

* **identity** — the client sends a credential (API key) or a host principal
  header; it never sends a tenant id in the request body;
* **errors** — every failure is raised as :class:`RagFormationError` carrying the
  server's error code, HTTP status, request id and hint, so callers can branch on
  ``error.code`` instead of parsing prose;
* **timeouts and retries** — reads retry on transport errors and 429/503 with
  bounded backoff; writes (ingest, delete) do not retry by default, because a
  blind retry of a non-idempotent call is how duplicates happen.

Usage::

    from ragformation_client import Client

    client = Client("http://127.0.0.1:8000")
    print(client.health()["chunks"])
    answer = client.ask("How long are audit logs retained?")
    print(answer["status"], answer["answer"])

Host-authenticated deployments (the RAG service runs behind your own app)::

    client = Client("http://127.0.0.1:8000", principal={
        "subjectId": "u-42",
        "tenantId": "acme",
        "roles": ["viewer"],
        "allowedSources": ["src-abc"],
    })

The header is only trusted when the server runs with ``RAG_TRUST_PROXY=1``; the
server, not this client, decides whether to believe it.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

DEFAULT_TIMEOUT = 30.0
RETRY_STATUSES = {429, 502, 503, 504}


class RagFormationError(Exception):
    """A structured error from the service (or from the transport)."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "client_error",
        status: int = 0,
        request_id: Optional[str] = None,
        hint: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.request_id = request_id
        self.hint = hint
        self.payload = dict(payload or {})

    @property
    def retryable(self) -> bool:
        return self.status in RETRY_STATUSES or self.code in {"rate_limited", "unavailable"}

    def __str__(self) -> str:  # pragma: no cover - formatting only
        parts = [super().__str__()]
        if self.code:
            parts.append(f"[{self.code}]")
        if self.request_id:
            parts.append(f"request={self.request_id}")
        if self.hint:
            parts.append(f"hint: {self.hint}")
        return " ".join(parts)


class Client:
    """Thin, explicit wrapper over ``/api/v1``."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        api_key: Optional[str] = None,
        principal: Optional[Mapping[str, Any]] = None,
        principal_header: str = "x-rag-principal",
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 2,
        user_agent: str = "ragformation-client/0.2.0",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.principal = dict(principal) if principal else None
        self.principal_header = principal_header
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.user_agent = user_agent

    # ------------------------------------------------------------- transport
    def _headers(self) -> Dict[str, str]:
        headers = {
            "accept": "application/json",
            "user-agent": self.user_agent,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        if self.principal is not None:
            headers[self.principal_header] = json.dumps(self.principal, separators=(",", ":"))
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        query: Optional[Mapping[str, Any]] = None,
        retry: Optional[bool] = None,
        raw: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            clean = {key: value for key, value in query.items() if value is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean, doseq=True)
        body = None
        headers = self._headers()
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["content-type"] = "application/json"
        attempts = (self.max_retries if retry is None else int(retry)) + 1
        last_error: Optional[RagFormationError] = None

        for attempt in range(attempts):
            request = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = response.read()
                    if raw:
                        return data
                    if not data:
                        return {}
                    return json.loads(data.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                data = exc.read()
                try:
                    parsed = json.loads(data.decode("utf-8")) if data else {}
                except ValueError:
                    parsed = {"message": data[:200].decode("utf-8", "replace")}
                error = RagFormationError(
                    parsed.get("message") or f"HTTP {exc.code}",
                    code=str(parsed.get("code") or parsed.get("error") or "http_error"),
                    status=exc.code,
                    request_id=parsed.get("requestId"),
                    hint=parsed.get("hint"),
                    payload=parsed,
                )
                if not error.retryable or attempt == attempts - 1:
                    raise error
                last_error = error
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                error = RagFormationError(
                    f"transport error: {getattr(exc, 'reason', exc)}",
                    code="transport_error",
                    status=0,
                )
                if attempt == attempts - 1:
                    raise error
                last_error = error
            time.sleep(min(2.0, 0.25 * (2**attempt)))
        if last_error is not None:  # pragma: no cover - loop always returns/raises
            raise last_error
        raise RagFormationError("request failed", code="transport_error")  # pragma: no cover

    # ----------------------------------------------------------------- reads
    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/health")

    def ready(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/ready")

    def config(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/config")

    def openapi(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/openapi.json")

    def sources(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        body = self._request("GET", "/api/v1/sources", query={"limit": limit})
        return list(body.get("sources") or [])

    def chunks(self, source_id: str) -> List[Dict[str, Any]]:
        body = self._request("GET", f"/api/v1/sources/{urllib.parse.quote(source_id)}/chunks")
        return list(body.get("chunks") or [])

    def trace(self, query_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/v1/traces/{urllib.parse.quote(query_id)}")

    def traces(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        body = self._request("GET", "/api/v1/traces", query={"limit": limit})
        return list(body.get("traces") or [])

    # ---------------------------------------------------------------- writes
    def ingest_text(self, filename: str, text: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/sources",
            payload={"filename": filename, "text": text},
            retry=False,
        )

    def ingest_file(self, path: str, *, filename: Optional[str] = None) -> Dict[str, Any]:
        """Multipart upload with no third-party dependency."""

        bucket = "----ragformation" + str(int(time.time() * 1000))
        name = filename or path.rsplit("/", 1)[-1]
        with open(path, "rb") as handle:
            content = handle.read()
        parts = [
            f"--{bucket}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            content,
            f"\r\n--{bucket}--\r\n".encode(),
        ]
        body = b"".join(parts)
        headers = self._headers()
        headers["content-type"] = f"multipart/form-data; boundary={bucket}"
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/sources", data=body, method="POST", headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            parsed = json.loads(exc.read().decode("utf-8") or "{}")
            raise RagFormationError(
                parsed.get("message") or f"HTTP {exc.code}",
                code=str(parsed.get("code") or "upload_failed"),
                status=exc.code,
                request_id=parsed.get("requestId"),
                payload=parsed,
            ) from exc

    def delete_source(self, source_id: str) -> Dict[str, Any]:
        return self._request(
            "DELETE", f"/api/v1/sources/{urllib.parse.quote(source_id)}", retry=False
        )

    def ask(
        self,
        question: str,
        *,
        top_k: Optional[int] = None,
        mode: Optional[str] = None,
        allowed_sources: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"question": question}
        if top_k is not None:
            payload["topK"] = top_k
        if mode is not None:
            payload["mode"] = mode
        if allowed_sources is not None:
            payload["allowedSources"] = list(allowed_sources)
        return self._request("POST", "/api/v1/query", payload=payload)

    # ------------------------------------------------------------------ live
    def recent_events(self, *, since: int = 0) -> List[Dict[str, Any]]:
        body = self._request("GET", "/api/v1/events/recent", query={"since": since})
        return list(body.get("events") or [])

    def stream_events(self, *, trace_id: Optional[str] = None) -> Iterable[Dict[str, Any]]:
        """Yield server-sent events as dictionaries.

        Implemented with ``urllib`` so the client stays dependency-free. The
        generator is lazy: nothing connects until you iterate.
        """

        url = f"{self.base_url}/api/v1/events"
        if trace_id:
            url += "?" + urllib.parse.urlencode({"trace_id": trace_id})
        headers = self._headers()
        headers["accept"] = "text/event-stream"
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            buffer: List[str] = []
            for raw in response:
                line = raw.decode("utf-8").rstrip("\r\n")
                if not line:
                    if buffer:
                        data = "\n".join(buffer)
                        buffer = []
                        try:
                            yield json.loads(data)
                        except ValueError:
                            continue
                    continue
                if line.startswith(":") or line.startswith("event:") or line.startswith("id:"):
                    continue
                if line.startswith("data:"):
                    buffer.append(line[5:].strip())
