"""``lear report``: one summary of the latest local artifacts.

Reads what the other commands wrote — it does not re-run anything expensive:

* the latest doctor JSON in ``docs/lear_agent/evidence/`` (or ``--out`` dir),
* a fresh ``docs verify`` (cheap; needs the Lear checkout for path checks),
* index statistics for the configured backend (only if the index exists),
* the newest ``eval_runs/*/run.json`` and ``bench/*/bench.json`` under the
  data directory, plus committed results under ``evals/lear/results``.

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


def build_report(settings: LearSettings, out: Optional[Path] = None) -> Tuple[str, Dict[str, Any]]:
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

        verify = verify_docs(Path(settings.docs_dir), settings.source_dir)
        data["docs"] = {
            "passed": verify["passed"],
            "checks": {c["name"]: c["status"] for c in verify["checks"]},
            "coverage": round(100 * (verify.get("coverage") or {}).get("narrativeCoverage", 0.0)),
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

    runs = list((Path(settings.data_dir) / "eval_runs").glob("*/run.json")) + list((repo / "evals" / "lear" / "results").glob("*/run.json"))
    eval_path = _latest(runs)
    run = _load(eval_path)
    if run:
        s = run.get("summary", {})
        data["evaluation"] = {
            "file": str(eval_path),
            "label": run.get("label"),
            "qualifying": s.get("qualifying"),
            "cases": s.get("cases"),
            "correct": s.get("correct"),
            "retrievalHitAtK": (s.get("retrieval") or {}).get("hitAtK"),
            "unanswerableAbstained": (s.get("unanswerable") or {}).get("abstained"),
            "judge": (s.get("judge") or {}).get("state"),
        }

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
        lines.append(f"docs verify: {'PASS' if docs['passed'] else 'FAIL'} ({len(docs['checks'])} checks; failing: {', '.join(failing) or 'none'}); narrative module coverage {docs.get('coverage')}%")
    idx = data["index"]
    lines.append(f"index: {idx.get('chunks', idx.get('status'))} chunks in {idx['dir']}" + (f" — {idx['embedding'].get('provider')}:{idx['embedding'].get('model')}" if isinstance(idx.get("embedding"), dict) else ""))
    if "evaluation" in data:
        e = data["evaluation"]
        lines.append(f"evaluation: {e['label']} — {e['cases']} cases, correct {e['correct']}, hit@k {e['retrievalHitAtK']}, unanswerable abstained {e['unanswerableAbstained']}, judge {e['judge']}, qualifying {e['qualifying']}")
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
