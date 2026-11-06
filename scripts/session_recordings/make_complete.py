#!/usr/bin/env python3
"""Assemble complete_agent_session.mp4 from the genuine scenario recordings.

Order: opening card, then for each scenario a chapter card followed by that
scenario's rendered recording (scenario 08 also shows its preserved, aborted
attempt 1 before attempt 2), then a closing card. Cards are editorial, labelled
"EDITORIAL CHAPTER CARD · not a recording" on screen, and contain only facts read
from evidence/results/*.json and the plan. Every segment is encoded by
render_cast.py with identical settings, so the concatenation is a lossless
stream copy (ffmpeg concat demuxer, -c copy). Nothing is cut from any segment.

  /tmp/recvenv/bin/python scripts/session_recordings/make_complete.py --run-id <run-id>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List

import imageio_ffmpeg

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "artifacts" / "agent_session_recordings"
PLAN = REPO / "scripts" / "session_recordings" / "scenario_plan.json"
RENDER = REPO / "scripts" / "session_recordings" / "render_cast.py"
STYLE = {"PASS": "ok", "FAIL": "fail", "PARTIAL": "warn", "BLOCKED": "fail"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wrap(prefix: str, text: str, style: str = "", width: int = 100) -> List[Dict[str, str]]:
    lines = textwrap.wrap(f"{prefix}{text}", width=width, subsequent_indent="    ")
    return [{"text": ln, "style": style} for ln in lines]


def card(spec: Dict[str, Any], out: Path) -> Dict[str, Any]:
    spec_path = out.with_suffix(".json")
    spec_path.write_text(json.dumps(spec, indent=2))
    r = subprocess.run([sys.executable, str(RENDER), "--card", str(spec_path), str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"card render failed: {r.stderr[-500:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    plan = json.loads(PLAN.read_text())
    work = Path("/tmp") / args.run_id / "complete"
    work.mkdir(parents=True, exist_ok=True)
    results = {s["id"]: json.loads((PKG / "evidence" / "results" / f"{s['id']}.json").read_text()) for s in plan["scenarios"]}
    casts = {s["id"]: json.loads((PKG / "evidence" / "render" / f"{s['file']}.json").read_text()) for s in plan["scenarios"]}
    common = {"bottomLeft": f"Lear RAG agent · {args.run_id}", "bottomRight": "card written by make_complete.py from evidence/results/*.json"}
    segments: List[Dict[str, Any]] = []
    counter = [0]

    def add_card(title: str, lines: List[Dict[str, str]], seconds: float, note: str) -> None:
        counter[0] += 1
        out = work / f"card_{counter[0]:02d}.mp4"
        spec = {"title": title, "lines": lines, "seconds": seconds, "topRight": note, **common}
        rep = card(spec, out)
        segments.append({"kind": "editorial chapter card (not a recording)", "file": str(out), "seconds": rep["seconds"],
                         "title": title, "text": [ln["text"] for ln in lines]})

    def add_recording(path: Path, kind: str, render: Dict[str, Any]) -> None:
        segments.append({"kind": kind, "file": str(path), "packagePath": str(path.relative_to(PKG)), "sha256": sha256(path),
                         "seconds": render.get("seconds"), "realtimeSeconds": render.get("realtimeSeconds"),
                         "pacedSeconds": render.get("pacedSeconds"), "recordedSeconds": render.get("castLastEventS")})

    statuses = " · ".join(f"S{k} {v['status']}" for k, v in results.items())
    add_card("Lear RAG agent — recorded agent sessions", [
        {"text": f"Run {args.run_id} · recorded 2026-11-06 UTC · branch arena/c2d86cd7-rag-formation"},
        {"text": "Every terminal segment is a genuine asciinema recording of the existing agent's CLI"},
        {"text": "(python3 -m ragformation lear ...). Each is shown at its real speed first, then as a paced"},
        {"text": "replay of the same recording that only pauses for reading (the banner says which)."},
        {"text": "Ollama is BLOCKED in this environment (not installed; model hosts unreachable).", "style": "warn"},
        {"text": "NO answer in this film comes from an LLM. Answers come from the labelled baseline:", "style": "warn"},
        {"text": "hashing embeddings + extractive synthesis, which selects and cites corpus sentences.", "style": "warn"},
        {"text": "Cards like this one are editorial. Failures and an aborted attempt are kept, not cut."},
        {"text": ""},
        *wrap("Results (pre-registered checks): ", statuses, "dim"),
    ], 10.0, "editorial · opening")

    for s in plan["scenarios"]:
        nn, res, rnd = s["id"], results[s["id"]], casts[s["id"]]
        passed = sum(c["outcome"] == "PASS" for c in res["checks"])
        cap = res.get("cap") or {}
        lines = [{"text": f"Result: {res['status']} · pre-registered checks passed {passed}/{len(res['checks'])}"
                          + (f" · status cap {cap.get('status')}" if cap.get("status") else " · no status cap"), "style": STYLE.get(res["status"], "")}]
        for c in res["checks"]:
            if c["outcome"] != "PASS":
                lines += wrap(f"failed {c['id']}: ", c["description"], "fail")
        lines += wrap("Objective: ", s["objective"])
        lines += wrap("Selection: ", s["selection"], "dim")
        if cap.get("reason"):
            lines += wrap("Cap reason: ", cap["reason"], "warn")
        lines += wrap("Session IDs: ", ", ".join(res.get("sessionIds") or ["none (no session-bound step)"]), "dim")
        lines.append({"text": f"Recorded {res['startedAt']} · real duration {rnd['castLastEventS']} s · then the paced replay", "style": "dim"})
        if nn == "08":
            att = PKG / "evidence" / "attempts" / "attempt1_08_error_recovery_session.mp4"
            att_r = json.loads((PKG / "evidence" / "render" / "attempt1_08_error_recovery_session.json").read_text())
            add_card("Scenario 08 · attempt 1 — aborted by a recording-harness bug", [
                {"text": "Kept, not cut. At step e3 the CLI correctly refused the injected non-loopback Ollama URL", "style": "warn"},
                {"text": "(exit 2, config_error). The recording harness then crashed while displaying that refusal", "style": "warn"},
                {"text": "(AttributeError in its compact view), so the rate-limit steps never ran.", "style": "warn"},
                {"text": "The agent was not at fault. Fix committed as 3cade47; attempt 2 follows this recording."},
                {"text": "Attempt 1 has no result file and is not counted as a scenario outcome.", "style": "dim"},
            ], 8.0, "editorial · S08 attempt 1")
            add_recording(att, "genuine recording · preserved aborted attempt (S08 attempt 1)", att_r)
        add_card(f"Scenario {nn} · {s['title']}", lines, 9.0, f"editorial · chapter {nn}")
        add_recording(PKG / f"{s['file']}.mp4", f"genuine recording · scenario {nn}", rnd)

    closing = [{"text": f"S{k} {plan['scenarios'][i]['title'][:52]:<53} {v['status']:<8} checks {sum(c['outcome'] == 'PASS' for c in v['checks'])}/{len(v['checks'])}",
                "style": STYLE.get(v["status"], "")} for i, (k, v) in enumerate(results.items())]
    closing += [{"text": ""},
                {"text": "The agent ran for real in all nine scenarios; no LLM was available (Ollama BLOCKED).", "style": "warn"},
                {"text": "Evidence: session_index.md · recording_verification.md · MANAGER_HANDOFF.md · traces/", "style": "dim"}]
    add_card("Results by scenario (pre-registered checks)", closing, 10.0, "editorial · closing")

    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{seg['file']}'\n" for seg in segments))
    out = PKG / "complete_agent_session.mp4"
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run([ff, "-hide_banner", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                        "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    manifest = {"schema": "lear-complete-session-manifest-v1", "output": "complete_agent_session.mp4", "method": "ffmpeg concat demuxer, -c copy (no re-encode)",
                "ffmpegExit": r.returncode, "ffmpegStderr": r.stderr[-400:], "segments": [{k: v for k, v in seg.items() if k != "file"} | {"order": i + 1}
                                                                                        for i, seg in enumerate(segments)],
                "totalSeconds": round(sum(seg["seconds"] or 0 for seg in segments), 3),
                "editorialSeconds": round(sum(seg["seconds"] for seg in segments if seg["kind"].startswith("editorial")), 3),
                "outputSha256": sha256(out) if out.exists() else None}
    (PKG / "evidence" / "complete_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ("ffmpegExit", "totalSeconds", "editorialSeconds", "outputSha256")}))
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
