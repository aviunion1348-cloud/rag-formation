"""``python -m ragformation lear ...`` — the Lear agent's command line.

Exit codes (documented in docs/lear_agent/README.md):

  0  success / GROUNDED answer
  1  ERROR answer or unexpected failure
  2  usage or configuration error
  3  ABSTAINED
  4  PARTIAL or CONFLICTED (needs review)
  5  evaluation or verification gate failed
  6  a required local dependency (Ollama, a model, the index) is unavailable
  7  rate limited

Only this module parses arguments; the work happens in the service modules so
every behaviour is testable without a terminal.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from ..errors import ProviderUnavailable, RagError, RateLimited


def add_lear_parser(sub: Any) -> None:
    lear = sub.add_parser(
        "lear",
        help="Lear backend engineering assistant (strict-local CLI agent)",
        description="Answer questions about the Lear backend from evidence-backed docs, using local Ollama models only.",
    )
    lear.add_argument("--backend", choices=["ollama", "baseline"], default=None,
                      help="ollama (default, qualifying) or baseline (hashing + extractive, offline, NOT qualifying)")
    lear.add_argument("--data-dir", default=None, help="agent data directory (default data/lear)")
    lear.add_argument("--docs-dir", default=None, help="generated Lear docs (default docs/lear_backend)")
    lsub = lear.add_subparsers(dest="lear_command", required=True)

    p = lsub.add_parser("doctor", help="probe Ollama, models, caps, streaming and judge JSON")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-generation", action="store_true", help="skip the generation and judge probes")

    docs = lsub.add_parser("docs", help="build or verify the Lear documentation")
    dsub = docs.add_subparsers(dest="docs_command", required=True)
    p = dsub.add_parser("build", help="generate reference docs + inventory from a pinned Lear checkout")
    p.add_argument("--source", required=True, help="path to the Lear backend checkout")
    p.add_argument("--json", action="store_true")
    p = dsub.add_parser("verify", help="check links, source paths, line ranges, commit pin, secrets, duplicates")
    p.add_argument("--source", default=None, help="Lear checkout used to verify path:line references")
    p.add_argument("--write-coverage", action="store_true", help="write docs/lear_agent/documentation_coverage.md")
    p.add_argument("--json", action="store_true")

    idx = lsub.add_parser("index", help="build or inspect the Lear docs index")
    isub = idx.add_subparsers(dest="index_command", required=True)
    p = isub.add_parser("rebuild", help="incremental ingest of docs/lear_backend (+ --full re-embed)")
    p.add_argument("--full", action="store_true", help="re-embed every chunk via the staged rebuild")
    p.add_argument("--json", action="store_true")
    p = isub.add_parser("stats", help="index and manifest statistics")
    p.add_argument("--json", action="store_true")

    p = lsub.add_parser("ask", help="ask one question")
    p.add_argument("question")
    p.add_argument("--user", default="local")
    p.add_argument("--session", default=None, help="keep context in this session id")
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--no-stream", action="store_true", help="print the answer only when complete")
    p.add_argument("--debug", action="store_true", help="print retrieval diagnostics")
    p.add_argument("--json", action="store_true")

    p = lsub.add_parser("chat", help="interactive multi-turn session (reads questions from stdin)")
    p.add_argument("--session", required=True)
    p.add_argument("--user", default="local")
    p.add_argument("--json", action="store_true", help="one JSON object per answer")

    ses = lsub.add_parser("sessions", help="list, inspect or delete sessions")
    ssub = ses.add_subparsers(dest="sessions_command", required=True)
    for name in ("list", "inspect", "delete", "purge-expired"):
        p = ssub.add_parser(name)
        if name in {"inspect", "delete"}:
            p.add_argument("session_id")
        p.add_argument("--user", default="local")
        p.add_argument("--json", action="store_true")

    mem = lsub.add_parser("memory", help="scoped long-term memory")
    msub = mem.add_subparsers(dest="memory_command", required=True)
    p = msub.add_parser("list")
    p.add_argument("--user", default="local")
    p.add_argument("--session", default=None)
    p.add_argument("--json", action="store_true")
    p = msub.add_parser("add")
    p.add_argument("text")
    p.add_argument("--user", default="local")
    p.add_argument("--type", default="preference", choices=["preference", "project_fact", "task_state"])
    p.add_argument("--scope", default="user", choices=["user", "session"])
    p.add_argument("--session", default=None)
    p.add_argument("--ttl", type=int, default=None, help="expire after N seconds")
    p.add_argument("--json", action="store_true")
    p = msub.add_parser("delete")
    p.add_argument("memory_id")
    p.add_argument("--user", default="local")
    p.add_argument("--json", action="store_true")
    p = msub.add_parser("import", help="read-only import from an approved shared-memory directory")
    p.add_argument("--source", required=True)
    p.add_argument("--json", action="store_true")

    ev = lsub.add_parser("eval", help="validate the golden set or run an evaluation")
    esub = ev.add_subparsers(dest="eval_command", required=True)
    p = esub.add_parser("validate", help="schema, splits, checksums and source evidence of the golden set")
    p.add_argument("--dataset", default=None)
    p.add_argument("--source", default=None, help="Lear checkout for evidence verification")
    p.add_argument("--write-checksums", action="store_true", help="(re)record case/split checksums before validating")
    p.add_argument("--json", action="store_true")
    p = esub.add_parser("run", help="answer every case, score retrieval/answers, run the local judge")
    p.add_argument("--dataset", default=None)
    p.add_argument("--suite", default="dev", help="all | calibration | dev | test | holdout (comma separated)")
    p.add_argument("--label", default="", help="run label, e.g. round-0")
    p.add_argument("--judge", choices=["auto", "on", "off"], default="auto")
    p.add_argument("--out", default=None, help="output directory (default <data-dir>/eval_runs/<label>)")
    p.add_argument("--allow-holdout", action="store_true", help="required to score the holdout split")
    p.add_argument("--json", action="store_true")

    p = lsub.add_parser("bench", help="dummy-user load benchmark through the agent service layer")
    p.add_argument("--users", default="1,2,5")
    p.add_argument("--requests", type=int, default=3, help="requests per user")
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")

    p = lsub.add_parser("report", help="summarise the latest doctor/docs/index/eval/bench artifacts")
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true")


# ------------------------------------------------------------------- helpers
def _emit(data: Any, as_json: bool, text: Optional[str] = None) -> None:
    if as_json:
        print(json.dumps(_public(data), indent=2, ensure_ascii=False, default=str))
    else:
        print(text if text is not None else json.dumps(_public(data), indent=2, ensure_ascii=False, default=str))


def _public(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _public(v) for k, v in data.items() if not str(k).startswith("_")}
    if isinstance(data, list):
        return [_public(v) for v in data]
    return data


def _settings(args: argparse.Namespace):
    from .settings import LearSettings

    overrides: Dict[str, Any] = {}
    if args.backend:
        overrides["backend"] = args.backend
    if args.data_dir:
        overrides["data_dir"] = Path(args.data_dir)
    if args.docs_dir:
        overrides["docs_dir"] = Path(args.docs_dir)
    return LearSettings.from_env(**overrides)


def _render_answer(answer: Dict[str, Any], streamed: bool) -> str:
    lines = []
    if not streamed or not answer.get("answer"):
        lines.append(answer.get("answer") or "(no answer)")
    lines.append("")
    lines.append(f"Status: {answer['status']}" + ("" if answer.get("qualifying") else "   [NOT QUALIFYING: no live local LLM/embedder in this answer]"))
    for c in answer.get("citations", []):
        refs = f"  source: {', '.join(c['sourceRefs'][:3])}" if c.get("sourceRefs") else ""
        lines.append(f"  [{c['id']}] {c['document']} § {c['heading']} (lines {c.get('lines') or '?'}){refs}")
    models = answer.get("models", {})
    gen = models.get("generation", {})
    emb = models.get("embedding", {})
    lines.append(f"Models: generation={gen.get('used') or gen.get('configured')} embedding={emb.get('provider', '')}:{emb.get('model', '')}")
    t = answer.get("timings", {})
    lines.append(
        f"Timing: ttft={t.get('ttftMs')} ms  total={t.get('totalMs')} ms  tok/s={t.get('tokensPerSec')}  "
        f"itl_p50={((t.get('itlMs') or {}).get('p50'))} ms"
    )
    if answer.get("error"):
        lines.append(f"Error: {answer['error'].get('message')} — {answer['error'].get('hint') or ''}")
    if answer.get("warnings"):
        lines.append("Warnings: " + ", ".join(answer["warnings"]))
    lines.append(f"Correlation id: {answer.get('correlationId')}  prompt={answer.get('promptVersion')}")
    return "\n".join(lines)


def _render_debug(answer: Dict[str, Any]) -> str:
    r = answer.get("retrieval", {})
    lines = ["--- retrieval diagnostics", f"query: {r.get('query')}"]
    if r.get("retrievalQuery"):
        lines.append(f"rewritten retrieval query: {r['retrievalQuery']}")
    lines.append(f"fingerprint: {r.get('fingerprint')}  mode: {r.get('mode')}  context tokens: {r.get('contextTokens')}  best support: {r.get('bestSupport')}")
    for c in r.get("candidates", []):
        mark = "*" if c["chunkId"] in r.get("selected", []) else " "
        lines.append(f" {mark} {c['chunkId']}  score={c.get('score')} support={c.get('support')}  {c.get('document')} § {c.get('heading')}")
    for e in r.get("excluded", []):
        lines.append(f"   excluded {e['chunkId']}: {e.get('reason')}")
    for q in r.get("quarantined", []):
        lines.append(f"   quarantined {q['chunkId']}: {q.get('rules')}")
    for stage, info in (r.get("stages") or {}).items():
        lines.append(f"   {stage}: {info.get('status')} {info.get('ms')} ms")
    return "\n".join(lines)


# ------------------------------------------------------------------ handlers
def _cmd_doctor(args, settings) -> int:
    from .doctor import doctor_exit_code, render_doctor, run_doctor

    report = run_doctor(settings, probe_generation=not args.no_generation)
    _emit(report, args.json, render_doctor(report))
    return doctor_exit_code(report)


def _cmd_docs(args, settings) -> int:
    if args.docs_command == "build":
        from .docgen import build_docs

        report = build_docs(Path(args.source), Path(settings.docs_dir))
        _emit(report, args.json, f"generated {len(report['written'])} files in {settings.docs_dir} from {report['pin']['repository']} @ {report['pin']['commit_short']}")
        return 0
    from .docverify import render_verify, verify_docs

    source = Path(args.source) if args.source else settings.source_dir
    report = verify_docs(Path(settings.docs_dir), source)
    if args.write_coverage:
        from .docverify import render_coverage

        target = Path(settings.docs_dir).parent / "lear_agent" / "documentation_coverage.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_coverage(report), encoding="utf-8")
        report["coverageReport"] = str(target)
    _emit(report, args.json, render_verify(report))
    return 0 if report["passed"] else 5


def _cmd_index(args, settings) -> int:
    from . import indexer
    from .agent import LearAgent

    agent = LearAgent(settings)
    try:
        engine = agent.engine
        if args.index_command == "rebuild":
            report = indexer.rebuild(engine, settings, full=args.full)
            text = (
                f"namespace={report['namespace']} files={report['files']} chunks={report['chunks']} counts={report['counts']}\n"
                f"embedding={report['embedding']['provider']}:{report['embedding']['model']} dim={report['embedding']['dimension']}"
                + ("" if report["qualifying"] else "\n[NOT QUALIFYING: baseline hashing embeddings]")
            )
            _emit(report, args.json, text)
            return 0 if not report["failures"] else 1
        report = indexer.stats(engine, settings)
        _emit(report, args.json)
        return 0
    finally:
        agent.close()


def _cmd_ask(args, settings) -> int:
    from .agent import LearAgent, exit_code_for

    agent = LearAgent(settings)
    streamed = not args.json and not args.no_stream
    try:
        answer = agent.ask(
            args.question,
            user_id=args.user,
            session_id=args.session,
            top_k=args.top_k,
            on_token=(lambda t: (sys.stdout.write(t), sys.stdout.flush())) if streamed else None,
        )
    finally:
        agent.close()
    if streamed:
        print()
    if args.json:
        _emit(answer, True)
    else:
        print(_render_answer(answer, streamed))
        if args.debug:
            print(_render_debug(answer))
    return exit_code_for(answer)


def _cmd_chat(args, settings) -> int:
    from .agent import LearAgent, exit_code_for

    agent = LearAgent(settings)
    code = 0
    try:
        if not args.json:
            print(f"Lear chat — session {args.session} (user {args.user}). Empty line or /exit to quit.", file=sys.stderr)
        for raw in sys.stdin:
            question = raw.strip()
            if not question or question in {"/exit", "/quit"}:
                break
            try:
                answer = agent.ask(
                    question,
                    user_id=args.user,
                    session_id=args.session,
                    on_token=None if args.json else (lambda t: (sys.stdout.write(t), sys.stdout.flush())),
                )
            except RateLimited as exc:
                print(json.dumps(exc.to_dict()), file=sys.stderr)
                code = 7
                continue
            if args.json:
                print(json.dumps(_public(answer), ensure_ascii=False, default=str))
            else:
                print()
                print(_render_answer(answer, True))
            code = max(code, exit_code_for(answer)) if exit_code_for(answer) in (1, 6) else code
    finally:
        agent.close()
    return code


def _cmd_sessions(args, settings) -> int:
    from .agent import LearAgent

    agent = LearAgent(settings)
    try:
        if args.sessions_command == "list":
            data = agent.sessions.list(args.user)
        elif args.sessions_command == "inspect":
            data = {
                "session": agent.sessions.get(args.session_id, args.user, allow_expired=True),
                "messages": agent.sessions.messages(args.session_id, args.user, allow_expired=True),
                "compactions": agent.sessions.compactions(args.session_id, args.user),
            }
        elif args.sessions_command == "delete":
            data = agent.sessions.delete(args.session_id, args.user)
        else:
            data = {"purged": agent.sessions.purge_expired()}
    finally:
        agent.close()
    _emit(data, args.json)
    return 0


def _cmd_memory(args, settings) -> int:
    from .agent import LearAgent

    agent = LearAgent(settings)
    try:
        if args.memory_command == "list":
            data = agent.memory.list(args.user, session_id=args.session)
        elif args.memory_command == "add":
            data = agent.memory.add(
                args.user, args.text, type=args.type, scope=args.scope, session_id=args.session, ttl_s=args.ttl,
                provenance={"origin": "cli", "command": "lear memory add"},
            )
        elif args.memory_command == "delete":
            data = {"deleted": agent.memory.delete(args.memory_id, args.user)}
        else:
            from .shared_memory import LocalDirectoryConnector, import_shared_memory

            data = import_shared_memory(LocalDirectoryConnector(args.source), agent.memory)
    finally:
        agent.close()
    _emit(data, args.json)
    return 0


def _cmd_eval(args, settings) -> int:
    from . import evalset

    dataset = Path(args.dataset) if args.dataset else evalset.DEFAULT_DATASET
    if args.eval_command == "validate":
        source = Path(args.source) if args.source else settings.source_dir
        if args.write_checksums:
            evalset.write_checksums(dataset)
        report = evalset.validate(dataset, source=source)
        _emit(report, args.json, evalset.render_validation(report))
        return 0 if report["valid"] else 5
    from .evaluate import render_run, run_evaluation

    report = run_evaluation(
        settings,
        dataset=dataset,
        suites=[s.strip() for s in args.suite.split(",") if s.strip()],
        label=args.label or "adhoc",
        judge=args.judge,
        out_dir=Path(args.out) if args.out else None,
        allow_holdout=args.allow_holdout,
    )
    _emit(report["summary"], args.json, render_run(report))
    if report["summary"].get("status") == "BLOCKED":
        return 6
    if args.judge == "on" and (report.get("judge") or {}).get("available") is False:
        return 6
    return 0


def _cmd_bench(args, settings) -> int:
    from .bench import render_bench, run_bench

    users = [int(u) for u in args.users.split(",") if u.strip()]
    report = run_bench(settings, users=users, requests_per_user=args.requests, out=Path(args.out) if args.out else None)
    _emit(report, args.json, render_bench(report))
    return 0


def _cmd_report(args, settings) -> int:
    from .report import build_report

    text, data = build_report(settings, out=Path(args.out) if args.out else None)
    _emit(data, args.json, text)
    return 0


HANDLERS = {
    "doctor": _cmd_doctor,
    "docs": _cmd_docs,
    "index": _cmd_index,
    "ask": _cmd_ask,
    "chat": _cmd_chat,
    "sessions": _cmd_sessions,
    "memory": _cmd_memory,
    "eval": _cmd_eval,
    "bench": _cmd_bench,
    "report": _cmd_report,
}


def run_lear(args: argparse.Namespace) -> int:
    try:
        settings = _settings(args)
    except RagError as exc:
        print(json.dumps(exc.to_dict()), file=sys.stderr)
        return 2
    try:
        return HANDLERS[args.lear_command](args, settings)
    except RateLimited as exc:
        print(json.dumps(exc.to_dict()), file=sys.stderr)
        return 7
    except ProviderUnavailable as exc:
        print(json.dumps({**exc.to_dict(), "blocked": "local dependency unavailable"}), file=sys.stderr)
        return 6
    except RagError as exc:
        print(json.dumps(exc.to_dict()), file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130
