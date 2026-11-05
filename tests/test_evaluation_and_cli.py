"""Golden-set evaluation and the CLI surface."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from helpers import CORPUS_DIR, REPO_ROOT, TempDirTestCase

from ragformation import cli
from ragformation.evaluation import run_evaluation


class EvaluationTests(TempDirTestCase):
    def test_golden_set_passes_on_the_bundled_corpus(self):
        engine = self.corpus_engine()
        report = run_evaluation(engine, golden_path=str(REPO_ROOT / "evals" / "golden.json"))
        self.assertEqual(report["questions"], 14)
        self.assertGreaterEqual(report["retrieval"]["recallAtK"], 0.9)
        self.assertGreaterEqual(report["retrieval"]["mrrAtK"], 0.8)
        self.assertGreaterEqual(report["retrieval"]["ndcgAtK"], 0.8)
        self.assertEqual(report["generation"]["abstainAccuracy"], 1.0)
        self.assertGreaterEqual(report["generation"]["factRecall"], 0.9)
        self.assertEqual(report["generation"]["forbiddenFactRate"], 1.0)
        self.assertTrue(report["passed"], [row["failures"] for row in report["cases"] if not row["passed"]])
        # Metrics must be real observations, not placeholders.
        self.assertGreater(report["latency"]["totalMs"]["count"], 0)
        self.assertGreater(report["latency"]["totalMs"]["p95"], 0)

    def test_retrieval_modes_are_measured_separately(self):
        engine = self.corpus_engine()
        hybrid = run_evaluation(engine, top_k=6, mode="hybrid")
        lexical = run_evaluation(engine, top_k=6, mode="lexical")
        self.assertEqual(hybrid["mode"], "hybrid")
        self.assertEqual(lexical["mode"], "lexical")
        self.assertGreaterEqual(lexical["retrieval"]["recallAtK"], 0.7)


class CliTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.env_backup = {key: os.environ.get(key) for key in ("RAG_DATA_DIR", "RAG_EMBED_PROVIDER", "RAG_LLM_PROVIDER")}
        os.environ["RAG_DATA_DIR"] = str(self.settings.data_dir)
        os.environ["RAG_EMBED_PROVIDER"] = "hashing"
        os.environ["RAG_LLM_PROVIDER"] = "extractive"

    def tearDown(self):
        for key, value in self.env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def run_cli(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_ingest_ask_and_sources(self):
        code, out, _ = self.run_cli(["ingest", str(CORPUS_DIR)])
        self.assertEqual(code, 0)
        self.assertIn("indexed", out)

        code, out, _ = self.run_cli(["ask", "How long are audit logs retained?"])
        self.assertEqual(code, 0)
        self.assertIn("400 days", out)
        self.assertIn("status", out)

        code, out, _ = self.run_cli(["sources", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out))

    def test_ask_json_reports_abstention_with_exit_code(self):
        self.run_cli(["ingest", str(CORPUS_DIR)])
        code, out, _ = self.run_cli(["ask", "What is the lunar payload insurance policy?", "--json"])
        self.assertEqual(code, 3)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "abstained")
        self.assertEqual(payload["citations"], [])

    def test_stats_and_doctor_run(self):
        self.run_cli(["ingest", str(CORPUS_DIR)])
        self.run_cli(["ask", "How long are audit logs retained?"])
        code, out, _ = self.run_cli(["stats", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["queries"], 1)

        code, out, _ = self.run_cli(["doctor", "--json"])
        report = json.loads(out)
        self.assertIn("checks", report)
        self.assertTrue(any(check["name"] == "index_integrity" for check in report["checks"]))

    def test_rebuild_and_chunks_and_delete(self):
        self.run_cli(["ingest", str(CORPUS_DIR)])
        code, out, _ = self.run_cli(["sources", "--json"])
        source_id = json.loads(out)[0]["sourceId"]

        code, out, _ = self.run_cli(["chunks", source_id, "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out))

        code, out, _ = self.run_cli(["rebuild"])
        self.assertEqual(code, 0)

        code, out, _ = self.run_cli(["delete", source_id, "--yes"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["existed"])

    def test_eval_command_exit_code(self):
        self.run_cli(["ingest", str(CORPUS_DIR)])
        code, out, _ = self.run_cli(["eval", "--v1", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["passed"])

    def test_eval_v2_is_report_only_until_a_gate_is_requested(self):
        self.run_cli(["ingest", str(CORPUS_DIR)])
        code, out, _ = self.run_cli(["eval", "--no-ablations", "--json"])
        report = json.loads(out)
        self.assertEqual(code, 0)  # a measurement, not a CI verdict
        self.assertIn("failedIds", report)
        self.assertEqual(report["casesTotal"], len(report["rows"]))

        # A split that was not run cannot silently pass a gate.
        code, _, err = self.run_cli(["eval", "--no-ablations", "--gate", "nope", "--json"])
        self.assertEqual(code, 2)

        code, out, _ = self.run_cli(
            ["eval", "--no-ablations", "--gate", "all", "--min-pass-rate", "0.0", "--json"]
        )
        self.assertEqual(code, 0)
        report = json.loads(out)  # stdout stays machine-parseable in --json mode
        self.assertTrue(report["gate"]["held"])
        self.assertEqual(report["gate"]["splits"]["all"]["total"], 46)

    def test_eval_gate_fails_on_a_known_bad_dataset(self):
        self.run_cli(["ingest", str(CORPUS_DIR)])
        dataset = {
            "dataset": "gate-fixture",
            "revision": "gate-fixture-1",
            "cases": [
                {
                    "id": "passes",
                    "question": "How long are audit logs retained?",
                    "split": "test",
                    "category": "direct",
                    "expectedStatus": "answered",
                    "mustContain": ["400 days"],
                    "mustNotContain": [],
                    "expectedSources": [],
                },
                {
                    "id": "fails",
                    "question": "What is the capital of Australia?",
                    "split": "test",
                    "category": "no-answer",
                    "expectedStatus": "abstained",
                    "mustContain": [],
                    "mustNotContain": [],
                    "expectedSources": [],
                },
            ],
        }
        path = os.path.join(str(self.settings.data_dir), "gate.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(dataset, handle)
        code, out, _ = self.run_cli(
            ["eval", "--dataset", path, "--no-ablations", "--gate", "test", "--min-pass-rate", "1.0", "--json"]
        )
        self.assertEqual(code, 5, out)

    def test_invalid_question_is_reported_as_usage_error(self):
        self.run_cli(["ingest", str(CORPUS_DIR)])
        code, out, err = self.run_cli(["ask", "   "])
        self.assertEqual(code, 2)
        self.assertIn("validation_error", err)


if __name__ == "__main__":
    unittest.main()
