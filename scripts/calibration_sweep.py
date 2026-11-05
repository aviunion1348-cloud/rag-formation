#!/usr/bin/env python3
"""Abstention-threshold sensitivity, computed from an evaluation report.

Purpose: make the threshold decision auditable instead of anecdotal. The script
reads the per-case ``rows`` already produced by ``python3 -m ragformation eval``
(so it costs no engine runs and cannot drift from the report), then sweeps the
threshold on several subsets of the golden v2 split.

The decision rule this script exists to serve is *not* "pick the threshold with the
best number on the largest subset". It is:

  1. choose the threshold on **calibration + dev** only;
  2. report what it costs on test, and what it costs on off-scope cases;
  3. never move the threshold in response to a test or holdout case.

Run:
    python3 scripts/calibration_sweep.py \
        --report docs/final_qualification/golden_v2_report.json \
        --out docs/final_qualification/evidence/calibration_sweep.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

FROZEN_THRESHOLD = None  # filled from the report's own abstention block


def sweep(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    answerable = [row["support"] for row in rows if not row["expectAbstain"] and row["support"] is not None]
    unanswerable = [row["support"] for row in rows if row["expectAbstain"] and row["support"] is not None]
    if not answerable or not unanswerable:
        return {"answerable": len(answerable), "unanswerable": len(unanswerable), "usable": False}

    best: Optional[Dict[str, Any]] = None
    curve: List[Dict[str, Any]] = []
    step = 0.01
    for index in range(5, 96):
        threshold = round(index * step, 4)
        true_abstain = sum(1 for value in unanswerable if value < threshold) / len(unanswerable)
        true_answer = sum(1 for value in answerable if value >= threshold) / len(answerable)
        balanced = (true_abstain + true_answer) / 2
        curve.append(
            {
                "threshold": threshold,
                "balancedAccuracy": round(balanced, 4),
                "abstentionRecall": round(true_abstain, 4),
                "answerRetention": round(true_answer, 4),
            }
        )
        if best is None or balanced > best["balancedAccuracy"]:
            best = curve[-1]

    assert best is not None
    return {
        "usable": True,
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "supportRangeAnswerable": [round(min(answerable), 4), round(max(answerable), 4)],
        "supportRangeUnanswerable": [round(min(unanswerable), 4), round(max(unanswerable), 4)],
        "separation": round(min(answerable) - max(unanswerable), 4),
        "bestThreshold": best["threshold"],
        "bestBalancedAccuracy": best["balancedAccuracy"],
        "curve": curve,
    }


def at_threshold(rows: Sequence[Dict[str, Any]], threshold: float) -> Dict[str, Any]:
    answerable = [row for row in rows if not row["expectAbstain"] and row["support"] is not None]
    unanswerable = [row for row in rows if row["expectAbstain"] and row["support"] is not None]
    kept = [row["id"] for row in answerable if row["support"] >= threshold]
    lost = [row["id"] for row in answerable if row["support"] < threshold]
    caught = [row["id"] for row in unanswerable if row["support"] < threshold]
    leaked = [row["id"] for row in unanswerable if row["support"] >= threshold]
    return {
        "threshold": threshold,
        "balancedAccuracy": round(
            (
                (len(caught) / len(unanswerable) if unanswerable else 1.0)
                + (len(kept) / len(answerable) if answerable else 1.0)
            )
            / 2,
            4,
        ),
        "answerableKept": len(kept),
        "answerableLost": lost,
        "unanswerableCaught": caught,
        "unanswerableLeaked": leaked,
    }


def measure_margins(case_ids: Sequence[str], dataset_path: str, data_dir: str) -> List[Dict[str, Any]]:
    """Best-vs-second support and question coverage for named cases.

    Runs the real engine (hashing embeddings unless ``RAG_EMBED_PROVIDER`` says
    otherwise) against an already-ingested index. Used to test the claim "no
    second signal separates the off-scope case either".
    """

    import os
    import sys

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("RAG_DATA_DIR", data_dir)
    from ragformation.config import Settings
    from ragformation.engine import RAGEngine
    from ragformation.retrieval import coverage_score

    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    questions = {case["id"]: case["question"] for case in dataset["cases"]}
    settings = Settings.from_env()
    rows: List[Dict[str, Any]] = []
    with RAGEngine(settings) as engine:
        for case_id in case_ids:
            question = questions.get(case_id)
            if not question:
                continue
            answer = engine.ask(question, save=False)
            supports = [float(item.get("support") or 0.0) for item in answer.evidence]
            best = supports[0] if supports else None
            second = supports[1] if len(supports) > 1 else None
            best_excerpt = (
                answer.evidence[0].get("text") or answer.evidence[0].get("excerpt") or ""
            ) if answer.evidence else ""
            rows.append(
                {
                    "id": case_id,
                    "status": answer.status,
                    "bestSupport": None if best is None else round(best, 4),
                    "secondSupport": None if second is None else round(second, 4),
                    "margin": None if best is None or second is None else round(best - second, 4),
                    "questionCoverageOfBestChunk": round(coverage_score(best_excerpt, question), 4),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="docs/final_qualification/golden_v2_report.json")
    parser.add_argument("--out", default="docs/final_qualification/evidence/calibration_sweep.json")
    parser.add_argument("--margins", action="store_true", help="also measure best/second support per case")
    parser.add_argument("--dataset", default="evals/golden_v2.json")
    parser.add_argument(
        "--cases",
        default="t23-off-scope,t01-paraphrase-audit,t02-paraphrase-leaver,t03-paraphrase-rehearsal,c06-open-actions",
    )
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    rows = report["rows"]
    frozen = float(report["abstention"]["threshold"])

    subsets = {
        "all": rows,
        "calibration": [row for row in rows if row.get("split") == "calibration"],
        "calibration+dev": [row for row in rows if row.get("split") in {"calibration", "dev"}],
        "test": [row for row in rows if row.get("split") == "test"],
    }

    result: Dict[str, Any] = {
        "report": args.report,
        "datasetRevision": report.get("datasetRevision"),
        "frozenThreshold": frozen,
        "decision": (
            "Threshold stays at the value frozen before the holdouts (see release_decision.md section 4). "
            "The test-only optimum is reported for completeness and is explicitly NOT adopted: choosing it "
            "would be fitting the threshold to the test split."
        ),
        "sweeps": {},
        "frozenThresholdBySplit": {},
    }
    for name, subset in subsets.items():
        result["sweeps"][name] = sweep(subset)
        result["frozenThresholdBySplit"][name] = at_threshold(subset, frozen)

    # What would it take to abstain the documented off-scope failure, and what
    # does that cost elsewhere? This is the number that decides D1 in
    # acceptance_matrix.md.
    off_scope = [row for row in rows if row["id"] == "t23-off-scope"]
    if off_scope and off_scope[0]["support"] is not None:
        needed = round(off_scope[0]["support"] + 0.001, 4)
        result["offScopeCase"] = {
            "id": "t23-off-scope",
            "support": off_scope[0]["support"],
            "thresholdNeededToAbstain": needed,
            "costAtThatThreshold": at_threshold(rows, needed),
        }

    if args.margins:
        result["margins"] = measure_margins(
            [part.strip() for part in args.cases.split(",") if part.strip()],
            args.dataset,
            __import__("os").environ.get("RAG_DATA_DIR", "data"),
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    for name, data in result["sweeps"].items():
        if not data.get("usable"):
            print(f"{name:16s} not usable (needs both classes)")
            continue
        print(
            f"{name:16s} answerable={data['answerable']:2d} unanswerable={data['unanswerable']:2d} "
            f"best={data['bestThreshold']:.2f} bal={data['bestBalancedAccuracy']:.4f} "
            f"frozen={frozen:.2f} bal={result['frozenThresholdBySplit'][name]['balancedAccuracy']:.4f} "
            f"separation={data['separation']:+.4f}"
        )
    if "offScopeCase" in result:
        case = result["offScopeCase"]
        cost = case["costAtThatThreshold"]
        print(
            f"t23-off-scope support={case['support']:.3f} needs threshold>{case['thresholdNeededToAbstain']:.3f}; "
            f"at that threshold answerable lost={cost['answerableLost']}"
        )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
