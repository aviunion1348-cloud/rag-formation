#!/usr/bin/env python3
"""Benchmark retrieval configurations on the same corpus and the same queries.

Configurations are (embedding provider x retrieval mode). Every configuration
ingests the identical corpus into its own throw-away data directory, then runs:

* golden v1 (14 labelled questions: recall/MMR/nDCG),
* golden v2 (46 cases with per-category pass/fail),
* the paraphrase and exact-id/structured subsets separately, because that is
  where a semantic provider is supposed to differ from a lexical one,
* an off-corpus group (cases whose label is "must abstain"), to see whether a
  richer representation also improves *refusal*,
* latency and a determinism re-run.

Nothing here changes the engine: it selects providers and modes that already
exist, so the numbers describe shipped behaviour.

    python scripts/bench_semantic_providers.py --out docs/final_qualification/semantic_bench.json
"""

from __future__ import annotations

import argparse
import json
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
from ragformation.embeddings import describe  # noqa: E402
from ragformation.engine import RAGEngine  # noqa: E402
from ragformation import evaluation, evaluation_v2  # noqa: E402

CONFIGS: List[Dict[str, Any]] = [
    {
        "name": "lexical-bm25",
        "embed_provider": "hashing",
        "retrieval_mode": "lexical",
        "note": "BM25 only, no vectors. Control for 'what does ranking without embeddings buy?'",
    },
    {
        "name": "dense-hashing",
        "embed_provider": "hashing",
        "retrieval_mode": "semantic",
        "note": "Feature-hashed bag-of-words as the 'dense' channel: lexical by construction.",
    },
    {
        "name": "dense-lsa",
        "embed_provider": "distributional",
        "retrieval_mode": "semantic",
        "note": "TF-IDF + truncated SVD (LSA) fitted on the corpus: distributional but not pretrained.",
    },
    {
        "name": "hybrid-hashing",
        "embed_provider": "hashing",
        "retrieval_mode": "hybrid",
        "note": "Shipped default: RRF fusion of BM25 and hashed vectors.",
    },
    {
        "name": "hybrid-lsa",
        "embed_provider": "distributional",
        "retrieval_mode": "hybrid",
        "note": "RRF fusion of BM25 and corpus-fitted LSA vectors.",
    },
]


def corpus_files() -> List[str]:
    return sorted(str(path) for path in (ROOT / "corpus").glob("*") if path.is_file())


def summarise(rows: List[Dict[str, Any]], category: str | None = None) -> Dict[str, Any]:
    subset = [row for row in rows if category is None or row.get("category") == category]
    if not subset:
        return {"cases": 0}
    abstain_expected = [row for row in subset if row.get("expectAbstain")]
    answerable = [row for row in subset if not row.get("expectAbstain")]
    return {
        "cases": len(subset),
        "passed": sum(1 for row in subset if row["passed"]),
        "passRate": round(sum(1 for row in subset if row["passed"]) / len(subset), 4),
        "abstainCases": len(abstain_expected),
        "abstainCorrect": sum(1 for row in subset if row.get("expectAbstain") and row["status"] == "abstained"),
        "answerableCases": len(answerable),
        "answerableAnswered": sum(1 for row in subset if not row.get("expectAbstain") and row["status"] != "abstained"),
    }


def run_config(spec: Dict[str, Any], dataset_v2: Path, repeats: int) -> Dict[str, Any]:
    data_dir = Path(tempfile.mkdtemp(prefix=f"rsem-{spec['name']}-"))
    settings = replace(
        Settings.from_env(),
        data_dir=data_dir,
        embed_provider=spec["embed_provider"],
        retrieval_mode=spec["retrieval_mode"],
        llm_provider="extractive",
    )
    engine = RAGEngine(settings)
    started = time.perf_counter()
    report = engine.ingest_path(corpus_files())
    ingest_ms = round((time.perf_counter() - started) * 1000.0, 1)

    record: Dict[str, Any] = {
        "name": spec["name"],
        "note": spec["note"],
        "embedProvider": settings.embed_provider,
        "retrievalMode": settings.retrieval_mode,
        "embedding": describe(engine.embedder),
        "semantic": bool(getattr(engine.embedder, "semantic", False)),
        "ingestMs": ingest_ms,
        "sources": len(report.sources) if hasattr(report, "sources") else None,
    }

    # ---------------------------------------------------------------- golden v1
    v1 = evaluation.run_evaluation(engine, golden_path=str(ROOT / "evals" / "golden.json"))
    v1_retrieval = v1.get("retrieval") or {}
    record["v1"] = {
        "recallAtK": v1_retrieval.get("recallAtK"),
        "mrrAtK": v1_retrieval.get("mrrAtK"),
        "ndcgAtK": v1_retrieval.get("ndcgAtK"),
        "casesPassed": v1.get("casesPassed"),
        "casesTotal": v1.get("casesTotal"),
        "passed": v1.get("passed"),
        "generation": v1.get("generation"),
    }

    # ---------------------------------------------------------------- golden v2
    v2 = evaluation_v2.run_evaluation(engine, dataset_path=str(dataset_v2), ablations=False)
    rows = v2["rows"]
    record["v2"] = {
        "cases": len(rows),
        "passed": sum(1 for row in rows if row["passed"]),
        "passRate": round(sum(1 for row in rows if row["passed"]) / len(rows), 4),
        "retrieval": v2.get("retrieval"),
        "generation": v2.get("generation"),
        "splits": v2.get("splits"),
    }
    record["byCategory"] = {
        category: summarise(rows, category)
        for category in sorted({row.get("category") for row in rows if row.get("category")})
    }
    record["offCorpusGroup"] = summarise([row for row in rows if row.get("expectAbstain")])

    # ---------------------------------------------------------------- latency
    questions = [row["question"] for row in rows]
    latencies: List[float] = []
    for _ in range(repeats):
        for question in questions:
            started = time.perf_counter()
            engine.ask(question)
            latencies.append((time.perf_counter() - started) * 1000.0)
    latencies.sort()
    record["latencyMs"] = {
        "samples": len(latencies),
        "p50": round(statistics.median(latencies), 2),
        "p95": round(latencies[int(len(latencies) * 0.95) - 1], 2),
        "mean": round(statistics.fmean(latencies), 2),
    }

    # ------------------------------------------------------------ determinism
    probes = [rows[0]["question"], rows[len(rows) // 2]["question"]]
    first = [engine.ask(question).answer for question in probes]
    second = [engine.ask(question).answer for question in probes]
    record["deterministic"] = first == second

    engine.close()
    record["indexSizeBytes"] = round(
        sum(path.stat().st_size for path in data_dir.rglob("*") if path.is_file()) / 1024.0, 1
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(ROOT / "evals" / "golden_v2.json"))
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "final_qualification" / "semantic_bench.json")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()

    results = []
    for spec in CONFIGS:
        sys.stderr.write(f"running {spec['name']} ...\n")
        results.append(run_config(spec, Path(args.dataset), args.repeats))

    payload = {"corpusChunks": None, "configurations": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    header = f"{'config':16}{'v2 pass':>9}{'v1 pass':>9}{'v1 ndcg':>9}{'paraphr':>9}{'exact+struct':>14}{'abstain':>9}{'p50 ms':>8}"
    print(header)
    for record in results:
        para = record["byCategory"].get("paraphrase", {})
        exact = record["byCategory"].get("exact-id", {}).get("passed", 0) + record["byCategory"].get(
            "structured", {}
        ).get("passed", 0)
        exact_total = record["byCategory"].get("exact-id", {}).get("cases", 0) + record["byCategory"].get(
            "structured", {}
        ).get("cases", 0)
        off = record["offCorpusGroup"]
        print(
            f"{record['name']:16}{record['v2']['passed']:>4}/{record['v2']['cases']:<4}"
            f"{record['v1']['casesPassed']:>4}/{record['v1']['casesTotal']:<4}"
            f"{(record['v1']['ndcgAtK'] or 0.0):>9.4f}"
            f"{para.get('passed', 0):>4}/{para.get('cases', 0):<4}"
            f"{exact:>7}/{exact_total:<5}"
            f"{off.get('abstainCorrect', 0):>4}/{off.get('cases', 0):<4}"
            f"{record['latencyMs']['p50']:>8.1f}"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
