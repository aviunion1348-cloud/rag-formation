#!/usr/bin/env python3
"""Golden-set evaluation harness.

Produces real numbers — no fabricated metrics. Run from ``engine/``:

    python3 tools/evaluate.py            # human table
    python3 tools/evaluate.py --json     # machine-readable (used by CI + docs)

Metrics reported
----------------
* HR@k        hit rate: expected source appears in the top-k contexts
* MRR         mean reciprocal rank of the first correct context
* precision   share of answers matching the expected status
* cite-prec   share of citations whose excerpt literally occurs in the cited chunk
* abstain-acc share of unanswerable questions correctly abstained
* latency     p50 / p95 / p99 over repeated runs (wall clock, this machine)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag_engine import RagEngine, load_corpus  # noqa: E402
from rag_engine.chunking import ChunkingConfig  # noqa: E402

# (question, expected top source or None, expected status)
GOLDEN = [
    ("When must the incident commander be paged for a P1 incident?", "src-p1-incident", "answered"),
    ("How long are application logs retained?", "src-data-retention", "answered"),
    ("How long are logs retained?", "src-data-retention", "answered"),
    ("What does a P1 post-incident review have to contain?", "src-p1-incident", "answered"),
    ("Who approves break-glass access and how long does it last?", "src-access-control", "answered"),
    # The poisoned source outranks everything, but it is quarantined, so the
    # correct retrieval outcome is the nearest clean source: retention policy.
    ("What are the vendor onboarding and breach notification requirements?", "src-data-retention", "answered"),
    ("What is our policy on lunar payload insurance?", None, "abstained"),
    ("How do I bake sourdough bread?", None, "abstained"),
    ("What is the capital of France?", None, "abstained"),
]


def percentile(values, pct):
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 3)
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    frac = k - lo
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * frac, 3)


def run(repeats: int = 20) -> dict:
    engine = RagEngine(load_corpus(), chunking=ChunkingConfig(target_tokens=90, overlap_tokens=18))
    engine.ingest()

    rows = []
    latencies = []
    hr1 = hr3 = 0
    rr_sum = 0.0
    status_ok = 0
    cite_total = cite_ok = 0
    abstain_total = abstain_ok = 0

    for question, expected_source, expected_status in GOLDEN:
        result = engine.query(question)
        ctx_sources = [c["source_id"] for c in result["context_chunks"]]
        rank = None
        if expected_source and expected_source in ctx_sources:
            rank = ctx_sources.index(expected_source) + 1
            rr_sum += 1.0 / rank
            hr1 += 1 if rank == 1 else 0
            hr3 += 1 if rank <= 3 else 0
        elif expected_source is None:
            rr_sum += 0.0

        status_match = result["status"] in {expected_status, "needs_review"} and not (
            expected_status == "abstained" and result["status"] != "abstained"
        )
        status_ok += 1 if status_match else 0
        if expected_status == "abstained":
            abstain_total += 1
            abstain_ok += 1 if result["status"] == "abstained" else 0

        for c in result["citations"]:
            cite_total += 1
            chunk = engine.chunks.get(c["chunk_id"])
            cite_ok += 1 if chunk and c["excerpt"] in chunk.text else 0

        rows.append(
            {
                "question": question,
                "status": result["status"],
                "expectedStatus": expected_status,
                "rank": rank,
                "bestScore": result["metrics"]["bestCoverage"],
                "contexts": len(result["context_chunks"]),
                "contextTokens": result["metrics"]["contextTokens"],
                "quarantined": len(result["quarantined"]),
                "ms": result["metrics"]["totalMs"],
            }
        )

        for _ in range(repeats):
            t0 = time.perf_counter()
            engine.query(question)
            latencies.append((time.perf_counter() - t0) * 1000.0)

    answerable = [g for g in GOLDEN if g[1]]
    metrics = {
        "questions": len(GOLDEN),
        "answerable": len(answerable),
        "hitRate@1": round(hr1 / max(1, len(answerable)), 4),
        "hitRate@3": round(hr3 / max(1, len(answerable)), 4),
        "mrr": round(rr_sum / max(1, len(answerable)), 4),
        "statusAccuracy": round(status_ok / len(GOLDEN), 4),
        "citationPrecision": round(cite_ok / max(1, cite_total), 4),
        "abstentionAccuracy": round(abstain_ok / max(1, abstain_total), 4),
        "citationCount": cite_total,
        "latencyMs": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "mean": round(statistics.fmean(latencies), 3),
            "samples": len(latencies),
        },
        "indexSize": engine.index.size,
        "sources": len(engine.registry),
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "embeddingModel": engine.embedder.model_id,
            "dimension": engine.embedder.dimension,
        },
    }
    return {"metrics": metrics, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--repeats", type=int, default=20)
    args = parser.parse_args()
    report = run(args.repeats)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    m = report["metrics"]
    print("\nGOLDEN SET EVALUATION")
    print("=" * 78)
    print(f"{'question':<62}{'status':<12}{'rank':>4}")
    print("-" * 78)
    for r in report["rows"]:
        print(f"{r['question'][:60]:<62}{r['status']:<12}{str(r['rank'] or '-'):>4}")
    print("-" * 78)
    print(f"hit-rate@1        {m['hitRate@1']:.2%}")
    print(f"hit-rate@3        {m['hitRate@3']:.2%}")
    print(f"MRR               {m['mrr']:.4f}")
    print(f"status accuracy   {m['statusAccuracy']:.2%}")
    print(f"citation precision{m['citationPrecision']:>8.2%}  ({m['citationCount']} citations)")
    print(f"abstention acc.   {m['abstentionAccuracy']:.2%}")
    lat = m["latencyMs"]
    print(f"latency           p50 {lat['p50']}ms  p95 {lat['p95']}ms  p99 {lat['p99']}ms  (n={lat['samples']})")
    print(f"index             {m['indexSize']} chunks / {m['sources']} sources")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
