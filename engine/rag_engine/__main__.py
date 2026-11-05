"""Zero-dependency HTTP + CLI surface for the RAG engine.

Run:  python3 -m rag_engine serve --port 8787
CLI:  python3 -m rag_engine ask "When must the incident commander be paged?"

Endpoints
---------
GET  /api/health            engine + index state
GET  /api/corpus            source metadata + chunk inventory
POST /api/query             {question, topK, mode, rerank, temperature}
POST /api/lifecycle/update  {sourceId} -> swap to the alternate revision
POST /api/lifecycle/delete  {sourceId}
GET  /api/trace/ingest      the recorded ingestion trace
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from .chunking import ChunkingConfig, chunk_document
from .ingest import Document, Block, SourceRegistry, load_corpus
from .pipeline import RagEngine, ABSTAIN_THRESHOLD, CONTEXT_TOKEN_BUDGET
from .rerank import RERANKER_ID

MAX_BODY = 32 * 1024

DEMO_QUESTIONS = [
    "When must the incident commander be paged for a P1 incident?",
    "How long are application logs retained?",
    "What does a P1 post-incident review have to contain?",
    "Who approves break-glass access and how long does it last?",
    "What is our policy on lunar payload insurance?",  # out-of-corpus -> abstain
    "Summarise vendor onboarding requirements",  # reaches the poisoned fixture
]


def build_engine() -> RagEngine:
    engine = RagEngine(load_corpus(), chunking=ChunkingConfig(target_tokens=90, overlap_tokens=18))
    engine.ingest()
    return engine


def _revision_variant(engine: RagEngine, source_id: str) -> Optional[Document]:
    """Create the 'r4' revision of the incident standard for the lifecycle demo."""
    if source_id != "src-p1-incident":
        return None
    base = engine.registry.get(source_id)
    blocks = list(base.blocks)
    # Amend the escalation window: 10 -> 15 minutes. Real text change, real reindex.
    amended = []
    for b in blocks:
        text = b.text.replace(
            "paged within 10 minutes", "paged within 15 minutes"
        ).replace("unresolved after 45 minutes", "unresolved after 40 minutes")
        amended.append(Block(kind=b.kind, text=text, page=b.page))
    amended.append(
        Block(
            kind="paragraph",
            page=5,
            text="Revision r4 amendment: the commander paging window is now 15 minutes, and director escalation is required after 40 minutes.",
        )
    )
    return Document(
        source_id=base.source_id,
        title=base.title,
        revision="r4",
        origin=base.origin,
        media_type=base.media_type,
        blocks=amended,
    )


def make_handler(engine: RagEngine):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RagEngine/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter default logs
            sys.stderr.write("[rag] " + (fmt % args) + "\n")

        # ---------------------------------------------------------- utilities
        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", os.environ.get("RAG_CORS_ORIGIN", "*"))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            if length > MAX_BODY:
                raise ValueError("request body too large")
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("body must be a JSON object")
            return data

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        # -------------------------------------------------------------- routes
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/api/health", "/api/health/"):
                self._json(
                    {
                        "ok": True,
                        "mode": "live",
                        "indexSize": engine.index.size,
                        "sources": len(engine.registry),
                        "embeddingModel": engine.embedder.model_id,
                        "dimension": engine.embedder.dimension,
                        "reranker": RERANKER_ID,
                        "synthesis": "extractive-synthesis-v1",
                        "abstainThreshold": ABSTAIN_THRESHOLD,
                        "contextTokenBudget": CONTEXT_TOKEN_BUDGET,
                    }
                )
                return
            if path in ("/api/corpus", "/api/corpus/"):
                self._json(
                    {
                        "sources": engine.registry.to_meta(),
                        "chunks": [c.to_dict() for c in engine.index.chunks()],
                        "questions": DEMO_QUESTIONS,
                        "notice": "Synthetic fixture corpus for demonstration.",
                    }
                )
                return
            if path in ("/api/trace/ingest", "/api/trace/ingest/"):
                self._json({"report": engine.ingest_report, "trace": engine.ingest_trace})
                return
            if path == "/":
                self._json(
                    {
                        "service": "rag-formation engine",
                        "endpoints": [
                            "/api/health",
                            "/api/corpus",
                            "/api/query",
                            "/api/trace/ingest",
                            "/api/lifecycle/update",
                            "/api/lifecycle/delete",
                        ],
                    }
                )
                return
            self._json({"error": "not_found", "path": path}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": "bad_request", "message": str(exc)}, 400)
                return

            try:
                if path == "/api/query":
                    question = str(payload.get("question", ""))[:1000]
                    top_k = int(payload.get("topK", 6))
                    mode = str(payload.get("mode", "hybrid"))
                    rerank_flag = bool(payload.get("rerank", True))
                    temperature = float(payload.get("temperature", 0.0))
                    result = engine.query(
                        question,
                        top_k=top_k,
                        mode=mode,
                        rerank_enabled=rerank_flag,
                        temperature=temperature,
                    )
                    self._json(result)
                    return
                if path == "/api/lifecycle/update":
                    source_id = str(payload.get("sourceId", "src-p1-incident"))
                    variant = _revision_variant(engine, source_id)
                    if variant is None:
                        self._json({"error": "no_alternate_revision", "sourceId": source_id}, 409)
                        return
                    self._json(engine.update_source(variant))
                    return
                if path == "/api/lifecycle/delete":
                    source_id = str(payload.get("sourceId", ""))
                    if not source_id:
                        self._json({"error": "bad_request", "message": "sourceId required"}, 400)
                        return
                    self._json(engine.delete_source(source_id))
                    return
            except ValueError as exc:
                self._json({"error": "bad_request", "message": str(exc)}, 400)
                return
            except Exception as exc:  # pragma: no cover - defensive
                self._json({"error": "internal_error", "message": str(exc)}, 500)
                return

            self._json({"error": "not_found", "path": path}, 404)

    return Handler


def serve(port: int = 8787, host: str = "0.0.0.0") -> None:
    engine = build_engine()
    httpd = ThreadingHTTPServer((host, port), make_handler(engine))
    print(f"rag engine listening on http://{host}:{port}  (index={engine.index.size} chunks)")
    httpd.serve_forever()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="rag_engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="run the HTTP API")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument("--host", default="0.0.0.0")

    p_ask = sub.add_parser("ask", help="ask one question from the CLI")
    p_ask.add_argument("question")
    p_ask.add_argument("--top-k", type=int, default=6)
    p_ask.add_argument("--mode", default="hybrid", choices=["semantic", "lexical", "hybrid"])
    p_ask.add_argument("--json", action="store_true")

    sub.add_parser("ingest", help="print the ingestion trace")
    sub.add_parser("export", help="print the corpus + traces as JSON for the frontend")

    args = parser.parse_args(argv)
    engine = build_engine()

    if args.cmd == "serve":
        serve(args.port, args.host)
        return 0
    if args.cmd == "ask":
        result = engine.query(args.question, top_k=args.top_k, mode=args.mode)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\nQ: {result['question']}\n")
            print(result["answer_text"])
            if result["citations"]:
                print("\nCitations:")
                for c in result["citations"]:
                    print(f"  {c['marker']} {c['source_title']} p.{c['page']} ({c['chunk_id']})")
            print(f"\nstatus={result['status']}  metrics={json.dumps(result['metrics'])}")
        return 0
    if args.cmd == "ingest":
        print(json.dumps({"report": engine.ingest_report, "trace": engine.ingest_trace}, indent=2))
        return 0
    if args.cmd == "export":
        print(json.dumps(export_bundle(engine), indent=2))
        return 0
    return 1


def export_bundle(engine: RagEngine) -> Dict[str, Any]:
    """Everything the standalone demo needs, generated from the real engine."""
    return {
        "generatedBy": "rag_engine.export",
        "corpus": {
            "sources": engine.registry.to_meta(),
            "chunks": [c.to_dict() for c in engine.index.chunks()],
        },
        "ingest": {"report": engine.ingest_report, "trace": engine.ingest_trace},
        "questions": DEMO_QUESTIONS,
        "answers": {q: engine.query(q) for q in DEMO_QUESTIONS},
    }


if __name__ == "__main__":
    raise SystemExit(main())
