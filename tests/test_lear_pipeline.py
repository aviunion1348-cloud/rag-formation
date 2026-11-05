"""Pipeline tests for the Lear agent.

* Baseline tests run the real engine with the labelled offline baseline
  (hashing embeddings + extractive answers) on a small fixture corpus.
* "stub" tests point the strict-local Ollama code paths at
  ``tests/_lear_stub_ollama.py`` — a fake HTTP server, not a model. They check
  wiring (loopback client, caps, streaming, citations, judge retries) only.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _lear_stub_ollama import StubOllama

from ragformation.cli import main as cli_main
from ragformation.errors import RagError, RateLimited
from ragformation.lear import indexer
from ragformation.lear.agent import LearAgent, exit_code_for
from ragformation.lear.bench import run_bench
from ragformation.lear.doctor import doctor_exit_code, run_doctor
from ragformation.lear.evalset import DEFAULT_DATASET, REPO_ROOT
from ragformation.lear.evaluate import run_evaluation
from ragformation.lear.judge import LocalJudge
from ragformation.lear.ratelimit import LearRateLimiter
from ragformation.lear.settings import LearSettings

CLOSED_PORT_URL = "http://127.0.0.1:9"  # discard port; nothing listens in the sandbox

GUIDE = """# Guide

## Permission engine

The permission engine has five permission modes: read-only, ask, auto-safe, environment-scoped and bypass. Evidence: `prash/permissions.py:L26-L31`.

APPROVAL-tier actions always need an explicit per-action grant, even in bypass mode. Evidence: `prash/permissions.py:L62-L64`.

## Circuit breaker

The circuit breaker allows 5 actions per 60 seconds for each resource before it opens. Evidence: `prash/circuit_breaker.py:L28-L30`.
"""

OPS = """# Operations

## Exit codes

`prash run` returns exit code 3 when an action needs a secret that was not supplied. Evidence: `prash/cli.py:L365-L367`.
"""

TABLE = """# Variables

## Environment variables

| Variable | Default |
|---|---|
| `PRASH_WATCH_INTERVAL_SECONDS` | 30 |
| `PRASH_CIRCUIT_MAX_ACTIONS` | 5 |
"""

NONSENSE = "Which seasoning pairs best with grilled aubergine at a summer picnic?"


def make_docs(root: Path) -> Path:
    docs = root / "docs"
    (docs / "reference").mkdir(parents=True)
    (docs / "guide.md").write_text(GUIDE, encoding="utf-8")
    (docs / "ops.md").write_text(OPS, encoding="utf-8")
    (docs / "reference" / "variables.md").write_text(TABLE, encoding="utf-8")
    (docs / "SOURCE_PIN.json").write_text(json.dumps({"repository": "example/lear", "commit": "a" * 40, "commit_short": "a" * 12}), encoding="utf-8")
    return docs


class PipelineCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lear-pipe-"))
        self.docs = make_docs(self.tmp)
        self.data = self.tmp / "data"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, **overrides) -> LearSettings:
        values = dict(backend="baseline", data_dir=self.data, docs_dir=self.docs, ollama_url=CLOSED_PORT_URL)
        values.update(overrides)
        settings = LearSettings(**values)
        settings.validate()
        return settings


# ----------------------------------------------------------------- indexing
class IndexerTests(PipelineCase):
    REQUIRED = {"chunk_id", "source_id", "source_revision", "source_path", "source_commit", "document_title", "heading_path",
                "start_line", "end_line", "chunk_index", "content_hash", "embedding_model", "embedding_fingerprint",
                "embedding_dimension", "created_at", "namespace"}

    def test_incremental_update_delete_and_restart_persistence(self) -> None:
        settings = self.settings()
        agent = LearAgent(settings)
        try:
            first = indexer.rebuild(agent.engine, settings)
            self.assertEqual(first["counts"]["new"], 3)
            ids_first = sorted(e["chunk_id"] for e in indexer.load_manifest(settings)["chunks"])
            second = indexer.rebuild(agent.engine, settings)
            self.assertEqual(second["counts"]["unchanged"], 3)
            self.assertEqual(sorted(e["chunk_id"] for e in indexer.load_manifest(settings)["chunks"]), ids_first)
            (self.docs / "ops.md").write_text(OPS + "\n## Dry run\n\n`--dry-run` only plans the action and never executes it.\n", encoding="utf-8")
            third = indexer.rebuild(agent.engine, settings)
            self.assertEqual((third["counts"]["updated"], third["counts"]["unchanged"]), (1, 2))
            (self.docs / "reference" / "variables.md").unlink()
            fourth = indexer.rebuild(agent.engine, settings)
            self.assertEqual(fourth["counts"]["deleted"], 1)
            manifest = indexer.load_manifest(settings)
            entry = manifest["chunks"][0]
            self.assertTrue(self.REQUIRED.issubset(entry), self.REQUIRED - set(entry))
            self.assertEqual(entry["source_commit"], "a" * 40)
            self.assertEqual(entry["namespace"], "lear-docs")
            chunks = len(manifest["chunks"])
        finally:
            agent.close()
        restarted = LearAgent(settings)
        try:
            self.assertEqual(indexer.stats(restarted.engine, settings)["chunks"], chunks)
        finally:
            restarted.close()

    def test_chunking_change_rechunks_every_document(self) -> None:
        # Regression: the engine keeps one global chunking fingerprint and sets
        # it on the first re-ingested file, so unchanged files used to keep
        # chunks made with the old settings after an incremental rebuild.
        old = self.settings(chunk_overlap_tokens=20, chunk_target_tokens=40, chunk_max_tokens=80)
        agent = LearAgent(old)
        try:
            indexer.rebuild(agent.engine, old)
        finally:
            agent.close()
        (self.docs / "ops.md").write_text(OPS + "\nOne more line about `prash run` exit codes.\n", encoding="utf-8")
        new = self.settings(chunk_overlap_tokens=0, chunk_target_tokens=40, chunk_max_tokens=80)
        agent = LearAgent(new)
        try:
            report = indexer.rebuild(agent.engine, new)
            self.assertTrue(report["rechunked"])
            self.assertEqual(report["counts"]["updated"], 3)
            mixed = sorted(c.text for c in agent.engine.store.all_chunks())
        finally:
            agent.close()
        fresh_settings = self.settings(data_dir=self.tmp / "fresh", chunk_overlap_tokens=0, chunk_target_tokens=40, chunk_max_tokens=80)
        fresh = LearAgent(fresh_settings)
        try:
            indexer.rebuild(fresh.engine, fresh_settings)
            self.assertEqual(mixed, sorted(c.text for c in fresh.engine.store.all_chunks()))
        finally:
            fresh.close()

    def test_index_markers_exclude_navigation(self) -> None:
        (self.docs / "nav.md").write_text("# Nav\n\n<!-- lear-index: skip-page -->\n\n- [Guide](guide.md)\n", encoding="utf-8")
        (self.docs / "guide.md").write_text(GUIDE + "\n<!-- lear-index: skip -->\n## Pages\n\n- [Ops](ops.md) zebra-navigation-only\n<!-- lear-index: end -->\n", encoding="utf-8")
        settings = self.settings()
        agent = LearAgent(settings)
        try:
            report = indexer.rebuild(agent.engine, settings)
            self.assertEqual(report["excludedPages"], ["nav.md"])
            texts = " ".join(c.text for c in agent.engine.store.all_chunks())
            self.assertNotIn("zebra-navigation-only", texts)
            self.assertNotIn("**", texts.replace("**[", ""))  # labels are normalised
        finally:
            agent.close()

    def test_table_rows_stay_together(self) -> None:
        settings = self.settings()
        agent = LearAgent(settings)
        try:
            indexer.rebuild(agent.engine, settings)
            texts = [c.text for c in agent.engine.store.all_chunks() if "PRASH_WATCH_INTERVAL_SECONDS" in c.text]
            self.assertEqual(len(texts), 1)
            self.assertIn("PRASH_CIRCUIT_MAX_ACTIONS", texts[0])
        finally:
            agent.close()


# ------------------------------------------------------------ baseline agent
class BaselineAgentTests(PipelineCase):
    def setUp(self) -> None:
        super().setUp()
        self.s = self.settings()
        self.agent = LearAgent(self.s)
        indexer.rebuild(self.agent.engine, self.s)

    def tearDown(self) -> None:
        self.agent.close()
        super().tearDown()

    def test_grounded_answer_has_line_cited_evidence(self) -> None:
        answer = self.agent.ask("Which permission modes does the permission engine have?")
        self.assertEqual(answer["status"], "GROUNDED")
        self.assertFalse(answer["qualifying"])  # baseline answers are labelled
        self.assertTrue(answer["citations"])
        self.assertTrue(all(c["lines"] for c in answer["citations"]))
        self.assertIn("guide.md", answer["citations"][0]["document"])
        self.assertEqual(exit_code_for(answer), 0)
        self.assertIn("candidates", answer["retrieval"])
        self.assertIn("retrieval.lexical", answer["retrieval"]["stages"])

    def test_unrelated_question_abstains(self) -> None:
        answer = self.agent.ask(NONSENSE)
        self.assertEqual(answer["status"], "ABSTAINED")
        self.assertEqual(exit_code_for(answer), 3)
        self.assertEqual(answer["citations"], [])

    def test_follow_up_uses_session_history(self) -> None:
        self.agent.ask("What does the circuit breaker allow?", session_id="s-1")
        follow = self.agent.ask("What is its window?", session_id="s-1")
        self.assertIsNotNone(follow["retrieval"]["retrievalQuery"])
        self.assertIn("circuit breaker", follow["retrieval"]["retrievalQuery"])
        self.assertEqual(follow["session"]["turn"], 2)
        self.assertEqual(len(self.agent.sessions.messages("s-1", "local")), 4)

    def test_rate_limit_is_enforced_in_the_service_layer(self) -> None:
        agent = LearAgent(self.s, limiter=LearRateLimiter(per_user_per_min=1, per_session_per_min=5, max_concurrent=1))
        try:
            agent.ask("Which permission modes exist?", user_id="u1")
            with self.assertRaises(RateLimited):
                agent.ask("Which permission modes exist?", user_id="u1")
        finally:
            agent.close()

    def test_oversized_question_is_rejected_before_retrieval(self) -> None:
        with self.assertRaises(RagError):
            self.agent.ask("x" * (self.s.max_input_chars + 1))


# --------------------------------------------------------------- stub ollama
class StubOllamaTests(PipelineCase):
    def strict(self, stub: StubOllama, **extra) -> LearSettings:
        return self.settings(backend="ollama", ollama_url=stub.url, embed_model="nomic-embed-text",
                             llm_model="qwen2.5:1.5b", judge_model="llama3.2:1b", **extra)

    def test_stub_doctor_ready_caps_and_missing_models(self) -> None:
        with StubOllama() as stub:
            report = run_doctor(self.strict(stub))
            self.assertTrue(report["ready"], report["requiredFailures"])
            self.assertEqual(doctor_exit_code(report), 0)
            stub.models["qwen2.5:1.5b"] = 5_000_000_000
            capped = run_doctor(self.strict(stub))
            status = {c["check"]: c["status"] for c in capped["checks"]}
            self.assertEqual(status["models.llm"], "FAIL")
            del stub.models["llama3.2:1b"]
            missing = run_doctor(self.strict(stub))
            installed = next(c for c in missing["checks"] if c["check"] == "models.installed")
            self.assertEqual(installed["status"], "FAIL")
            self.assertIn("ollama pull llama3.2:1b", installed["hint"])

    def test_doctor_reports_unreachable_ollama(self) -> None:
        report = run_doctor(self.settings(backend="ollama"))
        self.assertFalse(report["ready"])
        self.assertEqual(doctor_exit_code(report), 6)
        self.assertIn("ollama.version", report["requiredFailures"])

    def test_stub_pipeline_streams_and_validates_citations(self) -> None:
        with StubOllama() as stub:
            settings = self.strict(stub)
            agent = LearAgent(settings)
            try:
                built = indexer.rebuild(agent.engine, settings)
                self.assertEqual(built["embedding"]["provider"], "ollama")
                self.assertEqual(built["embedding"]["dimension"], 64)
                tokens = []
                answer = agent.ask("Which permission modes does the permission engine have?", on_token=tokens.append)
                self.assertEqual(answer["status"], "GROUNDED", answer.get("warnings"))
                self.assertGreater(len(tokens), 3)
                self.assertIsNotNone(answer["timings"]["ttftMs"])
                self.assertEqual(answer["timings"]["completionTokens"], len(answer["answer"].split(" ")) or None)
                self.assertTrue(answer["citations"])
                self.assertEqual(answer["models"]["generation"]["used"], "ollama:qwen2.5:1.5b")
                stub.answer_mode = "abstain"
                self.assertEqual(agent.ask("Which permission modes exist in the engine?")["status"], "ABSTAINED")
                chats = [r for r in stub.requests if r["path"] == "/api/chat"]
                self.assertTrue(all(r["payload"]["model"] == "qwen2.5:1.5b" for r in chats))
            finally:
                agent.close()

    def test_stub_embedding_dimension_change_is_refused_until_full_rebuild(self) -> None:
        # Regression: the core dot() returns 0.0 for mismatched lengths, so a
        # changed embedder used to degrade retrieval silently (status GROUNDED).
        with StubOllama() as stub:
            settings = self.strict(stub)
            agent = LearAgent(settings)
            try:
                indexer.rebuild(agent.engine, settings)
            finally:
                agent.close()
            stub.dimension = 32
            agent = LearAgent(settings)
            try:
                answer = agent.ask("Which permission modes does the permission engine have?")
                self.assertEqual(answer["status"], "ERROR")
                self.assertEqual(answer["error"]["error"], "embedding_mismatch")
                self.assertIn("rebuild --full", answer["error"]["hint"])
                with self.assertRaises(RagError):
                    indexer.rebuild(agent.engine, settings)  # incremental refuses too
                rebuilt = indexer.rebuild(agent.engine, settings, full=True)
                self.assertEqual(rebuilt["embedding"]["dimension"], 32)
                self.assertEqual(agent.ask("Which permission modes does the permission engine have?")["status"], "GROUNDED")
            finally:
                agent.close()

    def test_stub_judge_retries_invalid_json_and_saves_raw_output(self) -> None:
        with StubOllama() as stub:
            stub.judge_replies = ["this is not json"]
            raw = self.tmp / "raw"
            judge = LocalJudge(self.strict(stub), raw_dir=raw, max_retries=2)
            self.assertTrue(judge.probe()["available"])
            result = judge.score({"id": "lear-x", "question": "q", "expected": {"must_include": [["a"]]}},
                                 {"status": "GROUNDED", "answer": "a [1]"}, [{"document": "d", "heading": "h", "text": "a"}])
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["attempts"], 2)
            self.assertEqual(len(list(raw.glob("lear-x.attempt*.json"))), 2)

    def test_judge_is_blocked_without_ollama(self) -> None:
        probe = LocalJudge(self.settings(backend="ollama")).probe()
        self.assertFalse(probe["available"])
        self.assertEqual(probe["status"], "BLOCKED")


# ---------------------------------------------------------------------- CLI
class CliTests(PipelineCase):
    def run_cli(self, *argv: str, env: dict | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli_main(["lear", "--data-dir", str(self.data), "--docs-dir", str(self.docs), *argv])
        return code, out.getvalue()

    def test_exit_codes(self) -> None:
        base = ("--backend", "baseline")
        code, out = self.run_cli(*base, "index", "rebuild", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["counts"]["new"], 3)
        code, out = self.run_cli(*base, "ask", "Which permission modes does the permission engine have?", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "GROUNDED")
        code, _ = self.run_cli(*base, "ask", NONSENSE, "--no-stream")
        self.assertEqual(code, 3)
        code, _ = self.run_cli("doctor", "--json", env={"RAG_LEAR_OLLAMA_URL": CLOSED_PORT_URL})
        self.assertEqual(code, 6)
        code, _ = self.run_cli(*base, "eval", "run", "--suite", "holdout")
        self.assertEqual(code, 2)
        code, out = self.run_cli(*base, "sessions", "list", "--json")
        self.assertEqual((code, json.loads(out)), (0, []))
        code, _ = self.run_cli(*base, "sessions", "inspect", "missing-session")
        self.assertEqual(code, 2)


# -------------------------------------------------------------- bench / eval
class BenchTests(PipelineCase):
    def test_bench_keeps_rate_limited_requests_in_the_results(self) -> None:
        settings = self.settings(rate_session_per_min=1)
        agent = LearAgent(settings)
        try:
            indexer.rebuild(agent.engine, settings)
        finally:
            agent.close()
        report = run_bench(settings, users=[2], requests_per_user=2, questions=["Which permission modes exist?", "What does the circuit breaker allow?"])
        level = report["levels"][0]
        self.assertEqual(level["requests"], 4)
        self.assertEqual(level["rateLimited"], 2)
        self.assertEqual(level["ok"], 2)
        self.assertFalse(report["qualifying"])
        self.assertIn("PIPELINE-ONLY", report["label"])
        self.assertTrue(Path(report["output"]).exists())


class EvaluationRunTests(unittest.TestCase):
    def test_calibration_run_writes_artifacts_and_reports_judge_blocked(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lear-eval-"))
        try:
            settings = LearSettings(backend="baseline", data_dir=tmp / "data", docs_dir=REPO_ROOT / "docs" / "lear_backend",
                                    ollama_url=CLOSED_PORT_URL)
            agent = LearAgent(settings)
            try:
                indexer.rebuild(agent.engine, settings)
            finally:
                agent.close()
            report = run_evaluation(settings, dataset=DEFAULT_DATASET, suites=["calibration"], label="test", judge="auto",
                                    out_dir=tmp / "run")
            summary = report["summary"]
            self.assertEqual(summary["cases"], 10)
            self.assertEqual(summary["judge"]["state"], "BLOCKED")
            self.assertFalse(summary["qualifying"])
            for name in ("run.json", "cases.jsonl", "summary.md"):
                self.assertTrue((tmp / "run" / name).exists(), name)
            self.assertTrue((tmp / "run" / "eval_agent.db").exists())  # eval sessions stay in the run dir
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
