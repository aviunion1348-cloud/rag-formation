#!/usr/bin/env python3
"""Compare retrieval quality on paraphrase probes across provider/mode setups.

Usage (from the repository root, data dir already ingested):

    python3 scripts/paraphrase_bench.py --label hashing --json out.json
    RAG_EMBED_PROVIDER=distributional python3 scripts/paraphrase_bench.py --label lsa

The metric is deliberately blunt and honest: is the expected source present in
the citations of the returned answer? Abstentions count as misses and are listed
so a lower abstention rate cannot flatter a retrieval channel.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ragformation import RAGConfig, RAGEngine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(ROOT / "evals" / "paraphrase.json"))
    parser.add_argument("--label", default="run")
    parser.add_argument("--json", dest="out", default="")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--modes", default="hybrid,lexical,semantic")
    args = parser.parse_args()

    cases = json.loads(Path(args.dataset).read_text())["cases"]
    config = RAGConfig.from_env_or_mapping({})
    engine = RAGEngine(config=config)
    health = engine.health()
    results = {"label": args.label, "embedding": health["embedding"], "modes": {}}
    try:
        for mode in args.modes.split(","):
            hits = 0
            abstained = 0
            latencies = []
            detail = []
            for case in cases:
                started = time.perf_counter()
                answer = engine.ask(case["question"], mode=mode, top_k=args.top_k)
                latencies.append((time.perf_counter() - started) * 1000.0)
                titles = [c.source_title for c in answer.citations]
                sources = [c.source_id for c in answer.citations]
                hit = case["expectedSource"] in titles or any(
                    case["expectedSource"] in (title or "") for title in titles
                )
                if answer.status == "abstained":
                    abstained += 1
                hits += int(hit)
                detail.append(
                    {
                        "id": case["id"],
                        "status": answer.status,
                        "hit": hit,
                        "citations": sources,
                        "support": answer.metrics.get("bestSupport"),
                    }
                )
            results["modes"][mode] = {
                "cases": len(cases),
                "hits": hits,
                "hitRate": round(hits / len(cases), 3),
                "abstained": abstained,
                "latencyMs": {
                    "mean": round(statistics.fmean(latencies), 2),
                    "p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2),
                },
                "detail": detail,
            }
    finally:
        engine.close()

    for mode, stats in results["modes"].items():
        print(f"{args.label:>14} {mode:<9} hit={stats['hits']}/{stats['cases']} "
              f"rate={stats['hitRate']:.3f} abstained={stats['abstained']} "
              f"mean={stats['latencyMs']['mean']:.1f}ms")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))
        print("written:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
