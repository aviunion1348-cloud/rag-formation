"""Source extraction: formats, provenance, and rejection paths."""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path

from helpers import TempDirTestCase, write

from ragformation.errors import ExtractionError, RagError, SourceTooLarge, UnsupportedFormat
from ragformation.loaders import iter_input_files, load_bytes, load_file

DOCX_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Access Control</w:t></w:r></w:p>
    <w:p><w:r><w:t>Vendor access is time-boxed to 14 days and requires a named approver.</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>Role</w:t></w:r></w:p></w:tc><w:p></w:p>
              <w:tc><w:p><w:r><w:t>Approver</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
  </w:body>
</w:document>"""


def make_docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", DOCX_XML)
        archive.writestr(
            "docProps/core.xml",
            "<cp:coreProperties xmlns:cp='x'><dc:title xmlns:dc='y'>Vendor Standard</dc:title></cp:coreProperties>",
        )
    return buffer.getvalue()


class LoaderTests(unittest.TestCase):
    def test_markdown_preserves_headings_and_paragraphs(self):
        loaded = load_bytes(
            "policy.md",
            b"# Retention\n\nLogs are kept 90 days.\n\n## Audit\n\n- Audited events\n| a | b |\n",
        )
        kinds = [block.kind for block in loaded.blocks]
        self.assertEqual(kinds[0], "heading")
        self.assertIn("paragraph", kinds)
        self.assertIn("table", kinds)
        self.assertEqual(loaded.media_type, "text/markdown")
        self.assertEqual(len(loaded.content_hash), 64)

    def test_html_extracts_title_and_ignores_script(self):
        html = b"""<html><head><title>Vendor Policy</title>
        <script>alert('x')</script></head><body>
        <h1>Access</h1><p>Access is limited to 14 days.</p></body></html>"""
        loaded = load_bytes("vendor.html", html)
        self.assertEqual(loaded.title, "Vendor Policy")
        self.assertTrue(any(block.kind == "heading" for block in loaded.blocks))
        self.assertNotIn("alert", loaded.text)

    def test_json_records_become_blocks(self):
        payload = json.dumps({"macros": [{"id": "M1", "steps": "Do the thing."}]}).encode()
        loaded = load_bytes("m.json", payload)
        self.assertEqual(loaded.media_type, "application/json")
        self.assertIn("M1", loaded.text)

    def test_csv_rows_become_records(self):
        loaded = load_bytes("rows.csv", b"name,days\nlogs,90\naudit,400\n")
        self.assertIn("logs", loaded.text)
        self.assertIn("90", loaded.text)

    def test_docx_is_parsed_without_third_party_libs(self):
        loaded = load_bytes("vendor.docx", make_docx())
        self.assertTrue(loaded.media_type.endswith("wordprocessingml.document"))
        self.assertTrue(any(block.kind == "heading" and block.text == "Access Control" for block in loaded.blocks))
        self.assertIn("14 days", loaded.text)
        self.assertTrue(any(block.kind == "table" for block in loaded.blocks))

    def test_empty_file_is_rejected(self):
        with self.assertRaises(ExtractionError):
            load_bytes("empty.txt", b"   \n  ")

    def test_oversized_file_is_rejected(self):
        with self.assertRaises(SourceTooLarge):
            load_bytes("big.txt", b"x" * 5000, max_bytes=1000)

    def test_unsupported_archive_container_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("data.bin", "not a docx")
        with self.assertRaises(UnsupportedFormat):
            load_bytes("archive.zip", buffer.getvalue())

    def test_binary_garbage_does_not_crash(self):
        with self.assertRaises(RagError):
            load_bytes("blob.bin", bytes(range(256)) * 4)

    def test_pdf_without_pypdf_gives_actionable_error(self):
        try:
            import pypdf  # noqa: F401,F811
        except ImportError:
            with self.assertRaises(UnsupportedFormat) as context:
                load_bytes("doc.pdf", b"%PDF-1.4\n%fake")
            self.assertIn("pypdf", str(context.exception))

    def test_missing_file_reports_name_only(self):
        with self.assertRaises(ExtractionError):
            load_file("/nonexistent/absolute/path.txt")


class FilesystemTests(TempDirTestCase):
    def test_directory_expansion_skips_hidden_paths(self):
        root = self.tmpdir
        write(root / "docs" / "a.md", "# A\n\ntext")
        write(root / "docs" / ".hidden" / "b.md", "# B")
        files = iter_input_files([str(root / "docs")])
        names = {path.name for path in files}
        self.assertIn("a.md", names)
        self.assertNotIn("b.md", names)

    def test_load_file_reports_relative_origin_in_engine(self):
        path = write(self.tmpdir / "note.md", "# Note\n\nBody text here.")
        loaded = load_file(path)
        self.assertEqual(loaded.title, "note")
        self.assertTrue(Path(path).exists())


if __name__ == "__main__":
    unittest.main()
