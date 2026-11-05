"""Source ingestion: documents, provenance and a small registry.

The corpus in ``fixtures/corpus.json`` is synthetic and clearly labelled as
such. Provenance (source id, revision, page, section) survives every later
stage so citations can point back at a real location.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Block:
    """A structural unit of a document (heading, paragraph, table, note)."""

    kind: str  # "heading" | "paragraph" | "list" | "table" | "note"
    text: str
    page: int


@dataclass
class Document:
    source_id: str
    title: str
    revision: str
    origin: str
    media_type: str
    blocks: List[Block] = field(default_factory=list)
    #: True while the source is included in the eligible retrieval set.
    active: bool = True

    @property
    def checksum(self) -> str:
        h = hashlib.sha256()
        for b in self.blocks:
            h.update(b.text.encode("utf-8"))
        return h.hexdigest()[:16]

    @property
    def byte_size(self) -> int:
        return sum(len(b.text.encode("utf-8")) for b in self.blocks)

    def full_text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)

    def to_meta(self) -> Dict[str, object]:
        return {
            "sourceId": self.source_id,
            "title": self.title,
            "revision": self.revision,
            "origin": self.origin,
            "mediaType": self.media_type,
            "checksum": self.checksum,
            "byteSize": self.byte_size,
            "pageCount": max((b.page for b in self.blocks), default=0),
            "blockCount": len(self.blocks),
            "active": self.active,
        }


class SourceRegistry:
    """In-memory catalogue of sources, with revision + delete lifecycle."""

    def __init__(self, documents: Optional[List[Document]] = None) -> None:
        self._docs: Dict[str, Document] = {}
        for d in documents or []:
            self.register(d)

    def register(self, doc: Document) -> None:
        self._docs[doc.source_id] = doc

    def upsert_revision(self, doc: Document) -> str:
        """Replace a source with a new revision. Returns previous revision."""
        prev = self._docs.get(doc.source_id)
        self._docs[doc.source_id] = doc
        return prev.revision if prev else ""

    def delete(self, source_id: str) -> bool:
        return self._docs.pop(source_id, None) is not None

    def get(self, source_id: str) -> Document:
        return self._docs[source_id]

    def all(self) -> List[Document]:
        return sorted(self._docs.values(), key=lambda d: d.source_id)

    def active(self) -> List[Document]:
        return [d for d in self.all() if d.active]

    def __len__(self) -> int:
        return len(self._docs)

    def to_meta(self) -> List[Dict[str, object]]:
        return [d.to_meta() for d in self.all()]


DEFAULT_CORPUS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "corpus.json"
)


def load_corpus(path: Optional[str] = None) -> SourceRegistry:
    """Load the deterministic demo corpus (synthetic fixtures)."""
    target = path or DEFAULT_CORPUS
    with open(target, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    docs = []
    for item in raw["documents"]:
        blocks = [Block(kind=b["kind"], text=b["text"], page=b["page"]) for b in item["blocks"]]
        docs.append(
            Document(
                source_id=item["sourceId"],
                title=item["title"],
                revision=item.get("revision", "r1"),
                origin=item.get("origin", "fixtures/corpus.json"),
                media_type=item.get("mediaType", "text/markdown"),
                blocks=blocks,
                active=item.get("active", True),
            )
        )
    return SourceRegistry(docs)


def document_to_dict(doc: Document) -> Dict[str, object]:
    data = doc.to_meta()
    data["blocks"] = [asdict(b) for b in doc.blocks]
    return data
