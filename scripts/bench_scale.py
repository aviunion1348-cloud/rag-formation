#!/usr/bin/env python3
"""Measure this build at a chosen corpus size (100 / 500 / 1000 chunks).

The point of this script is not to produce a flattering number: it produces a
JSON file plus a console table that say exactly what was measured, on which
machine, with which providers, and what was not measured (no language model, no
network, one process, one thread).

Method
------
1. Generate a deterministic synthetic corpus: markdown service pages with a
   stable ``svc-###`` id, an owning team and an availability target.
2. Ingest it into a throwaway data directory and time the whole ingest.
3. Ask a fixed set of questions, half of them answerable from a known sentence
   and half off-topic, and record end-to-end latency plus the status mix.
4. Spot-check that an answerable question returns the *correct* fact (a fast
   system that answers the wrong thing is not a result).
5. Write ``evals/results/scale/<label>.json`` and print a summary.

Usage::

    python scripts/bench_scale.py --chunks 100 500 1000
    python scripts/bench_scale.py --chunks 1000 --queries 50 --out /tmp/scale
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragformation.config import Settings  # noqa: E402
from ragformation.engine import RAGEngine  # noqa: E402

SECTIONS = [
    ("Summary", "This page describes the service, its consumers and the boundary it defends."),
    ("Ownership", "The owning team is accountable for on-call cover, patches and capacity review."),
    ("Availability", "Availability is measured at the request boundary and reviewed every week."),
    ("Dependencies", "Upstream dependencies are listed with their failure behaviour and timeouts."),
    ("Runbook", "Operational procedures describe how to restart, drain and verify the service."),
    ("Change policy", "Changes follow the standard release process with an approved rollback plan."),
]


def build_document(index: int) -> str:
    service = f"svc-{index:03d}"
    team = f"team-{(index % 37):02d}"
    target = 99.0 + ((index % 90) / 100.0)
    lines = [
        f"# Service {service}",
        "",
        f"Service {service} is owned by {team} and reports an availability target of {target:.2f}% "
        f"over a rolling 30 day window.",
        "",
    ]
    for position, (heading, text) in enumerate(SECTIONS):
        lines.extend(
            [
                f"## {position + 1}. {heading}",
                "",
                f"{text} For {service} this section is reviewed by {team} and versioned with the "
                f"service catalogue. Ticket references use the form {service}-{position + 1:02d}.",
                "",
            ]
        )
    return "\n".join(lines)


def generate_corpus(directory: Path, target_chunks: int) -> List[Path]:
    """Create ~target_chunks chunks; each document yields about 8 chunks."""
    per_document = 8
    documents = max(1, math.ceil(target_chunks / per_document))
    paths = []
    for index in range(1, documents + 1):
        path = directory / f"service-{index:03d}.md"
        path.write_text(build_document(index), encoding="utf-8")
        paths.append(path)
    return paths


def percentile(values: List[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[position], 3)


def run_one(target_chunks: int, queries: int, label: str) -> Dict[str, Any]:
    corpus_dir = Path(tempfile.mkdtemp(prefix="ragformation-scale-corpus-"))
    data_dir = Path(tempfile.mkdtemp(prefix="ragformation-scale-data-"))
    try:
        paths = generate_corpus(corpus_dir, target_chunks)
        corpus_bytes = sum(path.stat().st_size for path in paths)

        settings = replace(
            Settings.from_env(),
            data_dir=data_dir,
            embed_provider="hashing",
            llm_provider="extractive",
            persist_traces=False,
        )
        engine = RAGEngine(settings)
        try:
            ingest_started = time.perf_counter()
            report = engine.ingest_path([str(path) for path in paths])
            ingest_seconds = time.perf_counter() - ingest_started

            health = engine.health()
            chunks = health["chunks"]

            # ------------------------------------------------------- queries
            latencies: List[float] = []
            statuses: Dict[str, int] = {}
            correct = 0
            checked = 0
            for index in range(queries):
                number = 1 + (index % max(1, len(paths)))
                if index % 2 == 0:
                    question = f"What availability target does svc-{number:03d} report?"
                    expected = f"{99.0 + (number % 90) / 100.0:.2f}%"
                else:
                    question = "What is the capital of Australia?"
                    expected = None
                started = time.perf_counter()
                result = engine.ask(question, save=False)
                latencies.append((time.perf_counter() - started) * 1000.0)
                statuses[result.status] = statuses.get(result.status, 0) + 1
                if expected is not None:
                    checked += 1
                    # `needs_review` is a legitimate outcome when another source
                    # disagrees; the fact must still be present and cited.
                    if expected in result.answer and result.status in {"answered", "needs_review"} and result.citations:
                        correct += 1

            sample = engine.ask("What availability target does svc-001 report?", save=False)
            db_path = Path(settings.db_path)
            # SQLite may keep most pages in the WAL until checkpoint: report the
            # real on-disk footprint, not just the main file.
            db_bytes = sum(
                candidate.stat().st_size
                for candidate in (
                    db_path,
                    db_path.with_name(db_path.name + "-wal"),
                    db_path.with_name(db_path.name + "-shm"),
                )
                if candidate.exists()
            )
            payload = {
                "label": label,
                "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "machine": {
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "cpuCount": __import__("os").cpu_count(),
                },
                "provider": {
                    "embedding": health["embedding"]["provider"],
                    "semantic": health["embedding"]["semantic"],
                    "dimension": health["embedding"]["dimension"],
                    "generation": health["generation"]["provider"],
                    "generationMode": health["generation"]["mode"],
                },
                "corpus": {
                    "requestedChunks": target_chunks,
                    "sources": health["sources"],
                    "chunks": chunks,
                    "bytes": corpus_bytes,
                    "dbBytes": db_bytes,
                },
                "ingest": {
                    "seconds": round(ingest_seconds, 3),
                    "chunksPerSecond": round(chunks / ingest_seconds, 1) if ingest_seconds else None,
                    "sourcesPerSecond": round(health["sources"] / ingest_seconds, 2) if ingest_seconds else None,
                    "statuses": {key: report.get(key) for key in ("indexed", "unchanged", "failed", "chunks")},
                },
                "query": {
                    "count": len(latencies),
                    "meanMs": round(statistics.fmean(latencies), 3),
                    "p50Ms": percentile(latencies, 0.5),
                    "p95Ms": percentile(latencies, 0.95),
                    "maxMs": round(max(latencies), 3),
                    "statuses": statuses,
                    "answerableChecked": checked,
                    "answerableCorrect": correct,
                },
                "sampleAnswer": {
                    "question": "What availability target does svc-001 report?",
                    "status": sample.status,
                    "answer": sample.answer[:400],
                    "citations": len(sample.citations),
                },
                "limits": [
                    "single process, single thread, no cache warm-up",
                    "hashing embeddings (semantic: false) and the extractive answer path",
                    "synthetic corpus: uniform documents, no PDF or OCR work",
                    "SQLite on local disk; numbers will differ on other hardware",
                ],
            }
            return payload
        finally:
            engine.close()
    finally:
        shutil.rmtree(corpus_dir, ignore_errors=True)
        shutil.rmtree(data_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=int, nargs="+", default=[100, 500, 1000])
    parser.add_argument("--queries", type=int, default=24)
    parser.add_argument("--out", type=Path, default=ROOT / "evals" / "results" / "scale")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for target in args.chunks:
        payload = run_one(target, args.queries, label=f"scale-{target}")
        destination = args.out / f"scale-{target}.json"
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        results.append(payload)
        corpus = payload["corpus"]
        ingest = payload["ingest"]
        query = payload["query"]
        print(
            f"{target:>5} requested -> {corpus['chunks']:>5} chunks / {corpus['sources']:>4} sources | "
            f"ingest {ingest['seconds']:>6.2f}s ({ingest['chunksPerSecond']:>7.1f} chunks/s) | "
            f"query p50 {query['p50Ms']:>6.2f}ms p95 {query['p95Ms']:>6.2f}ms | "
            f"answers {query['answerableCorrect']}/{query['answerableChecked']} | "
            f"db {corpus['dbBytes'] / 1024:.0f}KB"
        )
    print(f"\nwrote {len(results)} report(s) under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
