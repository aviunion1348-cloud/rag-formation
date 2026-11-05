"""Command-line interface.

    python3 -m ragformation init
    python3 -m ragformation ingest corpus/
    python3 -m ragformation ask "How long are audit logs retained?"
    python3 -m ragformation stats
    python3 -m ragformation serve --port 8787

Exit codes: 0 success, 3 abstained (no answer was invented), 4 needs_review,
1 expected error, 2 usage/validation error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional, Sequence

from . import __version__
from .config import Settings
from .engine import RAGEngine
from .errors import RagError


def _supports_color() -> bool:
    return sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    if not _supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _announce(engine: RAGEngine) -> None:
    """Never let a user think hashing embeddings are a neural model."""
    embedding = engine.health()["embedding"]
    generation = engine.health()["generation"]
    kind = "semantic" if embedding["semantic"] else "LEXICAL (not semantic)"
    print(
        _c(
            f"embedding: {embedding['provider']}/{embedding['model']} ({kind}, d={embedding['dimension']})"
            f" · generation: {generation['provider']}/{generation['model']} ({generation['kind']})",
            "2",
        ),
        file=sys.stderr,
    )


def _format_answer(result: Any) -> str:
    lines: List[str] = []
    status = result.status
    colour = {"answered": "32", "needs_review": "33", "abstained": "36", "failed": "31"}.get(status, "0")
    lines.append(f"{_c('status', '2')}: {_c(status, colour)}")
    lines.append("")
    lines.append(result.answer or "(no answer)")
    if result.citations:
        lines.append("")
        lines.append(_c("citations", "2"))
        for citation in result.citations:
            location = f"p.{citation.page}" if citation.page else ""
            lines.append(
                f"  [{citation.evidence_id}] {citation.source_title} · {citation.section} {location}"
                f"  ({citation.chunk_id}, support {citation.support:.2f})"
            )
    if result.conflict:
        lines.append("")
        lines.append(_c(f"conflict: {json.dumps(result.conflict, default=str)}", "33"))
    if result.warnings:
        lines.append("")
        lines.append(_c(f"warnings: {', '.join(result.warnings)}", "33"))
    metrics = result.metrics or {}
    interesting = {
        key: metrics.get(key)
        for key in ("totalMs", "retrievalMs", "generationMs", "ttftMs", "itlMs", "bestSupport", "contextTokens")
        if metrics.get(key) is not None
    }
    lines.append("")
    lines.append(_c(f"metrics: {json.dumps(interesting)}", "2"))
    return "\n".join(lines)


def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    settings.data_path.mkdir(parents=True, exist_ok=True)
    with RAGEngine(settings) as engine:
        health = engine.health()
    print(f"initialised {settings.db_path}")
    print(f"sources={health['sources']} chunks={health['chunks']}")
    return 0


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        report = engine.ingest_path(
            args.paths, tenant_id=args.tenant, tags=args.tag, force=args.force
        )
    if args.json:
        _print_json(report)
    else:
        for row in report["results"]:
            if row["status"] == "failed":
                error = row.get("error", {})
                print(f"  {_c('FAILED', '31')} {row['filename']}: {error.get('message')}")
                if error.get("hint"):
                    print(f"         hint: {error['hint']}")
            elif row["status"] == "unchanged":
                print(f"  {_c('unchanged', '2')} {row['filename']} (revision {row['revision']})")
            else:
                print(
                    f"  {_c('indexed', '32')} {row['filename']}: {row['chunks']} chunks, "
                    f"revision {row['revision']} ({row['ms']} ms)"
                )
                for warning in row.get("warnings") or []:
                    print(f"         warning: {warning}")
        print(
            f"\nindexed={report['indexed']} unchanged={report['unchanged']} failed={report['failed']} "
            f"chunks={report['chunks']} sources={report['sources']}"
        )
        print(f"embedding: {report['embedding']['fingerprint']} (semantic={report['embedding']['semantic']})")
    return 1 if report["failed"] else 0


def cmd_sources(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        sources = engine.list_sources()
    if args.json:
        _print_json(sources)
        return 0
    if not sources:
        print("no sources indexed yet — run: python3 -m ragformation ingest corpus/")
        return 0
    print(f"{'status':9} {'chunks':>6} {'rev':>3}  source")
    for source in sources:
        print(
            f"{source['status']:9} {source['chunks']:>6} {source['revision']:>3}  "
            f"{source['title']}  ({source['sourceId']})"
        )
    return 0


def cmd_chunks(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        chunks = engine.source_chunks(args.source_id)
    if args.json:
        _print_json(chunks)
        return 0
    for chunk in chunks:
        print(
            f"{chunk['chunkId']}  p.{chunk['pageStart']}  {chunk['tokenCount']} tok  "
            f"section={chunk['section']!r}"
        )
        print(f"    {chunk['text'][:160].replace(chr(10), ' ')}…")
    return 0


def cmd_delete(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        if not args.yes:
            sources = {s["sourceId"]: s for s in engine.list_sources()}
            if args.source_id not in sources:
                print(f"source not found: {args.source_id}", file=sys.stderr)
                return 2
            answer = input(
                f"Delete {sources[args.source_id]['title']} and all derived chunks/vectors? [y/N] "
            )
            if answer.strip().lower() not in {"y", "yes"}:
                print("aborted")
                return 1
        result = engine.delete_source(args.source_id)
    print(json.dumps(result, indent=2))
    return 0


def cmd_rebuild(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        result = engine.rebuild(force=args.force)
    _print_json(result)
    return 0


def cmd_ask(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        if not args.json and not args.quiet:
            _announce(engine)

        def stream(token: str) -> None:
            if args.stream and not args.json:
                sys.stdout.write(token)
                sys.stdout.flush()

        result = engine.ask(
            args.question,
            top_k=args.top_k,
            mode=args.mode,
            temperature=args.temperature,
            top_p=args.top_p,
            on_token=stream if args.stream else None,
        )
    if args.json:
        _print_json(result.to_dict())
    else:
        if args.stream:
            print()
            if result.citations:
                print("\n" + _c("citations", "2"))
                for citation in result.citations:
                    print(f"  [{citation.evidence_id}] {citation.source_title} · {citation.section}")
        else:
            print(_format_answer(result))
        if args.show_trace:
            print("\n" + _c("trace", "2"))
            for event in result.trace:
                duration = f"{event.get('durationMs')} ms" if event.get("durationMs") is not None else ""
                print(f"  {event['stage']:22} {event['status']:9} {duration}")
        if args.show_evidence:
            print("\n" + _c("evidence", "2"))
            for item in result.evidence:
                print(f"  [{item['evidenceId']}] {item['sourceTitle']} · {item['section']}")
    return {
        "answered": 0,
        "abstained": 3,
        "needs_review": 4,
        "failed": 1,
    }.get(result.status, 1)


def cmd_stats(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        stats = engine.stats(limit=args.limit)
    if args.json:
        _print_json(stats)
        return 0
    print(f"queries: {stats['queries']}  window: {stats['window']}")
    print(f"statuses: {stats['statuses']}")
    print(f"abstention rate: {stats['abstentionRate']}")
    for label, key in (
        ("latency (ms)", "latencyMs"),
        ("retrieval (ms)", "retrievalMs"),
        ("generation (ms)", "generationMs"),
        ("ttft (ms)", "ttftMs"),
        ("itl (ms)", "itlMs"),
        ("citations/answer", "citationsPerAnswer"),
    ):
        block = stats[key]
        if block["count"]:
            print(
                f"  {label:17} mean={block['mean']:>8}  p50={block['p50']:>8}  "
                f"p95={block['p95']:>8}  (n={block['count']})"
            )
        else:
            print(f"  {label:17} no observations")
    if stats["citationPrecision"] is not None:
        print(f"  citation precision {stats['citationPrecision']}")
    return 0


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        report = engine.doctor()
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 1
    print(f"ragformation {__version__} — {settings.db_path}")
    for check in report["checks"]:
        mark = _c("PASS", "32") if check["ok"] else _c("WARN", "33")
        print(f"  [{mark}] {check['name']}")
        detail = check["detail"]
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, default=str)
        print(f"         {detail}")
        if check.get("fix") and not check["ok"]:
            print(f"         fix: {check['fix']}")
    print(f"\nok={report['ok']} (warnings are expected without Ollama/OpenAI configured)")
    return 0


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    from .api import serve

    serve(settings, host=args.host or settings.host, port=args.port or settings.port)
    return 0


def _print_v2(report: dict) -> None:
    retrieval = report["retrieval"]
    generation = report["generation"]
    abstention = report["abstention"]
    print(
        f"dataset: {report['dataset']} ({report['datasetRevision']})  cases: {report['questions']}  "
        f"chunks: {report['corpusChunks']}  embedding: {report['provider']['embedding']}"
    )
    print(
        f"  retrieval  recall@{report['topK']}={retrieval.get('recallAtK')} "
        f"precision={retrieval.get('precisionAtK')} mrr={retrieval.get('mrrAtK')} "
        f"ndcg={retrieval.get('ndcgAtK')} dupRate={retrieval.get('duplicateRate')}"
    )
    print(
        f"  generation answered={generation['answered']} needsReview={generation['needsReview']} "
        f"abstained={generation['abstained']} factRecall={generation['factRecall']} "
        f"forbiddenFree={generation['forbiddenFreeRate']} citationPrecision={generation['citationPrecision']}"
    )
    print(
        f"  abstention recall={abstention['abstentionRecall']} falseAnswer={abstention['falseAnswerRate']} "
        f"falseAbstain={abstention['falseAbstentionRate']} threshold={abstention['threshold']}"
    )
    print(f"  conflicts: {json.dumps(report['conflicts'])}")
    print(f"  calibration: {json.dumps(report['calibration'])}")
    if report.get("ablations") and "error" not in report["ablations"]:
        print("  ablations:")
        for name, values in report["ablations"].items():
            print(f"    {name:20} recall={values['recall']} mrr={values['mrr']} ndcg={values['ndcg']}")
    for split, values in report.get("bySplit", {}).items():
        print(f"  split {split:12} cases={values['cases']} passed={values['passed']}")
    failed = [row for row in report["rows"] if not row["passed"]]
    if failed:
        print("\nfailed cases:")
        for row in failed:
            print(f"  - [{row['id']}] {row['question']}")
            for reason in row["failures"]:
                print(f"      {reason}")
    print(f"\npassed {report['casesPassed']}/{report['casesTotal']}")


def cmd_eval(args: argparse.Namespace, settings: Settings) -> int:
    from .evaluation import run_evaluation

    if not args.v1 and not args.golden:
        from .evaluation_v2 import run_evaluation as run_v2
        from .evaluation_v2 import write_report

        splits = [s.strip() for s in (getattr(args, "splits", "") or "").split(",") if s.strip()]
        with RAGEngine(settings) as engine:
            report = run_v2(
                engine,
                dataset_path=args.dataset,
                top_k=args.top_k,
                splits=splits or None,
                ablations=not getattr(args, "no_ablations", False),
            )
        if getattr(args, "out", None):
            write_report(report, args.out)
        if not args.json:
            _print_v2(report)
        gate = [part.strip() for part in (getattr(args, "gate", "") or "").split(",") if part.strip()]
        # In --json mode stdout must stay machine-parseable, so diagnostics go
        # to stderr.
        emit = (lambda *a, **k: print(*a, file=sys.stderr, **k)) if args.json else print
        if not gate:
            if report["failedIds"]:
                emit(
                    "\nreport only: "
                    f"{len(report['failedIds'])} case(s) failed. "
                    "Pass --gate all (or --gate test) to make this exit non-zero."
                )
            report["gate"] = None
            if args.json:
                _print_json(report)
            return 0
        minimum = float(getattr(args, "min_pass_rate", 0.95))
        verdicts = {}
        ok = True
        for split in gate:
            if split == "all":
                passed = report["casesPassed"]
                total = report["casesTotal"]
            else:
                values = report.get("bySplit", {}).get(split)
                if not values:
                    emit(f"gate: split {split!r} was not run")
                    return 2
                passed = values["passed"]
                total = values["cases"]
            rate = passed / total if total else 0.0
            held = rate >= minimum
            verdicts[split] = {"passed": passed, "total": total, "rate": round(rate, 4), "held": held}
            ok = ok and held
        report["gate"] = {"minimumPassRate": minimum, "splits": verdicts, "held": ok}
        emit(f"gate: minimum pass rate {minimum:.2f} -> {json.dumps(verdicts)}")
        if args.json:
            _print_json(report)
        return 0 if ok else 5

    with RAGEngine(settings) as engine:
        report = run_evaluation(
            engine,
            golden_path=args.golden,
            top_k=args.top_k,
            mode=args.mode,
        )
    if args.json:
        _print_json(report)
    else:
        print(f"golden questions: {report['questions']}  corpus chunks: {report['corpusChunks']}")
        for key, value in report["retrieval"].items():
            print(f"  {key:16} {value}")
        print(f"  generation: {json.dumps(report['generation'], default=str)}")
        print(f"  latency: {json.dumps(report['latency'], default=str)}")
        failures = [row for row in report["cases"] if not row["passed"]]
        if failures:
            print("\nfailed cases:")
            for row in failures:
                print(f"  - {row['question']}")
                for reason in row["failures"]:
                    print(f"      {reason}")
    return 0 if report["passed"] else 5


def cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        bundle = engine.export_bundle(args.questions or ())
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(bundle, handle, indent=2, default=str)
        print(f"wrote {args.out}")
    else:
        print(json.dumps(bundle, indent=2, default=str))
    return 0


def cmd_feedback(args: argparse.Namespace, settings: Settings) -> int:
    with RAGEngine(settings) as engine:
        feedback_id = engine.store.record_feedback(args.query_id, args.rating, args.note or "")
    print(json.dumps({"feedbackId": feedback_id}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ragformation",
        description="A complete, operable RAG system (ingest → retrieve → grounded, cited answers).",
    )
    parser.add_argument("--version", action="version", version=f"ragformation {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the data directory and database")

    p_ingest = sub.add_parser("ingest", help="ingest files or directories")
    p_ingest.add_argument("paths", nargs="+")
    p_ingest.add_argument("--tenant", default="local")
    p_ingest.add_argument("--tag", action="append", default=[])
    p_ingest.add_argument("--force", action="store_true", help="reindex even if unchanged")
    p_ingest.add_argument("--json", action="store_true")

    p_sources = sub.add_parser("sources", help="list indexed sources")
    p_sources.add_argument("--json", action="store_true")

    p_chunks = sub.add_parser("chunks", help="list the chunks of one source")
    p_chunks.add_argument("source_id")
    p_chunks.add_argument("--json", action="store_true")

    p_delete = sub.add_parser("delete", help="delete a source and its derived data")
    p_delete.add_argument("source_id")
    p_delete.add_argument("--yes", action="store_true")

    p_rebuild = sub.add_parser("rebuild", help="re-embed stored chunks (recovery path)")
    # --reembed is the spelling used by the recovery hint and the docs
    # (handoff.md, integration.md); it is an alias of --force.
    p_rebuild.add_argument(
        "--force",
        "--reembed",
        dest="force",
        action="store_true",
        help="re-embed every chunk even if its fingerprint already matches",
    )

    p_ask = sub.add_parser("ask", help="ask a question")
    p_ask.add_argument("question")
    p_ask.add_argument("--top-k", type=int, default=None)
    p_ask.add_argument("--mode", choices=["hybrid", "semantic", "lexical"], default=None)
    p_ask.add_argument("--temperature", type=float, default=None)
    p_ask.add_argument("--top-p", type=float, default=None)
    p_ask.add_argument("--stream", action="store_true")
    p_ask.add_argument("--json", action="store_true")
    p_ask.add_argument("--quiet", action="store_true")
    p_ask.add_argument("--show-trace", action="store_true")
    p_ask.add_argument("--show-evidence", action="store_true")

    p_serve = sub.add_parser("serve", help="run the HTTP API and web console")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)

    p_stats = sub.add_parser("stats", help="latency and quality summary")
    p_stats.add_argument("--limit", type=int, default=500)
    p_stats.add_argument("--json", action="store_true")

    p_eval = sub.add_parser("eval", help="run the golden evaluation set")
    p_eval.add_argument("--golden", default=None, help="golden v1 dataset path")
    p_eval.add_argument("--dataset", default=None, help="evaluation v2 dataset path (default evals/golden_v2.json)")
    p_eval.add_argument("--v1", action="store_true", help="run the original 14-case runner instead of v2")
    p_eval.add_argument("--top-k", type=int, default=None)
    p_eval.add_argument("--mode", choices=["hybrid", "semantic", "lexical"], default=None)
    p_eval.add_argument("--splits", default="", help="comma separated split names to run (v2)")
    p_eval.add_argument("--no-ablations", action="store_true", help="skip the retrieval ablation matrix (v2)")
    p_eval.add_argument("--out", default=None, help="write the full JSON report to this path")
    p_eval.add_argument(
        "--gate",
        default="",
        help=(
            "comma separated split names (dev, calibration, test) or 'all': fail with exit 5 when the "
            "pass rate on those splits is below --min-pass-rate (v2)"
        ),
    )
    p_eval.add_argument("--min-pass-rate", type=float, default=0.95, help="gate threshold (default 0.95)")
    p_eval.add_argument("--json", action="store_true")

    p_doctor = sub.add_parser("doctor", help="diagnose configuration, providers and index")
    p_doctor.add_argument("--json", action="store_true")

    p_export = sub.add_parser("export", help="export corpus, chunks and answers as JSON")
    p_export.add_argument("--out", default=None)
    p_export.add_argument("--questions", action="append", default=[])

    p_feedback = sub.add_parser("feedback", help="record feedback for a query id")
    p_feedback.add_argument("query_id")
    p_feedback.add_argument("rating", type=int)
    p_feedback.add_argument("--note", default="")

    return parser


COMMANDS = {
    "init": cmd_init,
    "ingest": cmd_ingest,
    "sources": cmd_sources,
    "chunks": cmd_chunks,
    "delete": cmd_delete,
    "rebuild": cmd_rebuild,
    "ask": cmd_ask,
    "serve": cmd_serve,
    "stats": cmd_stats,
    "doctor": cmd_doctor,
    "eval": cmd_eval,
    "export": cmd_export,
    "feedback": cmd_feedback,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        settings.validate()
    except RagError as exc:
        print(json.dumps(exc.to_dict()), file=sys.stderr)
        return 2
    try:
        return COMMANDS[args.command](args, settings)
    except RagError as exc:
        print(json.dumps(exc.to_dict()), file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130

