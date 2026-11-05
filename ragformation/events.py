"""Versioned engine event contract — the bridge between the engine and any UI.

The S3 Experience (and any host application) must never parse log strings or
reach into engine internals. It consumes :class:`RAGEvent` objects with a stable
schema instead.

Design rules encoded here:

* **Schema is versioned** (``EVENT_SCHEMA_VERSION``). Adding fields is a minor
  bump; renaming or removing them is major.
* **Events never carry raw document text, prompts or secrets.** Summaries are
  truncated and credentials are redacted; excerpts require a separate authorised
  call.
* **Only stages that actually ran are emitted.** A skipped stage is reported as
  ``skipped`` with a reason; nothing invents a "completed" event for work that
  did not happen.
* **A slow visual client cannot block the engine.** Subscribers get bounded
  queues; overflow drops the oldest event and increments a counter the client
  can read to detect the gap.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from .guardrails import redact_credentials

EVENT_SCHEMA_VERSION = "1.0"

STATUSES = ("started", "progress", "completed", "failed", "skipped")

#: Event modes. ``live`` means a real pipeline run; ``demo`` means a recorded
#: fixture replay; ``extractive`` / ``fallback`` say which generation path ran.
MODES = ("live", "demo", "extractive", "fallback")

_SECRET_KEY = re.compile(r"(api[-_]?key|authorization|password|secret|token|cookie)", re.I)
_MAX_SUMMARY = 240


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean_summary(text: str) -> str:
    redacted = redact_credentials(str(text))
    text = (redacted[0] if isinstance(redacted, tuple) else redacted)
    text = str(text).replace("\n", " ").replace("\r", " ").strip()
    text = re.sub(r"\s{2,}", " ", text)
    if len(text) > _MAX_SUMMARY:
        text = text[: _MAX_SUMMARY - 1].rstrip() + "…"
    return text


def _clean_mapping(payload: Optional[Dict[str, Any]], *, numeric_only: bool = False) -> Dict[str, Any]:
    """Drop secrets and long strings; keep scalars that are safe to publish."""

    if not payload:
        return {}
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if _SECRET_KEY.search(str(key)):
            continue
        if numeric_only:
            if isinstance(value, bool):
                out[str(key)] = value
            elif isinstance(value, (int, float)):
                out[str(key)] = value
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            out[str(key)] = value
        elif isinstance(value, str):
            if len(value) <= 120:
                out[str(key)] = _clean_summary(value)
        elif isinstance(value, (list, tuple)) and len(value) <= 20:
            items = [v for v in value if isinstance(v, (str, int, float)) and len(str(v)) <= 80]
            if len(items) == len(value):
                out[str(key)] = list(items)
    return out


def _references_from(detail: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Extract stable ids a UI may resolve through an authorised detail call."""

    if not detail:
        return []
    refs: List[Dict[str, str]] = []
    for key, kind in (
        ("chunkIds", "chunk"),
        ("chunkId", "chunk"),
        ("sourceId", "source"),
        ("queryId", "query"),
        ("jobId", "job"),
    ):
        value = detail.get(key)
        if isinstance(value, str) and value:
            refs.append({"type": kind, "id": value})
        elif isinstance(value, (list, tuple)):
            for item in list(value)[:8]:
                if isinstance(item, str) and item:
                    refs.append({"type": kind, "id": item})
    return refs


@dataclass(frozen=True)
class RAGEvent:
    """One pipeline event. Immutable, JSON-ready, safe to show a UI."""

    stage: str
    status: str
    trace_id: str = ""
    sequence: int = 0
    event_id: str = field(default_factory=lambda: "evt-" + uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=_now)
    operation_id: Optional[str] = None
    duration_ms: Optional[float] = None
    counts: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    references: List[Dict[str, str]] = field(default_factory=list)
    safe_summary: str = ""
    mode: str = "live"
    warning_codes: List[str] = field(default_factory=list)
    schema_version: str = EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"invalid event status {self.status!r}")
        if self.mode not in MODES:
            raise ValueError(f"invalid event mode {self.mode!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "eventId": self.event_id,
            "traceId": self.trace_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "operationId": self.operation_id,
            "stage": self.stage,
            "status": self.status,
            "durationMs": None if self.duration_ms is None else round(self.duration_ms, 3),
            "counts": dict(self.counts),
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.metrics.items()},
            "references": list(self.references),
            "safeSummary": self.safe_summary,
            "mode": self.mode,
            "warningCodes": list(self.warning_codes),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "RAGEvent":
        return cls(
            stage=payload.get("stage", "unknown"),
            status=payload.get("status", "progress"),
            trace_id=payload.get("traceId", ""),
            sequence=int(payload.get("sequence") or 0),
            event_id=payload.get("eventId") or "evt-" + uuid.uuid4().hex[:12],
            timestamp=payload.get("timestamp") or _now(),
            operation_id=payload.get("operationId"),
            duration_ms=payload.get("durationMs"),
            counts=dict(payload.get("counts") or {}),
            metrics=dict(payload.get("metrics") or {}),
            references=list(payload.get("references") or []),
            safe_summary=payload.get("safeSummary", ""),
            mode=payload.get("mode", "live"),
            warning_codes=list(payload.get("warningCodes") or []),
            schema_version=payload.get("schemaVersion", EVENT_SCHEMA_VERSION),
        )


def make_event(
    stage: str,
    status: str,
    *,
    trace_id: str = "",
    mode: str = "live",
    detail: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[float] = None,
    summary: str = "",
    operation_id: Optional[str] = None,
    warnings: Iterable[str] = (),
) -> RAGEvent:
    """Build a sanitised event from an internal trace detail dict."""

    detail = detail or {}
    stage_name = stage.replace("_", ".")
    default_summary = f"{stage_name} {status}"
    if summary:
        text = summary
    else:
        extras = []
        for key in ("chunks", "candidates", "tokens", "sources", "evidence"):
            if isinstance(detail.get(key), int):
                extras.append(f"{detail[key]} {key}")
        text = default_summary + (": " + ", ".join(extras) if extras else "")
    return RAGEvent(
        stage=stage_name,
        status=status,
        trace_id=trace_id,
        operation_id=operation_id,
        duration_ms=duration_ms,
        counts=_clean_mapping(detail),
        metrics=_clean_mapping(detail, numeric_only=True),
        references=_references_from(detail),
        safe_summary=_clean_summary(text),
        mode=mode,
        warning_codes=[str(w) for w in warnings][:8],
    )


class Subscription:
    """A bounded queue of events for one consumer."""

    def __init__(self, bus: "EventBus", trace_id: Optional[str], maxsize: int) -> None:
        self.bus = bus
        self.trace_id = trace_id
        self.maxsize = maxsize
        self.queue: Deque[RAGEvent] = deque()
        self.dropped = 0
        self._closed = False
        self._cond = threading.Condition()

    def _deliver(self, event: RAGEvent) -> None:
        with self._cond:
            if self._closed:
                return
            if self.trace_id and event.trace_id and event.trace_id != self.trace_id:
                return
            if len(self.queue) >= self.maxsize:
                self.queue.popleft()
                self.dropped += 1
            self.queue.append(event)
            self._cond.notify_all()

    def drain(self, limit: int = 64) -> List[RAGEvent]:
        with self._cond:
            items = [self.queue.popleft() for _ in range(min(limit, len(self.queue)))]
        return items

    def get(self, timeout: float = 1.0) -> Optional[RAGEvent]:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while not self.queue and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            if self.queue:
                return self.queue.popleft()
            return None

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self.queue.clear()
            self._cond.notify_all()
        self.bus.unsubscribe(self)


class EventBus:
    """Thread-safe in-process fan-out with per-trace history for polling/replay."""

    def __init__(self, *, history: int = 512, per_trace: int = 256) -> None:
        self._lock = threading.Lock()
        self._subscribers: List[Subscription] = []
        self._history: Deque[RAGEvent] = deque(maxlen=history)
        self._seq: Dict[str, int] = {}
        self._per_trace = per_trace
        self.published = 0

    # ----------------------------------------------------------- publishing
    def publish(self, event: RAGEvent) -> RAGEvent:
        with self._lock:
            self.published += 1
            sequence = self._seq.get(event.trace_id, 0) + 1
            self._seq[event.trace_id] = sequence
            if len(self._seq) > 4096:  # bound memory for long-running servers
                self._seq = {k: v for k, v in list(self._seq.items())[-1024:]}
            final = RAGEvent(
                stage=event.stage,
                status=event.status,
                trace_id=event.trace_id,
                sequence=sequence,
                event_id=event.event_id,
                timestamp=event.timestamp,
                operation_id=event.operation_id,
                duration_ms=event.duration_ms,
                counts=event.counts,
                metrics=event.metrics,
                references=event.references,
                safe_summary=event.safe_summary,
                mode=event.mode,
                warning_codes=event.warning_codes,
            )
            self._history.append(final)
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub._deliver(final)
        return final

    def emit(self, stage: str, status: str, **kwargs: Any) -> Optional[RAGEvent]:
        if not self._subscribers and not self._history.maxlen:
            return None
        return self.publish(make_event(stage, status, **kwargs))

    # -------------------------------------------------------- subscriptions
    def subscribe(self, trace_id: Optional[str] = None, *, maxsize: int = 256) -> Subscription:
        sub = Subscription(self, trace_id, maxsize)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # ------------------------------------------------------------- history
    def history(self, *, trace_id: Optional[str] = None, since: int = 0, limit: int = 128) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._history)
        out = []
        for event in events:
            if trace_id and event.trace_id != trace_id:
                continue
            if since and event.sequence <= since:
                continue
            out.append(event.to_dict())
            if len(out) >= limit:
                break
        return out

    def replay(self, payloads: Iterable[Dict[str, Any]], *, mode: str = "demo") -> List[RAGEvent]:
        """Load recorded events for the demo/replay UI without touching the engine."""

        events = []
        for payload in payloads:
            try:
                event = RAGEvent.from_dict(payload)
            except (TypeError, ValueError):
                continue
            events.append(
                RAGEvent(
                    stage=event.stage,
                    status=event.status,
                    trace_id=event.trace_id,
                    sequence=event.sequence,
                    event_id=event.event_id,
                    timestamp=event.timestamp,
                    operation_id=event.operation_id,
                    duration_ms=event.duration_ms,
                    counts=event.counts,
                    metrics=event.metrics,
                    references=event.references,
                    safe_summary=event.safe_summary,
                    mode=mode,
                    warning_codes=event.warning_codes,
                )
            )
        return events


class NullBus(EventBus):
    """Bus that keeps history but never fans out; used by CLI-only runs."""

    def subscribe(self, trace_id: Optional[str] = None, *, maxsize: int = 256) -> Subscription:  # noqa: D401
        return Subscription(self, trace_id, maxsize)


def events_as_dicts(events: Iterable[RAGEvent]) -> List[Dict[str, Any]]:
    return [e.to_dict() for e in events]


def sse_frame(event: Dict[str, Any], *, event_name: str = "rag") -> bytes:
    """Encode one event as a Server-Sent-Events frame."""

    body = json.dumps(event, separators=(",", ":"), default=str)
    return f"event: {event_name}\ndata: {body}\n\n".encode("utf-8")


def sequence_gaps(events: Iterable[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """Return (expected, received) pairs where a client missed events."""

    gaps: List[Tuple[int, int]] = []
    expected = None
    for event in events:
        seq = int(event.get("sequence") or 0)
        if expected is not None and seq != expected:
            gaps.append((expected, seq))
        expected = seq + 1
    return gaps
