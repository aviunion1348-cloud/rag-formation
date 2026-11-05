"""Regression tests for defects found in the 2026-11 repository audit.

Each test reproduces one observed failure of the code at commit 986ba55 and
pins the corrected behaviour. They use the same offline fixtures as the rest of
the suite (hashing embeddings + extractive answers, temporary data dir).
"""

from __future__ import annotations

import json
import unittest

from helpers import REPO_ROOT, TempDirTestCase, make_settings

from ragformation import cli
from ragformation.errors import ConfigError
from ragformation.events import EventBus, make_event
from ragformation.retrieval import Candidate, Retriever
from ragformation.store import StoredChunk


def _chunk(chunk_id: str, text: str) -> StoredChunk:
    return StoredChunk(
        chunk_id=chunk_id,
        source_id="src-test",
        revision=1,
        ordinal=0,
        text=text,
        token_count=len(text.split()),
        section="",
        section_path=[],
        page_start=0,
        page_end=0,
        content_hash=chunk_id,
    )


class RequireSemanticTests(TempDirTestCase):
    def test_lexical_embedder_with_require_semantic_raises_config_error(self):
        # Before the fix the guard referenced ConfigError without importing it,
        # so the misconfiguration surfaced as NameError instead of a clean
        # config_error with a hint.
        with self.assertRaises(ConfigError) as ctx:
            self.make_engine(require_semantic=True)
        self.assertIn("RAG_REQUIRE_SEMANTIC", ctx.exception.message)


class MMRRelevanceTests(TempDirTestCase):
    def test_zero_support_is_not_replaced_by_the_raw_channel_score(self):
        retriever = Retriever(None, make_settings(self.tmpdir / "mmr"))  # _mmr never touches the store
        unsupported = Candidate(_chunk("c-unsupported", "alpha"), score=7.5, channel="lexical", support=0.0)
        supported = Candidate(_chunk("c-supported", "beta"), score=0.4, channel="lexical", support=0.6)
        ordered = retriever._mmr([unsupported, supported], limit=2, vectors={})
        self.assertEqual([c.chunk.chunk_id for c in ordered], ["c-supported", "c-unsupported"])

    def test_missing_support_still_falls_back_to_the_score(self):
        retriever = Retriever(None, make_settings(self.tmpdir / "mmr2"))
        low = Candidate(_chunk("c-low", "alpha"), score=0.1, channel="semantic", support=None)
        high = Candidate(_chunk("c-high", "beta"), score=0.9, channel="semantic", support=None)
        ordered = retriever._mmr([low, high], limit=2, vectors={})
        self.assertEqual(ordered[0].chunk.chunk_id, "c-high")


class TraceAndDiagnosticsTests(TempDirTestCase):
    def test_trace_reports_mmr_as_completed_when_it_ran(self):
        engine = self.corpus_engine()
        result = engine.ask("How long are audit logs retained?", save=False)
        mmr_events = [e for e in result.trace if e.get("stage") == "retrieval.mmr"]
        self.assertTrue(mmr_events, "trace has no retrieval.mmr event")
        self.assertEqual(mmr_events[-1]["status"], "completed", mmr_events[-1])

    def test_excluded_candidates_are_reported_once_and_never_selected(self):
        engine = self.make_engine(max_context_tokens=120)
        engine.ingest_path([str(REPO_ROOT / "corpus")])
        result = engine.ask("What is the refund policy and the incident escalation process?", save=False)
        ids = [item["chunkId"] for item in result.excluded]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate excluded entries: {ids}")
        cited = {c.chunk_id for c in result.citations} if result.citations else set()
        self.assertFalse(cited & set(ids), "a cited chunk is also reported as excluded")
        for item in result.excluded:
            self.assertIn(item["reason"], {"context_token_budget", "ranked_outside_top_k"})

    def test_metrics_and_stored_row_reflect_the_request_parameters(self):
        engine = self.corpus_engine()
        result = engine.ask(
            "How long are audit logs retained?", mode="lexical", top_k=3, temperature=0.0, save=True
        )
        self.assertEqual(result.metrics["mode"], "lexical")
        self.assertEqual(result.metrics["topK"], 3)
        self.assertEqual(result.metrics["temperature"], 0.0)
        row = engine.store.query_row(result.query_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["mode"], "lexical")
        self.assertEqual(int(row["top_k"]), 3)
        self.assertEqual(float(row["temperature"]), 0.0)


class EventHistoryTests(unittest.TestCase):
    def test_history_returns_the_newest_events_in_order(self):
        bus = EventBus(history=512)
        for index in range(100):
            bus.publish(make_event("retrieval", "completed", trace_id="t-1", detail={"i": index}))
        events = bus.history(limit=10)
        self.assertEqual(len(events), 10)
        sequences = [event["sequence"] for event in events]
        self.assertEqual(sequences, list(range(91, 101)))


class AblationTests(TempDirTestCase):
    def test_each_ablation_row_pins_rerank_and_mmr(self):
        from ragformation import evaluation_v2

        captured = []
        original = evaluation_v2._retrieval_score

        def spy(engine, case, sources, *, k, **options):
            captured.append(options)
            return original(engine, case, sources, k=k, **options)

        engine = self.corpus_engine()
        cases = json.loads((REPO_ROOT / "evals" / "golden_v2.json").read_text(encoding="utf-8"))["cases"]
        labelled = [case for case in cases if case.get("expectedSources")][:4]
        sources = {s["sourceId"]: s for s in engine.list_sources()}
        evaluation_v2._retrieval_score = spy
        try:
            output = evaluation_v2._ablation(engine, labelled, sources, 6)
        finally:
            evaluation_v2._retrieval_score = original
        per_config = {}
        for options in captured:
            per_config.setdefault((options["mode"], options["rerank"], options["mmr"]), 0)
        self.assertEqual(
            set(per_config),
            {
                ("semantic", False, False),
                ("lexical", False, False),
                ("hybrid", False, False),
                ("hybrid", True, False),
                ("hybrid", True, True),
            },
        )
        self.assertEqual(set(output), {"dense", "lexical", "hybrid", "hybrid_rerank", "hybrid_rerank_mmr"})


class CliAliasTests(unittest.TestCase):
    def test_rebuild_accepts_the_documented_reembed_flag(self):
        args = cli.build_parser().parse_args(["rebuild", "--reembed"])
        self.assertTrue(args.force)
        args = cli.build_parser().parse_args(["rebuild", "--force"])
        self.assertTrue(args.force)
        args = cli.build_parser().parse_args(["rebuild"])
        self.assertFalse(args.force)


if __name__ == "__main__":
    unittest.main()
