"""Tests for the Lear documentation generator and verifier.

Synthetic tests build a throwaway git repository, so they always run. The
pinned-checkout tests need the real Lear clone (``RAG_LEAR_SOURCE`` or
``/home/user/lear-backend``) and are skipped, visibly, when it is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ragformation.lear.docgen import build_docs
from ragformation.lear.docverify import check_api_auth, verify_docs

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs" / "lear_backend"
LEAR_SOURCE = Path(os.environ.get("RAG_LEAR_SOURCE", "/home/user/lear-backend"))
HAVE_SOURCE = (LEAR_SOURCE / ".git").exists() and (LEAR_SOURCE / "prash" / "server.py").exists()

SERVER_OPEN = '''from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])


@app.get("/health")
def health():
    return {"ok": True}


@app.api_route("/approve/{item}", methods=["GET", "POST"])
def approve(item: str):
    return {"approved": item}
'''

SERVER_AUTH = SERVER_OPEN + '''

def current_user():
    return "u"


@app.post("/secure")
def secure(user: str = Depends(current_user)):
    return {"user": user}
'''.replace("from fastapi import FastAPI", "from fastapi import Depends, FastAPI")


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return proc.stdout.strip()


class SyntheticRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lear-docs-"))
        self.repo = self.tmp / "src"
        (self.repo / "prash").mkdir(parents=True)
        git(self.repo, "init", "-q")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, server: str) -> str:
        (self.repo / "prash" / "server.py").write_text(server, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "fixture")
        return git(self.repo, "rev-parse", "HEAD")

    def docs(self, commit: str, body: str) -> Path:
        docs = self.tmp / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "SOURCE_PIN.json").write_text(json.dumps({"repository": "example/src", "commit": commit}), encoding="utf-8")
        (docs / "architecture.md").write_text(
            f"# Architecture\n\nSource commit `{commit[:12]}`.\n\n{body}\n", encoding="utf-8")
        return docs


class ApiAuthClaimTests(SyntheticRepoCase):
    def test_claim_holds_without_dependencies_and_counts_api_route_methods(self) -> None:
        commit = self.commit(SERVER_OPEN)
        result = check_api_auth(self.repo, commit)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["routes"], 3)  # GET /health + GET/POST /approve

    def test_claim_is_contradicted_when_a_route_declares_depends(self) -> None:
        commit = self.commit(SERVER_AUTH)
        result = check_api_auth(self.repo, commit)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("secure" in e for e in result["examples"]))


class VerifyDocsTests(SyntheticRepoCase):
    def statuses(self, docs: Path) -> dict:
        report = verify_docs(docs, self.repo)
        return {c["name"]: c for c in report["checks"]}

    def test_valid_reference_passes(self) -> None:
        commit = self.commit(SERVER_OPEN)
        checks = self.statuses(self.docs(commit, "- **[implemented]** `/health` returns ok. Evidence: `prash/server.py:L8-L10`."))
        self.assertEqual(checks["pin"]["status"], "PASS")
        self.assertEqual(checks["source_refs"]["status"], "PASS")
        self.assertEqual(checks["claim_labels"]["status"], "PASS")
        self.assertEqual(checks["secrets"]["status"], "PASS")

    def test_out_of_range_reference_fails(self) -> None:
        commit = self.commit(SERVER_OPEN)
        checks = self.statuses(self.docs(commit, "- **[implemented]** Something. Evidence: `prash/server.py:L900-L910`."))
        self.assertEqual(checks["source_refs"]["status"], "FAIL")

    def test_missing_path_fails(self) -> None:
        commit = self.commit(SERVER_OPEN)
        checks = self.statuses(self.docs(commit, "- **[implemented]** Something. Evidence: `prash/nowhere.py:L1-L2`."))
        self.assertEqual(checks["source_refs"]["status"], "FAIL")

    def test_foreign_commit_id_fails(self) -> None:
        commit = self.commit(SERVER_OPEN)
        checks = self.statuses(self.docs(commit, "As of commit deadbeefcafe1234 the server had one route."))
        self.assertEqual(checks["stale_commits"]["status"], "FAIL")

    def test_credentials_and_personal_email_fail(self) -> None:
        commit = self.commit(SERVER_OPEN)
        token = "ghp_" + "Z" * 32
        checks = self.statuses(self.docs(commit, f"Example token {token} and owner someone@example.org."))
        self.assertEqual(checks["secrets"]["status"], "FAIL")

    def test_unlabelled_claim_fails(self) -> None:
        commit = self.commit(SERVER_OPEN)
        checks = self.statuses(self.docs(commit, "- The server exposes `/health`. Evidence: `prash/server.py:L8-L10`."))
        self.assertEqual(checks["claim_labels"]["status"], "FAIL")


@unittest.skipUnless(HAVE_SOURCE, f"pinned Lear checkout not found at {LEAR_SOURCE}")
class PinnedCheckoutTests(unittest.TestCase):
    def test_shipped_docs_verify_against_the_pinned_checkout(self) -> None:
        report = verify_docs(DOCS, LEAR_SOURCE)
        failing = [c for c in report["checks"] if c["status"] == "FAIL"]
        self.assertTrue(report["passed"], failing)
        names = {c["name"] for c in report["checks"]}
        self.assertTrue({"api_auth_claim", "route_count", "source_refs", "secrets"}.issubset(names))

    def test_generator_is_deterministic_and_matches_committed_pages(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lear-docgen-"))
        try:
            out, agent = tmp / "lear_backend", tmp / "lear_agent"
            first = build_docs(LEAR_SOURCE, out, agent_docs_dir=agent)
            second = build_docs(LEAR_SOURCE, out, agent_docs_dir=agent)
            self.assertEqual(second["written"], [])
            self.assertEqual(first["counts"]["routes"], 87)
            for generated in sorted(out.rglob("*")):
                if generated.is_file():
                    committed = DOCS / generated.relative_to(out)
                    self.assertEqual(generated.read_bytes(), committed.read_bytes(), str(committed))
            self.assertEqual((agent / "source_inventory.md").read_bytes(),
                             (REPO_ROOT / "docs" / "lear_agent" / "source_inventory.md").read_bytes())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
