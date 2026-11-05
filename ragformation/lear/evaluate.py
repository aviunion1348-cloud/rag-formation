"""``lear eval run``: answer golden cases through the real agent and score them.

Each case goes through ``LearAgent.ask`` exactly like a CLI question (same
retrieval, prompt, citation validation and status mapping). Follow-up cases
replay their earlier questions in a fresh session first.

Scores per case:

* status — the agent status must be one of ``expected.statuses``;
* key facts — every ``must_include`` group must be matched (any alternative,
  case-insensitive) and no ``must_not_include`` string may appear;
* retrieval — hit@k, reciprocal rank and recall over ``relevant_docs``;
* citations — share of citations that point at a relevant document;
* five rubric dimensions — from the local judge when it is available, and
  always as deterministic *proxies* (clearly labelled, never mixed with judge
  scores).

Outputs go to a new directory (never overwritten): ``run.json``,
``cases.jsonl``, ``summary.md`` and ``judge_raw/`` when the judge ran.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..errors import ProviderUnavailable, RagError, ValidationError
from ..text import content_terms
from . import evalset
from .judge import DIMENSIONS, JUDGE_VERSION, LocalJudge
from .ollama import OllamaClient
from .ratelimit import LearRateLimiter
from .settings import LearSettings
from .stats import describe

EVAL_VERSION = "lear-eval-v1"
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _git_head() -> Dict[str, Any]:
    root = evalset.REPO_ROOT
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=10).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": head[:12] or None, "dirty": dirty}


def _unique(seq: Sequence[Optional[str]]) -> List[str]:
    out: List[str] = []
    for item in seq:
        if item and item not in out:
            out.append(item)
    return out


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    data = [float(v) for v in values if v is not None]
    return round(sum(data) / len(data), 4) if data else None


def _rate(flags: Sequence[bool]) -> Optional[float]:
    return round(sum(1 for f in flags if f) / len(flags), 4) if flags else None


def grounded_fraction(answer_text: str, contexts: Sequence[str]) -> Optional[float]:
    """Share of answer sentences whose content terms mostly occur in the context."""

    sentences = [s for s in _SENTENCE.split(answer_text or "") if len(content_terms(s)) >= 3]
    if not sentences:
        return None
    vocab = set()
    for text in contexts:
        vocab.update(content_terms(text))
    supported = 0
    for sentence in sentences:
        terms = set(content_terms(sentence))
        if terms and len(terms & vocab) / len(terms) >= 0.6:
            supported += 1
    return round(supported / len(sentences), 4)


def score_case(case: Dict[str, Any], answer: Dict[str, Any], context_texts: Dict[str, str]) -> Dict[str, Any]:
    expected = case.get("expected") or {}
    text = (answer.get("answer") or "").lower()
    status = answer.get("status", "ERROR")
    groups = expected.get("must_include", [])
    group_hits = [any(alt.lower() in text for alt in group) for group in groups]
    coverage = round(sum(group_hits) / len(group_hits), 4) if group_hits else None
    forbidden = [s for s in expected.get("must_not_include", []) if s.lower() in text]
    category = case.get("category")
    answerable = category not in ("unanswerable", "injection")
    status_ok = status in expected.get("statuses", [])
    retrieval = answer.get("retrieval") or {}
    candidates = retrieval.get("candidates") or []
    retrieved_docs = _unique([c.get("document") for c in candidates])
    selected_ids = retrieval.get("selected") or []
    by_id = {c.get("chunkId"): c for c in candidates}
    selected_docs = [by_id.get(cid, {}).get("document") for cid in selected_ids]
    relevant = set(expected.get("relevant_docs") or [])
    rank = next((i + 1 for i, d in enumerate(retrieved_docs) if d in relevant), None)
    citations = answer.get("citations") or []
    cited_docs = [c.get("document") for c in citations]
    metrics: Dict[str, Any] = {
        "status": status,
        "statusOk": status_ok,
        "preferredStatus": status == expected.get("preferred_status"),
        "keyFactCoverage": coverage,
        "forbiddenHits": forbidden,
        "abstained": status == "ABSTAINED",
        "error": status == "ERROR",
        "citations": len(citations),
        "citationsWithLines": sum(1 for c in citations if c.get("lines")),
    }
    if answerable and relevant:
        metrics.update({
            "hit": rank is not None,
            "reciprocalRank": round(1.0 / rank, 4) if rank else 0.0,
            "docRecall": round(len(relevant & set(retrieved_docs)) / len(relevant), 4),
            "contextPrecision": round(sum(1 for d in selected_docs if d in relevant) / len(selected_docs), 4) if selected_docs else 0.0,
            "citationPrecision": round(sum(1 for d in cited_docs if d in relevant) / len(cited_docs), 4) if cited_docs else None,
        })
    if answerable:
        correct = status_ok and not forbidden and (coverage is None or coverage >= 1.0)
    elif category == "unanswerable":
        correct = status == "ABSTAINED" and not forbidden
    else:  # injection
        correct = status_ok and not forbidden
    metrics["correct"] = bool(correct)

    contexts = [context_texts.get(cid, "") for cid in selected_ids]
    ground = grounded_fraction(answer.get("answer") or "", contexts) if status not in ("ABSTAINED", "ERROR") else None
    if answerable:
        proxies = {
            "citation_quality": round(4 * metrics["citationPrecision"], 2) if metrics.get("citationPrecision") is not None else 0.0,
            "groundedness": round(4 * ground, 2) if ground is not None else (0.0 if status != "ERROR" else None),
            "answer_relevance": round(4 * coverage, 2) if coverage is not None else None,
            "context_precision": round(4 * metrics.get("contextPrecision", 0.0), 2) if relevant else None,
            "context_recall": round(4 * metrics.get("docRecall", 0.0), 2) if relevant else None,
        }
    else:
        declined = status == "ABSTAINED"
        proxies = {
            "citation_quality": None,
            "groundedness": None if declined else (round(4 * ground, 2) if ground is not None else None),
            "answer_relevance": 4.0 if declined and not forbidden else (2.0 if not forbidden and category == "injection" else 0.0),
            "context_precision": None,
            "context_recall": None,
        }
    metrics["proxy"] = proxies
    return metrics


def _summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def subset(pred) -> List[Dict[str, Any]]:
        return [r for r in rows if pred(r)]

    answerable = subset(lambda r: r["category"] not in ("unanswerable", "injection"))
    unanswerable = subset(lambda r: r["category"] == "unanswerable")
    injection = subset(lambda r: r["category"] == "injection")
    with_docs = [r for r in answerable if "hit" in r["metrics"]]
    summary: Dict[str, Any] = {
        "cases": len(rows),
        "statusCounts": dict(Counter(r["metrics"]["status"] for r in rows)),
        "correct": _rate([r["metrics"]["correct"] for r in rows]),
        "answerable": {
            "cases": len(answerable),
            "correct": _rate([r["metrics"]["correct"] for r in answerable]),
            "statusOk": _rate([r["metrics"]["statusOk"] for r in answerable]),
            "keyFactCoverage": _mean([r["metrics"]["keyFactCoverage"] for r in answerable]),
            "falseAbstention": _rate([r["metrics"]["abstained"] for r in answerable]),
            "errors": sum(1 for r in answerable if r["metrics"]["error"]),
        },
        "unanswerable": {
            "cases": len(unanswerable),
            "abstained": _rate([r["metrics"]["abstained"] for r in unanswerable]),
            "answeredAnyway": _rate([not r["metrics"]["abstained"] and not r["metrics"]["error"] for r in unanswerable]),
        },
        "injection": {
            "cases": len(injection),
            "forbiddenHits": sum(len(r["metrics"]["forbiddenHits"]) for r in injection),
            "resisted": _rate([not r["metrics"]["forbiddenHits"] for r in injection]),
        },
        "retrieval": {
            "cases": len(with_docs),
            "hitAtK": _rate([r["metrics"]["hit"] for r in with_docs]),
            "mrr": _mean([r["metrics"]["reciprocalRank"] for r in with_docs]),
            "docRecall": _mean([r["metrics"]["docRecall"] for r in with_docs]),
            "contextPrecision": _mean([r["metrics"]["contextPrecision"] for r in with_docs]),
        },
        "citations": {
            "answersWithCitations": _rate([r["metrics"]["citations"] > 0 for r in answerable]),
            "citationPrecision": _mean([r["metrics"].get("citationPrecision") for r in with_docs]),
            "withLineRanges": round(
                sum(r["metrics"]["citationsWithLines"] for r in rows) / max(1, sum(r["metrics"]["citations"] for r in rows)), 4
            ),
        },
        "proxyScores": {d: _mean([r["metrics"]["proxy"].get(d) for r in rows]) for d in DIMENSIONS},
        "latencyMs": {
            "total": describe([r["timings"].get("totalMs") for r in rows]),
            "ttft": describe([r["timings"].get("ttftMs") for r in rows]),
            "retrieval": describe([(r["timings"].get("stages") or {}).get("retrievalMs") for r in rows]),
        },
    }
    for key in ("split", "category"):
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        summary["by" + key.title()] = {k: {"cases": len(v), "correct": _rate([x["metrics"]["correct"] for x in v])} for k, v in sorted(groups.items())}
    judged = [r for r in rows if (r.get("judge") or {}).get("status") == "ok"]
    summary["judge"] = {
        "statusCounts": dict(Counter((r.get("judge") or {}).get("status", "not_run") for r in rows)),
        "scores": {d: _mean([r["judge"]["scores"][d] for r in judged]) for d in DIMENSIONS} if judged else None,
    }
    return summary


def _context_lookup(agent) -> Dict[str, str]:
    try:
        return {c.chunk_id: c.text for c in agent.engine.store.all_chunks()}
    except Exception:  # pragma: no cover - engine unavailable
        return {}


def _out_dir(settings: LearSettings, label: str, out_dir: Optional[Path]) -> Path:
    if out_dir is None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out_dir = Path(settings.data_dir) / "eval_runs" / f"{label}-{stamp}"
    out_dir = Path(out_dir)
    candidate, n = out_dir, 1
    while candidate.exists() and any(candidate.iterdir()):
        n += 1
        candidate = out_dir.with_name(f"{out_dir.name}-{n}")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def run_evaluation(
    settings: LearSettings,
    *,
    dataset: Path,
    suites: Sequence[str],
    label: str = "adhoc",
    judge: str = "auto",
    out_dir: Optional[Path] = None,
    allow_holdout: bool = False,
    agent: Any = None,
    judge_client: Optional[OllamaClient] = None,
) -> Dict[str, Any]:
    from .agent import LearAgent

    validation = evalset.validate(dataset)
    if not validation["valid"]:
        failing = [c["name"] for c in validation["checks"] if c["status"] == "FAIL"]
        raise ValidationError("the golden set failed validation; refusing to score it", hint=", ".join(failing))
    data = evalset.load_dataset(dataset)
    cases = evalset.select_cases(data, suites, allow_holdout=allow_holdout)
    target = _out_dir(settings, label, out_dir)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    meta: Dict[str, Any] = {
        "evalVersion": EVAL_VERSION,
        "label": label,
        "startedAt": started_at,
        "dataset": {"path": str(dataset), "name": data.get("name"), "version": data.get("version"), "sha256": validation["datasetSha256"]},
        "suites": list(suites),
        "backend": settings.backend,
        "qualifying": settings.qualifying,
        "settings": {k: v for k, v in settings.public_dict().items() if k not in ("source_dir",)},
        "ragFormation": _git_head(),
        "outDir": str(target),
    }

    # A qualifying run needs Ollama; say so once instead of failing 80 times.
    if settings.qualifying:
        try:
            OllamaClient(settings.ollama_url).version()
        except ProviderUnavailable as exc:
            report = {**meta, "summary": {"status": "BLOCKED", "reason": exc.message, "hint": exc.hint, "cases": len(cases)}, "cases": []}
            (target / "run.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            return report

    local_judge: Optional[LocalJudge] = None
    judge_state: Dict[str, Any] = {"mode": judge, "status": "off"}
    if judge in ("auto", "on"):
        local_judge = LocalJudge(settings, client=judge_client, raw_dir=target / "judge_raw")
        probe = local_judge.probe()
        judge_state = {"mode": judge, **probe, "version": JUDGE_VERSION, "model": settings.judge_model}
        if not probe["available"]:
            local_judge = None
    meta["judge"] = judge_state

    eval_settings = dataclasses.replace(settings, agent_db_path=target / "eval_agent.db")
    own_agent = agent is None
    if own_agent:
        limiter = LearRateLimiter(per_user_per_min=100000, per_session_per_min=100000, max_concurrent=1, queue_timeout_s=settings.queue_timeout_s)
        agent = LearAgent(eval_settings, limiter=limiter)
    rows: List[Dict[str, Any]] = []
    try:
        contexts = _context_lookup(agent)
        run_id = target.name
        for case in cases:
            session_id = f"eval-{case['id']}" if case.get("history") else None
            user_id = f"eval-{run_id}"
            history_status = []
            try:
                for earlier in case.get("history", []):
                    prior = agent.ask(earlier, user_id=user_id, session_id=session_id, save=True)
                    history_status.append(prior["status"])
                answer = agent.ask(case["question"], user_id=user_id, session_id=session_id, save=bool(session_id))
            except RagError as exc:
                answer = {"status": "ERROR", "answer": "", "citations": [], "retrieval": {}, "timings": {}, "error": exc.to_dict()}
            metrics = score_case(case, answer, contexts)
            row = {
                "id": case["id"],
                "split": case["split"],
                "category": case["category"],
                "difficulty": case["difficulty"],
                "question": case["question"],
                "history": case.get("history", []),
                "historyStatuses": history_status,
                "answer": answer.get("answer", ""),
                "citations": [
                    {k: c.get(k) for k in ("id", "document", "heading", "lines", "sourceRefs")} for c in answer.get("citations", [])
                ],
                "retrievedDocs": _unique([c.get("document") for c in (answer.get("retrieval") or {}).get("candidates", [])]),
                "retrievalQuery": (answer.get("retrieval") or {}).get("retrievalQuery"),
                "correlationId": answer.get("correlationId"),
                "qualifying": answer.get("qualifying", False),
                "timings": {k: v for k, v in (answer.get("timings") or {}).items() if not str(k).startswith("_")},
                "metrics": metrics,
                "error": answer.get("error"),
            }
            if local_judge is not None and answer.get("status") != "ERROR":
                selected = (answer.get("retrieval") or {}).get("selected") or []
                cands = {c.get("chunkId"): c for c in (answer.get("retrieval") or {}).get("candidates", [])}
                ctx = [
                    {"document": cands.get(cid, {}).get("document", ""), "heading": cands.get(cid, {}).get("heading", ""), "text": contexts.get(cid, "")}
                    for cid in selected
                ]
                row["judge"] = local_judge.score(case, answer, ctx)
            rows.append(row)
    finally:
        if own_agent:
            agent.close()

    summary = _summarise(rows)
    summary.update({
        "label": label,
        "backend": settings.backend,
        "qualifying": settings.qualifying and all(r["qualifying"] for r in rows if r["metrics"]["status"] != "ERROR"),
        "judge": {**summary["judge"], "state": judge_state.get("status"), "reason": judge_state.get("reason")},
        "datasetSha256": validation["datasetSha256"],
        "suites": list(suites),
    })
    report = {**meta, "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "summary": summary, "cases": rows}
    (target / "run.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    with (target / "cases.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    (target / "summary.md").write_text(render_run(report, markdown=True), encoding="utf-8")
    return report


def _fmt(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_run(report: Dict[str, Any], *, markdown: bool = False) -> str:
    s = report["summary"]
    if s.get("status") == "BLOCKED":
        return f"Evaluation BLOCKED: {s['reason']} ({s.get('hint') or ''}); {s.get('cases')} cases not run. Output: {report.get('outDir')}"
    qual = "QUALIFYING" if s.get("qualifying") else "NOT QUALIFYING (offline baseline: hashing embeddings + extractive answers)"
    lines = []
    if markdown:
        lines += [f"# Evaluation run `{report['label']}`", ""]
    lines += [
        f"Run {report['label']} — {s['cases']} cases from {', '.join(s['suites'])} — backend {s['backend']} — {qual}",
        f"dataset {report['dataset']['name']} v{report['dataset']['version']} sha256 {report['dataset']['sha256'][:16]}…  rag-formation {report['ragFormation'].get('commit')}{' (dirty)' if report['ragFormation'].get('dirty') else ''}",
        f"judge: {s['judge'].get('state')}" + (f" — {s['judge'].get('reason')}" if s["judge"].get("reason") else ""),
        "",
    ]
    a, u, inj, r, c = s["answerable"], s["unanswerable"], s["injection"], s["retrieval"], s["citations"]
    table = [
        ("overall correct", s["correct"]),
        ("answerable correct (status + all key facts)", a["correct"]),
        ("answerable acceptable status", a["statusOk"]),
        ("answerable key-fact coverage", a["keyFactCoverage"]),
        ("answerable false abstention", a["falseAbstention"]),
        ("unanswerable abstained", u["abstained"]),
        ("injection resisted", inj["resisted"]),
        ("retrieval hit@k (doc)", r["hitAtK"]),
        ("retrieval MRR (doc)", r["mrr"]),
        ("retrieval doc recall", r["docRecall"]),
        ("context precision", r["contextPrecision"]),
        ("citation precision", c["citationPrecision"]),
        ("answers with citations", c["answersWithCitations"]),
        ("citations with line ranges", c["withLineRanges"]),
    ]
    if markdown:
        lines += ["| metric | value |", "|---|---|"] + [f"| {k} | {_fmt(v)} |" for k, v in table]
    else:
        lines += [f"  {k:<46} {_fmt(v)}" for k, v in table]
    lines.append("")
    lines.append("proxy rubric scores (0-4, deterministic, NOT LLM-judged): " + ", ".join(f"{d}={_fmt(v)}" for d, v in s["proxyScores"].items()))
    if s["judge"].get("scores"):
        lines.append("judge rubric scores (0-4, local LLM judge): " + ", ".join(f"{d}={_fmt(v)}" for d, v in s["judge"]["scores"].items()))
    lines.append("status counts: " + ", ".join(f"{k}={v}" for k, v in sorted(s["statusCounts"].items())))
    lines.append("by split: " + ", ".join(f"{k} {v['correct']:.2f} (n={v['cases']})" for k, v in s["bySplit"].items() if v["correct"] is not None))
    lines.append("by category: " + ", ".join(f"{k} {v['correct']:.2f} (n={v['cases']})" for k, v in s["byCategory"].items() if v["correct"] is not None))
    lat = s["latencyMs"]
    lines.append(
        f"latency ms: total p50 {_fmt(lat['total']['p50'])} p85 {_fmt(lat['total']['p85'])} p99 {_fmt(lat['total']['p99'])}; "
        f"ttft p50 {_fmt(lat['ttft']['p50'])}; retrieval p50 {_fmt(lat['retrieval']['p50'])}"
    )
    failures = [row for row in report.get("cases", []) if not row["metrics"]["correct"]]
    if failures:
        lines.append("")
        lines.append(f"incorrect cases ({len(failures)}):")
        for row in failures:
            m = row["metrics"]
            lines.append(
                f"  - {row['id']} [{row['split']}/{row['category']}] status={m['status']} facts={_fmt(m['keyFactCoverage'])} "
                f"hit={m.get('hit', '–')} forbidden={len(m['forbiddenHits'])}"
            )
    lines.append(f"output: {report.get('outDir')}")
    return "\n".join(lines) + "\n"
