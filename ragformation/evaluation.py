"""Golden-set evaluation: retrieval and generation measured separately.

Method (stated here because numbers without a method are noise):

* Relevance labels come from ``evals/golden.json`` and are *document level*:
  a retrieved chunk counts as relevant when it belongs to an expected source.
* ``recall@k``  fraction of answerable questions with at least one relevant
  chunk in the top k. ``MRR@k`` uses the rank of the first relevant chunk.
  ``nDCG@k`` uses binary gains. ``abstain accuracy`` covers the unanswerable
  questions.
* Generation is scored with deterministic checks (exact facts that must appear,
  facts that must not appear) plus citation validity. There is no LLM judge in
  the default run, so nothing here is a subjective claim of quality.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .engine import RAGEngine
from .metrics import summarize

DEFAULT_GOLDEN = Path(__file__).resolve().parent.parent / "evals" / "golden.json"


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _matches_expected_source(chunk: Dict[str, Any], expected: Sequence[str], sources: Dict[str, Any]) -> bool:
    """A chunk matches when its source's origin or title mentions an expected name."""
    source = sources.get(chunk["sourceId"], {})
    haystack = _normalize(
        " ".join(
            str(source.get(key, ""))
            for key in ("title", "origin", "sourceId")
        )
        + " "
        + str(chunk.get("sourceTitle", ""))
    )
    return any(_normalize(name) in haystack for name in expected)


def _ndcg(ranked_relevance: Sequence[int], k: int) -> float:
    gains = [rel for rel in ranked_relevance[:k]]
    dcg = sum((2**rel - 1) / math.log2(index + 2) for index, rel in enumerate(gains))
    ideal = sorted(gains, reverse=True)
    idcg = sum((2**rel - 1) / math.log2(index + 2) for index, rel in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def run_evaluation(
    engine: RAGEngine,
    golden_path: Optional[str] = None,
    *,
    top_k: Optional[int] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    path = Path(golden_path) if golden_path else DEFAULT_GOLDEN
    if not path.exists():
        raise FileNotFoundError(f"golden set not found: {path}")
    with open(path, encoding="utf-8") as handle:
        golden = json.load(handle)

    cases = golden["cases"]
    k = top_k or engine.settings.top_k
    sources = {source["sourceId"]: source for source in engine.list_sources()}

    rows: List[Dict[str, Any]] = []
    recalls: List[float] = []
    reciprocal_ranks: List[float] = []
    ndcgs: List[float] = []
    abstain_correct: List[int] = []
    must_contain_pass: List[int] = []
    must_not_contain_pass: List[int] = []
    citation_validity: List[float] = []
    latencies: List[float] = []
    ttfts: List[float] = []

    for case in cases:
        result = engine.ask(case["question"], top_k=k, mode=mode, save=False)
        answer = _normalize(result.answer or "")

        retrieved = [
            {
                "chunkId": item["chunkId"],
                "sourceId": item["sourceId"],
                "sourceTitle": item.get("sourceTitle", ""),
            }
            for item in result.evidence
        ]
        expected = case.get("expectedSources", [])
        relevance = [
            1 if _matches_expected_source(chunk, expected, sources) else 0 for chunk in retrieved
        ] if expected else []

        failures: List[str] = []
        if expected:
            recall = 1.0 if any(relevance) else 0.0
            first = next((index + 1 for index, rel in enumerate(relevance) if rel), None)
            reciprocal = 1.0 / first if first else 0.0
            ndcg = _ndcg(relevance, k)
            recalls.append(recall)
            reciprocal_ranks.append(reciprocal)
            ndcgs.append(ndcg)
            if not recall:
                failures.append(f"no expected source in top {k}")
        else:
            recall = reciprocal = ndcg = None

        if case.get("expectAbstain"):
            correct = result.status == "abstained"
            abstain_correct.append(1 if correct else 0)
            if not correct:
                failures.append(f"expected abstention, got {result.status}")
        elif result.status == "abstained":
            failures.append("abstained on an answerable question")

        contains = [term for term in case.get("mustContain", []) if _normalize(term) in answer]
        missing = [term for term in case.get("mustContain", []) if _normalize(term) not in answer]
        if case.get("mustContain"):
            must_contain_pass.append(1 if not missing else 0)
            if missing:
                failures.append(f"answer missing required fact(s): {missing}")

        forbidden = [term for term in case.get("mustNotContain", []) if _normalize(term) in answer]
        if case.get("mustNotContain"):
            must_not_contain_pass.append(0 if forbidden else 1)
            if forbidden:
                failures.append(f"answer contains forbidden text: {forbidden}")

        markers = [claim for claim in result.claims if claim.citations]
        claims = [claim for claim in result.claims if claim.text]
        if claims and result.status != "abstained":
            citation_validity.append(len(markers) / len(claims))

        metrics = result.metrics or {}
        if metrics.get("totalMs"):
            latencies.append(metrics["totalMs"])
        if metrics.get("ttftMs"):
            ttfts.append(metrics["ttftMs"])

        rows.append(
            {
                "id": case.get("id"),
                "question": case["question"],
                "status": result.status,
                "answer": result.answer[:400],
                "expectedSources": expected,
                "retrievedSources": sorted({item["sourceTitle"] for item in retrieved}),
                "recallAtK": recall,
                "reciprocalRank": reciprocal,
                "ndcgAtK": ndcg,
                "containsExpected": contains,
                "missingFacts": missing,
                "forbiddenFound": forbidden,
                "citations": [c.to_dict() for c in result.citations],
                "warnings": result.warnings,
                "passed": not failures,
                "failures": failures,
            }
        )

    def mean(values: Sequence[float]) -> Optional[float]:
        return round(sum(values) / len(values), 4) if values else None

    passed = sum(1 for row in rows if row["passed"])
    report = {
        "goldenSet": path.name,
        "corpusChunks": engine.store.counts()["chunks"],
        "questions": len(rows),
        "topK": k,
        "mode": mode or engine.settings.retrieval_mode,
        "retrieval": {
            "recallAtK": mean(recalls),
            "mrrAtK": mean(reciprocal_ranks),
            "ndcgAtK": mean(ndcgs),
            "labelledQuestions": len(recalls),
        },
        "generation": {
            "answered": sum(1 for row in rows if row["status"] == "answered"),
            "needsReview": sum(1 for row in rows if row["status"] == "needs_review"),
            "abstained": sum(1 for row in rows if row["status"] == "abstained"),
            "abstainAccuracy": mean([float(v) for v in abstain_correct]),
            "factRecall": mean([float(v) for v in must_contain_pass]),
            "forbiddenFactRate": mean([float(v) for v in must_not_contain_pass]),
            "citationCoverage": mean(citation_validity),
        },
        "latency": {
            "totalMs": summarize(latencies),
            "ttftMs": summarize(ttfts),
        },
        "casesPassed": passed,
        "casesTotal": len(rows),
        "passed": passed == len(rows),
        "cases": rows,
        "notice": golden.get("notice", ""),
        "provider": {
            "embedding": engine.embedder.fingerprint,
            "generation": f"{engine.generator.name}/{engine.generator.model}",
        },
    }
    return report


def write_report(report: Dict[str, Any], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
