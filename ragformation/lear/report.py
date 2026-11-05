"""``lear report``: one summary of the latest local artifacts.

Reads what the other commands wrote — it does not re-run anything expensive:

* the latest doctor JSON in ``docs/lear_agent/evidence/`` (or ``--out`` dir),
* a fresh ``docs verify`` (cheap; path checks and coverage need the Lear
  checkout via ``--source`` or ``RAG_LEAR_SOURCE``),
* index statistics for the configured backend (only if the index exists),
* every evaluation run — ``eval_runs/*/run.json`` under the data directory plus
  the committed copies in ``evals/lear/results`` — oldest first, so the
  improvement rounds read as one table; strict key-fact scores of runs made
  before the strict scorer come from the committed rescore evidence,
* the newest benchmark JSON.

Each section states whether its numbers come from a qualifying (live Ollama)
run or from the labelled offline baseline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import evalset
from .settings import LearSettings


def _latest(paths: List[Path]) -> Optional[Path]:
    paths = [p for p in paths if p.is_file()]
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None


def _load(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _strict_scores(evidence_dir: Path) -> Dict[Tuple[str, str], float]:
    """(label, commit12) -> correctStrict from ``lear eval rescore`` evidence files."""

    scores: Dict[Tuple[str, str], float] = {}
    for path in sorted(evidence_dir.glob("rescore_*.json")):
        for row in (_load(path) or {}).get("runs", []) or []:
            if row.get("label") and row.get("correctStrict") is not None:
                scores[(str(row["label"]), str(row.get("commit") or "")[:12])] = row["correctStrict"]
    return scores


def evaluation_runs(settings: LearSettings, *, include_committed: bool = True) -> List[Dict[str, Any]]:
    """All saved evaluation runs, oldest first; a committed copy of a local run counts once."""

    repo = evalset.REPO_ROOT
    paths = sorted((Path(settings.data_dir) / "eval_runs").glob("*/run.json"))
    if include_committed:
        paths += sorted((repo / "evals" / "lear" / "results").glob("*/run.json"))
    strict = _strict_scores(repo / "docs" / "lear_agent" / "evidence")
    rows: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    for path in paths:
        run = _load(path)
        if not run or "summary" not in run:
            continue
        key = (run.get("label"), run.get("startedAt"))
        if key in rows:
            continue
        s = run.get("summary") or {}
        rf = run.get("ragFormation") or {}
        commit = str(rf.get("commit") or "")[:12]
        recorded_strict = s.get("correctStrict")
        rows[key] = {
            "file": str(path),
            "label": run.get("label"),
            "startedAt": run.get("startedAt"),
            "suites": run.get("suites") or s.get("suites") or [],
            "commit": commit or None,
            "dirty": rf.get("dirty"),
            "cases": s.get("cases"),
            "correct": s.get("correct"),
            "correctStrict": recorded_strict if recorded_strict is not None else strict.get((str(run.get("label")), commit)),
            "strictSource": "run" if recorded_strict is not None else ("rescore evidence" if (str(run.get("label")), commit) in strict else None),
            "hitAtK": (s.get("retrieval") or {}).get("hitAtK"),
            "mrr": (s.get("retrieval") or {}).get("mrr"),
            "unanswerableAbstained": (s.get("unanswerable") or {}).get("abstained"),
            "judge": (s.get("judge") or {}).get("state"),
            "qualifying": s.get("qualifying"),
        }
    return sorted(rows.values(), key=lambda r: str(r.get("startedAt") or ""))


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def build_report(
    settings: LearSettings,
    out: Optional[Path] = None,
    *,
    source: Optional[Path] = None,
    include_committed: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    repo = evalset.REPO_ROOT
    evidence_dir = repo / "docs" / "lear_agent" / "evidence"
    data: Dict[str, Any] = {"backend": settings.backend, "qualifyingBackend": settings.qualifying}

    doctor_path = _latest(list(evidence_dir.glob("doctor*.json")))
    doctor = _load(doctor_path)
    if doctor:
        data["doctor"] = {
            "file": str(doctor_path.relative_to(repo)) if doctor_path else None,
            "status": "READY" if doctor.get("ready") else "NOT READY",
            "checkedAt": doctor.get("checkedAt"),
            "failed": list(doctor.get("requiredFailures") or []),
        }

    try:
        from .docverify import verify_docs

        source_dir = Path(source) if source else settings.source_dir
        verify = verify_docs(Path(settings.docs_dir), source_dir)
        cov = verify.get("coverage") or {}
        data["docs"] = {
            "passed": verify["passed"],
            "source": str(source_dir) if source_dir else None,
            "checks": {c["name"]: c["status"] for c in verify["checks"]},
            "coverage": round(100 * cov["narrativeCoverage"]) if "narrativeCoverage" in cov else None,
            "coverageAny": round(100 * cov["anyCoverage"]) if "anyCoverage" in cov else None,
        }
    except Exception as exc:  # pragma: no cover - reporting must not crash
        data["docs"] = {"error": str(exc)}

    manifest = Path(settings.index_dir) / "chunk_manifest.json"
    if manifest.exists():
        man = _load(manifest) or {}
        data["index"] = {
            "dir": str(settings.index_dir),
            "chunks": len(man.get("chunks", [])),
            "embedding": man.get("embedding"),
            "generatedAt": man.get("generatedAt"),
        }
    else:
        data["index"] = {"dir": str(settings.index_dir), "status": "missing — run `lear index rebuild`"}

    runs = evaluation_runs(settings, include_committed=include_committed)
    if runs:
        data["evaluationRuns"] = runs

    benches = list((Path(settings.data_dir) / "bench").glob("*/bench.json")) + list(evidence_dir.glob("bench*.json"))
    bench_path = _latest(benches)
    bench = _load(bench_path)
    if bench:
        data["benchmark"] = {
            "file": str(bench_path),
            "label": bench.get("label"),
            "qualifying": bench.get("qualifying"),
            "levels": [
                {"users": lv["users"], "ok": lv["ok"], "rateLimited": lv["rateLimited"], "errors": lv["errors"],
                 "totalP50": (lv.get("totalMs") or {}).get("p50"), "ttftP50": (lv.get("ttftMs") or {}).get("p50")}
                for lv in bench.get("levels", [])
            ],
        }

    lines = [f"Lear agent report — backend {settings.backend}" + ("" if settings.qualifying else " (offline baseline, NOT qualifying)")]
    if "doctor" in data:
        d = data["doctor"]
        lines.append(f"doctor: {d['status']} at {d['checkedAt']} (failed: {', '.join(d['failed']) or 'none'}) [{d['file']}]")
    else:
        lines.append("doctor: no saved report (run `lear doctor --json`)")
    docs = data.get("docs", {})
    if "passed" in docs:
        failing = [k for k, v in docs["checks"].items() if v == "FAIL"]
        warned = [k for k, v in docs["checks"].items() if v == "WARN"]
        coverage = (
            f"module coverage {docs['coverage']}% narrative, {docs['coverageAny']}% anywhere"
            if docs.get("coverage") is not None
            else "coverage n/a (needs --source)"
        )
        lines.append(
            f"docs verify: {'PASS' if docs['passed'] else 'FAIL'} ({len(docs['checks'])} checks; failing: {', '.join(failing) or 'none'}"
            + (f"; warnings: {', '.join(warned)}" if warned else "")
            + f"); {coverage}"
        )
    elif docs.get("error"):
        lines.append(f"docs verify: error — {docs['error']}")
    idx = data["index"]
    if "chunks" in idx:
        lines.append(f"index: {idx['chunks']} chunks in {idx['dir']}" + (f" — {idx['embedding'].get('provider')}:{idx['embedding'].get('model')}" if isinstance(idx.get("embedding"), dict) else ""))
    else:
        lines.append(f"index: {idx.get('status')} ({idx['dir']})")
    if data.get("evaluationRuns"):
        lines.append("evaluation runs (oldest first):")
        lines.append(f"  {'label':<16} {'suites':<16} {'commit':<14} {'cases':>5} {'correct':>7} {'strict':>6} {'hit@k':>6} {'MRR':>6} {'unans':>6}  judge     qualifying")
        for r in data["evaluationRuns"]:
            commit = (r["commit"] or "–") + ("+" if r.get("dirty") else "")
            lines.append(
                f"  {str(r['label'])[:16]:<16} {','.join(r['suites'])[:16]:<16} {commit:<14} {_fmt(r['cases']):>5} {_fmt(r['correct']):>7} "
                f"{_fmt(r['correctStrict']):>6} {_fmt(r['hitAtK']):>6} {_fmt(r['mrr']):>6} {_fmt(r['unanswerableAbstained']):>6}  {str(r['judge'] or '–'):<9} {r['qualifying']}"
            )
        lines.append("  (+ = uncommitted changes at run time; strict = key facts after removing code refs and markers; unans = unanswerable cases abstained)")
    else:
        lines.append("evaluation: none yet (run `lear eval run`)")
    if "benchmark" in data:
        b = data["benchmark"]
        lines.append(f"benchmark: {b['label']}")
        for lv in b["levels"]:
            lines.append(f"  users={lv['users']} ok={lv['ok']} rate_limited={lv['rateLimited']} errors={lv['errors']} total_p50={lv['totalP50']} ms ttft_p50={lv['ttftP50']} ms")
    else:
        lines.append("benchmark: none yet (run `lear bench`)")
    text = "\n".join(lines)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        data["output"] = str(out)
    return text, data
