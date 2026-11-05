#!/usr/bin/env python3
"""Record the S3 Experience demo fixture from a real local run.

The experience can be opened with no server at all (Director mode). In that
mode every number on screen must come from something that actually ran, and be
labelled ``demo`` — so this script performs a real ingest and a real query into
a throwaway data directory, captures the engine event stream, and writes
``ragformation/web/experience/demo_trace.json``.

It never touches the user's index: the data directory is created under
``tempfile`` and removed before exit.

Usage::

    python scripts/record_experience_demo.py
    python scripts/record_experience_demo.py --question "..." --out /tmp/demo.json
"""

from __future__ import annotations

import argparse
import json
import shutil
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
from ragformation.events import EventBus  # noqa: E402

DEFAULT_QUESTION = "How long is access review evidence retained?"


def _provider_labels(engine: RAGEngine) -> Dict[str, Any]:
    health = engine.health()
    embedding = health["embedding"]
    generation = health["generation"]
    return {
        "embeddingProvider": embedding.get("provider"),
        "embeddingModel": embedding.get("model"),
        "dimension": embedding.get("dimension"),
        "semantic": bool(embedding.get("semantic")),
        "generationProvider": generation.get("provider"),
        "generationModel": generation.get("model"),
        "generationMode": generation.get("mode"),
    }


def record(corpus: Path, question: str, out: Path) -> Dict[str, Any]:
    files = sorted(path for path in corpus.rglob("*") if path.is_file() and not path.name.startswith("."))
    if not files:
        raise SystemExit(f"no corpus files found under {corpus}")

    settings = replace(
        Settings.from_env(),
        embed_provider="hashing",
        llm_provider="extractive",
    )
    data_dir = Path(tempfile.mkdtemp(prefix="ragformation-demo-"))
    settings = replace(settings, data_dir=data_dir)
    bus = EventBus()
    started = time.perf_counter()
    try:
        engine = RAGEngine(settings, event_bus=bus)
        try:
            ingest = engine.ingest_path([str(path) for path in files])
            result = engine.ask(question)
            labels = _provider_labels(engine)
            health = engine.health()

            # Scene 14 (lifecycle) is only honest if a delete and a rebuild
            # really happened: replay them here, on the throwaway copy.
            lifecycle: Dict[str, Any] = {}
            try:
                ingest_results = getattr(ingest, "results", None) or ingest.get("results", [])
                victim = ingest_results[-1] if ingest_results else None
                source_id = getattr(victim, "source_id", None) or (victim or {}).get("sourceId")
                if source_id:
                    deleted = engine.delete_source(source_id)
                    rebuilt = engine.rebuild()
                    lifecycle = {
                        "deletedSourceId": source_id,
                        "deletedChunks": deleted.get("deletedChunks") or deleted.get("chunks"),
                        "chunksAfterDelete": deleted.get("indexSize"),
                        "rebuildChunks": rebuilt.get("chunks"),
                        "rebuildSeconds": rebuilt.get("seconds"),
                        "rollbackAvailable": bool(rebuilt.get("backup") or rebuilt.get("rollbackAvailable")),
                    }
            except Exception as exc:  # never fail the recording over a demo flourish
                lifecycle = {"error": f"{type(exc).__name__}: {exc}"[:160]}
        finally:
            engine.close()
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    elapsed = round((time.perf_counter() - started) * 1000.0, 1)

    events: List[Dict[str, Any]] = bus.history(limit=400)
    merged: Dict[str, Dict[str, Any]] = {}
    for event in events:
        stage = event.get("stage")
        if not stage:
            continue
        bucket = merged.setdefault(stage, {"counts": {}, "status": None})
        bucket["counts"].update(event.get("counts") or {})
        bucket["status"] = event.get("status") or bucket["status"]

    def stage_counts(stage: str, key: str, default: Any = None) -> Any:
        return merged.get(stage, {}).get("counts", {}).get(key, default)

    metrics = result.metrics or {}
    summary = {
        "sources": health["sources"],
        "chunks": health["chunks"],
        "vectors": health["vectors"],
        "lexicalRows": health["lexicalRows"],
        "dimension": labels["dimension"],
        "evidenceChunks": len(result.evidence),
        "contextChunks": stage_counts("context.assembly", "selected", len(result.evidence)),
        "contextTokens": metrics.get("contextTokens"),
        "citations": len(result.citations),
        "unresolvedCitations": len([c for c in result.citations if not c.valid]),
        "status": result.status,
        "support": round(float(result.metrics.get("bestSupport", 0.0)), 4),
        "warnings": list(result.warnings),
        "quarantined": len(result.quarantined),
        "latencyMs": metrics.get("totalMs"),
        "retrievalMs": metrics.get("retrievalMs"),
        "ttftMs": (result.timings or {}).get("ttftMs") if hasattr(result, "timings") else None,
        "denseCandidates": stage_counts("retrieval.semantic", "candidates", metrics.get("semanticCandidates")),
        "lexicalCandidates": stage_counts("retrieval.lexical", "candidates", metrics.get("lexicalCandidates")),
        "fused": stage_counts("retrieval.fusion", "fused"),
        "rerankStatus": merged.get("retrieval.rerank", {}).get("status"),
        "mmrStatus": merged.get("retrieval.mmr", {}).get("status"),
        "revision": health.get("lastIngestAt"),
        "lifecycle": lifecycle,
        "recordMs": elapsed,
        "ingestedFiles": [path.name for path in files],
        "ingestJob": ingest if isinstance(ingest, str) else str(ingest)[:120],
    }

    document = {
        "kind": "demo",
        "labelling": {
            "mode": "demo",
            "note": (
                "Recorded from a real local run of this repository: real ingest of the bundled "
                "fixture corpus, real query, real event stream. No language model was called — "
                "answers are produced by the extractive fallback."
            ),
            "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **labels,
            "corpus": f"{len(files)} fixture documents",
        },
        "question": question,
        "answer": result.answer,
        "status": result.status,
        "citations": [citation.to_dict() for citation in result.citations],
        "warnings": list(result.warnings),
        "events": events,
        "summary": summary,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "corpus")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "ragformation" / "web" / "experience" / "demo_trace.json",
    )
    args = parser.parse_args()

    document = record(args.corpus, args.question, args.out)
    summary = document["summary"]
    print(f"wrote {args.out}")
    print(
        "  {sources} sources / {chunks} chunks · status {status} · citations {citations}".format(
            sources=summary["sources"],
            chunks=summary["chunks"],
            status=summary["status"],
            citations=summary["citations"],
        )
    )
    print(f"  events: {len(document['events'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
