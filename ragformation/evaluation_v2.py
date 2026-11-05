"""Evaluation v2: versioned dataset, explicit splits, ablations, calibration.

Differences from the v1 runner (``evaluation.py``, kept unchanged so the original
14-case baseline stays comparable):

* the dataset is versioned and every case declares a ``split`` and ``category``;
* retrieval metrics include precision@k, hit rate, source coverage and the
  duplicate-chunk rate, not just recall/MRR/nDCG;
* generation metrics separate *support* (required facts present) from *safety*
  (forbidden facts absent) and citation validity;
* abstention is scored as a classifier (precision, recall, false-answer rate,
  false-abstention rate) per split;
* conflict cases are scored separately (did the engine mark ``needs_review`` and
  cite both sides?);
* pipeline ablations run the same cases through dense-only, lexical-only, hybrid,
  hybrid+rerank and hybrid+rerank+MMR retrieval;
* threshold calibration reports the support-score distribution for answerable vs
  unanswerable cases and the threshold that maximises balanced accuracy.

Nothing here is an LLM judge. Every number is a deterministic function of the
engine's own output, the labels, and the elapsed time.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .engine import RAGEngine
from .metrics import summarize

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "evals" / "golden_v2.json"


def _normalise(text: str) -> str:
    return " ".join((text or "").lower().split())


def _source_matches(item: Dict[str, Any], expected: Sequence[str], sources: Dict[str, Any]) -> bool:
    if not expected:
        return False
    source = sources.get(item.get("sourceId"), {})
    haystack = _normalise(
        " ".join(
            [
                str(item.get("sourceTitle", "")),
                str(source.get("title", "")),
                str(source.get("origin", "")),
                str(item.get("sourceId", "")),
            ]
        )
    )
    return any(_normalise(name) in haystack for name in expected)


def _precision_at_k(relevance: Sequence[int], k: int) -> float:
    window = relevance[:k]
    return sum(window) / len(window) if window else 0.0


def _ndcg(relevance: Sequence[int], k: int) -> float:
    gains = list(relevance[:k])
    dcg = sum((2**rel - 1) / math.log2(index + 2) for index, rel in enumerate(gains))
    ideal = sorted(gains, reverse=True)
    idcg = sum((2**rel - 1) / math.log2(index + 2) for index, rel in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [float(v) for v in values if v is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def _retrieval_score(
    engine: RAGEngine,
    case: Dict[str, Any],
    sources: Dict[str, Any],
    *,
    k: int,
    mode: Optional[str] = None,
    rerank: Optional[bool] = None,
    mmr: bool = True,
) -> Dict[str, Any]:
    from .retrieval import dedupe_overlaps

    vector = engine.embedder.embed_one(case["question"])
    result = engine.retriever.search(
        case["question"], vector, top_k=k, mode=mode, rerank=rerank, mmr=mmr
    )
    candidates = dedupe_overlaps(result.candidates)[:k]
    relevance = [1 if _source_matches(c.to_dict(include_text=False), case.get("expectedSources", []), sources) else 0 for c in candidates]
    order = [c.chunk.chunk_id for c in candidates]
    return {
        "relevance": relevance,
        "chunkIds": order,
        "duplicates": len(order) - len(set(order)),
        "recall": 1.0 if any(relevance) else 0.0,
        "precision": _precision_at_k(relevance, k),
        "hit": 1.0 if any(relevance) else 0.0,
        "reciprocalRank": next((1.0 / (i + 1) for i, rel in enumerate(relevance) if rel), 0.0),
        "ndcg": _ndcg(relevance, k),
        "sourceCoverage": len({candidates[i].chunk.source_id for i, rel in enumerate(relevance) if rel}),
    }


def _aggregate_retrieval(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    labelled = [row for row in rows if row["expectedCount"]]
    if not labelled:
        return {"labelled": 0}
    return {
        "labelled": len(labelled),
        "recallAtK": _mean([row["retrieval"]["recall"] for row in labelled]),
        "precisionAtK": _mean([row["retrieval"]["precision"] for row in labelled]),
        "hitRateAtK": _mean([row["retrieval"]["hit"] for row in labelled]),
        "mrrAtK": _mean([row["retrieval"]["reciprocalRank"] for row in labelled]),
        "ndcgAtK": _mean([row["retrieval"]["ndcg"] for row in labelled]),
        "duplicateRate": round(
            sum(row["retrieval"]["duplicates"] for row in labelled)
            / max(1, sum(len(row["retrieval"]["chunkIds"]) for row in labelled)),
            4,
        ),
        "sourceCoverage": _mean([row["retrieval"]["sourceCoverage"] for row in labelled]),
    }


def _ablation(engine: RAGEngine, cases: Sequence[Dict[str, Any]], sources: Dict[str, Any], k: int) -> Dict[str, Any]:
    # Every row pins rerank and MMR explicitly. Leaving them unset inherited the
    # engine defaults (rerank on, MMR on), which made the hybrid rows identical
    # and reported the same pipeline three times.
    configurations = [
        ("dense", {"mode": "semantic", "rerank": False, "mmr": False}),
        ("lexical", {"mode": "lexical", "rerank": False, "mmr": False}),
        ("hybrid", {"mode": "hybrid", "rerank": False, "mmr": False}),
        ("hybrid_rerank", {"mode": "hybrid", "rerank": True, "mmr": False}),
        ("hybrid_rerank_mmr", {"mode": "hybrid", "rerank": True, "mmr": True}),
    ]
    output: Dict[str, Any] = {}
    for name, options in configurations:
        rows = []
        for case in cases:
            if not case.get("expectedSources"):
                continue
            rows.append(_retrieval_score(engine, case, sources, k=k, **options))
        hits = sum(row["hit"] for row in rows)
        output[name] = {
            "cases": len(rows),
            "recall": round(hits / len(rows), 4) if rows else None,
            "mrr": _mean([row["reciprocalRank"] for row in rows]),
            "ndcg": _mean([row["ndcg"] for row in rows]),
            "precision": _mean([row["precision"] for row in rows]),
        }
    return output


def _calibration(rows: Sequence[Dict[str, Any]], split: Optional[str] = None) -> Dict[str, Any]:
    answerable = [
        row["support"] for row in rows
        if (split is None or row.get("split") == split)
        and not row.get("expectAbstain")
        and row["support"] is not None
    ]
    unanswerable = [
        row["support"] for row in rows
        if (split is None or row.get("split") == split)
        and row.get("expectAbstain")
        and row["support"] is not None
    ]
    if not answerable or not unanswerable:
        return {"answerable": len(answerable), "unanswerable": len(unanswerable)}

    best: Optional[Tuple[float, float, float, float]] = None
    for candidate in [i / 100 for i in range(5, 96)]:
        true_abstain = sum(1 for value in unanswerable if value < candidate) / len(unanswerable)
        true_answer = sum(1 for value in answerable if value >= candidate) / len(answerable)
        balanced = (true_abstain + true_answer) / 2
        if best is None or balanced > best[0]:
            best = (balanced, candidate, true_abstain, true_answer)
    assert best is not None
    return {
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "answerableRange": [round(min(answerable), 4), round(max(answerable), 4)],
        "unanswerableRange": [round(min(unanswerable), 4), round(max(unanswerable), 4)],
        "bestThreshold": round(best[1], 3),
        "balancedAccuracy": round(best[0], 4),
        "abstentionRecall": round(best[2], 4),
        "answerRetention": round(best[3], 4),
        "gap": round(min(answerable) - max(unanswerable), 4),
    }


def run_evaluation(
    engine: RAGEngine,
    dataset_path: Optional[str] = None,
    *,
    top_k: Optional[int] = None,
    splits: Optional[Iterable[str]] = None,
    ablations: bool = True,
) -> Dict[str, Any]:
    path = Path(dataset_path) if dataset_path else DEFAULT_DATASET
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")
    dataset = json.loads(path.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    selected_splits = set(splits or [])
    if selected_splits:
        cases = [case for case in cases if case.get("split") in selected_splits]

    k = top_k or engine.settings.top_k
    sources = {source["sourceId"]: source for source in engine.list_sources()}
    rows: List[Dict[str, Any]] = []
    started_all = time.perf_counter()

    for case in cases:
        started = time.perf_counter()
        answer = engine.ask(case["question"], top_k=k, save=False)
        elapsed = (time.perf_counter() - started) * 1000.0
        text = _normalise(answer.answer)
        citations = [c.to_dict() for c in answer.citations]
        retrieved = [
            {
                "chunkId": item["chunkId"],
                "sourceId": item["sourceId"],
                "sourceTitle": item.get("sourceTitle", ""),
            }
            for item in answer.evidence
        ]
        expected = case.get("expectedSources", [])
        relevance = [1 if _source_matches(item, expected, sources) else 0 for item in retrieved]
        order = [item["chunkId"] for item in retrieved]

        missing = [term for term in case.get("mustContain", []) if _normalise(term) not in text]
        forbidden = [term for term in case.get("mustNotContain", []) if _normalise(term) in text]

        markers = sum(1 for claim in answer.claims if claim.citations)
        claim_count = max(1, sum(1 for claim in answer.claims if claim.text))
        citation_validity = markers / claim_count
        citation_precision = (
            sum(1 for c in citations if c.get("valid")) / len(citations) if citations else 0.0
        )
        location_valid = sum(1 for c in citations if c.get("section") or c.get("page")) / len(citations) if citations else 0.0

        expect_abstain = bool(case.get("expectAbstain"))
        conflict_expected = bool(case.get("expectConflict"))
        conflict_actual = bool(answer.conflict)
        failures: List[str] = []
        if expected and relevance and not any(relevance):
            failures.append(f"no expected source in top {k}")
        if case.get("expectedStatus") and answer.status != case["expectedStatus"]:
            failures.append(f"expected status {case['expectedStatus']}, got {answer.status}")
        if expect_abstain and answer.status != "abstained":
            failures.append("answered an unanswerable question")
        if not expect_abstain and answer.status == "abstained":
            failures.append("abstained on an answerable question")
        if missing:
            failures.append(f"missing facts: {missing}")
        if forbidden:
            failures.append(f"forbidden text present: {forbidden}")
        if conflict_expected and not conflict_actual:
            failures.append("conflict not surfaced")
        if case.get("expectConflict") is False and conflict_actual:
            failures.append("false conflict")
        if expect_abstain and answer.status == "abstained":
            failures = [f for f in failures if "unanswerable" not in f]

        rows.append(
            {
                "id": case["id"],
                "split": case.get("split"),
                "category": case.get("category"),
                "question": case["question"],
                "status": answer.status,
                "answer": (answer.answer or "")[:400],
                "expectAbstain": expect_abstain,
                "expectConflict": case.get("expectConflict"),
                "conflictDetected": conflict_actual,
                "support": (answer.metrics or {}).get("bestSupport"),
                "expectedCount": len(expected),
                "expectedSources": expected,
                "retrievedSources": sorted({item["sourceTitle"] for item in retrieved}),
                "retrieval": {
                    "relevance": relevance,
                    "chunkIds": order,
                    "duplicates": len(order) - len(set(order)),
                    "recall": 1.0 if any(relevance) else 0.0,
                    "precision": _precision_at_k(relevance, k),
                    "hit": 1.0 if any(relevance) else 0.0,
                    "reciprocalRank": next((1.0 / (i + 1) for i, rel in enumerate(relevance) if rel), 0.0),
                    "ndcg": _ndcg(relevance, k),
                    "sourceCoverage": len({retrieved[i]["sourceId"] for i, rel in enumerate(relevance) if rel}),
                },
                "generation": {
                    "factRecall": 1.0 if not missing else 0.0,
                    "forbiddenFree": 1.0 if not forbidden else 0.0,
                    "citationCoverage": citation_validity,
                    "citationPrecision": citation_precision,
                    "citationLocationValidity": location_valid,
                },
                "citations": citations,
                "latencyMs": round(elapsed, 3),
                "warnings": answer.warnings,
                "failures": failures,
                "passed": not failures,
            }
        )

    # ------------------------------------------------------------- aggregates
    answerable_rows = [row for row in rows if not row["expectAbstain"]]
    unanswerable_rows = [row for row in rows if row["expectAbstain"]]
    conflict_rows = [row for row in rows if row["expectConflict"] is not None]
    answered_unanswerable = [row for row in unanswerable_rows if row["status"] != "abstained"]
    abstained_answerable = [row for row in answerable_rows if row["status"] == "abstained"]

    by_split: Dict[str, Any] = {}
    for split in sorted({row["split"] for row in rows if row["split"]}):
        subset = [row for row in rows if row["split"] == split]
        by_split[split] = {
            "cases": len(subset),
            "passed": sum(1 for row in subset if row["passed"]),
            "retrieval": _aggregate_retrieval(subset),
            "abstained": sum(1 for row in subset if row["status"] == "abstained"),
            "needsReview": sum(1 for row in subset if row["status"] == "needs_review"),
        }

    by_category: Dict[str, Any] = {}
    for category in sorted({row["category"] for row in rows if row["category"]}):
        subset = [row for row in rows if row["category"] == category]
        by_category[category] = {
            "cases": len(subset),
            "passed": sum(1 for row in subset if row["passed"]),
            "failureIds": [row["id"] for row in subset if not row["passed"]],
        }

    report = {
        "dataset": path.name,
        "datasetRevision": dataset.get("revision"),
        "corpusChunks": engine.store.counts()["chunks"],
        "corpusSources": engine.store.counts()["sources"],
        "questions": len(rows),
        "topK": k,
        "retrievalMode": engine.settings.retrieval_mode,
        "provider": {
            "embedding": engine.embedder.fingerprint,
            "embeddingSemantic": engine.embedder.semantic,
            "generation": f"{engine.generator.name}/{engine.generator.model}",
            "generationMode": engine._event_mode(),
        },
        "retrieval": _aggregate_retrieval(rows),
        "generation": {
            "answered": sum(1 for row in rows if row["status"] == "answered"),
            "needsReview": sum(1 for row in rows if row["status"] == "needs_review"),
            "abstained": sum(1 for row in rows if row["status"] == "abstained"),
            "factRecall": _mean([row["generation"]["factRecall"] for row in answerable_rows]),
            "forbiddenFreeRate": _mean([row["generation"]["forbiddenFree"] for row in rows]),
            "citationCoverage": _mean([row["generation"]["citationCoverage"] for row in answerable_rows]),
            "citationPrecision": _mean([row["generation"]["citationPrecision"] for row in rows if row["citations"]]),
            "citationLocationValidity": _mean([row["generation"]["citationLocationValidity"] for row in rows if row["citations"]]),
        },
        "abstention": {
            "answerable": len(answerable_rows),
            "unanswerable": len(unanswerable_rows),
            "abstentionRecall": (
                round((len(unanswerable_rows) - len(answered_unanswerable)) / len(unanswerable_rows), 4)
                if unanswerable_rows
                else None
            ),
            "falseAnswerRate": round(len(answered_unanswerable) / len(unanswerable_rows), 4) if unanswerable_rows else None,
            "falseAbstentionRate": round(len(abstained_answerable) / len(answerable_rows), 4) if answerable_rows else None,
            "falseAbstentionIds": [row["id"] for row in abstained_answerable],
            "falseAnswerIds": [row["id"] for row in answered_unanswerable],
            "threshold": engine.settings.abstain_threshold,
        },
        "conflicts": {
            "cases": len(conflict_rows),
            "detected": sum(1 for row in conflict_rows if row["conflictDetected"]),
            "precision": round(
                sum(1 for row in conflict_rows if row["conflictDetected"] and row["expectConflict"]) /
                max(1, sum(1 for row in conflict_rows if row["conflictDetected"])), 4
            ),
            "recall": round(
                sum(1 for row in conflict_rows if row["conflictDetected"] and row["expectConflict"]) /
                max(1, sum(1 for row in conflict_rows if row["expectConflict"])), 4
            ),
        },
        "calibration": _calibration(rows),
        "bySplit": by_split,
        "byCategory": by_category,
        "latency": {
            "totalMs": summarize([row["latencyMs"] for row in rows]),
            "wallMs": round((time.perf_counter() - started_all) * 1000.0, 1),
        },
        "casesPassed": sum(1 for row in rows if row["passed"]),
        "casesTotal": len(rows),
        "failedIds": [row["id"] for row in rows if not row["passed"]],
        "rows": rows,
        "notice": dataset.get("notice", ""),
    }
    if ablations:
        labelled = [case for case in cases if case.get("expectedSources")]
        try:
            report["ablations"] = _ablation(engine, labelled, sources, k)
        except Exception as exc:  # pragma: no cover - defensive
            report["ablations"] = {"error": str(exc)[:200]}
    return report


def write_report(report: Dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
