"""Typed public contracts returned by :class:`ragformation.RAGEngine`.

These objects are the *public* API. They never expose SQLite rows or provider
response bodies, they are safe to ``json.dumps`` (every one has ``to_dict``), and
they are stable across minor versions.

Backward compatibility: the mapping types subclass ``dict``, so existing code
that reads ``report["indexed"]`` or ``health["ok"]`` keeps working while new code
can write ``report.indexed`` / ``health.ok``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .config import Settings


# --------------------------------------------------------------------------- config
class RAGConfig(Settings):
    """Public name for :class:`Settings` (same frozen dataclass, same fields)."""

    @classmethod
    def from_env_or_mapping(cls, mapping: Optional[Dict[str, Any]] = None, **overrides: Any) -> "RAGConfig":
        """Build a validated config from ``RAG_*`` env plus an optional mapping.

        Mapping keys may be dataclass field names (``data_dir``) or environment
        names (``RAG_DATA_DIR``). Mapping values win over the environment, and
        explicit keyword overrides win over both.
        """

        base = Settings.from_env()
        updates: Dict[str, Any] = {}
        for key, value in (mapping or {}).items():
            name = key.lower()
            if name.startswith("rag_"):
                name = name[4:]
            if name not in Settings.__dataclass_fields__:
                raise ValueError(f"unknown configuration key {key!r}")
            updates[name] = value
        updates.update(overrides)
        if "data_dir" in updates:
            from pathlib import Path

            updates["data_dir"] = Path(updates["data_dir"])
        if updates:
            from dataclasses import replace

            base = replace(base, **updates)
        base.validate()
        return cls(**{f: getattr(base, f) for f in Settings.__dataclass_fields__})

    def to_public_dict(self) -> Dict[str, Any]:
        return super().to_public_dict()


# -------------------------------------------------------------------------- results
@dataclass
class Citation:
    """A validated pointer from an answer claim to a stored chunk."""

    evidence_id: int
    chunk_id: str
    source_id: str
    source_title: str
    section: str
    page: int
    excerpt: str
    support: float
    valid: bool = True
    note: str = ""

    @property
    def location(self) -> str:
        """Human-readable location, e.g. ``2. Retention Periods p.1``."""

        parts = [p for p in (self.section, f"p.{self.page}" if self.page else "") if p]
        return " ".join(parts) or "document"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidenceId": self.evidence_id,
            "chunkId": self.chunk_id,
            "sourceId": self.source_id,
            "sourceTitle": self.source_title,
            "section": self.section,
            "page": self.page,
            "location": self.location,
            "excerpt": self.excerpt,
            "support": round(self.support, 4),
            "valid": self.valid,
            "note": self.note,
        }


@dataclass
class SourceInfo:
    """A registered source document (public projection, no filesystem paths)."""

    source_id: str
    title: str
    kind: str
    origin: str
    chunks: int
    revision: int
    status: str
    created_at: str
    updated_at: str
    content_hash: str = ""
    tenant_id: str = "local"
    tags: Sequence[str] = ()

    @classmethod
    def from_store(cls, record: Any) -> "SourceInfo":
        return cls(
            source_id=record.source_id,
            title=record.title,
            kind=record.kind,
            origin=record.origin,
            chunks=int(getattr(record, "chunk_count", 0) or 0),
            revision=int(getattr(record, "revision", 1) or 1),
            status=getattr(record, "status", "active"),
            created_at=getattr(record, "created_at", ""),
            updated_at=getattr(record, "updated_at", ""),
            content_hash=getattr(record, "content_hash", "") or "",
            tenant_id=getattr(record, "tenant_id", "local") or "local",
            tags=tuple(getattr(record, "tags", ()) or ()),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "title": self.title,
            "kind": self.kind,
            "origin": self.origin,
            "chunks": self.chunks,
            "revision": self.revision,
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "contentHash": self.content_hash,
            "tenantId": self.tenant_id,
            "tags": list(self.tags),
        }


@dataclass
class IngestResult:
    """Outcome for a single source (one file, one upload, one text registration)."""

    filename: str
    status: str  # indexed | unchanged | failed
    chunks: int = 0
    source_id: Optional[str] = None
    duration_ms: float = 0.0
    warnings: List[str] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.status in {"indexed", "unchanged"}

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "filename": self.filename,
            "status": self.status,
            "chunks": self.chunks,
            "sourceId": self.source_id,
            "durationMs": round(self.duration_ms, 3),
            "warnings": list(self.warnings),
        }
        if self.error:
            payload["error"] = self.error
        return payload


class IngestReport(dict):
    """``dict`` of counters (back-compatible) plus typed per-file results."""

    def __init__(self, payload: Dict[str, Any], results: Optional[List[IngestResult]] = None) -> None:
        super().__init__(payload)
        self.results: List[IngestResult] = results or []

    # -- convenience accessors -------------------------------------------------
    @property
    def indexed(self) -> int:
        return int(self.get("indexed", 0))

    @property
    def unchanged(self) -> int:
        return int(self.get("unchanged", 0))

    @property
    def failed(self) -> int:
        return int(self.get("failed", 0))

    @property
    def chunks(self) -> int:
        return int(self.get("chunks", 0))

    @property
    def source_ids(self) -> List[str]:
        return [r.source_id for r in self.results if r.source_id]

    @property
    def ok(self) -> bool:
        return self.failed == 0


class HealthReport(dict):
    """``dict`` health payload plus ``.ok`` / ``.semantic`` attributes."""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok", False))

    @property
    def semantic_embeddings(self) -> bool:
        embedding = self.get("embedding") or {}
        return bool(embedding.get("semantic"))

    @property
    def problems(self) -> List[Dict[str, Any]]:
        return list((self.get("integrity") or {}).get("problems") or [])


@dataclass
class QueryTrace:
    """Sanitised stage timings and counts for one query."""

    query_id: str
    stages: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @classmethod
    def from_result(cls, result: Any) -> "QueryTrace":
        return cls(
            query_id=getattr(result, "query_id", ""),
            stages=list(getattr(result, "trace", []) or []),
            metrics=dict(getattr(result, "metrics", {}) or {}),
            warnings=list(getattr(result, "warnings", []) or []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "queryId": self.query_id,
            "stages": self.stages,
            "metrics": self.metrics,
            "warnings": self.warnings,
        }


def citations_to_dicts(citations: Iterable[Citation]) -> List[Dict[str, Any]]:
    return [c.to_dict() for c in citations]
