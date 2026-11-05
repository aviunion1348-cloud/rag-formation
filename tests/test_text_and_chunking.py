"""Text primitives and heading-aware chunking."""

from __future__ import annotations

import unittest

import helpers  # noqa: F401  (ensures sys.path setup)

from ragformation.chunking import Chunker, make_chunk_id, preview_segmentation
from ragformation.config import Settings
from ragformation.loaders import Block, LoadedSource
from ragformation.text import (
    approx_tokens,
    matched_terms,
    normalize_text,
    safe_display_name,
    split_sentences,
    stem,
    term_matches,
    tokenize,
)


def source(blocks, title="Doc"):
    return LoadedSource(
        title=title,
        filename="doc.md",
        media_type="text/markdown",
        byte_size=100,
        content_hash="hash",
        blocks=blocks,
    )


class TextTests(unittest.TestCase):
    def test_normalisation_is_conservative(self):
        raw = "Line one  \r\n\r\n\r\nLine   two\u00a0with nbsp"
        self.assertEqual(normalize_text(raw), "Line one\n\nLine   two with nbsp")

    def test_tokenizer_keeps_identifiers(self):
        tokens = tokenize("Set OLLAMA_HOST=http://x:11434 and p95 latency to 12.5%")
        self.assertIn("p95", tokens)
        self.assertIn("12.5%", tokens)
        self.assertIn("ollama_host", tokens)

    def test_stemming_and_prefix_matching(self):
        self.assertEqual(stem("deletion"), stem("deleted"))
        self.assertEqual(stem("backups"), stem("backup"))
        self.assertTrue(term_matches("deploy", "deployment"))
        self.assertFalse(term_matches("log", "login"))

    def test_matched_terms_handles_split_compounds(self):
        matched = matched_terms({"roll", "back"}, {"rollback", "must", "possible"})
        self.assertEqual(matched, {"roll", "back"})

    def test_sentence_split_keeps_citation_markers_with_text(self):
        sentences = split_sentences("Logs are kept 90 days. [1] Next sentence here.")
        self.assertIn("Logs are kept 90 days.", sentences)

    def test_safe_display_name_strips_paths(self):
        self.assertEqual(safe_display_name("../../etc/passwd"), "passwd")
        self.assertEqual(safe_display_name("   "), "untitled")

    def test_approx_tokens_is_positive(self):
        self.assertGreaterEqual(approx_tokens(""), 1)
        self.assertGreater(approx_tokens("word " * 100), 50)


class ChunkingTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(chunk_target_tokens=60, chunk_max_tokens=100, chunk_overlap_tokens=15)
        self.chunker = Chunker(self.settings)

    def test_heading_starts_new_chunk_and_sets_section_path(self):
        doc = source(
            [
                Block("heading", "1. Scope", 1, 1),
                Block("paragraph", "A scope paragraph with enough words to matter here.", 1),
                Block("heading", "1.1 Details", 2, 2),
                Block("paragraph", "Detail paragraphs explain the finer points of the policy.", 2),
            ]
        )
        chunks = self.chunker.chunk(doc, "src-1")
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(chunks[0].section_path, ["1. Scope"])
        self.assertTrue(any(chunk.section_path == ["1. Scope", "1.1 Details"] for chunk in chunks))
        self.assertEqual(chunks[0].page_start, 1)

    def test_chunks_carry_stable_ids_and_hashes(self):
        doc = source([Block("paragraph", "One two three four five six seven eight nine ten.", 1)])
        first = self.chunker.chunk(doc, "src-9")
        second = Chunker(self.settings).chunk(doc, "src-9")
        self.assertEqual([c.chunk_id for c in first], [c.chunk_id for c in second])
        self.assertEqual(first[0].chunk_id, make_chunk_id("src-9", 0, first[0].text))
        self.assertEqual(len(first[0].content_hash), 64)

    def test_long_paragraph_is_split_with_overlap(self):
        body = ". ".join(f"Sentence number {i} talks about retention rules" for i in range(20)) + "."
        chunks = self.chunker.chunk(source([Block("paragraph", body, 1)]), "src-2")
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.token_count <= self.settings.chunk_max_tokens + 20 for chunk in chunks))
        self.assertTrue(any(chunk.overlap_tokens > 0 for chunk in chunks[1:]))

    def test_table_and_code_blocks_are_preserved(self):
        doc = source(
            [
                Block("heading", "Table", 1, 1),
                Block("table", "Severity | Paging target. SEV-1 | incident commander.", 1),
                Block("code", "def deploy():\n    return True", 1),
            ]
        )
        text = " ".join(chunk.text for chunk in self.chunker.chunk(doc, "src-3"))
        self.assertIn("incident commander", text)
        self.assertIn("def deploy", text)

    def test_preview_segmentation_reports_tradeoffs(self):
        doc = source([Block("paragraph", " ".join(f"Sentence {i} about retention rules." for i in range(60)), 1)])
        preview = preview_segmentation(doc, self.settings, sizes=(40, 120))
        self.assertEqual(len(preview), 2)
        self.assertGreater(preview[0]["chunkCount"], preview[1]["chunkCount"])


if __name__ == "__main__":
    unittest.main()
