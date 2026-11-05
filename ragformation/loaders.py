"""Source extraction: file bytes -> structured blocks with page/section provenance.

Supported without third-party dependencies
------------------------------------------
``.md`` / ``.markdown``, ``.txt``, ``.rst``, ``.html`` / ``.htm``,
``.json``, ``.csv``, ``.docx`` (a zip of XML — parsed with stdlib
``zipfile`` + ``ElementTree``).

Optional
--------
``.pdf`` text extraction when ``pypdf`` is installed. Scanned PDFs would need
OCR, which this project does **not** claim to support; instead the loader
returns a warning when a PDF page yields no text.

Every loader returns :class:`LoadedSource` carrying a content hash, byte size,
detected media type, the parser identity and structured warnings. Nothing here
follows links, executes macros or trusts filenames.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree

from .errors import ExtractionError, SourceTooLarge, UnsupportedFormat
from .text import normalize_text, sha256_hex, safe_display_name

PARSER_VERSION = "loaders-v1"
SUPPORTED_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".text",
    ".html",
    ".htm",
    ".json",
    ".csv",
    ".docx",
    ".pdf",
}

MEDIA_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".rst": "text/x-rst",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}


@dataclass
class Block:
    """One structural unit of a document."""

    kind: str  # heading | paragraph | list | table | code | record
    text: str
    page: int = 1
    level: int = 0  # heading depth, 0 for non-headings

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "page": self.page, "level": self.level}


@dataclass
class LoadedSource:
    title: str
    filename: str
    media_type: str
    byte_size: int
    content_hash: str
    blocks: List[Block]
    parser: str = PARSER_VERSION
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return max((b.page for b in self.blocks), default=0)

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "filename": self.filename,
            "mediaType": self.media_type,
            "byteSize": self.byte_size,
            "contentHash": self.content_hash,
            "parser": self.parser,
            "pageCount": self.page_count,
            "blockCount": len(self.blocks),
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- md
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")


def _markdown_blocks(text: str) -> List[Block]:
    blocks: List[Block] = []
    paragraph: List[str] = []
    code: List[str] = []
    in_code = False
    page = 1

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            joined = " ".join(line.strip() for line in paragraph).strip()
            if joined:
                blocks.append(Block("paragraph", joined, page))
            paragraph = []

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                blocks.append(Block("code", "\n".join(code).strip(), page))
                code, in_code = [], False
            else:
                flush_paragraph()
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        if not stripped:
            flush_paragraph()
            continue
        heading = _MD_HEADING.match(stripped)
        if heading:
            flush_paragraph()
            blocks.append(Block("heading", heading.group(2).strip(), page, len(heading.group(1))))
            continue
        if _MD_LIST.match(line):
            flush_paragraph()
            blocks.append(Block("list", stripped.lstrip("-*+ ").strip(), page))
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            blocks.append(Block("table", stripped.strip("|").replace("|", " | "), page))
            continue
        paragraph.append(line)
    if in_code and code:
        blocks.append(Block("code", "\n".join(code).strip(), page))
    flush_paragraph()
    return blocks


def _plain_text_blocks(text: str) -> List[Block]:
    blocks: List[Block] = []
    page = 1
    for chunk in re.split(r"\n\s*\n", text):
        cleaned = " ".join(line.strip() for line in chunk.split("\n") if line.strip())
        if not cleaned:
            continue
        kind = "list" if _MD_LIST.match(" " + cleaned[:2]) or cleaned[:2].startswith(("- ", "* ")) else "paragraph"
        blocks.append(Block(kind, cleaned, page))
        page += 1
    return blocks


# -------------------------------------------------------------------------- html
class _HTMLBlocks(HTMLParser):
    BLOCK_TAGS = {"p", "div", "section", "article", "li", "td", "th", "blockquote", "pre"}
    SKIP_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[Block] = []
        self.title = ""
        self._buffer: List[str] = []
        self._mode: Optional[str] = None
        self._heading_level = 0
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK_TAGS:
            self._flush()
            self._mode = "table" if tag in {"td", "th"} else "paragraph"
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._mode = "heading"
            self._heading_level = int(tag[1])

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK_TAGS or re.fullmatch(r"h[1-6]", tag):
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data.strip()
            return
        self._buffer.append(data)

    def _flush(self) -> None:
        text = " ".join(" ".join(self._buffer).split())
        if self._mode and text:
            kind = "heading" if self._mode == "heading" else self._mode
            level = self._heading_level if kind == "heading" else 0
            self.blocks.append(Block(kind, text, 1, level))
        self._buffer = []
        self._mode = None


def _html_blocks(raw: str) -> Tuple[List[Block], str]:
    parser = _HTMLBlocks()
    parser.feed(raw)
    parser.close()
    parser._flush()
    return parser.blocks, parser.title


# ------------------------------------------------------------------------- docx
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_blocks(data: bytes, media_type: str) -> Tuple[List[Block], str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
            core_title = ""
            if "docProps/core.xml" in archive.namelist():
                try:
                    core = ElementTree.fromstring(archive.read("docProps/core.xml"))
                    for node in core.iter():
                        if node.tag.endswith("}title") and node.text:
                            core_title = node.text.strip()
                except ElementTree.ParseError:
                    core_title = ""
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ExtractionError("not a readable .docx file", hint="the file is not a valid OOXML zip") from exc

    root = ElementTree.fromstring(xml)
    blocks: List[Block] = []
    for para in root.iter(f"{_W}p"):
        style = ""
        for node in para.iter(f"{_W}pStyle"):
            style = node.get(f"{_W}val", "")
        text = "".join(node.text or "" for node in para.iter(f"{_W}t")).strip()
        if not text:
            continue
        if style.lower().startswith("heading") or style.lower() == "title":
            level = 1
            digits = re.findall(r"\d", style)
            if digits:
                level = max(1, min(6, int(digits[0])))
            blocks.append(Block("heading", text, 1, level))
        elif style.lower().startswith("list"):
            blocks.append(Block("list", text, 1))
        else:
            blocks.append(Block("paragraph", text, 1))
    for table in root.iter(f"{_W}tbl"):
        rows: List[str] = []
        for row in table.iter(f"{_W}tr"):
            cells = [
                " ".join("".join(t.text or "" for t in cell.iter(f"{_W}t")).split())
                for cell in row.iter(f"{_W}tc")
            ]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            blocks.append(Block("table", "; ".join(rows), 1))
    return blocks, core_title


# -------------------------------------------------------------------------- pdf
def _pdf_blocks(data: bytes, max_bytes: int) -> Tuple[List[Block], List[str]]:
    warnings: List[str] = []
    try:
        import pypdf  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise UnsupportedFormat(
            "PDF support needs the optional 'pypdf' dependency",
            hint="pip install pypdf   (or convert the PDF to text/markdown)",
        ) from exc
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception as exc:  # pragma: no cover - library-specific
        raise ExtractionError("PDF could not be parsed", hint=str(exc)[:160]) from exc
    if getattr(reader, "is_encrypted", False):
        raise ExtractionError(
            "encrypted PDFs are not supported",
            hint="remove the password protection before ingesting",
        )
    blocks: List[Block] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # pragma: no cover
            text = ""
            warnings.append(f"page {index}: text extraction failed")
        normalized = normalize_text(text)
        if not normalized:
            warnings.append(f"page {index}: no extractable text (scanned or image-only page)")
            continue
        for para in re.split(r"\n\s*\n", normalized):
            cleaned = " ".join(para.split())
            if cleaned:
                blocks.append(Block("paragraph", cleaned, index))
    if not blocks:
        warnings.append("no text extracted; OCR is not supported by this build")
    return blocks, warnings


# ---------------------------------------------------------------- json / csv
#: Field names that identify the thing a record is about. Evidence rendered
#: without them is unattributable ("99.95%" alone cannot be cited as *the
#: ingest-api availability SLO*), which is why they are repeated on every line.
_RECORD_LABEL_KEYS = ("id", "name", "title", "slug", "key", "service", "macro")


def _display_field(path: str) -> str:
    """Readable form of a flattened JSON path.

    The dotted form (``slo.availability``) is one opaque token to a word-level
    tokeniser, so a question that says "availability SLO" could never match it.
    Depth-two paths are therefore rendered as ``availability (slo)``, which
    keeps both words matchable while staying attributable; deeper paths keep the
    dotted form because rewriting them buys nothing.
    """
    parts = [part for part in path.split(".") if part]
    if len(parts) == 2:
        return f"{parts[1]} ({parts[0]})"
    return path


def _json_blocks(raw: str) -> List[Block]:
    """Render JSON as attributable, quotable evidence.

    A JSON document is a set of records, not a run of loose scalars. The old
    renderer walked nested objects key-by-key and emitted bare values
    (``"ingest-api"``, ``1``, ``"platform-team"``), which lost the key -> value
    mapping: retrieval could match a term but no answer could bind the value to
    its subject, and citations pointed at a chunk whose text no longer contained
    the relationship. This renderer:

    * emits one heading block per record, so each service or macro gets its own
      chunk and a stable section label for citations;
    * flattens nested objects into dotted field names
      (``slo.availability``) and joins scalar lists;
    * repeats the record label on every field line, so a quoted line such as
      ``ingest-api — slo.availability: 99.95%`` is self-describing evidence.
    """

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionError("invalid JSON", hint=f"line {exc.lineno}: {exc.msg}") from exc

    blocks: List[Block] = []

    def is_scalar(node: Any) -> bool:
        return isinstance(node, (str, int, float, bool)) or node is None

    def render_scalar(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def label_for(node: Dict[str, Any], fallback: str) -> str:
        for key in _RECORD_LABEL_KEYS:
            value = node.get(key)
            if is_scalar(value) and value not in (None, ""):
                return str(value)
        return fallback

    def emit_record(node: Dict[str, Any], path: str) -> None:
        label = label_for(node, path or "record")
        fields: List[tuple] = []

        def flatten(prefix: str, value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    flatten(f"{prefix}.{key}" if prefix else str(key), nested)
            elif isinstance(value, list):
                if all(is_scalar(item) for item in value):
                    joined = ", ".join(render_scalar(item) for item in value)
                    fields.append((prefix, joined))
                else:
                    for index, item in enumerate(value):
                        emit_any(item, f"{prefix}[{index}]")
            else:
                fields.append((prefix, render_scalar(value)))

        for key, value in node.items():
            flatten(str(key), value)

        blocks.append(Block("heading", f"{path or 'record'}: {label}", level=1))
        for key, value in fields:
            if not key:
                blocks.append(Block("paragraph", value))
                continue
            blocks.append(Block("record", f"{label} — {_display_field(key)}: {value}"))

    def emit_any(node: Any, path: str) -> None:
        if isinstance(node, dict):
            emit_record(node, path)
        elif isinstance(node, list):
            if all(is_scalar(item) for item in node):
                blocks.append(Block("record", f"{path}: " + ", ".join(render_scalar(i) for i in node)))
            else:
                for index, item in enumerate(node):
                    emit_any(item, f"{path}[{index}]")
        else:
            blocks.append(Block("paragraph", render_scalar(node)))

    if isinstance(payload, dict):
        # Top-level scalars are document metadata, not a record of their own.
        metadata = {k: v for k, v in payload.items() if is_scalar(v)}
        for key, value in metadata.items():
            blocks.append(Block("paragraph", f"{key}: {render_scalar(value)}"))
        for key, value in payload.items():
            if is_scalar(value):
                continue
            if isinstance(value, list):
                for index, item in enumerate(value):
                    emit_any(item, str(key))
            else:
                emit_any(value, str(key))
    else:
        emit_any(payload, "record")
    return blocks


def _csv_blocks(raw: str) -> List[Block]:
    reader = csv.reader(io.StringIO(raw))
    rows = [row for row in reader]
    if not rows:
        return []
    header = rows[0]

    def render(row: List[str]) -> str:
        return " | ".join(
            f"{header[i] if i < len(header) else 'column'}: {value}" for i, value in enumerate(row)
        )

    blocks = [Block("table", " | ".join(header))]
    for row in rows[1:]:
        if any(cell.strip() for cell in row):
            blocks.append(Block("record", render(row)))
    return blocks


# --------------------------------------------------------------------- facade
def detect_media_type(path: Path, data: bytes) -> str:
    """Content wins over extension. Extension is a hint, never the authority."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except zipfile.BadZipFile:
            pass
        return "application/zip"
    head = data[:1024].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<body" in head[:512]:
        return "text/html"
    if data[:1] in {b"{", b"["}:
        return "application/json"
    return MEDIA_TYPES.get(path.suffix.lower(), "text/plain")


def load_bytes(
    filename: str,
    data: bytes,
    *,
    max_bytes: int = 8 * 1024 * 1024,
    title: Optional[str] = None,
) -> LoadedSource:
    """Turn raw bytes into a :class:`LoadedSource`. Never touches the filesystem."""
    if not data or not data.strip():
        raise ExtractionError("source is empty", hint="nothing to index")
    if len(data) > max_bytes:
        raise SourceTooLarge(
            f"source is larger than the {max_bytes // 1024} KiB limit",
            hint="split the document or raise RAG_MAX_SOURCE_BYTES",
        )
    safe_name = safe_display_name(filename)
    suffix = Path(safe_name).suffix.lower()
    media_type = detect_media_type(Path(safe_name), data)

    # Refuse an extension/content mismatch loudly instead of mis-parsing.
    if media_type == "application/zip":
        raise UnsupportedFormat(
            "unsupported binary container (zip/docx expected)",
            hint=f"detected {media_type} for {safe_name}",
        )

    warnings: List[str] = []
    blocks: List[Block] = []
    detected_title = ""

    try:
        if media_type == "application/pdf":
            blocks, warnings = _pdf_blocks(data, max_bytes)
        elif media_type.endswith("wordprocessingml.document"):
            blocks, detected_title = _docx_blocks(data, media_type)
        elif media_type == "text/html":
            raw = data.decode("utf-8", errors="replace")
            blocks, detected_title = _html_blocks(raw)
        elif media_type == "application/json":
            blocks = _json_blocks(data.decode("utf-8", errors="replace"))
        elif media_type == "text/csv":
            blocks = _csv_blocks(data.decode("utf-8", errors="replace"))
        else:
            if suffix not in SUPPORTED_EXTENSIONS and media_type == "text/plain":
                warnings.append(f"extension {suffix or '(none)'} treated as plain text")
            decoded = data.decode("utf-8", errors="replace")
            # A binary file masquerading as text decodes into replacement
            # characters or NULs. Refuse it instead of indexing mojibake.
            if decoded.count("\ufffd") > max(8, len(decoded) * 0.02) or "\x00" in decoded:
                raise UnsupportedFormat(
                    "file does not look like text",
                    hint="binary content detected; supported formats are md, txt, rst, html, json, csv, docx, pdf",
                )
            raw = normalize_text(decoded)
            is_markdown = suffix in {".md", ".markdown"} or media_type == "text/markdown"
            blocks = _markdown_blocks(raw) if is_markdown else _plain_text_blocks(raw)
    except UnicodeDecodeError as exc:
        raise ExtractionError("source is not valid UTF-8 text", hint=str(exc)[:160]) from exc

    cleaned_blocks: List[Block] = []
    for block in blocks:
        text = normalize_text(block.text) if block.kind != "code" else block.text.strip()
        if text:
            cleaned_blocks.append(Block(block.kind, text, block.page, block.level))

    if not cleaned_blocks:
        raise ExtractionError(
            "no extractable text found",
            hint="the document may be empty, image-only, or use an unsupported encoding",
        )

    if not any(b.kind == "heading" for b in cleaned_blocks):
        warnings.append("no headings detected; chunks will follow paragraph boundaries")

    return LoadedSource(
        title=(title or detected_title or Path(safe_name).stem).strip()[:200],
        filename=safe_name,
        media_type=media_type,
        byte_size=len(data),
        content_hash=sha256_hex(data),
        blocks=cleaned_blocks,
        warnings=warnings,
        meta={"suffix": suffix},
    )


def load_file(path: str | Path, *, max_bytes: int = 8 * 1024 * 1024) -> LoadedSource:
    target = Path(path)
    if not target.exists():
        raise ExtractionError("file not found", hint=str(target.name))
    if not target.is_file():
        raise ExtractionError("not a regular file", hint=str(target.name))
    size = target.stat().st_size
    if size > max_bytes:
        raise SourceTooLarge(
            f"{target.name} is larger than the {max_bytes // 1024} KiB limit",
            hint="split the document or raise RAG_MAX_SOURCE_BYTES",
        )
    return load_bytes(target.name, target.read_bytes(), max_bytes=max_bytes)


def iter_input_files(paths: Iterable[str]) -> List[Path]:
    """Expand files and directories (sorted, non-recursive-then-recursive)."""
    found: List[Path] = []
    for raw in paths:
        target = Path(raw)
        if target.is_dir():
            found.extend(
                sorted(
                    p
                    for p in target.rglob("*")
                    if p.is_file()
                    and not any(part.startswith(".") for part in p.relative_to(target).parts)
                )
            )
        else:
            found.append(target)
    return found
