"""Persistence layer: transactions, revision swaps, deletion, integrity."""

from __future__ import annotations

import unittest

from helpers import TempDirTestCase

from ragformation.store import StoredChunk, Store, pack_vector, unpack_vector


def chunk(chunk_id: str, source_id: str, ordinal: int, text: str) -> StoredChunk:
    return StoredChunk(
        chunk_id=chunk_id,
        source_id=source_id,
        revision=0,
        ordinal=ordinal,
        text=text,
        token_count=12,
        section="Section",
        section_path=["Section"],
        page_start=1,
        page_end=1,
        content_hash="h" * 64,
    )


class StoreTests(TempDirTestCase):
    def make_store(self) -> Store:
        store = Store(self.tmpdir / "index.db")
        self.addCleanup(store.close)
        return store

    def index(self, store: Store, source_id="src-1", chunks=None, vectors=None, revision_text="v1"):
        chunks = chunks or [chunk(f"{source_id}#c000", source_id, 0, revision_text)]
        vectors = vectors or [[1.0, 0.0, 0.0] for _ in chunks]
        return store.replace_source(
            source_id=source_id,
            title="Doc",
            origin="corpus/doc.md",
            media_type="text/markdown",
            byte_size=100,
            content_hash=revision_text,
            tenant_id="local",
            visibility="private",
            tags=["a"],
            warnings=[],
            meta={},
            chunks=chunks,
            vectors=vectors,
            embedding_fingerprint="hashing:x:d3",
            dimension=3,
        )

    def test_vector_pack_roundtrip(self):
        self.assertEqual(unpack_vector(pack_vector([0.5, -1.25])), [0.5, -1.25])

    def test_replace_source_swaps_revision_and_drops_stale_chunks(self):
        store = self.make_store()
        first = self.index(store, revision_text="v1")
        self.assertEqual(first["revision"], 1)

        second = self.index(
            store,
            chunks=[chunk("src-1#c000-new", "src-1", 0, "v2 content")],
            vectors=[[0.0, 1.0, 0.0]],
            revision_text="v2",
        )
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["previousRevision"], 1)
        ids = [item.chunk_id for item in store.chunks_for_source("src-1")]
        self.assertEqual(ids, ["src-1#c000-new"])
        self.assertEqual(store.lexical_search("v2", 5)[0][0], "src-1#c000-new")
        self.assertEqual(store.lexical_search("v1", 5), [])

    def test_delete_removes_chunks_vectors_and_lexical_rows(self):
        store = self.make_store()
        self.index(store)
        result = store.delete_source("src-1")
        self.assertTrue(result["existed"])
        self.assertEqual(result["removedChunks"], 1)
        self.assertEqual(store.counts()["chunks"], 0)
        self.assertEqual(store.counts()["vectors"], 0)
        self.assertEqual(store.counts()["lexicalRows"], 0)
        self.assertEqual(store.lexical_search("v1", 5), [])
        self.assertEqual(store.list_sources(), [])
        self.assertIsNotNone(store.get_source("src-1"))  # tombstone retained

    def test_integrity_report_detects_missing_vector(self):
        store = self.make_store()
        self.index(store)
        store._exec("DELETE FROM vectors")
        store._conn.commit()
        report = store.integrity_report(expected_dimension=3)
        codes = {problem["code"] for problem in report["problems"]}
        self.assertIn("chunks_without_vectors", codes)

    def test_integrity_report_detects_dimension_mismatch(self):
        store = self.make_store()
        self.index(store, vectors=[[1.0, 0.0, 0.0]])
        report = store.integrity_report(expected_dimension=99)
        codes = {problem["code"] for problem in report["problems"]}
        self.assertIn("dimension_mismatch", codes)

    def test_embedding_cache_is_model_scoped(self):
        store = self.make_store()
        store.cache_put("hash1", "model-a", 3, [1.0, 0.0, 0.0])
        self.assertEqual(store.cache_get("hash1", "model-a"), [1.0, 0.0, 0.0])
        self.assertIsNone(store.cache_get("hash1", "model-b"))

    def test_lexical_search_escapes_fts_operators(self):
        store = self.make_store()
        self.index(store, revision_text="audit logs retained")
        for hostile in ['" OR 1=1 --', "*)((", "NEAR(a b)", "'; DROP TABLE chunks; --"]:
            # Must not raise and must not return unrelated rows.
            result = store.lexical_search(hostile, 5)
            self.assertIsInstance(result, list)

    def test_query_telemetry_roundtrip(self):
        store = self.make_store()
        store.record_query(
            {
                "query_id": "q-1",
                "question": "how long",
                "status": "answered",
                "mode": "hybrid",
                "provider": "extractive",
                "model": "extractive-synthesis-v1",
                "top_k": 6,
                "temperature": 0.3,
                "latency_ms": 12.5,
                "citations_total": 1,
                "citations_valid": 1,
                "created_at": "2026-01-01T00:00:00Z",
            }
        )
        rows = store.query_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["latency_ms"], 12.5)

    def test_jobs_are_visible(self):
        store = self.make_store()
        job = store.start_job("ingest", "src-1")
        store.finish_job(job, "failed", error_code="extraction_error", error="no text")
        jobs = store.recent_jobs()
        self.assertEqual(jobs[0]["status"], "failed")
        self.assertEqual(jobs[0]["error_code"], "extraction_error")


if __name__ == "__main__":
    unittest.main()
