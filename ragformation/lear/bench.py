"""``lear bench``: dummy-user load through the agent service layer.

Each concurrency level starts N threads ("dummy users" ``bench-u1..uN``) that
send ``requests_per_user`` questions one after another through
``LearAgent.ask`` — the same path as the CLI, including the per-user and
per-session rate limits and the concurrency gate. Every request records:

* TTFT (first streamed token after the request started),
* inter-token latencies from the per-token timestamps (ITL),
* total latency, completion tokens per second (when the model reports
  tokens) and stream events per second,
* per-stage timings (retrieval, generation),
* the outcome (status, rate-limited, error) — failures stay in the results.

With the offline baseline backend there is no LLM decode: the "tokens" are the
extractive answer's streamed words, so the numbers are *pipeline-only* timings
and are labelled that way. They say nothing about model latency.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..errors import RagError, RateLimited
from . import evalset
from .settings import LearSettings
from .stats import describe

BENCH_VERSION = "lear-bench-v1"


def default_questions(limit: int = 12) -> List[str]:
    """Answerable single-turn dev questions from the golden set (never test/holdout)."""

    try:
        data = evalset.load_dataset(evalset.DEFAULT_DATASET)
    except Exception:
        return ["Which permission modes does the Lear permission engine support?"]
    picks = [
        c["question"] for c in data["cases"]
        if c["split"] == "dev" and not c.get("history") and c["category"] not in ("unanswerable", "injection")
    ]
    return picks[:limit] or ["Which permission modes does the Lear permission engine support?"]


def _one_level(agent, users: int, requests_per_user: int, questions: Sequence[str]) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(users)

    def worker(index: int) -> None:
        user = f"bench-u{index + 1}"
        session = f"bench-{users}-{index + 1}"
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            pass
        for n in range(requests_per_user):
            question = questions[(index * requests_per_user + n) % len(questions)]
            started = time.perf_counter()
            record: Dict[str, Any] = {"user": user, "request": n + 1, "question": question}
            try:
                answer = agent.ask(question, user_id=user, session_id=session, save=True)
                t = answer.get("timings") or {}
                record.update({
                    "outcome": "ok" if answer["status"] != "ERROR" else "error",
                    "status": answer["status"],
                    "ttftMs": t.get("ttftMs"),
                    "totalMs": t.get("totalMs"),
                    "itlMs": t.get("_itlRaw") or [],
                    "tokensPerSec": t.get("tokensPerSec"),
                    "streamEventsPerSec": t.get("decodeEventsPerSec"),
                    "completionTokens": t.get("completionTokens"),
                    "retrievalMs": (t.get("stages") or {}).get("retrievalMs"),
                    "generationMs": (t.get("stages") or {}).get("generationMs"),
                    "qualifying": answer.get("qualifying", False),
                    "error": (answer.get("error") or {}).get("message") if answer.get("error") else None,
                })
            except RateLimited as exc:
                record.update({"outcome": "rate_limited", "status": "RATE_LIMITED", "error": exc.message, "detail": exc.details,
                               "totalMs": round((time.perf_counter() - started) * 1000.0, 2)})
            except RagError as exc:
                record.update({"outcome": "error", "status": "ERROR", "error": exc.message,
                               "totalMs": round((time.perf_counter() - started) * 1000.0, 2)})
            with lock:
                records.append(record)

    wall_started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(users)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall_s = time.perf_counter() - wall_started
    ok = [r for r in records if r["outcome"] == "ok"]
    itl: List[float] = []
    for r in ok:
        itl.extend(r.get("itlMs") or [])
    return {
        "users": users,
        "requests": len(records),
        "ok": len(ok),
        "rateLimited": sum(1 for r in records if r["outcome"] == "rate_limited"),
        "errors": sum(1 for r in records if r["outcome"] == "error"),
        "wallSeconds": round(wall_s, 3),
        "throughputRps": round(len(ok) / wall_s, 3) if wall_s > 0 else None,
        "ttftMs": describe([r.get("ttftMs") for r in ok]),
        "itlMs": describe(itl),
        "totalMs": describe([r.get("totalMs") for r in ok]),
        "totalMsAllOutcomes": describe([r.get("totalMs") for r in records]),
        "tokensPerSec": describe([r.get("tokensPerSec") for r in ok]),
        "streamEventsPerSec": describe([r.get("streamEventsPerSec") for r in ok]),
        "retrievalMs": describe([r.get("retrievalMs") for r in ok]),
        "generationMs": describe([r.get("generationMs") for r in ok]),
        "statusCounts": {s: sum(1 for r in records if r.get("status") == s) for s in sorted({r.get("status") for r in records})},
        "records": [{k: v for k, v in r.items() if k != "itlMs"} | {"itlCount": len(r.get("itlMs") or [])} for r in records],
    }


def run_bench(
    settings: LearSettings,
    *,
    users: Sequence[int] = (1, 2, 5),
    requests_per_user: int = 3,
    out: Optional[Path] = None,
    questions: Optional[Sequence[str]] = None,
    agent: Any = None,
) -> Dict[str, Any]:
    from .agent import LearAgent

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = Path(settings.data_dir) / "bench" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    questions = list(questions or default_questions())
    own = agent is None
    if own:
        agent = LearAgent(dataclasses.replace(settings, agent_db_path=run_dir / "bench_agent.db"))
    levels = []
    try:
        # Warm-up outside the measurement: opens the index and loads the models.
        try:
            agent.ask(questions[0], user_id="bench-warmup", save=False)
            warm = "ok"
        except RagError as exc:
            warm = f"failed: {exc.message}"
        for n in users:
            levels.append(_one_level(agent, int(n), int(requests_per_user), questions))
    finally:
        if own:
            agent.close()
    qualifying = settings.qualifying and all(r.get("qualifying") for level in levels for r in level["records"] if r["outcome"] == "ok")
    report = {
        "benchVersion": BENCH_VERSION,
        "startedAt": stamp,
        "backend": settings.backend,
        "qualifying": qualifying,
        "label": "live local model timings" if qualifying else "PIPELINE-ONLY timings (offline baseline: no LLM decode; not model latency)",
        "models": {"llm": settings.llm_model if settings.qualifying else "extractive (baseline)", "embedding": settings.embed_model if settings.qualifying else "hashing (baseline)"},
        "limits": {
            "perUserPerMin": settings.rate_user_per_min,
            "perSessionPerMin": settings.rate_session_per_min,
            "maxConcurrent": settings.max_concurrent,
            "queueTimeoutS": settings.queue_timeout_s,
        },
        "requestsPerUser": requests_per_user,
        "questions": len(questions),
        "warmup": warm,
        "levels": levels,
        "machine": _machine(),
    }
    target = Path(out) if out else run_dir / "bench.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["output"] = str(target)
    return report


def _machine() -> Dict[str, Any]:
    import os
    import platform

    info: Dict[str, Any] = {"python": platform.python_version(), "platform": platform.platform(), "cpus": os.cpu_count()}
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal"):
                    info["memTotalKiB"] = int(line.split()[1])
                    break
    except OSError:
        pass
    return info


def render_bench(report: Dict[str, Any]) -> str:
    lines = [f"Lear bench — backend {report['backend']} — {report['label']}",
             f"limits: {report['limits']}  requests/user={report['requestsPerUser']}  warm-up: {report['warmup']}"]
    header = f"{'users':>5} {'reqs':>4} {'ok':>3} {'429':>4} {'err':>3} {'rps':>7} {'ttft p50':>9} {'ttft p99':>9} {'itl p50':>8} {'total p50':>10} {'total p85':>10} {'total p99':>10} {'tok/s p50':>9}"
    lines.append(header)
    for level in report["levels"]:
        def g(block: str, key: str) -> str:
            value = (level.get(block) or {}).get(key)
            return "–" if value is None else f"{value:.1f}"
        lines.append(
            f"{level['users']:>5} {level['requests']:>4} {level['ok']:>3} {level['rateLimited']:>4} {level['errors']:>3} "
            f"{(level['throughputRps'] or 0):>7.2f} {g('ttftMs', 'p50'):>9} {g('ttftMs', 'p99'):>9} {g('itlMs', 'p50'):>8} "
            f"{g('totalMs', 'p50'):>10} {g('totalMs', 'p85'):>10} {g('totalMs', 'p99'):>10} "
            f"{(g('tokensPerSec', 'p50') if report['qualifying'] else 'n/a'):>9}"
        )
    if not report["qualifying"]:
        lines.append("tok/s is n/a: the baseline streams extractive words, not model tokens (raw values stay in the JSON, labelled pipeline-only)")
    if report.get("output"):
        lines.append(f"output: {report['output']}")
    return "\n".join(lines)
