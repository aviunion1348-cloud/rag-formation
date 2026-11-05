"""HTTP surface: routes, validation, auth, static serving, error codes."""

from __future__ import annotations

import base64
import json
import threading
import unittest
import urllib.error
import urllib.request

from helpers import TempDirTestCase

from ragformation.api import Api, make_handler
from ragformation.engine import RAGEngine
from ragformation.config import Settings
from http.server import ThreadingHTTPServer


class ApiTestCase(TempDirTestCase):
    settings_overrides = {"api_key": None}

    def start(self, **overrides):
        settings = Settings(
            **{
                **self.settings.__dict__,
                **overrides,
                "host": "127.0.0.1",
                "port": 0,
            }
        )
        engine = RAGEngine(settings)
        self._engines.append(engine)
        engine.ingest_bytes(
            "policy.md", b"# Retention\n\nAudit logs are retained for 400 days in total.\n"
        )
        api = Api(engine, settings)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api, settings))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def stop():
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop)
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"
        self.engine = engine
        return self.base

    def request(self, path, method="GET", payload=None, headers=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "")
                return response.status, json.loads(body) if "json" in content_type else body
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body.decode("utf-8", errors="replace")


class ApiTests(ApiTestCase):
    def test_health_and_config(self):
        self.start()
        status, payload = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["embedding"]["semantic"])

        status, payload = self.request("/api/config")
        self.assertEqual(status, 200)
        self.assertNotIn("api_key", json.dumps(payload))
        self.assertNotIn(str(self.settings.data_dir), json.dumps(payload))

    def test_ask_returns_answer_with_citations(self):
        self.start()
        status, payload = self.request(
            "/api/ask", "POST", {"question": "How long are audit logs retained?", "topK": 4}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "answered")
        self.assertIn("400 days", payload["answer"])
        self.assertTrue(payload["citations"])
        self.assertIn("trace", payload)

    def test_ask_validates_input(self):
        self.start()
        status, payload = self.request("/api/ask", "POST", {"question": ""})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "validation_error")

        status, payload = self.request("/api/ask", "POST", {"question": "ok", "topK": 900})
        self.assertEqual(status, 400)

        status, payload = self.request("/api/ask", "POST", {"question": "ok", "mode": "telepathy"})
        self.assertEqual(status, 400)

    def test_source_listing_chunks_and_delete(self):
        self.start()
        status, payload = self.request("/api/sources")
        self.assertEqual(status, 200)
        source = payload["sources"][0]

        status, payload = self.request(f"/api/sources/{source['sourceId']}/chunks")
        self.assertEqual(status, 200)
        self.assertTrue(payload["chunks"])

        status, payload = self.request(f"/api/sources/{source['sourceId']}", "DELETE")
        self.assertEqual(status, 200)
        self.assertEqual(payload["indexSize"], 0)

    def test_unknown_source_returns_404(self):
        self.start()
        status, payload = self.request("/api/sources/src-nope/chunks")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "not_found")

    def test_upload_and_text_ingest(self):
        self.start()
        content = base64.b64encode(b"# Vendor\n\nVendor access lasts 14 days.\n").decode()
        status, payload = self.request(
            "/api/sources/upload", "POST", {"filename": "vendor.md", "contentBase64": content}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["results"][0]["status"], "indexed")

        status, payload = self.request(
            "/api/sources/text", "POST", {"title": "handover", "text": "The pager rotates weekly."}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["results"][0]["status"], "indexed")

    def test_upload_rejects_bad_base64_and_oversized_payload(self):
        self.start()
        status, payload = self.request(
            "/api/sources/upload", "POST", {"filename": "x.md", "contentBase64": "not base64!!"}
        )
        self.assertEqual(status, 400)

    def test_ingest_endpoint_can_be_disabled(self):
        self.start(allow_path_ingest=False)
        status, payload = self.request("/api/ingest", "POST", {"path": "corpus"})
        self.assertEqual(status, 400)
        self.assertIn("disabled", payload["message"])

    def test_rebuild_and_stats_and_feedback(self):
        self.start()
        status, payload = self.request("/api/rebuild", "POST", {})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["chunks"], 1)

        status, payload = self.request("/api/ask", "POST", {"question": "How long are audit logs retained?"})
        query_id = payload["queryId"]

        status, payload = self.request("/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(payload["queries"], 1)

        status, payload = self.request("/api/feedback", "POST", {"queryId": query_id, "rating": 1})
        self.assertEqual(status, 200)
        self.assertIn("feedbackId", payload)

        status, payload = self.request("/api/feedback", "POST", {"queryId": query_id, "rating": 99})
        self.assertEqual(status, 400)

    def test_static_assets_are_served_and_traversal_is_blocked(self):
        self.start()
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"ragformation", body)

        status, body = self.request("/app.js")
        self.assertEqual(status, 200)

        status, payload = self.request("/../../etc/passwd")
        self.assertIn(status, (400, 404))

        status, payload = self.request("/api/does-not-exist")
        self.assertEqual(status, 404)

    def test_api_key_is_enforced_when_configured(self):
        self.start(api_key="secret-key")
        status, payload = self.request("/api/sources")
        self.assertEqual(status, 401)

        status, payload = self.request("/api/sources", headers={"X-Api-Key": "secret-key"})
        self.assertEqual(status, 200)

        # Health stays open so a proxy can probe readiness.
        status, payload = self.request("/api/health")
        self.assertEqual(status, 200)

    def test_malformed_json_body_is_rejected(self):
        self.start()
        request = urllib.request.Request(
            f"{self.base}/api/ask",
            data=b"{not json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            self.fail("expected an error")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


if __name__ == "__main__":
    unittest.main()
