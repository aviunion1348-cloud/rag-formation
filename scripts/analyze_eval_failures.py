#!/usr/bin/env python3
"""Diagnose every evaluation-v2 failure against the actual index and answer.

For each failing case this reports, per expected fact:
  * whether the fact exists in the corpus at all (and in which chunk/section),
  * the rank at which a chunk containing it was retrieved (or "not retrieved"),
  * whether the answer contains the fact,
  * whether the model abstained, answered, or flagged a conflict.

That is enough to separate "the evidence was never retrieved" (retrieval/embedding
problem) from "the evidence was retrieved but the generator quoted another
sentence" (generation/selection problem) from "the label asks for something the
corpus does not say" (fixture or label problem) without touching the dataset.

Usage:
    python scripts/analyze_eval_failures.py --out-dir /tmp/q-analysis
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragformation.config import Settings  # noqa: E402
from ragformation.engine import RAGEngine  # noqa: E402
from ragformation.evaluation_v2 import run_evaluation  # noqa: E402


def normalize(text: str) -> str:
    return " ".join((text or "").split()).lower()


def locate_fact(fact: str, chunks: List[Any]) -> List[Dict[str, Any]]:
    needle = normalize(fact)
    hits = []
    for chunk in chunks:
        haystack = normalize(chunk.text)
        if needle and needle in haystack:
            hits.append(
                {
                    "chunkId": chunk.chunk_id,
                    "sourceId": chunk.source_id,
                    "section": chunk.section,
                    "ordinal": chunk.ordinal,
                }
            )
    return hits


def analyze(report: Dict[str, Any], engine: RAGEngine, dataset: Dict[str, Any]) -> Dict[str, Any]:
    cases = {case["id"]: case for case in dataset["cases"]}
    chunks = engine.store.all_chunks()
    title_by_source = {chunk.source_id: chunk.source_title for chunk in chunks}

    dossier = []
    for row in report["rows"]:
        if row["passed"]:
            continue
        case = cases.get(row["id"], {})
        retrieved_ids = row.get("retrieval", {}).get("chunkIds") or []
        rank_by_id = {chunk_id: index + 1 for index, chunk_id in enumerate(retrieved_ids)}
        facts = []
        for fact in case.get("mustContain") or []:
            locations = locate_fact(fact, chunks)
            best_rank = min(
                (rank_by_id.get(location["chunkId"]) for location in locations if location["chunkId"] in rank_by_id),
                default=None,
            )
            facts.append(
                {
                    "fact": fact,
                    "inCorpus": bool(locations),
                    "chunks": locations[:4],
                    "bestRankInPool": best_rank,
                    "inAnswer": normalize(fact) in normalize(row["answer"]),
                }
            )
        forbidden = [
            {
                "fact": fact,
                "inAnswer": normalize(fact) in normalize(row["answer"]),
            }
            for fact in case.get("mustNotContain") or []
        ]

        # Classify without consulting the score: evidence availability decides.
        if not row["answer"] and row["status"] == "abstained":
            cause = "abstained_before_generation"
        elif any(not fact["inCorpus"] for fact in facts):
            cause = "expected_fact_not_in_corpus (label or fixture problem)"
        elif any(fact["bestRankInPool"] is None for fact in facts):
            cause = "fact_exists_but_chunk_not_retrieved (retrieval ranking)"
        elif any(not fact["inAnswer"] for fact in facts):
            cause = "fact_retrieved_but_not_quoted (answer selection)"
        else:
            cause = "other (inspect manually)"

        dossier.append(
            {
                "id": row["id"],
                "split": row["split"],
                "category": row["category"],
                "question": row["question"],
                "status": row["status"],
                "support": row["support"],
                "expectedSources": row.get("expectedSources"),
                "retrievedSources": row.get("retrievedSources"),
                "expectedCount": row.get("expectedCount"),
                "facts": facts,
                "forbidden": forbidden,
                "citations": [
                    {
                        "sourceTitle": citation.get("sourceTitle"),
                        "section": citation.get("section"),
                        "chunkId": citation.get("chunkId"),
                    }
                    for citation in row.get("citations") or []
                ],
                "answer": row["answer"],
                "failures": row["failures"],
                "warnings": row["warnings"],
                "cause": cause,
                "sourceTitles": {
                    source_id: title_by_source.get(source_id) for source_id in {c["sourceId"] for c in facts for c in c["chunks"]}
                },
            }
        )
    return {"cases": len(report["rows"]), "failed": len(dossier), "dossier": dossier}


def to_markdown(result: Dict[str, Any], meta: Dict[str, Any]) -> str:
    lines = [
        "# Evaluation v2 — failure dossier",
        "",
        f"- Dataset revision: `{meta['datasetRevision']}`",
        f"- Embedding: `{meta['embedding']}` (semantic: {meta['semantic']})",
        f"- Answer path: `{meta['generation']}`",
        f"- Cases: {result['cases']} · failed: {result['failed']}",
        "",
        "Each entry states whether the expected fact exists in the corpus, whether a chunk",
        "containing it reached the retrieval pool (and at what rank), and whether the generated",
        "answer quoted it. That separates a retrieval failure from an answer-selection failure",
        "from a bad label.",
        "",
    ]
    for item in result["dossier"]:
        lines.extend(
            [
                f"## {item['id']} — {item['question']}",
                "",
                f"- Split: `{item['split']}` · category: `{item['category']}` · status: **{item['status']}** · support {item['support']}",
                f"- Expected sources: {', '.join(item['expectedSources'] or []) or '—'}",
                f"- Retrieved sources: {', '.join((item['retrievedSources'] or [])[:8]) or '—'}",
                f"- Recorded failure(s): {'; '.join(item['failures']) or '—'}",
                f"- Warnings: {', '.join(item['warnings'] or []) or '—'}",
                f"- **Classified cause:** {item['cause']}",
                "",
                "| Expected fact | In corpus | Best rank in pool | In answer |",
                "| --- | --- | --- | --- |",
            ]
        )
        for fact in item["facts"]:
            lines.append(
                f"| `{fact['fact']}` | {'yes' if fact['inCorpus'] else 'NO'} | "
                f"{fact['bestRankInPool'] if fact['bestRankInPool'] is not None else 'not retrieved'} | "
                f"{'yes' if fact['inAnswer'] else 'no'} |"
            )
        for fact in item["forbidden"]:
            lines.append(f"| forbidden `{fact['fact']}` | — | — | {'PRESENT (bad)' if fact['inAnswer'] else 'absent (ok)'} |")
        lines.extend(["", "```text", (item["answer"] or "(empty answer)").strip()[:600], "```", ""])
        if item["citations"]:
            cited = ", ".join(f"{c['sourceTitle']} · {c['section']}" for c in item["citations"])
            lines.extend([f"Cited: {cited}", ""])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(ROOT / "evals" / "golden_v2.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/q-analysis"))
    parser.add_argument("--embed-provider", default="hashing")
    parser.add_argument("--splits", default="")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env()
    settings = Settings(**{**settings.__dict__, "embed_provider": args.embed_provider})
    engine = RAGEngine(settings)
    try:
        corpus = sorted(str(path) for path in (ROOT / "corpus").glob("*") if path.is_file())
        engine.ingest_path(corpus)
        splits = [s.strip() for s in args.splits.split(",") if s.strip()] or None
        report = run_evaluation(engine, dataset_path=args.dataset, splits=splits, ablations=False)
        dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
        result = analyze(report, engine, dataset)
        meta = {
            "datasetRevision": report["datasetRevision"],
            "embedding": report["provider"]["embedding"],
            "semantic": report["provider"]["embeddingSemantic"],
            "generation": report["provider"]["generation"],
        }
    finally:
        engine.close()

    (args.out_dir / "failure_dossier.json").write_text(
        json.dumps({"meta": meta, "result": result}, indent=2) + "\n", encoding="utf-8"
    )
    (args.out_dir / "failure_dossier.md").write_text(to_markdown(result, meta), encoding="utf-8")

    by_cause = defaultdict(int)
    for item in result["dossier"]:
        by_cause[item["cause"].split(" (")[0]] += 1
    print(f"failed: {result['failed']}/{result['cases']}")
    for cause, count in sorted(by_cause.items(), key=lambda kv: -kv[1]):
        print(f"  {count}  {cause}")
    print(f"wrote {args.out_dir/'failure_dossier.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
