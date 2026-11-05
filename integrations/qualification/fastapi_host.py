#!/usr/bin/env python3
"""Host fixture C — a FastAPI application with the same trust boundary as host B.

    RAG_BASE_URL=http://127.0.0.1:8787 RAG_API_KEY=... \\
        uvicorn integrations.qualification.fastapi_host:app --port 8082

The interesting parts for a reviewer:

* the engine key never leaves the host process (it is not in any response),
* the principal is built from the session, not from the request body,
* an attempt to widen scope through the body (`allowedSources`) is intersected
  server-side, so the host cannot be tricked into a cross-scope read,
* streaming is proxied as SSE: the engine's `/api/v1/events` stream is relayed
  unchanged, so a browser sees lifecycle events as they happen.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

ENGINE = os.environ.get("RAG_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
API_KEY = os.environ.get("RAG_API_KEY", "")
SESSIONS = {
    "session=analyst": {"subjectId": "analyst", "tenantId": "local", "roles": ["viewer"]},
    "session=ops": {"subjectId": "ops", "tenantId": "local", "roles": ["operator"]},
}

app = FastAPI(title="ragformation FastAPI host", version="1.0.0")


def principal_for(cookie: str) -> Dict[str, Any]:
    principal = SESSIONS.get((cookie or "").strip())
    if principal is None:
        raise HTTPException(status_code=401, detail="not signed in")
    return principal


def engine_headers(principal: Dict[str, Any]) -> Dict[str, str]:
    return {
        "content-type": "application/json",
        "accept": "application/json",
        "x-api-key": API_KEY,
        "x-rag-principal": json.dumps(principal, separators=(",", ":")),
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (
        "<!doctype html><meta charset=utf-8><title>FastAPI host</title>"
        "<h1>FastAPI host in front of ragformation</h1>"
        "<p>POST /ask with cookie <code>session=analyst</code>.</p>"
    )


@app.post("/ask")
async def ask(request: Request, cookie: Optional[str] = Header(default=None)) -> JSONResponse:
    header_cookie = request.headers.get("cookie", cookie or "")
    principal = principal_for(header_cookie)
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question required")
    payload: Dict[str, Any] = {"question": question, "topK": 6}
    if body.get("allowedSources") is not None:
        # Forwarded deliberately: the engine intersects it with the principal's
        # scope, so a host bug cannot widen access. The test asserts that an
        # empty intersection yields no evidence rather than no filter.
        payload["allowedSources"] = body["allowedSources"]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{ENGINE}/api/v1/ask", headers=engine_headers(principal), json=payload)
    data = response.json()
    return JSONResponse(
        {
            "question": question,
            "status": data.get("status"),
            "answer": data.get("answer"),
            "citations": [
                {"title": c.get("sourceTitle"), "section": c.get("section")} for c in data.get("citations") or []
            ],
        },
        status_code=response.status_code,
    )


@app.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Relay the engine's real SSE endpoint to a browser.

    The engine exposes ``GET /api/v1/events`` (lifecycle + query events, plus a
    heartbeat every ten seconds). A host that wants live progress in a browser
    proxies exactly that stream; it does not need a second streaming protocol.
    """

    principal = principal_for(request.headers.get("cookie", ""))

    async def relay():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "GET",
                f"{ENGINE}/api/v1/events",
                headers={**engine_headers(principal), "accept": "text/event-stream"},
            ) as response:
                async for chunk in response.aiter_raw():
                    if chunk:
                        yield chunk

    return StreamingResponse(relay(), media_type="text/event-stream")


@app.get("/healthz")
async def health() -> JSONResponse:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{ENGINE}/healthz")
    return JSONResponse({"host": "ok", "engineHttp": response.status_code, "engine": response.json().get("status")})
