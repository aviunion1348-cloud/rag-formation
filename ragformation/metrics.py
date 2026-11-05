"""Latency and quality statistics.

The whiteboard asks for mean / p50 / p95 and for streaming metrics (TTFT, ITL).
Everything here is computed from real observations — there are no simulated
numbers anywhere in this module.

Definitions used consistently across CLI, API and UI:

* **TTFT** — time to first token, measured from the moment the generation
  request is issued to the first non-empty chunk of text arriving.
* **ITL** — inter-token latency: the mean gap between successive streamed
  chunks, excluding the first. Only meaningful for streaming providers.
* **tokens/s** — completion tokens divided by generation wall-clock time. When
  the provider does not report token counts we estimate at ~4 chars/token and
  mark the value ``estimated``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile (the method numpy calls "linear").

    Returns 0.0 for an empty sample rather than raising: callers render metrics
    for a fresh install where no query has run yet.
    """
    if not values:
        return 0.0
    if not 0.0 <= p <= 100.0:
        raise ValueError("percentile must be in [0, 100]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (p / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(ordered[int(rank)])
    weight = rank - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def summarize(values: Iterable[Optional[float]], *, digits: int = 2) -> Dict[str, float]:
    """count / mean / p50 / p95 / min / max for one metric."""
    clean = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not clean:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    return {
        "count": len(clean),
        "mean": round(sum(clean) / len(clean), digits),
        "p50": round(percentile(clean, 50), digits),
        "p95": round(percentile(clean, 95), digits),
        "min": round(min(clean), digits),
        "max": round(max(clean), digits),
    }


@dataclass
class StreamTiming:
    """Observed timing of one streamed generation."""

    start: float = field(default_factory=time.perf_counter)
    first_token_at: Optional[float] = None
    last_token_at: Optional[float] = None
    chunks: int = 0
    characters: int = 0
    _gaps: List[float] = field(default_factory=list)

    def mark(self, text: str) -> None:
        if not text:
            return
        now = time.perf_counter()
        if self.first_token_at is None:
            self.first_token_at = now
        elif self.last_token_at is not None:
            self._gaps.append(now - self.last_token_at)
        self.last_token_at = now
        self.chunks += 1
        self.characters += len(text)

    @property
    def ttft_ms(self) -> Optional[float]:
        if self.first_token_at is None:
            return None
        return round((self.first_token_at - self.start) * 1000.0, 3)

    @property
    def generation_ms(self) -> Optional[float]:
        if self.last_token_at is None:
            return None
        return round((self.last_token_at - self.start) * 1000.0, 3)

    @property
    def itl_ms(self) -> Optional[float]:
        """Mean inter-token latency. None when there was only one chunk."""
        if not self._gaps:
            return None
        return round((sum(self._gaps) / len(self._gaps)) * 1000.0, 3)

    def as_dict(self) -> Dict[str, Optional[float]]:
        return {
            "ttftMs": self.ttft_ms,
            "itlMs": self.itl_ms,
            "generationMs": self.generation_ms,
            "streamChunksMeasured": self.chunks,
            "characters": self.characters,
        }


def tokens_per_second(tokens: Optional[int], seconds: Optional[float]) -> Optional[float]:
    if not tokens or not seconds or seconds <= 0:
        return None
    return round(tokens / seconds, 2)
