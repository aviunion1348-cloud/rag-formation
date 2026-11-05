"""HTTP API tests: real server on an ephemeral port, real requests."""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag_engine.__main__ import build_engine, make_handler  # noqa: E402


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.engine))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())

    def _post(self, path, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_health(self):
        status, body = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertGreater(body["indexSize"], 0)

    def test_corpus(self):
        status, body = self._get("/api/corpus")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(body["sources"]), 5)
        self.assertTrue(body["questions"])

    def test_query_ok(self):
        status, body = self._post("/api/query", {"question": "How long are logs retained?"})
        self.assertEqual(status, 200)
        self.assertIn(body["status"], {"answered", "needs_review"})
        self.assertTrue(body["trace"])

    def test_query_validation(self):
        status, body = self._post("/api/query", {"question": ""})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_request")
        status, body = self._post("/api/query", {"question": "logs", "topK": 0})
        self.assertEqual(status, 400)

    def test_oversized_body_rejected(self):
        big = json.dumps({"question": "x" * 40000}).encode()
        req = urllib.request.Request(
            self.base + "/api/query", data=big, headers={"Content-Type": "application/json"}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_route(self):
        try:
            self._get("/api/nope")
            self.fail("expected 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_lifecycle_endpoints(self):
        status, body = self._post("/api/lifecycle/update", {"sourceId": "src-p1-incident"})
        self.assertEqual(status, 200)
        self.assertIn("trace", body)
        status, body = self._post("/api/lifecycle/delete", {"sourceId": "src-legacy-archive"})
        self.assertEqual(status, 200)
        self.assertFalse(
            any(s["sourceId"] == "src-legacy-archive" for s in self.engine.registry.to_meta())
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
