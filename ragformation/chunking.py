"""Heading-aware chunking.

The whiteboard says "chunk by heading/topic", and that is exactly what this
does: a heading always opens a new chunk, its text is carried inside the chunk
(so lexical search can match the section name), and the section path
(``["2. Escalation", "2.3 Paging"]``) is stored as metadata for citations.

Overlap is a *measured* sentence tail from the previous chunk, recorded as
``overlapTokens`` so a reviewer can audit it rather than take it on faith.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .config import Settings
from .loaders import Block, LoadedSource
from .text import approx_tokens, normalize_text, split_sentences, text_hash


@dataclass
class Chunk:
    chunk_id: str
    source_id: str
    ordinal: int
    text: str
    token_count: int
    section: str
    section_path: List[str]
    page_start: int
    page_end: int
    content_hash: str
    overlap_tokens: int = 0
    kinds: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "sourceId": self.source_id,
            "ordinal": self.ordinal,
            "text": self.text,
            "tokenCount": self.token_count,
            "section": self.section,
            "sectionPath": self.section_path,
            "pageStart": self.page_start,
            "pageEnd": self.page_end,
            "contentHash": self.content_hash,
            "overlapTokens": self.overlap_tokens,
            "kinds": self.kinds,
        }


def make_chunk_id(source_id: str, ordinal: int, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{source_id}#c{ordinal:03d}-{digest}"


class Chunker:
    def __init__(self, settings: Settings) -> None:
        self.target = settings.chunk_target_tokens
        self.maximum = settings.chunk_max_tokens
        self.overlap = settings.chunk_overlap_tokens

    def chunk(self, source: LoadedSource, source_id: str) -> List[Chunk]:
        chunks: List[Chunk] = []
        stack: List[str] = []  # heading stack
        buffer: List[Block] = []
        buffer_tokens = 0
        carry = ""
        ordinal = 0

        def current_section() -> List[str]:
            return [part for part in stack if part]

        def flush(at_heading: bool = False) -> None:
            nonlocal buffer, buffer_tokens, carry, ordinal
            if not buffer:
                return
            body = _render(buffer)
            text = normalize_text(f"{carry}\n{body}" if carry else body)
            section_path = current_section()
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(source_id, ordinal, text),
                    source_id=source_id,
                    ordinal=ordinal,
                    text=text,
                    token_count=approx_tokens(text),
                    section=section_path[-1] if section_path else source.title,
                    section_path=section_path,
                    page_start=min(b.page for b in buffer),
                    page_end=max(b.page for b in buffer),
                    content_hash=text_hash(text),
                    overlap_tokens=approx_tokens(carry) if carry else 0,
                    kinds=sorted({b.kind for b in buffer}),
                )
            )
            ordinal += 1
            # Overlap exists to keep a *split section* readable, never to drag
            # text across a heading. Carrying a tail across a boundary put the
            # previous record's fields inside the next record's chunk, so a
            # citation could attribute one service's owner to another service.
            carry = _tail(body, self.overlap) if (self.overlap and not at_heading) else ""
            buffer = []
            buffer_tokens = 0

        for block in source.blocks:
            if block.kind == "heading":
                flush(at_heading=True)
                # Maintain the heading hierarchy: deeper headings replace the tail.
                level = block.level or len(stack) + 1
                stack = stack[: max(0, level - 1)]
                stack.append(block.text)
                buffer.append(block)
                buffer_tokens += approx_tokens(block.text)
                if buffer_tokens >= self.target:
                    flush()
                continue

            block_tokens = approx_tokens(block.text)
            if buffer and buffer_tokens + block_tokens > self.maximum:
                flush()

            if block_tokens > self.maximum:
                # Split an oversized block on sentence boundaries, never mid-word.
                for sentence in split_sentences(block.text):
                    piece = Block(block.kind, sentence, block.page, block.level)
                    piece_tokens = approx_tokens(sentence)
                    if buffer and buffer_tokens + piece_tokens > self.target:
                        flush()
                    buffer.append(piece)
                    buffer_tokens += piece_tokens
                    if buffer_tokens >= self.target:
                        flush()
                continue

            buffer.append(block)
            buffer_tokens += block_tokens
            if buffer_tokens >= self.target:
                flush()

        flush()

        # Merge a tiny trailing chunk into its predecessor when they share a
        # section: a 20-token fragment retrieves badly and cites even worse.
        if len(chunks) >= 2:
            last = chunks[-1]
            previous = chunks[-2]
            if (
                last.token_count < max(24, self.target // 4)
                and previous.section_path == last.section_path
                and previous.token_count + last.token_count <= self.maximum
            ):
                merged_text = normalize_text(previous.text + "\n" + last.text)
                chunks[-2] = Chunk(
                    chunk_id=make_chunk_id(source_id, previous.ordinal, merged_text),
                    source_id=source_id,
                    ordinal=previous.ordinal,
                    text=merged_text,
                    token_count=approx_tokens(merged_text),
                    section=previous.section,
                    section_path=previous.section_path,
                    page_start=min(previous.page_start, last.page_start),
                    page_end=max(previous.page_end, last.page_end),
                    content_hash=text_hash(merged_text),
                    overlap_tokens=previous.overlap_tokens,
                    kinds=sorted(set(previous.kinds) | set(last.kinds)),
                )
                chunks.pop()
        return chunks


def _render(blocks: Sequence[Block]) -> str:
    parts: List[str] = []
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if block.kind == "heading":
            parts.append(text)
        elif block.kind in {"list", "record"}:
            parts.append(f"- {text}" if not text.startswith(("-", "*")) else text)
        elif block.kind == "table":
            parts.append(text)
        elif block.kind == "code":
            parts.append(text)
        else:
            parts.append(text)
    return "\n\n".join(parts)


def _tail(text: str, tokens: int) -> str:
    """Last ~``tokens`` tokens of ``text``, snapped to sentence boundaries."""
    if tokens <= 0:
        return ""
    sentences = split_sentences(text)
    if not sentences:
        return ""
    picked: List[str] = []
    budget = 0
    for sentence in reversed(sentences):
        size = approx_tokens(sentence)
        if picked and budget + size > tokens:
            break
        picked.append(sentence)
        budget += size
        if budget >= tokens:
            break
    tail = " ".join(reversed(picked)).strip()
    # A single monstrous sentence is not useful overlap context.
    return tail if approx_tokens(tail) <= tokens * 2 else ""


def preview_segmentation(source: LoadedSource, settings: Settings, sizes: Sequence[int] = (80, 180, 400)) -> List[Dict[str, Any]]:
    """Explain chunking trade-offs without touching the index (used by the UI)."""
    from dataclasses import replace

    output: List[Dict[str, Any]] = []
    for size in sizes:
        probe = replace(
            settings,
            chunk_target_tokens=size,
            chunk_max_tokens=max(size, int(size * 1.6)),
            chunk_overlap_tokens=min(settings.chunk_overlap_tokens, max(0, size // 5)),
        )
        chunks = Chunker(probe).chunk(source, "preview")
        output.append(
            {
                "targetTokens": size,
                "chunkCount": len(chunks),
                "averageTokens": round(
                    sum(c.token_count for c in chunks) / max(1, len(chunks)), 1
                ),
                "sample": [c.text[:220] for c in chunks[:2]],
            }
        )
    return output


def split_offline_sentences(text: str) -> List[str]:
    return split_sentences(text)

