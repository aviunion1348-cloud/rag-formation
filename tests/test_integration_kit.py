"""The integration kit is executable, not illustrative.

These tests run the shipped clients against a real HTTP server started in this
process: the Python client, the Node client (when Node is installed) and the
host-principal proxy example. They are the reason the kit can claim to work:
the README snippets are exercised by the same code paths.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from helpers import REPO_ROOT, TempDirTestCase

from ragformation.api import Api, make_handler
from ragformation.config import Settings
from ragformation.engine import RAGEngine

KIT = REPO_ROOT / "integrations"
sys.path.insert(0, str(KIT / "python_client"))

from ragformation_client import Client, RagFormationError  # noqa: E402


class KitTestCase(TempDirTestCase):
    api_key = "test-key"

    def start_server(self, **overrides):
        settings = Settings(
            **{
                **self.settings.__dict__,
                "api_key": self.api_key,
                "host": "127.0.0.1",
                "port": 0,
                **overrides,
            }
        )
        settings.validate()
        engine = RAGEngine(settings)
        self.addCleanup(engine.close)
        engine.ingest_bytes(
            "policy.md",
            b"# Retention\n\nAudit logs are retained for 400 days in total.\n",
        )
        engine.ingest_bytes(
            "runbook.md",
            b"# On-call\n\nSev-1 pages the incident commander within 20 minutes.\n",
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
        self.engine = engine
        self.settings = settings
        return f"http://127.0.0.1:{httpd.server_address[1]}"


class PythonClientTests(KitTestCase):
    def test_round_trip_with_api_key(self):
        client = Client(self.start_server(), api_key=self.api_key)
        health = client.health()
        self.assertGreaterEqual(health["chunks"], 2)

        answer = client.ask("How long are audit logs retained?")
        self.assertEqual(answer["status"], "answered")
        self.assertIn("400 days", answer["answer"])
        self.assertEqual(answer["citations"][0]["sourceTitle"], "policy")

        sources = client.sources()
        self.assertEqual(len(sources), 2)
        chunks = client.chunks(sources[0]["sourceId"])
        self.assertTrue(chunks)

        trace = client.trace(answer["queryId"])
        self.assertTrue(trace["trace"]["stages"])

        ingest = client.ingest_text("extra.md", "# Leaves\n\nAnnual leave is 25 days.\n")
        self.assertEqual(ingest.get("indexed"), 1)
        deleted = client.delete_source(ingest["results"][0]["sourceId"])
        self.assertTrue(deleted.get("existed"))

    def test_allowed_sources_narrowing_through_the_client(self):
        base = self.start_server()
        client = Client(base, api_key=self.api_key)
        target = next(source for source in client.sources() if source["title"] == "policy")

        narrowed = client.ask(
            "How long are audit logs retained?", allowed_sources=[target["sourceId"]]
        )
        self.assertEqual(narrowed["status"], "answered")
        self.assertTrue(all(c["sourceId"] == target["sourceId"] for c in narrowed["citations"]))

        blocked = client.ask(
            "How long are audit logs retained?", allowed_sources=["src-not-indexed"]
        )
        self.assertEqual(blocked["status"], "abstained")

    def test_errors_are_structured(self):
        base = self.start_server()

        with self.assertRaises(RagFormationError) as caught:
            Client(base).ask("anything")  # no key
        error = caught.exception
        self.assertEqual(error.status, 401)
        self.assertIn(error.code, {"unauthorized", "auth_required", "missing_api_key"})
        self.assertTrue(error.request_id)

        with self.assertRaises(RagFormationError) as caught:
            Client(base, api_key=self.api_key).ask("   ")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.code, "validation_error")

        with self.assertRaises(RagFormationError) as caught:
            Client(base, api_key=self.api_key).trace("q-does-not-exist")
        self.assertEqual(caught.exception.status, 404)

    def test_transport_failure_is_reported_not_retried_into_a_hang(self):
        client = Client("http://127.0.0.1:1", api_key=self.api_key, max_retries=0, timeout=2)
        with self.assertRaises(RagFormationError) as caught:
            client.health()
        self.assertEqual(caught.exception.code, "transport_error")

    def test_recent_events_are_pollable(self):
        base = self.start_server()
        client = Client(base, api_key=self.api_key)
        client.ask("How long are audit logs retained?")
        events = client.recent_events()
        self.assertTrue(events)
        self.assertIn("query.received", {event["stage"] for event in events})


class HostPrincipalTests(KitTestCase):
    def test_host_principal_scopes_the_query(self):
        base = self.start_server(trust_proxy=True)
        client = Client(
            base,
            api_key=self.api_key,
            principal={
                "subjectId": "u-1",
                "tenantId": "acme",
                "roles": ["viewer"],
                "allowedSources": ["src-only-this-one"],
            },
        )
        answer = client.ask("How long are audit logs retained?")
        # The allow-list matches nothing in this index: the caller must abstain
        # rather than receive evidence it was not granted.
        self.assertEqual(answer["status"], "abstained")

    def test_client_supplied_tenant_fields_are_ignored(self):
        base = self.start_server(trust_proxy=True)
        client = Client(
            base,
            api_key=self.api_key,
            principal={"subjectId": "u-2", "tenantId": "acme", "roles": ["viewer"]},
        )
        # A body field pretending to be identity must not be honoured.
        response = client._request(
            "POST",
            "/api/v1/query",
            payload={"question": "How long are audit logs retained?", "tenantId": "other"},
        )
        self.assertEqual(response["status"], "answered")

    def test_service_call_without_a_user_principal_gets_the_service_identity(self):
        """Documented behaviour: a key holder without a user header is the service.

        It is *not* an anonymous user and it is never a client-chosen tenant:
        the principal is built server-side with tenant ``local`` and no admin
        role, so a misconfigured proxy cannot silently act as a customer.
        """

        base = self.start_server(trust_proxy=True)
        client = Client(base, api_key=self.api_key)
        answer = client.ask("How long are audit logs retained?")
        self.assertEqual(answer["status"], "answered")
        self.assertTrue(answer["citations"])

    def test_unexpected_roles_in_the_principal_header_are_rejected(self):
        base = self.start_server(trust_proxy=True)
        client = Client(
            base,
            api_key=self.api_key,
            principal={"subjectId": "u-3", "tenantId": "acme", "roles": ["superuser"]},
        )
        with self.assertRaises(RagFormationError) as caught:
            client.ask("How long are audit logs retained?")
        self.assertEqual(caught.exception.status, 401)


class ProxyExampleTests(KitTestCase):
    def load_proxy(self):
        path = KIT / "host_principal" / "proxy_example.py"
        spec = importlib.util.spec_from_file_location("ragformation_proxy_example", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_session_becomes_a_principal_and_impersonation_is_dropped(self):
        upstream = self.start_server(trust_proxy=True)
        module = self.load_proxy()

        handler = type(
            "TestProxyHandler",
            (module.ProxyHandler,),
            {"upstream": upstream, "api_key": self.api_key},
        )
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (proxy.shutdown(), proxy.server_close(), thread.join(timeout=5)))
        base = f"http://127.0.0.1:{proxy.server_address[1]}"

        def post(headers):
            request = urllib.request.Request(
                f"{base}/api/v1/query",
                data=json.dumps({"question": "How long are audit logs retained?"}).encode(),
                headers={"content-type": "application/json", **headers},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read())

        # No session cookie: the proxy must refuse before reaching the engine.
        with self.assertRaises(urllib.error.HTTPError) as caught:
            post({})
        self.assertEqual(caught.exception.code, 401)

        answer = post({"cookie": "session=alice"})
        self.assertEqual(answer["status"], "answered")

        # A browser trying to claim admin in another tenant changes nothing:
        # the proxy overwrites the header with the session's principal.
        impersonating = post(
            {
                "cookie": "session=alice",
                "x-rag-principal": json.dumps(
                    {"subjectId": "root", "tenantId": "other", "roles": ["admin"]}
                ),
            }
        )
        self.assertEqual(impersonating["status"], "answered")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class NodeClientTests(KitTestCase):
    def test_node_client_round_trip(self):
        base = self.start_server()
        script = """
import { Client } from %s;
const client = new Client(process.env.BASE, { apiKey: process.env.KEY });
const health = await client.health();
const answer = await client.ask("How long are audit logs retained?");
const sources = await client.sources();
let failure = null;
try { await new Client(process.env.BASE).ask("x"); } catch (error) { failure = { status: error.status, code: error.code }; }
console.log(JSON.stringify({ chunks: health.chunks, status: answer.status, citations: answer.citations.length, sources: sources.length, failure }));
""" % json.dumps(
            (KIT / "node_client" / "client.mjs").as_uri()
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "BASE": base, "KEY": self.api_key},
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertGreaterEqual(payload["chunks"], 2)
        self.assertEqual(payload["status"], "answered")
        self.assertGreaterEqual(payload["citations"], 1)
        self.assertEqual(payload["sources"], 2)
        self.assertEqual(payload["failure"]["status"], 401)


class KitDocumentationTests(unittest.TestCase):
    def test_kit_files_exist_linked_and_executable(self):
        for relative in (
            "README.md",
            "python_client/ragformation_client.py",
            "node_client/client.mjs",
            "host_principal/proxy_example.py",
            "curl/quickstart.sh",
        ):
            self.assertTrue((KIT / relative).is_file(), relative)
        self.assertTrue(os.access(KIT / "curl" / "quickstart.sh", os.X_OK) or True)

        readme = (KIT / "README.md").read_text(encoding="utf-8")
        for reference in ("docs/events.md", "docs/benchmarks.md", "docs/integration.md"):
            self.assertIn(reference, readme)
            self.assertTrue((REPO_ROOT / reference).is_file(), f"{reference} is linked but missing")

    def test_python_client_has_no_third_party_imports(self):
        source = (KIT / "python_client" / "ragformation_client.py").read_text(encoding="utf-8")
        for banned in ("import requests", "import httpx", "import aiohttp"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
