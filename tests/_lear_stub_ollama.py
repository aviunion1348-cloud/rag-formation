"""A STUB of the Ollama HTTP API for wiring tests. It is not a model.

It answers the endpoints the Lear agent uses (``/api/version``, ``/api/tags``,
``/api/show``, ``/api/embed``, ``/api/chat`` streaming and JSON) with
deterministic fake data so tests can exercise the strict-local code paths —
loopback client, parameter-cap checks, streaming timestamps, citation
validation, judge retries — without a real Ollama. Any result produced through
this stub is a wiring result, never a model-quality or latency result.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional


def _vector(text: str, dim: int) -> List[float]:
    vec = [0.0] * dim
    for token in re.findall(r"[a-z0-9_]+", text.lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        vec[int.from_bytes(digest[:4], "big") % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class StubOllama:
    """Start with ``with StubOllama() as stub: stub.url``."""

    def __init__(self) -> None:
        self.models: Dict[str, int] = {
            "nomic-embed-text:latest": 137_000_000,
            "qwen2.5:1.5b": 1_540_000_000,
            "llama3.2:1b": 1_240_000_000,
        }
        self.dimension = 64
        self.answer_mode = "cite"  # cite | abstain | uncited
        self.judge_replies: List[str] = []  # consumed in order; then a valid reply
        self.requests: List[Dict[str, Any]] = []
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------ lifecycle
    def __enter__(self) -> "StubOllama":
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                return

            def _send(self, payload: Any, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                stub.requests.append({"method": "GET", "path": self.path})
                if self.path == "/api/version":
                    self._send({"version": "0.0.0-stub"})
                elif self.path == "/api/tags":
                    self._send({"models": [{"name": name, "model": name} for name in stub.models]})
                else:
                    self._send({"error": "not found"}, 404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append({"method": "POST", "path": self.path, "payload": payload})
                if self.path == "/api/show":
                    name = payload.get("model", "")
                    key = name if name in stub.models else f"{name}:latest"
                    if key not in stub.models:
                        self._send({"error": f"model '{name}' not found"}, 404)
                        return
                    self._send({"model_info": {"general.parameter_count": stub.models[key]},
                                "details": {"family": key.split(":")[0], "parameter_size": "stub", "format": "gguf"}})
                elif self.path == "/api/embed":
                    texts = payload.get("input") or []
                    texts = [texts] if isinstance(texts, str) else texts
                    self._send({"embeddings": [_vector(t, stub.dimension) for t in texts]})
                elif self.path == "/api/chat":
                    if payload.get("format") is not None:
                        content = stub.judge_replies.pop(0) if stub.judge_replies else json.dumps({
                            "citation_quality": 3, "groundedness": 4, "answer_relevance": 3,
                            "context_precision": 2, "context_recall": 3, "rationale": "stub judgement",
                        })
                        self._send({"message": {"role": "assistant", "content": content}, "done": True})
                        return
                    if not payload.get("stream", False):
                        self._send({"message": {"role": "assistant", "content": "ready"}, "done": True})
                        return
                    self._stream(stub.answer_text(payload))
                else:
                    self._send({"error": "not found"}, 404)

            def _stream(self, text: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                words = text.split(" ")
                for i, word in enumerate(words):
                    piece = word if i == 0 else " " + word
                    self.wfile.write((json.dumps({"message": {"role": "assistant", "content": piece}, "done": False}) + "\n").encode())
                    self.wfile.flush()
                self.wfile.write((json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop",
                                              "prompt_eval_count": 100, "eval_count": len(words)}) + "\n").encode())
                self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    # --------------------------------------------------------------- answers
    def answer_text(self, payload: Dict[str, Any]) -> str:
        if self.answer_mode == "abstain":
            return "INSUFFICIENT_EVIDENCE"
        user = next((m["content"] for m in reversed(payload.get("messages", [])) if m.get("role") == "user"), "")
        match = re.search(r'<evidence id="1"[^>]*>\n(.+?)\n</evidence>', user, re.S)
        excerpt = " ".join((match.group(1) if match else "the evidence").split()[:14])
        if self.answer_mode == "uncited":
            return f"Stub answer without markers: {excerpt}."
        return f"{excerpt} [1]."
