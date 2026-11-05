"""Chunking with structure awareness and explicit overlap.

Rules (deterministic, inspectable):

1. A heading always starts a new chunk and is carried into it as context.
2. Blocks are appended to the current chunk until the token budget is hit.
3. Overlap repeats the tail tokens of the previous chunk so a sentence split
   across a boundary stays retrievable from both sides.
4. Every chunk keeps source id, revision, page range and section path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Sequence

from .ingest import Document

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-_./%]*")


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) — labelled as an estimate in UI."""
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 96
    overlap_tokens: int = 20
    max_tokens: int = 160

    def validate(self) -> None:
        if self.target_tokens < 24:
            raise ValueError("target_tokens too small (loses context)")
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be < target_tokens")
        if self.max_tokens < self.target_tokens:
            raise ValueError("max_tokens must be >= target_tokens")


@dataclass
class Chunk:
    chunk_id: str
    source_id: str
    source_title: str
    revision: str
    ordinal: int
    text: str
    section: str
    page_start: int
    page_end: int
    token_count: int
    overlap_tokens: int = 0
    kinds: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def chunk_document(doc: Document, config: Optional[ChunkingConfig] = None) -> List[Chunk]:
    cfg = config or ChunkingConfig()
    cfg.validate()

    chunks: List[Chunk] = []
    buf: List[str] = []
    buf_tokens = 0
    section = doc.title
    pages: List[int] = []
    kinds: List[str] = []
    ordinal = 0
    carry: str = ""

    def flush(next_hint: str = "") -> None:
        nonlocal buf, buf_tokens, pages, kinds, ordinal, carry, section
        if not buf:
            return
        body = " ".join(b if b[-1] in ".!?" else b + "." for b in buf).strip()
        text = (carry + " " + body).strip() if carry else body
        cid = f"{doc.source_id}:{doc.revision}:c{ordinal:03d}"
        chunks.append(
            Chunk(
                chunk_id=cid,
                source_id=doc.source_id,
                source_title=doc.title,
                revision=doc.revision,
                ordinal=ordinal,
                text=text,
                section=section,
                page_start=min(pages) if pages else 1,
                page_end=max(pages) if pages else 1,
                token_count=approx_tokens(text),
                overlap_tokens=approx_tokens(carry) if carry else 0,
                kinds=sorted(set(kinds)),
            )
        )
        ordinal += 1
        # Build overlap carry from the tail of what we just emitted.
        words = body.split()
        overlap_words = max(1, int(cfg.overlap_tokens * 0.75)) if cfg.overlap_tokens else 0
        carry = " ".join(words[-overlap_words:]) if overlap_words else ""
        buf = []
        buf_tokens = 0
        pages = []
        kinds = []
        section = next_hint or section

    for block in doc.blocks:
        if block.kind == "heading":
            flush(next_hint=block.text)
            section = block.text
            buf.append(block.text)
            kinds.append("heading")
            pages.append(block.page)
            buf_tokens += approx_tokens(block.text)
            continue

        block_tokens = approx_tokens(block.text)
        if buf and buf_tokens + block_tokens > cfg.max_tokens:
            flush()
        # Oversized single block: split on sentence boundaries.
        if block_tokens > cfg.max_tokens:
            sentences = re.split(r"(?<=[.!?])\s+", block.text)
            for sentence in sentences:
                st = approx_tokens(sentence)
                if buf and buf_tokens + st > cfg.target_tokens:
                    flush()
                buf.append(sentence)
                kinds.append(block.kind)
                pages.append(block.page)
                buf_tokens += st
            continue

        buf.append(block.text)
        kinds.append(block.kind)
        pages.append(block.page)
        buf_tokens += block_tokens
        if buf_tokens >= cfg.target_tokens:
            flush()

    flush()
    return chunks
