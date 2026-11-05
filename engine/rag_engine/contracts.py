"""Versioned visual event contract.

The walkthrough frontend must never need to know about Python objects. It
consumes ``RagExperienceEvent`` records serialised as JSON. Keep this file in
sync with ``rag-experience/src/core/contracts.js`` (contract tests enforce it).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1

#: The only stage names a consumer may receive. Mirrors the spec contract.
RagStage = str
ALLOWED_STAGES = frozenset(
    {
        "source_received",
        "extracting",
        "normalising",
        "chunking",
        "embedding",
        "indexing",
        "query_received",
        "query_embedding",
        "lexical_retrieval",
        "semantic_retrieval",
        "hybrid_fusion",
        "reranking",
        "context_assembly",
        "generation",
        "citation_validation",
        "abstention",
        "completed",
        "failed",
        "source_updated",
        "source_deleted",
    }
)

ALLOWED_STATUS = frozenset({"started", "progress", "completed", "failed", "skipped"})


@dataclass
class RagExperienceEvent:
    """One immutable step of a trace."""

    stage: RagStage
    status: str
    traceId: str
    eventId: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    occurredAt: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    durationMs: Optional[float] = None
    sourceId: Optional[str] = None
    sourceRevision: Optional[str] = None
    chunkIds: Optional[List[str]] = None
    resultIds: Optional[List[str]] = None
    safeMetadata: Dict[str, Any] = field(default_factory=dict)
    warningCode: Optional[str] = None
    errorCode: Optional[str] = None
    schemaVersion: int = SCHEMA_VERSION

    def validate(self) -> None:
        if self.stage not in ALLOWED_STAGES:
            raise ValueError(f"unknown stage: {self.stage!r}")
        if self.status not in ALLOWED_STATUS:
            raise ValueError(f"unknown status: {self.status!r}")
        if self.schemaVersion != SCHEMA_VERSION:
            raise ValueError(f"unsupported schemaVersion: {self.schemaVersion}")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        data = asdict(self)
        # Drop nulls to keep payloads small and diffs readable.
        return {k: v for k, v in data.items() if v is not None}


class TraceRecorder:
    """Collects events for one trace id."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.events: List[RagExperienceEvent] = []

    def emit(self, stage: str, status: str, **kwargs: Any) -> RagExperienceEvent:
        ev = RagExperienceEvent(stage=stage, status=status, traceId=self.trace_id, **kwargs)
        ev.validate()
        self.events.append(ev)
        return ev

    def timed(self, stage: str, **kwargs: Any):  # pragma: no cover - tiny helper
        """Context manager emitting started/completed with a real duration."""
        recorder = self

        class _Ctx:
            def __enter__(self_inner):
                self_inner._t0 = time.perf_counter()
                recorder.emit(stage, "started", **kwargs)
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                ms = (time.perf_counter() - self_inner._t0) * 1000.0
                recorder.emit(
                    stage,
                    "failed" if exc_type else "completed",
                    durationMs=round(ms, 3),
                    **kwargs,
                )
                return False

        return _Ctx()

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.events]

    def to_json(self) -> str:
        return json.dumps(self.to_dicts(), indent=2)


def validate_event_dict(payload: Dict[str, Any]) -> None:
    """Validate a raw JSON event (used by contract tests and live ingestion)."""
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("bad schemaVersion")
    if payload.get("stage") not in ALLOWED_STAGES:
        raise ValueError(f"bad stage {payload.get('stage')!r}")
    if payload.get("status") not in ALLOWED_STATUS:
        raise ValueError(f"bad status {payload.get('status')!r}")
    for key in ("traceId", "eventId", "occurredAt"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError(f"missing {key}")


def validate_stream(events: Iterable[Dict[str, Any]]) -> int:
    n = 0
    for ev in events:
        validate_event_dict(ev)
        n += 1
    return n
