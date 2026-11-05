"""Regression tests for the defects found during release qualification.

Each test names the observed failure it prevents, so a future refactor that
reintroduces the bug fails here with a readable reason instead of quietly
degrading an evaluation score.
"""

from __future__ import annotations

import json
import unittest

from helpers import TempDirTestCase, write

from ragformation.chunking import Chunker
from ragformation.config import Settings
from ragformation.engine import RAGEngine
from ragformation.evaluation_v2 import _canonical_tokens, _fact_present
from ragformation.generation import ExtractiveGenerator, _quotable_sentences
from ragformation.loaders import Block, LoadedSource, load_bytes
from ragformation.text import TextSignals, matched_terms, stem, tokenize


class TermMatchingTests(unittest.TestCase):
    def test_compound_query_matches_split_source_phrase(self):
        # d07: the question said "rollback", the document said "rolled back".
        self.assertIn("rollback", matched_terms({"rollback"}, ["rolled", "back", "within", "15", "minutes"]))

    def test_hyphenated_identifier_exposes_its_parts(self):
        # t09: "team" had to match the token "platform-team".
        signals = TextSignals(tokenize("ingest-api — owner: platform-team"))
        self.assertTrue(signals.matches("team"))
        self.assertTrue(signals.matches("api"))
        self.assertTrue(signals.matches("platform-team"))

    def test_trailing_e_stemming_joins_verb_forms(self):
        # t03: "restoring" had to match "restore".
        self.assertEqual(stem("restoring"), stem("restore"))
        self.assertEqual(stem("rehearsed"), stem("rehearse"))

    def test_unrelated_words_are_not_joined(self):
        # The split rule only fires when *both* halves match some passage token,
        # which random splits of unrelated words fail to do.
        self.assertNotIn("audit", matched_terms({"audit"}, ["author", "identity"]))
        # Both halves must exist in the passage, so a two-word compound that is
        # genuinely present still matches -- that is the point of the rule -- but
        # a compound of *unrelated* words does not.
        self.assertIn("rollbacks", matched_terms({"rollbacks"}, ["rolled", "back"]))
        self.assertNotIn("rollbacks", matched_terms({"rollbacks"}, ["rolled", "forward"]))


class JsonRenderingTests(unittest.TestCase):
    PAYLOAD = json.dumps(
        {
            "catalog": "demo",
            "services": [
                {
                    "name": "ingest-api",
                    "owner": "platform-team",
                    "slo": {"availability": "99.95%", "p95LatencyMs": 250},
                    "dependsOn": ["queue-broker", "customer-store"],
                }
            ],
        }
    )

    def test_structured_facts_keep_their_subject(self):
        # t08/t09: the old renderer emitted bare values with no key -> value binding.
        loaded = load_bytes("catalog.json", self.PAYLOAD.encode("utf-8"))
        text = "\n".join(block.text for block in loaded.blocks)
        self.assertIn("ingest-api", text)
        self.assertIn("owner: platform-team", text)
        self.assertIn("availability (slo): 99.95%", text)
        self.assertIn("dependsOn: queue-broker, customer-store", text)

    def test_each_record_gets_its_own_heading_for_citation(self):
        loaded = load_bytes("catalog.json", self.PAYLOAD.encode("utf-8"))
        headings = [block.text for block in loaded.blocks if block.kind == "heading"]
        self.assertTrue(any("ingest-api" in heading for heading in headings))


class ChunkingTests(unittest.TestCase):
    def test_overlap_never_crosses_a_heading(self):
        # A chunk used to inherit the previous section's tail, which let a
        # citation attribute one record's fields to another record.
        source = LoadedSource(
            title="doc",
            filename="doc.md",
            media_type="text/markdown",
            byte_size=10,
            content_hash="abc",
            blocks=[
                Block("heading", "One", level=1),
                Block("paragraph", "The first section explains the canary rollout in detail. " * 6),
                Block("heading", "Two", level=1),
                Block("paragraph", "The second section explains rollback limits and window values."),
            ],
        )
        settings = Settings.from_env()
        chunks = Chunker(settings).chunk(source, "src-test")
        second = [chunk for chunk in chunks if chunk.section == "Two"]
        self.assertTrue(second)
        self.assertNotIn("canary rollout", second[0].text)
        self.assertEqual(second[0].overlap_tokens, 0)


class ExtractiveSelectionTests(unittest.TestCase):
    def _answer(self, question: str, text: str) -> str:
        output = ExtractiveGenerator().generate(
            question,
            [{"id": 1, "text": text, "sourceId": "s", "sourceTitle": "t", "section": "s", "page": 1, "chunkId": "c"}],
            temperature=0.0,
            top_p=1.0,
            max_tokens=256,
            timeout=5.0,
        )
        return output.text

    def test_quantity_question_prefers_the_sentence_with_a_quantity(self):
        # t18: the definition sentence used to win on shared words alone.
        text = (
            "Production deploys use the blue/green model. "
            "A deploy is promoted only after health checks pass for 10 minutes and the error budget is positive."
        )
        answer = self._answer("When is a deploy promoted under the blue/green model?", text)
        self.assertIn("10 minutes", answer)

    def test_identifier_question_prefers_the_sentence_naming_it(self):
        text = (
            "MACRO-BILLING-01 — title: Duplicate charge investigation\n\n"
            "MACRO-BILLING-01 — steps: Confirm the duplicate authorisation, refund the second charge. "
            "Refunds settle within 5 business days."
        )
        answer = self._answer("How long do refunds take under MACRO-BILLING-01?", text)
        self.assertIn("5 business days", answer)

    def test_restatement_of_the_question_is_not_an_answer(self):
        text = (
            "A canary is aborted if the error rate exceeds 2 percent. "
            "Rollback requires no schema change and no data migration."
        )
        answer = self._answer("What is the rollback window for a release?", text)
        self.assertNotIn("schema change", answer)

    def test_record_lines_are_quoted_whole_with_their_label(self):
        # t08: sentence-splitting a record line used to strip the record identity.
        sentences = _quotable_sentences("ingest-api — availability (slo): 99.95%\n\nSome prose sentence that is long enough.")
        self.assertTrue(any(sentence.startswith("ingest-api — availability") for sentence in sentences))


class ConflictGateTests(TempDirTestCase):
    QUESTION = "How long is configuration state retained in backups?"

    def _corpus(self):
        self.docs = self.tmpdir / "docs"
        write(
            self.docs / "backup.md",
            "# Backup\n\n## Backup Schedule\n\nConfiguration and infrastructure state are backed up daily "
            "with a **35-day** retention window.\n",
        )
        write(
            self.docs / "access.md",
            "# Access\n\n## Access Reviews\n\nEvidence of each access review is retained for **3 years**.\n",
        )
        write(
            self.docs / "memo.md",
            "# Memo\n\n## Retention\n\nEvidence of each access review is retained for **5 years**.\n",
        )

    def test_unrelated_contradiction_is_not_surfaced(self):
        # t15: 3 years vs 5 years about access reviews is real, but it is not an
        # answer to a question about backup retention.
        self._corpus()
        engine = self.make_engine()
        engine.ingest_path([str(self.docs)])
        result = engine.ask(self.QUESTION, save=False)
        self.assertFalse(result.conflict, result.conflict)

    def test_contradiction_about_the_question_is_still_surfaced(self):
        self._corpus()
        engine = self.make_engine()
        engine.ingest_path([str(self.docs)])
        result = engine.ask("How long is evidence of access reviews retained?", save=False)
        self.assertTrue(result.conflict, "the real contradiction must still surface")
        self.assertEqual(result.status, "needs_review")


class FactMatcherTests(unittest.TestCase):
    def test_markdown_and_hyphenation_do_not_hide_a_fact(self):
        # t15: the answer said "**35-day**", the label said "35 days".
        self.assertTrue(_fact_present("35 days", "backed up daily with a **35-day** retention window"))
        self.assertTrue(_fact_present("35 days", "retention window of 35 days"))
        self.assertTrue(_fact_present("five business days", "published within five business days"))

    def test_facts_still_have_to_be_contiguous_and_ordered(self):
        self.assertFalse(_fact_present("15 minutes", "within 15 business days and 3 minutes"))
        self.assertFalse(_fact_present("400 days", "audit logs are retained"))

    def test_legacy_matcher_keeps_its_old_behaviour(self):
        self.assertFalse(_fact_present("35 days", "a **35-day** window", matcher="legacy"))
        self.assertTrue(_fact_present("35 days", "a window of 35 days", matcher="legacy"))

    def test_canonical_tokens_fold_plurals_only(self):
        self.assertEqual(_canonical_tokens("35-day"), ["35", "day"])
        self.assertEqual(_canonical_tokens("35 days"), ["35", "day"])
        self.assertEqual(_canonical_tokens("business"), ["business"])  # "ss" is not a plural


if __name__ == "__main__":
    unittest.main()
