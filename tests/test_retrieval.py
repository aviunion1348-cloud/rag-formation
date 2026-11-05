"""Retrieval: semantic, lexical, fusion, rerank, MMR, context budget, filters."""

from __future__ import annotations

import unittest

from helpers import TempDirTestCase

from ragformation.retrieval import (
    Candidate,
    Retriever,
    assemble_context,
    coverage_score,
    dedupe_overlaps,
)
from ragformation.store import StoredChunk


def stored(chunk_id: str, source_id: str, text: str, ordinal: int = 0, tokens: int = 40) -> StoredChunk:
    return StoredChunk(
        chunk_id=chunk_id,
        source_id=source_id,
        revision=1,
        ordinal=ordinal,
        text=text,
        token_count=tokens,
        section="Section",
        section_path=["Section"],
        page_start=1,
        page_end=1,
        content_hash="h" * 64,
        source_title=source_id,
    )


class RetrieverTests(TempDirTestCase):
    settings_overrides = {"top_k": 3, "rerank": True}

    def build(self, engine):
        self.docs = [
            ("src-a", "a1", "Audit logs are retained for 400 days in cold storage."),
            ("src-a", "a2", "Application logs are retained for 90 days in hot storage."),
            ("src-b", "b1", "The incident commander must be paged within 10 minutes of a SEV-1."),
            ("src-c", "c1", "Vendor access to production is time-boxed to 14 days."),
        ]
        for source_id, chunk_id, text in self.docs:
            engine.ingest_bytes(f"{source_id}-{chunk_id}.md", f"# Title\n\n{text}\n".encode())
        return Retriever(engine.store, engine.settings)

    def test_hybrid_returns_the_relevant_chunk_first(self):
        engine = self.make_engine()
        retriever = self.build(engine)
        vector = engine.embedder.embed_one("How long are audit logs retained?")
        result = retriever.search("How long are audit logs retained?", vector, top_k=3)
        self.assertTrue(result.candidates)
        top = result.candidates[0]
        self.assertIn("400 days", top.chunk.text)
        self.assertGreater(result.semantic_candidates, 0)
        self.assertGreater(result.lexical_candidates, 0)
        self.assertIn("semanticMs", result.timings_ms)

    def test_lexical_only_mode_uses_bm25(self):
        engine = self.make_engine()
        retriever = self.build(engine)
        vector = engine.embedder.embed_one("SEV-1 paging")
        result = retriever.search("SEV-1 paging", vector, top_k=3, mode="lexical")
        self.assertEqual(result.mode, "lexical")
        self.assertEqual(result.semantic_candidates, 0)
        self.assertIn("10 minutes", result.candidates[0].chunk.text)

    def test_allowed_sources_filter_is_enforced_before_context(self):
        engine = self.make_engine()
        retriever = self.build(engine)
        vector = engine.embedder.embed_one("retention of logs")
        allowed = engine.store.active_source_ids() - {
            source["sourceId"] for source in engine.list_sources() if source["title"] == "src-a-a1"
        }
        result = retriever.search("retention of logs", vector, top_k=5, allowed_sources=allowed)
        self.assertTrue(all(candidate.chunk.source_id in allowed for candidate in result.candidates))

    def test_empty_allowed_set_returns_nothing(self):
        engine = self.make_engine()
        retriever = self.build(engine)
        vector = engine.embedder.embed_one("anything")
        result = retriever.search("anything", vector, top_k=3, allowed_sources=set())
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.best_support, 0.0)

    def test_coverage_score_is_bounded_and_prefix_tolerant(self):
        self.assertAlmostEqual(coverage_score("audit logs retained 400 days", "audit logs retained"), 1.0, places=6)
        self.assertGreaterEqual(coverage_score("deployment rollback window", "deploy rollback"), 0.5)
        self.assertEqual(coverage_score("unrelated text", "audit logs"), 0.0)

    def test_context_budget_reports_why_a_chunk_was_skipped(self):
        candidates = [
            Candidate(chunk=stored("c1", "s", "one " * 100, tokens=100), score=1.0, channel="hybrid", support=1.0),
            Candidate(chunk=stored("c2", "s", "two " * 100, tokens=100), score=0.9, channel="hybrid", support=0.9),
        ]
        selected, used, skipped = assemble_context(candidates, token_budget=150)
        self.assertEqual(len(selected), 1)
        self.assertEqual(used, 100)
        self.assertEqual(skipped[0]["reason"], "context_token_budget")

    def test_dedupe_overlaps_removes_near_duplicates(self):
        text = "Audit logs are retained for 400 days in cold storage and reviewed annually."
        a = Candidate(chunk=stored("c1", "s", text), score=1.0, channel="hybrid", support=1.0)
        b = Candidate(chunk=stored("c2", "s", text + " Reviewed."), score=0.9, channel="hybrid", support=0.9)
        c = Candidate(chunk=stored("c3", "s", "Vendor access is limited to 14 days."), score=0.8, channel="hybrid", support=0.8)
        kept = dedupe_overlaps([a, b, c])
        self.assertEqual(len(kept), 2)
        self.assertIn("c3", [item.chunk.chunk_id for item in kept])

    def test_rerank_can_be_disabled(self):
        engine = self.make_engine()
        retriever = self.build(engine)
        vector = engine.embedder.embed_one("vendor access duration")
        result = retriever.search("vendor access duration", vector, top_k=3, rerank=False)
        self.assertFalse(result.reranked)
        self.assertNotIn("rerankMs", result.timings_ms)


if __name__ == "__main__":
    unittest.main()
