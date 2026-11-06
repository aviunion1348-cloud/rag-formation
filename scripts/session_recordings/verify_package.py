#!/usr/bin/env python3
"""Verify the Lear agent session recording package.

  /tmp/recvenv/bin/python scripts/session_recordings/verify_package.py verify
      Checks every MP4 (container, codec, resolution, fps, duration, full decode,
      frame count, black frames, audio), every asciicast (structure, monotonic
      time, `asciinema cat` replay, byte-level consistency with the per-step
      trace files and with the harness timestamps), every scenario's traces, and
      scans the whole package for secrets, environment dumps, personal data and
      prompt disclosure. Writes evidence/verification.json and decoded sample
      frames to evidence/frames/.

  python3 scripts/session_recordings/verify_package.py scan
      Re-runs only the security scan (after the docs are written) and updates
      the security section of evidence/verification.json.

  python3 scripts/session_recordings/verify_package.py checksums
      Writes checksums.sha256 (sha256sum format) for every other file in the
      package, then re-reads it and re-verifies every entry. Run it last.

Read-only with respect to everything else. No network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "artifacts" / "agent_session_recordings"
PLAN = REPO / "scripts" / "session_recordings" / "scenario_plan.json"
TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".cast", ".out", ".txt", ".log", ".sha256", ".csv"}


def utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise SystemExit("ffmpeg not found (install imageio-ffmpeg in the recording venv)")
        return found


def asciinema_exe() -> Optional[str]:
    for cand in ("/tmp/recvenv/bin/asciinema", shutil.which("asciinema")):
        if cand and Path(cand).exists():
            return cand
    return None


def run(argv: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def file_record(path: Path) -> Dict[str, Any]:
    st = path.stat()
    return {"path": str(path.relative_to(PKG)), "bytes": st.st_size, "modifiedUtc": utc(st.st_mtime), "sha256": sha256(path)}


# ---------------------------------------------------------------- video checks
def probe_video(ff: str, path: Path) -> Dict[str, Any]:
    info = run([ff, "-hide_banner", "-i", str(path)]).stderr
    out: Dict[str, Any] = {"probeTool": "ffmpeg -i (ffprobe is not installed)"}
    m = re.search(r"Input #0, ([^ ]+), from", info)
    out["container"] = m.group(1).rstrip(",") if m else None
    m = re.search(r"major_brand\s*:\s*(\S+)", info)
    out["majorBrand"] = m.group(1) if m else None
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    out["durationS"] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 3) if m else None
    m = re.search(r"Video: (\w+)(?: \(([^)]+)\))?[^\n]*?, (\w+)\([^\n]*?, (\d{2,5})x(\d{2,5})", info)
    if m:
        out.update({"codec": m.group(1), "profile": m.group(2), "pixelFormat": m.group(3), "width": int(m.group(4)), "height": int(m.group(5))})
    m = re.search(r"Video:[^\n]*?([\d.]+) fps", info)
    out["fps"] = float(m.group(1)) if m else None
    out["audioStreams"] = len(re.findall(r"Stream #\d+:\d+[^\n]*: Audio:", info))
    out["videoStreams"] = len(re.findall(r"Stream #\d+:\d+[^\n]*: Video:", info))
    return out


def decode_video(ff: str, path: Path) -> Dict[str, Any]:
    full = run([ff, "-hide_banner", "-nostats", "-v", "error", "-progress", "pipe:1", "-i", str(path), "-map", "0:v", "-f", "null", "-"])
    frames = [int(x.split("=")[1]) for x in full.stdout.splitlines() if x.startswith("frame=")]
    black = run([ff, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:v",
                 "-vf", "blackdetect=d=0.5:pic_th=0.98:pix_th=0.05", "-f", "null", "-"])
    spans = re.findall(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", black.stderr)
    return {"fullDecodeExit": full.returncode, "decodeErrors": full.stderr.strip().splitlines()[:5],
            "decodedFrames": frames[-1] if frames else None,
            "blackdetect": "blackdetect d=0.5 s, pic_th=0.98, pix_th=0.05 (terminal background is RGB 24,26,33, above the threshold)",
            "blackSpans": [{"startS": float(a), "endS": float(b), "durationS": float(c)} for a, b, c in spans]}


def sample_frames(ff: str, path: Path, times: List[float], outdir: Path) -> List[Dict[str, Any]]:
    from PIL import Image, ImageStat  # recording venv
    outdir.mkdir(parents=True, exist_ok=True)
    res = []
    for t in times:
        dst = outdir / f"{path.stem}__t{t:07.2f}s.jpg"
        r = run([ff, "-hide_banner", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-q:v", "3", str(dst)])
        rec: Dict[str, Any] = {"atS": t, "file": str(dst.relative_to(PKG)), "exit": r.returncode}
        if r.returncode == 0 and dst.exists():
            img = Image.open(dst).convert("L")
            st = ImageStat.Stat(img)
            rec.update({"size": list(img.size), "meanLuma": round(st.mean[0], 2), "stddevLuma": round(st.stddev[0], 2),
                        "nonBlank": st.stddev[0] > 8.0})
        res.append(rec)
    return res


# ---------------------------------------------------------------- cast checks
def check_cast(path: Path) -> Dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rec: Dict[str, Any] = {"path": str(path.relative_to(PKG))}
    try:
        header = json.loads(lines[0])
        events = [json.loads(x) for x in lines[1:] if x.strip()]
    except Exception as exc:
        return {**rec, "valid": False, "error": f"{type(exc).__name__}: {exc}"}
    times = [e[0] for e in events]
    kinds = sorted({e[1] for e in events})
    rec.update({"valid": header.get("version") == 2 and all(len(e) == 3 for e in events),
                "version": header.get("version"), "width": header.get("width"), "height": header.get("height"),
                "startedUtc": utc(header["timestamp"]) if header.get("timestamp") else None,
                "headerEnv": header.get("env"), "title": header.get("title"), "events": len(events), "eventKinds": kinds,
                "monotonic": all(b >= a for a, b in zip(times, times[1:])), "durationS": round(times[-1], 3) if times else 0.0,
                "idleTimeLimit": header.get("idle_time_limit")})
    tool = asciinema_exe()
    if tool:
        r = subprocess.run([tool, "cat", str(path)], capture_output=True, timeout=120)
        rec["asciinemaCat"] = {"exit": r.returncode, "bytes": len(r.stdout), "tool": "asciinema 2.4.0 `cat` (full replay to stdout)"}
    rec["_text"] = "".join(e[2] for e in events if e[1] == "o").replace("\r", "")
    return rec


def iso(s: str) -> dt.datetime:
    return dt.datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")


HELPER_KINDS = {"scan_corpus": "harness helper: read-only corpus scan; summary on screen, full JSON in the .out file",
                "compare_eval": "harness helper: comparison with committed round-3; summary on screen, full JSON in the .out file"}


def scenario_consistency(result: Dict[str, Any], cast: Dict[str, Any]) -> Dict[str, Any]:
    text = cast.get("_text", "")
    kinds = {}
    for row in (PKG / "traces" / result["scenario"] / "steps.jsonl").read_text().splitlines():
        r = json.loads(row)
        kinds[r["stepId"]] = r.get("kind")
    steps = []
    for s in result["steps"]:
        if not s.get("out"):
            continue
        raw = (PKG / s["out"]).read_bytes().decode("utf-8", errors="replace").replace("\r", "")
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        found = sum(1 for ln in lines if ln in text)
        kind = kinds.get(s["stepId"])
        steps.append({"stepId": s["stepId"], "kind": kind, "helper": HELPER_KINDS.get(kind), "outFile": s["out"],
                      "outSha256Matches": sha256(PKG / s["out"]) == s.get("outSha256"),
                      "outputInCast": raw in text, "allLinesInCast": found == len(lines), "linesFoundInCast": f"{found}/{len(lines)}"})
    agent = [x for x in steps if not x["helper"]]
    traces = []
    for cid in result.get("correlationIds") or []:
        p = PKG / "traces" / result["scenario"] / f"engine_trace_{cid}.json"
        ok = False
        if p.exists():
            t = json.loads(p.read_text())
            ok = cid in json.dumps(t)[:4000]
        traces.append({"correlationId": cid, "engineTraceFile": str(p.relative_to(PKG)) if p.exists() else None, "idMatches": ok})
    start, end = iso(result["startedAt"]), iso(result["endedAt"])
    cast_start = dt.datetime.strptime(cast["startedUtc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if cast.get("startedUtc") else None
    rows = (PKG / result["events"]).read_text().splitlines() if result.get("events") else []
    step_rows = (PKG / "traces" / result["scenario"] / "steps.jsonl").read_text().splitlines()
    return {"steps": steps, "agentSteps": len(agent), "helperSteps": len(steps) - len(agent),
            "allAgentOutputLinesInCast": all(x["allLinesInCast"] for x in agent),
            "agentOutputsContiguous": f"{sum(x['outputInCast'] for x in agent)}/{len(agent)}",
            "nonContiguousNote": "S08 r1 (chat): the harness prints a labelled USER line when it writes the burst into the chat's stdin, and "
                                 "that line lands between chunks of the chat's output; every output line is still present" if any(
                                     not x["outputInCast"] for x in agent) else None,
            "allStepOutputsInCast": all(x["outputInCast"] for x in steps), "allOutSha256Match": all(x["outSha256Matches"] for x in steps),
            "sessionIdsInCast": {sid: sid in text for sid in result.get("sessionIds") or []},
            "engineTraces": traces,
            "harnessElapsedS": round((end - start).total_seconds(), 3), "castDurationS": cast.get("durationS"),
            "castStartVsHarnessStartS": round((start - cast_start).total_seconds(), 3) if cast_start else None,
            "eventsJsonlRows": len(rows), "eventsJsonlValid": all(_is_json(x) for x in rows),
            "stepsJsonlRows": len(step_rows), "stepsJsonlMatchesResult": len(step_rows) == len(result["steps"])}


def _is_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- security scan
PATTERNS = {
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "github_token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "openai_style_key": r"\bsk-[A-Za-z0-9_-]{20,}",
    "slack_token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "private_key_block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "jwt": r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "secret_assignment": r"\b[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*\s*[=:]\s*['\"]?(?![$<{*\[])[A-Za-z0-9/+_\-]{16,}",
    "bearer_token": r"(?i)\bbearer\s+[A-Za-z0-9._\-]{24,}",
    "email_address": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "other_home_dir": r"/home/(?!user\b)[a-z][a-z0-9_-]*/",
    "env_dump_line": r"(?m)^(?:PATH|HOME|HOSTNAME|PWD|LANG|LOGNAME)=\S",
    "ipv4": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}
IPV4_ALLOWED = (re.compile(r"^127\."), re.compile(r"^0\.0\.0\.0$"), re.compile(r"^192\.0\.2\."))  # loopback, any, TEST-NET-1 (RFC 5737)


def security_scan() -> Dict[str, Any]:
    sys.path.insert(0, str(REPO))
    from ragformation.lear import prompts  # type: ignore
    prompt_lines = [ln.strip() for txt in (prompts.LEAR_SYSTEM_PROMPT, prompts.CONTEXT_TEMPLATE, prompts.MEMORY_TEMPLATE)
                    for ln in txt.splitlines() if len(ln.strip()) >= 40]
    files = [p for p in sorted(PKG.rglob("*")) if p.is_file() and p.suffix in TEXT_SUFFIXES and p.name != "checksums.sha256"]
    findings: List[Dict[str, Any]] = []
    ipv4_seen: Dict[str, int] = {}
    prompt_hits = []
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        for name, rx in PATTERNS.items():
            for m in re.finditer(rx, text):
                val = m.group(0)
                if name == "ipv4":
                    parts = val.split(".")
                    if any(int(x) > 255 for x in parts):
                        continue
                    ipv4_seen[val] = ipv4_seen.get(val, 0) + 1
                    if any(r.match(val) for r in IPV4_ALLOWED):
                        continue
                line = text.count("\n", 0, m.start()) + 1
                masked = val[:4] + "…" + val[-2:] if len(val) > 8 else val
                findings.append({"file": str(p.relative_to(PKG)), "line": line, "pattern": name, "masked": masked})
        for ln in prompt_lines:
            if ln in text:
                prompt_hits.append({"file": str(p.relative_to(PKG)), "promptLine": ln[:60] + "…"})
    return {"scope": f"{len(files)} text files under {PKG.relative_to(REPO)} (casts, per-step outputs, traces, JSON, Markdown); "
                     "MP4 frames are rendered from the scanned casts and add no text of their own",
            "filesScanned": len(files), "patterns": sorted(PATTERNS), "findings": findings,
            "ipv4Seen": ipv4_seen, "ipv4Allowed": "127.0.0.0/8, 0.0.0.0, 192.0.2.0/24 (TEST-NET-1, used as the injected unreachable host)",
            "systemPromptLinesChecked": len(prompt_lines), "systemPromptDisclosures": prompt_hits}


# ---------------------------------------------------------------- main
def verify(reuse_video: bool = False) -> int:
    plan = json.loads(PLAN.read_text())
    ff = ffmpeg_exe()
    previous: Dict[str, Dict[str, Any]] = {}
    old = PKG / "evidence" / "verification.json"
    if reuse_video and old.exists():
        previous = {v["sha256"]: v for v in json.loads(old.read_text()).get("videos", [])}
    report: Dict[str, Any] = {"schema": "lear-session-recording-verification-v1", "generatedAtUtc": utc(dt.datetime.now().timestamp()),
                              "ffmpeg": run([ff, "-version"]).stdout.splitlines()[0], "videos": [], "casts": [], "scenarios": []}
    videos = sorted(PKG.glob("*.mp4")) + sorted((PKG / "evidence" / "attempts").glob("*.mp4"))
    for v in videos:
        fr = file_record(v)
        if fr["sha256"] in previous:  # identical bytes: the earlier decode results still hold
            rec = {**previous[fr["sha256"]], **fr, "reusedFromEarlierDecode": True}
            report["videos"].append(rec)
            print(f"video {rec['path']}: ok={rec['ok']} (identical sha256; earlier full decode reused)", flush=True)
            continue
        rec = {**fr, **probe_video(ff, v), **decode_video(ff, v)}
        render = PKG / "evidence" / "render" / f"{v.stem}.json"
        r = json.loads(render.read_text()) if render.exists() else {}
        times = []
        if r.get("realtimeSeconds"):
            times.append(max(0.0, r["realtimeSeconds"] - 1.0))
        if r.get("pacedSeconds"):
            times.append(round(r["realtimeSeconds"] + r["pacedSeconds"] / 2, 2))
        if not times and rec.get("durationS"):
            times = [round(rec["durationS"] / 2, 2)]
        rec["sampleFrames"] = sample_frames(ff, v, times, PKG / "evidence" / "frames")
        rec["render"] = {k: r.get(k) for k in ("realtimeSeconds", "pacedSeconds", "holds", "holdSeconds", "castLastEventS", "frames")} if r else None
        exp = rec.get("durationS") and rec.get("fps") and rec.get("decodedFrames")
        rec["framesMatchDuration"] = bool(exp) and abs(rec["decodedFrames"] - rec["durationS"] * rec["fps"]) <= 2
        rec["ok"] = (rec.get("container", "") or "").startswith("mov,mp4") and rec.get("codec") == "h264" and rec.get("width") == 1920 \
            and rec.get("height") == 1080 and rec["fullDecodeExit"] == 0 and not rec["decodeErrors"] and (rec["decodedFrames"] or 0) > 0 \
            and not rec["blackSpans"] and rec["framesMatchDuration"] and all(f.get("nonBlank") for f in rec["sampleFrames"])
        report["videos"].append(rec)
        print(f"video {rec['path']}: ok={rec['ok']} {rec.get('durationS')} s {rec.get('decodedFrames')} frames", flush=True)
    casts: Dict[str, Dict[str, Any]] = {}
    for c in sorted((PKG / "terminal").glob("*.cast")) + sorted((PKG / "evidence" / "attempts").glob("*.cast")):
        rec = check_cast(c)
        casts[rec["path"]] = rec
        report["casts"].append({**file_record(c), **{k: v for k, v in rec.items() if k != "_text"}})
    for sc in plan["scenarios"]:
        res_path = PKG / "evidence" / "results" / f"{sc['id']}.json"
        if not res_path.exists():
            report["scenarios"].append({"scenario": sc["id"], "result": None})
            continue
        result = json.loads(res_path.read_text())
        cast = casts.get(result["cast"], {})
        report["scenarios"].append({"scenario": sc["id"], "name": sc["name"], "status": result["status"],
                                    "consistency": scenario_consistency(result, cast)})
    report["security"] = security_scan()
    sec = report["security"]
    report["summary"] = {
        "videosChecked": len(report["videos"]), "videosOk": sum(v["ok"] for v in report["videos"]),
        "castsChecked": len(report["casts"]), "castsValid": sum(bool(c.get("valid")) and c.get("monotonic") and (c.get("asciinemaCat") or {}).get("exit") == 0 for c in report["casts"]),
        "scenarioOutputsConsistent": sum(s.get("consistency", {}).get("allAgentOutputLinesInCast", False) for s in report["scenarios"]),
        "securityFindings": len(sec["findings"]), "systemPromptDisclosures": len(sec["systemPromptDisclosures"]),
    }
    out = PKG / "evidence" / "verification.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))
    return 0


def checksums() -> int:
    target = PKG / "checksums.sha256"
    files = [p for p in sorted(PKG.rglob("*")) if p.is_file() and p != target]
    lines = [f"{sha256(p)}  {p.relative_to(PKG).as_posix()}" for p in files]
    target.write_text("\n".join(lines) + "\n")
    bad = []
    for ln in target.read_text().splitlines():
        digest, rel = ln.split("  ", 1)
        if sha256(PKG / rel) != digest:
            bad.append(rel)
    total = sum(p.stat().st_size for p in files) + target.stat().st_size
    print(json.dumps({"entries": len(lines), "mismatches": bad, "packageBytes": total, "checksumsFile": str(target.relative_to(REPO))}))
    return 1 if bad else 0


def rescan() -> int:
    """Re-run only the security scan (fast) and update evidence/verification.json -- used after the docs are written."""
    out = PKG / "evidence" / "verification.json"
    report = json.loads(out.read_text())
    report["security"] = security_scan()
    report["securityScannedAtUtc"] = utc(dt.datetime.now().timestamp())
    report["summary"]["securityFindings"] = len(report["security"]["findings"])
    report["summary"]["systemPromptDisclosures"] = len(report["security"]["systemPromptDisclosures"])
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"filesScanned": report["security"]["filesScanned"], "findings": report["security"]["findings"],
                      "systemPromptDisclosures": report["security"]["systemPromptDisclosures"], "ipv4Seen": report["security"]["ipv4Seen"]}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["verify", "scan", "checksums"])
    ap.add_argument("--reuse-video", action="store_true", help="reuse earlier decode results for MP4s whose sha256 is unchanged")
    a = ap.parse_args()
    if a.command == "verify":
        return verify(a.reuse_video)
    return {"scan": rescan, "checksums": checksums}[a.command]()


if __name__ == "__main__":
    sys.exit(main())
