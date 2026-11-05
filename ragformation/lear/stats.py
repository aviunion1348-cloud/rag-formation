"""Small, dependency-free statistics helpers for latency reporting."""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence


def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile (no interpolation), ``None`` for no data."""

    data = sorted(float(v) for v in values if v is not None)
    if not data:
        return None
    rank = max(1, math.ceil(pct / 100.0 * len(data)))
    return round(data[min(rank, len(data)) - 1], 3)


def describe(values: Iterable[Optional[float]]) -> Dict[str, Optional[float]]:
    data: List[float] = [float(v) for v in values if v is not None]
    if not data:
        return {"count": 0, "mean": None, "p50": None, "p85": None, "p99": None, "min": None, "max": None}
    return {
        "count": len(data),
        "mean": round(sum(data) / len(data), 3),
        "p50": percentile(data, 50),
        "p85": percentile(data, 85),
        "p99": percentile(data, 99),
        "min": round(min(data), 3),
        "max": round(max(data), 3),
    }
