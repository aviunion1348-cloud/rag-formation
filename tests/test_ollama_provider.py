"""The Ollama path, verified against a local stub server.

Ollama is the whiteboard's intended stack (local embeddings + local generation).
It is not installed in CI, so this test stands up a stub that speaks the same
HTTP API (/api/tags, /api/embed, /api/chat with NDJSON streaming) and drives the
real provider code, the real engine and the real citation validator through it.

What this proves: the adapter contract, streaming token accounting (TTFT/ITL),
model availability checks and grounded generation. What it does not prove: that
a real model produces good prose — that needs a real Ollama install.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import TempDirTestCase

from ragformation.config import Settings
from ragformation.embeddings import OllamaEmbedder
from ragformation.engine import RAGEngine
from ragformation.generation import OllamaGenerator

DIMENSION = 24
FINAL_ANSWER = "Audit logs are retained for 400 days in total [1]."


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/tags":
            self._json({"models": [{"name": "nomic-embed-text:latest"}, {"name": "llama3.2:3b"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/api/embed":
            inputs = payload.get("input") or []
            if isinstance(inputs, str):
                inputs = [inputs]
            vectors = []
            for text in inputs:
                # Deterministic pseudo-embedding: character codes folded into a
                # fixed dimension, then L2-normalised by the provider.
                vector = [0.0] * DIMENSION
                for index, char in enumerate(text):
                    vector[index % DIMENSION] += (ord(char) % 17) + 1
                vectors.append(vector)
            self._json({"embeddings": vectors})
            return

        if self.path == "/api/chat":
            pieces = [FINAL_ANSWER[i : i + 12] for i in range(0, len(FINAL_ANSWER), 12)]
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in pieces:
                event = {"message": {"role": "assistant", "content": piece}, "done": False}
                raw = (json.dumps(event) + "\n").encode()
                self.wfile.write(f"{len(raw):X}\r\n".encode() + raw + b"\r\n")
                self.wfile.flush()
                time.sleep(0.005)
            final = {
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 321,
                "eval_count": 42,
            }
            raw = (json.dumps(final) + "\n").encode()
            self.wfile.write(f"{len(raw):X}\r\n".encode() + raw + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return

        self._json({"error": "not found"}, 404)


class OllamaProviderTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.host = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def ollama_settings(self, **overrides) -> Settings:
        base = {
            "data_dir": self.settings.data_dir,
            "embed_provider": "ollama",
            "embed_model": "nomic-embed-text",
            "llm_provider": "ollama",
            "llm_model": "llama3.2:3b",
            "ollama_host": self.host,
            "persist_traces": True,
        }
        base.update(overrides)
        return Settings(**base)

    def test_embedder_health_and_dimension(self):
        embedder = OllamaEmbedder(model="nomic-embed-text", host=self.host, timeout=5)
        health = embedder.health()
        self.assertTrue(health["ok"], health)
        vectors = embedder.embed(["audit logs", "vendor access"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(embedder.dimension, DIMENSION)
        for vector in vectors:
            magnitude = sum(value * value for value in vector) ** 0.5
            self.assertAlmostEqual(magnitude, 1.0, places=6)

    def test_missing_model_is_reported_not_faked(self):
        embedder = OllamaEmbedder(model="not-installed:1b", host=self.host, timeout=5)
        health = embedder.health()
        self.assertFalse(health["ok"])
        self.assertIn("ollama pull", health["hint"])

    def test_generator_streams_and_measures_tokens(self):
        generator = OllamaGenerator("llama3.2:3b", self.host)
        self.assertTrue(generator.availability()["ok"])
        tokens = []
        output = generator.generate(
            "How long are audit logs retained?",
            [{"id": 1, "text": "Audit logs are retained for 400 days.", "sourceTitle": "d", "section": "s", "page": 1}],
            temperature=0.3,
            top_p=0.9,
            max_tokens=200,
            timeout=10,
            on_token=tokens.append,
        )
        self.assertEqual(output.text, FINAL_ANSWER)
        self.assertEqual(output.prompt_tokens, 321)
        self.assertEqual(output.completion_tokens, 42)
        self.assertEqual(output.finish_reason, "stop")
        self.assertIsNotNone(output.timing["ttftMs"])
        self.assertIsNotNone(output.timing["itlMs"])
        self.assertEqual(len(tokens), len([t for t in tokens if t]))

    def test_engine_end_to_end_with_ollama_providers(self):
        settings = self.ollama_settings()
        engine = RAGEngine(settings)
        self.addCleanup(engine.close)
        self.assertEqual(engine.embedder.name, "ollama")
        self.assertEqual(engine.generator.name, "ollama")

        report = engine.ingest_bytes(
            "retention.md",
            b"# Retention\n\nAudit logs are retained for 400 days in total. Application logs are kept 90 days.\n",
        )
        self.assertEqual(report["results"][0]["status"], "indexed")

        result = engine.ask("How long are audit logs retained?")
        self.assertEqual(result.status, "answered", result.warnings)
        self.assertEqual(result.provider["used"], "ollama")
        self.assertEqual(result.answer, FINAL_ANSWER)
        self.assertEqual([citation.evidence_id for citation in result.citations], [1])
        self.assertEqual(result.metrics["promptTokens"], 321)
        self.assertEqual(result.metrics["completionTokens"], 42)
        self.assertIsNotNone(result.metrics["ttftMs"])
        self.assertIsNotNone(result.metrics["itlMs"])

    def test_auto_provider_prefers_a_reachable_ollama(self):
        settings = self.ollama_settings(embed_provider="auto", llm_provider="auto")
        engine = RAGEngine(settings)
        self.addCleanup(engine.close)
        self.assertEqual(engine.embedder.name, "ollama")
        self.assertEqual(engine.generator.name, "ollama")
        self.assertTrue(engine.health()["embedding"]["semantic"])

    def test_auto_provider_falls_back_when_ollama_is_absent(self):
        settings = self.ollama_settings(
            embed_provider="auto", llm_provider="auto", ollama_host="http://127.0.0.1:9"
        )
        engine = RAGEngine(settings)
        self.addCleanup(engine.close)
        self.assertEqual(engine.embedder.name, "hashing")
        self.assertIn(engine.generator.kind, {"extractive"})


if __name__ == "__main__":
    unittest.main()
