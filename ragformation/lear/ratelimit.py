"""Service-layer limits for the Lear agent.

Enforced inside :class:`~ragformation.lear.agent.LearAgent` (not in the CLI),
so every entry point that uses the agent gets the same behaviour:

* a sliding-window request limit per user and per session;
* a global concurrency cap with a bounded wait (``queue_timeout_s``) — excess
  callers are rejected with ``rate_limited`` instead of queueing forever;
* input / context / output caps are configuration values passed to the engine
  (``max_input_chars``, ``max_context_tokens``, ``max_output_tokens``).

The clock is injectable, so window behaviour is tested with a fake clock and
no sleeping. Counters are exposed for benchmarks (accepted / rejected / peak
in-flight).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Callable, Deque, Dict, Iterator, Optional

from ..errors import RateLimited


class LearRateLimiter:
    def __init__(
        self,
        *,
        per_user_per_min: int,
        per_session_per_min: int,
        max_concurrent: int,
        queue_timeout_s: float = 30.0,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if per_user_per_min < 1 or per_session_per_min < 1 or max_concurrent < 1:
            raise ValueError("limits must be positive")
        self.per_user = per_user_per_min
        self.per_session = per_session_per_min
        self.max_concurrent = max_concurrent
        self.queue_timeout_s = queue_timeout_s
        self.window_s = window_s
        self.clock = clock
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._users: Dict[str, Deque[float]] = {}
        self._sessions: Dict[str, Deque[float]] = {}
        self.counters = {
            "accepted": 0,
            "rejectedUser": 0,
            "rejectedSession": 0,
            "rejectedConcurrency": 0,
            "inFlight": 0,
            "peakInFlight": 0,
        }

    def _prune(self, bucket: Deque[float], now: float) -> None:
        while bucket and bucket[0] <= now - self.window_s:
            bucket.popleft()

    def _admit(self, user_id: str, session_id: Optional[str]) -> None:
        with self._lock:
            now = self.clock()
            user_bucket = self._users.setdefault(user_id, deque())
            self._prune(user_bucket, now)
            if len(user_bucket) >= self.per_user:
                self.counters["rejectedUser"] += 1
                retry = round(user_bucket[0] + self.window_s - now, 3)
                raise RateLimited(
                    "per-user request limit reached",
                    hint=f"limit {self.per_user}/{int(self.window_s)}s; retry in {retry}s",
                    scope="user",
                    retryAfterS=retry,
                )
            session_bucket = None
            if session_id:
                session_bucket = self._sessions.setdefault(session_id, deque())
                self._prune(session_bucket, now)
                if len(session_bucket) >= self.per_session:
                    self.counters["rejectedSession"] += 1
                    retry = round(session_bucket[0] + self.window_s - now, 3)
                    raise RateLimited(
                        "per-session request limit reached",
                        hint=f"limit {self.per_session}/{int(self.window_s)}s; retry in {retry}s",
                        scope="session",
                        retryAfterS=retry,
                    )
            user_bucket.append(now)
            if session_bucket is not None:
                session_bucket.append(now)

    @contextmanager
    def acquire(self, user_id: str, session_id: Optional[str] = None, *, timeout: Optional[float] = None) -> Iterator[None]:
        self._admit(user_id, session_id)
        wait = self.queue_timeout_s if timeout is None else timeout
        if not self._slots.acquire(timeout=max(0.0, wait)):
            with self._lock:
                self.counters["rejectedConcurrency"] += 1
            raise RateLimited(
                "too many concurrent requests",
                hint=f"at most {self.max_concurrent} in flight; waited {wait}s",
                scope="concurrency",
            )
        with self._lock:
            self.counters["accepted"] += 1
            self.counters["inFlight"] += 1
            self.counters["peakInFlight"] = max(self.counters["peakInFlight"], self.counters["inFlight"])
        try:
            yield
        finally:
            with self._lock:
                self.counters["inFlight"] -= 1
            self._slots.release()

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self.counters)
