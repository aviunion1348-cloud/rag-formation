#!/usr/bin/env python3
"""Recording harness for the Lear agent session recordings. NEW INSTRUMENTATION.

What it is
  The program that asciinema records. It executes one pre-registered scenario
  from ``scenario_plan.json``. It is not part of the agent, it is not imported
  by it, and it changes nothing in it.

What is real
  Every agent operation is a real ``python3 -m ragformation lear ...`` process,
  started in its own pseudo-terminal. Its bytes are copied unmodified to this
  terminal (which asciinema records) and to a raw per-step log
  ``traces/<NN>/<step>.out``.

What the harness adds (always on lines that start with a coloured "▌")
  * labels: scenario header, step titles, UTC timestamps, exit codes, durations;
  * VIEW blocks: readable views of the JSON the CLI just printed. The answer
    view is produced by the CLI's own formatters
    (``ragformation.lear.cli._render_answer`` and ``_render_debug``); the other
    rows copy fields from that same JSON;
  * TRACE blocks: a read-only export of the trace the engine itself persisted
    for the printed correlation id (SQLite table ``queries``, column
    ``trace_json``);
  * CHECK lines: the pre-registered acceptance checks, evaluated with the
    repository's own scorer (``ragformation.lear.evaluate.score_case``) where a
    golden case applies.

Usage (normally via record_all.sh, inside ``asciinema rec``):
  python3 scripts/session_recordings/harness.py --scenario 01 --run-id lear-rec-... \
      --stamp 20261106T1230Z --root /tmp/lear-rec-... --pkg artifacts/agent_session_recordings
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import platform
import pty
import re
import select
import shlex
import shutil
import sqlite3
import struct
import subprocess
import sys
import termios
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

RESET = "\x1b[0m"
STYLE = {
    "REC": "\x1b[1;35m",
    "NOTE": "\x1b[35m",
    "STEP": "\x1b[1;36m",
    "USER": "\x1b[1;33m",
    "EXIT": "\x1b[2;37m",
    "VIEW": "\x1b[36m",
    "TRACE": "\x1b[34m",
    "WAIT": "\x1b[33m",
    "CHECK": "\x1b[1;37m",
    "RESULT": "\x1b[1;35m",
}
CMD = "\x1b[1;32m"
OUTCOME_STYLE = {"PASS": "\x1b[1;32m", "FAIL": "\x1b[1;31m", "INFO": "\x1b[1;33m"}
SEVERITY = {"PASS": 0, "PARTIAL": 1, "FAIL": 2}
SECRET_PATTERNS = {
    "openai_style_key": r"sk-[A-Za-z0-9_\-]{12,}",
    "private_key_block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "aws_access_key_id": r"AKIA[0-9A-Z]{16}",
    "github_token": r"gh[pousr]_[A-Za-z0-9]{20,}",
    "slack_token": r"xox[abprs]-[A-Za-z0-9-]{10,}",
    "bearer_header": r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._\-]{12,}",
    "assigned_secret": r"(?i)\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*[=:]\s*['\"]?[A-Za-z0-9/+_\-]{16,}",
}
PROMPT_MARKERS = (
    "Rules, in priority order",
    "You are the Lear Backend Engineering Assistant",
    "Never invent an id",
    "reply with exactly",
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def hms(t: dt.datetime) -> str:
    return t.strftime("%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_of(raw: bytes) -> str:
    return re.sub(r"\r+\n", "\n", raw.decode("utf-8", "replace"))


def first_json(raw: bytes) -> Optional[Any]:
    """The first complete JSON value in a step's output (the CLI prints one)."""

    text = text_of(raw)
    for start in [m.start() for m in re.finditer(r"[\[{]", text)][:5]:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
            return value
        except ValueError:
            continue
    return None


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.plan_path = Path(args.plan).resolve()
        self.plan_bytes = self.plan_path.read_bytes()
        self.plan = json.loads(self.plan_bytes)
        self.scenario = next(s for s in self.plan["scenarios"] if s["id"] == args.scenario)
        self.golden = {c["id"]: c for c in json.loads((REPO / self.plan["golden"]).read_text())["cases"]}
        self.run_id, self.stamp = args.run_id, args.stamp
        self.root = Path(args.root)
        self.data = self.root / "data"
        self.pkg = Path(args.pkg).resolve()
        self.traces = self.pkg / "traces" / self.scenario["id"]
        self.results_dir = self.pkg / "evidence" / "results"
        self.traces.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
            self.cols, self.rows = size.columns, size.lines
        except OSError:
            self.cols, self.rows = self.plan["terminal"]["cols"], self.plan["terminal"]["rows"]
        self.ctx: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self.step_rows: List[Dict[str, Any]] = []
        self._contexts: Optional[Dict[str, str]] = None
        self.started = utcnow()

    # ------------------------------------------------------------- output
    def say(self, tag: str, text: str = "", *, style: Optional[str] = None) -> None:
        style = style or STYLE.get(tag, "")
        prefix = f"▌{tag:<6} "
        width = max(40, self.cols - len(prefix) - 1)
        lines: List[str] = []
        for para in str(text).split("\n"):
            lines.extend(textwrap.wrap(para, width=width, replace_whitespace=False, drop_whitespace=False) or [""])
        for line in lines:
            sys.stdout.write(f"{style}{prefix}{line}{RESET}\n")
        sys.stdout.flush()

    def substitute(self, value: str) -> str:
        return (value.replace("{STAMP}", self.stamp).replace("{RUN}", self.run_id)
                .replace("{D}", str(self.data)).replace("{EVAL}", str(self.root / "eval"))
                .replace("{BENCH}", str(self.root / "bench")))

    # ------------------------------------------------------------ process
    def child_env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RAG_", "OLLAMA_"))}
        env.update({"TERM": self.plan["terminal"].get("term", "xterm-256color"), "PYTHONDONTWRITEBYTECODE": "1"})
        env.pop("COLUMNS", None)
        env.pop("LINES", None)
        env.update(extra or {})
        return env

    def run_child(self, argv: List[str], env: Dict[str, str], out_path: Path, *, echo: bool = True,
                  driver: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))
        if not echo:
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
        started_wall, started = utcnow(), time.perf_counter()
        proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=str(REPO), env=env,
                                start_new_session=True, close_fds=True)
        os.close(slave)
        buf = bytearray()
        log = out_path.open("wb")

        def pump(timeout: float) -> str:
            ready, _, _ = select.select([master], [], [], timeout)
            if not ready:
                return "timeout"
            try:
                chunk = os.read(master, 65536)
            except OSError:
                return "eof"
            if not chunk:
                return "eof"
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            log.write(chunk)
            buf.extend(chunk)
            return "data"

        extra: Dict[str, Any] = {}
        try:
            if driver is not None:
                extra = driver(master, buf, pump) or {}
            while True:
                state = pump(0.1)
                if state == "eof":
                    break
                if state == "timeout" and proc.poll() is not None and pump(0.3) != "data":
                    break
        finally:
            log.close()
        code = proc.wait()
        os.close(master)
        ended_wall = utcnow()
        raw = bytes(buf)
        return {"argv": argv, "exit": code, "startedAt": iso(started_wall), "endedAt": iso(ended_wall),
                "ms": round((time.perf_counter() - started) * 1000.0, 1), "out": str(out_path.relative_to(self.pkg)),
                "outBytes": len(raw), "outSha256": sha256(raw), "raw": raw, **extra}

    def lear_argv(self, backend: str, args: List[str]) -> List[str]:
        return ["python3", "-m", "ragformation", "lear", "--backend", backend, "--data-dir", str(self.data)] + args

    def show_command(self, argv: List[str], env_extra: Optional[Dict[str, str]]) -> None:
        prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in (env_extra or {}).items())
        line = (prefix + " " if prefix else "") + shlex.join(argv)
        sys.stdout.write(f"{CMD}$ {line}{RESET}\n")
        sys.stdout.flush()

    def step_title(self, index: int, total: int, step: Dict[str, Any]) -> None:
        self.say("STEP", f"{index}/{total} · {step.get('label', step['kind'])} · {hms(utcnow())}")

    def finish_step(self, step: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.say("EXIT", f"exit {result['exit']} · {result['ms']:.0f} ms · finished {result['endedAt'][11:]} · raw output {result['out']} ({result['outBytes']} bytes)")
        row = {k: v for k, v in result.items() if k not in ("raw", "json")}
        row.update({"stepId": step["id"], "kind": step["kind"], "label": step.get("label")})
        self.step_rows.append(row)

    # -------------------------------------------------------------- steps
    def question_for(self, step: Dict[str, Any]) -> Tuple[str, str]:
        if "case" in step:
            return self.golden[step["case"]]["question"], f"golden {step['case']} ({self.golden[step['case']]['split']} split)"
        if "historyOf" in step:
            return self.golden[step["historyOf"]]["history"][0], f"golden history turn of {step['historyOf']}"
        if "controlled" in step:
            return self.scenario["controlled"][step["controlled"]], "CONTROLLED test input (written in the plan)"
        return step["text"], "CONTROLLED test input (written in the plan)"

    def do_cli(self, step: Dict[str, Any]) -> Dict[str, Any]:
        argv = self.lear_argv(step.get("backend", "baseline"), [self.substitute(a) for a in step["args"]])
        env_extra = {k: self.substitute(v) for k, v in (step.get("env") or {}).items()}
        self.show_command(argv, env_extra)
        result = self.run_child(argv, self.child_env(env_extra), self.traces / f"{step['id']}.out")
        result["json"] = first_json(result["raw"])
        return result

    def do_ask(self, step: Dict[str, Any]) -> Dict[str, Any]:
        question, source = self.question_for(step)
        args = ["ask", "--json"]
        if step.get("session"):
            args += ["--session", self.substitute(step["session"]), "--user", step.get("user", "local")]
        args.append(question)
        argv = self.lear_argv(step.get("backend", "baseline"), args)
        env_extra = {k: self.substitute(v) for k, v in (step.get("env") or {}).items()}
        self.say("USER", f"▶ {question}   [{source}]")
        self.show_command(argv, env_extra)
        result = self.run_child(argv, self.child_env(env_extra), self.traces / f"{step['id']}.out")
        answer = first_json(result["raw"])
        result["json"] = answer
        result["question"] = question
        result["questionSource"] = source
        if isinstance(answer, dict):
            result["correlationId"] = answer.get("correlationId")
            result["sessionId"] = (answer.get("session") or {}).get("id")
            (self.traces / f"{step['id']}.answer.json").write_text(json.dumps(answer, indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    def do_chat(self, step: Dict[str, Any]) -> Dict[str, Any]:
        question, source = self.question_for(step)
        session = self.substitute(step["session"])
        argv = self.lear_argv(step.get("backend", "baseline"), ["chat", "--session", session, "--user", step["user"]])
        burst, after = int(step["burst"]), int(step.get("after", 1))
        self.show_command(argv, None)
        harness = self

        def driver(master: int, buf: bytearray, pump: Callable[[float], str]) -> Dict[str, Any]:
            def answers() -> int:
                return bytes(buf).count(b"Correlation id:")

            def limits() -> List[float]:
                return [float(x) for x in re.findall(rb'"retryAfterS":\s*([0-9.]+)', bytes(buf))]

            for _ in range(10):  # let the chat print its banner first
                pump(0.1)
            t_burst = utcnow()
            harness.say("USER", f"▶ {burst} × \"{question}\" written to the chat's stdin at once at {hms(t_burst)} (one user, one session)   [{source}]")
            os.write(master, ((question + "\n") * burst).encode())
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline and answers() + len(limits()) < burst:
                pump(0.1)
            for _ in range(5):
                pump(0.05)
            answered, lim = answers(), limits()
            info: Dict[str, Any] = {"burst": burst, "burstAt": iso(t_burst), "answeredInBurst": answered,
                                    "rateLimitedInBurst": len(lim), "retryAfterS": lim[0] if lim else None}
            if lim:
                wait = lim[-1] + 1.0
                harness.say("WAIT", f"the agent's rate limiter reported retryAfterS={lim[-1]} · waiting {wait:.1f} s of real time (nothing is skipped or sped up)")
                end = time.monotonic() + wait
                next_tick = time.monotonic() + 10
                while time.monotonic() < end:
                    pump(min(0.5, max(0.0, end - time.monotonic())))
                    if time.monotonic() >= next_tick and end - time.monotonic() > 1:
                        harness.say("WAIT", f"{end - time.monotonic():.0f} s left · {hms(utcnow())}")
                        next_tick += 10
            for n in range(after):
                t_after = utcnow()
                before_a, before_l = answers(), len(limits())
                harness.say("USER", f"▶ \"{question}\" (request {burst + n + 1}) written at {hms(t_after)}")
                os.write(master, (question + "\n").encode())
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline and answers() == before_a and len(limits()) == before_l:
                    pump(0.1)
                for _ in range(5):
                    pump(0.05)
                info.setdefault("after", []).append({"at": iso(t_after), "answered": answers() - before_a,
                                                     "rateLimited": len(limits()) - before_l})
            os.write(master, b"/exit\n")
            return info

        result = self.run_child(argv, self.child_env(), self.traces / f"{step['id']}.out", echo=False, driver=driver)
        result["question"] = question
        result["questionSource"] = source
        result["sessionId"] = session
        result["correlationIds"] = re.findall(r"Correlation id: (q-[0-9a-f]{12})", text_of(result["raw"]))
        return result

    def do_scan(self, step: Dict[str, Any]) -> Dict[str, Any]:
        from ragformation.guardrails import INJECTION_RULES, scan_text

        db = self.engine_db()
        self.say("VIEW", f"read-only: SELECT chunk_id, text FROM chunks  ({db.relative_to(self.root.parent) if db else 'no index'})  → ragformation.guardrails.scan_text(text) per chunk")
        started_wall, started = utcnow(), time.perf_counter()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("SELECT chunk_id, text FROM chunks ORDER BY chunk_id").fetchall()
        con.close()
        by_rule: Dict[str, List[str]] = {rule: [] for rule in INJECTION_RULES}
        for chunk_id, text in rows:
            for finding in scan_text(text):
                by_rule.setdefault(finding.rule, []).append(chunk_id)
        flagged = sorted({cid for ids in by_rule.values() for cid in ids})
        for rule, ids in by_rule.items():
            self.say("VIEW", f"  {rule:<22} {len(ids):>3} finding(s)" + (f"  chunks: {', '.join(sorted(set(ids))[:6])}" if ids else ""))
        self.say("VIEW", f"  chunks scanned {len(rows)} · chunks with any finding {len(flagged)} (document text is not printed)")
        payload = {"chunksScanned": len(rows), "findingsByRule": {k: sorted(set(v)) for k, v in by_rule.items()},
                   "flaggedChunks": flagged, "rules": list(INJECTION_RULES), "function": "ragformation.guardrails.scan_text"}
        out = self.traces / f"{step['id']}.scan.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        raw = json.dumps(payload).encode()
        return {"argv": ["(harness) guardrail scan"], "exit": 0, "startedAt": iso(started_wall), "endedAt": iso(utcnow()),
                "ms": round((time.perf_counter() - started) * 1000.0, 1), "out": str(out.relative_to(self.pkg)),
                "outBytes": len(raw), "outSha256": sha256(out.read_bytes()), "raw": raw, "json": payload}

    def do_compare(self, step: Dict[str, Any]) -> Dict[str, Any]:
        started_wall, started = utcnow(), time.perf_counter()
        new_path, old_path = Path(self.substitute(step["run"])), REPO / step["baseline"]
        self.say("VIEW", f"read-only comparison: {new_path}  vs  {step['baseline']}")
        payload: Dict[str, Any] = {"run": str(new_path), "baseline": step["baseline"]}
        if not new_path.exists():
            payload["error"] = "run.json missing"
        else:
            new, old = json.loads(new_path.read_text()), json.loads(old_path.read_text())
            n_cases, o_cases = {c["id"]: c for c in new["cases"]}, {c["id"]: c for c in old["cases"]}
            common = sorted(set(n_cases) & set(o_cases))
            diffs = []
            same = {"status": 0, "correct": 0, "correctStrict": 0, "answerText": 0, "citations": 0}
            for cid in common:
                a, b = n_cases[cid], o_cases[cid]
                ma, mb = a.get("metrics") or {}, b.get("metrics") or {}
                eq = {"status": ma.get("status") == mb.get("status"), "correct": ma.get("correct") == mb.get("correct"),
                      "correctStrict": ma.get("correctStrict") == mb.get("correctStrict"),
                      "answerText": (a.get("answer") or "") == (b.get("answer") or ""),
                      "citations": len(a.get("citations") or []) == len(b.get("citations") or [])}
                for k, v in eq.items():
                    same[k] += int(v)
                if not (eq["status"] and eq["correct"]):
                    diffs.append({"id": cid, "status": [mb.get("status"), ma.get("status")], "correct": [mb.get("correct"), ma.get("correct")]})
            payload.update({"casesNew": len(n_cases), "casesBaseline": len(o_cases), "common": len(common),
                            "onlyNew": sorted(set(n_cases) - set(o_cases)), "onlyBaseline": sorted(set(o_cases) - set(n_cases)),
                            "identical": same, "differences": diffs,
                            "summaryNew": {k: new["summary"].get(k) for k in ("correct", "retrieval", "proxyScores", "statusCounts")},
                            "summaryBaseline": {k: old["summary"].get(k) for k in ("correct", "retrieval", "proxyScores", "statusCounts")},
                            "baselineFinishedAt": old.get("finishedAt"), "newFinishedAt": new.get("finishedAt")})
            self.say("VIEW", f"  cases: this run {len(n_cases)} · committed round-3 {len(o_cases)} · common {len(common)}")
            for k, v in same.items():
                self.say("VIEW", f"  identical {k:<13} {v}/{len(common)}")
            self.say("VIEW", f"  correct (this run)      {json.dumps(payload['summaryNew']['correct'])}")
            self.say("VIEW", f"  correct (round-3)       {json.dumps(payload['summaryBaseline']['correct'])}")
            self.say("VIEW", f"  status counts (this run) {json.dumps(payload['summaryNew']['statusCounts'])}")
            for d in diffs[:8]:
                self.say("VIEW", f"  differs: {d['id']} status {d['status'][0]} → {d['status'][1]} · correct {d['correct'][0]} → {d['correct'][1]}")
            for name in ("run.json", "summary.md"):
                src = new_path.parent / name
                if src.exists():
                    shutil.copyfile(src, self.traces / f"eval_{name}")
        out = self.traces / f"{step['id']}.compare.json"
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return {"argv": ["(harness) eval comparison"], "exit": 0 if "error" not in payload else 1, "startedAt": iso(started_wall),
                "endedAt": iso(utcnow()), "ms": round((time.perf_counter() - started) * 1000.0, 1),
                "out": str(out.relative_to(self.pkg)), "outBytes": out.stat().st_size, "outSha256": sha256(out.read_bytes()),
                "raw": out.read_bytes(), "json": payload}

    # --------------------------------------------------------- telemetry
    def engine_db(self) -> Optional[Path]:
        found = sorted(self.data.glob("index-*/ragformation.db"))
        return found[0] if found else None

    def engine_row(self, query_id: Optional[str]) -> Optional[Dict[str, Any]]:
        db = self.engine_db()
        if not query_id or not db:
            return None
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM queries WHERE query_id = ?", (query_id,)).fetchone()
        con.close()
        return dict(row) if row else None

    def contexts(self) -> Dict[str, str]:
        if self._contexts is None:
            db = self.engine_db()
            self._contexts = {}
            if db:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                self._contexts = dict(con.execute("SELECT chunk_id, text FROM chunks").fetchall())
                con.close()
        return self._contexts

    def export_trace(self, step_id: str, answer: Dict[str, Any]) -> None:
        corr = answer.get("correlationId")
        session_id = (answer.get("session") or {}).get("id")
        lifecycle = ((answer.get("timings") or {}).get("lifecycle") or {}).get("stages") or []
        for i, span in enumerate(lifecycle, 1):
            self.events.append({"event_id": None, "event_name": f"lifecycle.{span['stage']}", "timestamp_utc": None,
                                "timestamp_local": None, "monotonic_timestamp": {"msSinceAskStart": span.get("startMs")},
                                "session_id": session_id, "conversation_id": None, "trace_id": corr, "span_id": None,
                                "parent_span_id": None, "sequence_number": i, "stage": span["stage"], "status": "completed",
                                "duration_ms": span.get("ms"), "component": "agent (LearAgent.ask)", "model": None,
                                "provider": None, "input_metadata": None, "output_metadata": None,
                                "evidence_reference": f"traces/{self.scenario['id']}/{step_id}.out",
                                "source": "answer JSON printed by the CLI: timings.lifecycle (lear-lifecycle-v1)",
                                "run_id": self.run_id, "scenario_id": self.scenario["id"], "step_id": step_id})
        row = self.engine_row(corr)
        if not row:
            reason = "the engine was not called" if answer.get("engineStatus") in ("not_called", "failed") or not corr else "no persisted row found"
            self.say("TRACE", f"no engine trace for this answer: {reason} (engineStatus={answer.get('engineStatus')})")
            return
        events = json.loads(row.get("trace_json") or "[]")
        path = self.traces / f"engine_trace_{corr}.json"
        path.write_text(json.dumps({**{k: v for k, v in row.items() if k != "trace_json"}, "trace": events}, indent=2, ensure_ascii=False), encoding="utf-8")
        rel = str(path.relative_to(self.pkg))
        self.say("TRACE", f"┌ engine trace persisted by the engine itself (SQLite queries.trace_json) · trace id {corr} · read-only export")
        self.say("TRACE", f"│ {'#':>2}  {'at (UTC, 1 s)':<13} {'stage':<26} {'status':<10} {'ms':>8}  detail (as persisted by the engine; no document text; 'at' is stored at 1-second precision)")
        for i, ev in enumerate(events, 1):
            detail = ev.get("detail") or {}
            short = ", ".join(f"{k}={v}" for k, v in detail.items() if not isinstance(v, (list, dict)))[:78]
            dur = f"{ev['durationMs']:.3f}" if isinstance(ev.get("durationMs"), (int, float)) else ""
            self.say("TRACE", f"│ {i:>2}  {str(ev.get('at', ''))[11:24]:<13} {ev.get('stage', ''):<26} {ev.get('status', ''):<10} {dur:>8}  {short}")
            self.events.append({"event_id": None, "event_name": f"{ev.get('stage')}.{ev.get('status')}", "timestamp_utc": ev.get("at"),
                                "timestamp_local": None, "monotonic_timestamp": None, "session_id": session_id,
                                "conversation_id": None, "trace_id": corr, "span_id": None, "parent_span_id": None,
                                "sequence_number": i, "stage": ev.get("stage"), "status": ev.get("status"),
                                "duration_ms": ev.get("durationMs"), "component": "engine (RAGEngine.ask)",
                                "model": row.get("model"), "provider": row.get("provider"), "input_metadata": None,
                                "output_metadata": detail or None, "evidence_reference": rel,
                                "source": "engine SQLite queries.trace_json", "run_id": self.run_id,
                                "scenario_id": self.scenario["id"], "step_id": step_id})
        self.say("TRACE", f"├ row: provider {row.get('provider')}:{row.get('model')} · latency {row.get('latency_ms')} ms · retrieval {row.get('retrieval_ms')} ms · generation {row.get('generation_ms')} ms · ttft {row.get('ttft_ms')} ms · prompt tokens {row.get('prompt_tokens')} · completion tokens {row.get('completion_tokens')} (estimated={bool(row.get('tokens_estimated'))}) · citations valid {row.get('citations_valid')}/{row.get('citations_total')}")
        self.say("TRACE", f"└ saved {rel}")

    def view(self, step: Dict[str, Any], result: Dict[str, Any]) -> None:
        answer = result.get("json")
        if not isinstance(answer, dict):
            self.say("VIEW", "the command did not print an answer JSON (see the raw output above)")
            return
        mode = step.get("view", "full")
        t = answer.get("timings") or {}
        itl = t.get("itlMs") or {}
        sess = answer.get("session") or {}
        comp = sess.get("compaction")
        models = answer.get("models") or {}
        gen, emb = models.get("generation") or {}, models.get("embedding") or {}
        if mode == "compact":
            self.say("VIEW", f"status {answer.get('status')} · backend {answer.get('backend')} · qualifying {answer.get('qualifying')} · turn {sess.get('turn')} · kept history {sess.get('historyMessages')} msgs · prompt ≈{answer.get('promptTokens')} tok · total {t.get('totalMs')} ms · ttft {t.get('ttftMs')} ms · correlation {answer.get('correlationId')}")
            err = answer.get("error")
            if isinstance(err, dict):
                self.say("VIEW", f"error {err.get('code')}: {err.get('message')} · dependencyUnavailable={err.get('dependencyUnavailable')}")
            elif err:
                self.say("VIEW", f"error {err}: {answer.get('message')} · hint {answer.get('hint')}")
            if answer.get("answer"):
                flat = " ".join(answer["answer"].split())
                self.say("VIEW", "answer: " + (flat[:300] + " …" if len(flat) > 300 else flat))
            docs = [f"{c.get('document')} L{c.get('lines')}" for c in answer.get("citations") or []]
            if docs:
                self.say("VIEW", "citations: " + " · ".join(docs[:5]))
            rq = (answer.get("retrieval") or {}).get("retrievalQuery")
            if rq:
                self.say("VIEW", f"rewritten retrieval query: {rq[:200]}")
            if comp:
                self.say("VIEW", f"COMPACTION this turn: {comp.get('tokens_before') or comp.get('tokensBefore')} → {comp.get('tokens_after') or comp.get('tokensAfter')} tokens · version {comp.get('version')} · through message {comp.get('through_message_id') or comp.get('throughMessageId')}")
            return
        from ragformation.lear.cli import _render_answer, _render_debug

        self.say("VIEW", "┌ readable view of the JSON above, rendered by the CLI's own formatters (_render_answer + _render_debug)")
        for line in (_render_answer(answer, False) + "\n" + _render_debug(answer)).split("\n"):
            self.say("VIEW", "│ " + line)
        stages = ((t.get("lifecycle") or {}).get("stages")) or []
        if stages:
            cells = [f"{s['stage']} {s['ms']:.2f}" for s in stages]
            self.say("VIEW", f"├ agent lifecycle spans, ms (timings.lifecycle, monotonic perf_counter; total {(t.get('lifecycle') or {}).get('totalMs')} ms):")
            for k in range(0, len(cells), 7):
                self.say("VIEW", "│   " + " · ".join(cells[k:k + 7]))
        self.say("VIEW", f"├ stream: ttft {t.get('ttftMs')} ms · stream events {t.get('streamEvents')} · itl p50 {itl.get('p50')} / p95 {itl.get('p95')} ms · completion tokens {t.get('completionTokens')} (estimated={t.get('tokensEstimated')}) · tok/s {t.get('tokensPerSec')} · stages {json.dumps(t.get('stages') or {})}")
        blocks = ["rules"] + ([f"conversation_summary {sess.get('summaryVersion')}"] if sess.get("summaryVersion") else []) + ([f"memory ×{len(answer.get('memoryUsed') or [])}"] if answer.get("memoryUsed") else [])
        self.say("VIEW", f"├ prompt: template {answer.get('promptVersion')} · system prompt ≈{answer.get('promptTokens')} tokens · blocks: {' + '.join(blocks)} · prompt text and payload hash are not emitted by the agent (UNOBSERVED)")
        self.say("VIEW", f"├ models: generation configured={gen.get('configured')} used={gen.get('used')} fallbackUsed={gen.get('fallbackUsed')} · embedding {emb.get('provider')}:{emb.get('model')} d{emb.get('dimension')} · qualifying={answer.get('qualifying')}")
        self.say("VIEW", f"└ session {sess.get('id')} · turn {sess.get('turn')} · kept history {sess.get('historyMessages')} msgs · summary {sess.get('summaryVersion')} · compaction {json.dumps(comp) if comp else None} · memoryUsed {answer.get('memoryUsed')}")

    # ------------------------------------------------------------- checks
    def answer_of(self, step_id: str) -> Dict[str, Any]:
        value = (self.ctx.get(step_id) or {}).get("json")
        return value if isinstance(value, dict) else {}

    def check(self, chk: Dict[str, Any]) -> Dict[str, Any]:
        from ragformation.lear.evaluate import score_case

        kind = chk["type"]
        obs: Dict[str, Any] = {}
        ok = False
        if kind == "dependency_error":
            r, a = self.ctx[chk["step"]], self.answer_of(chk["step"])
            err = a.get("error") if isinstance(a.get("error"), dict) else {}
            obs = {"exit": r["exit"], "status": a.get("status"), "errorCode": err.get("code"), "message": err.get("message"),
                   "dependencyUnavailable": err.get("dependencyUnavailable"), "answerChars": len(a.get("answer") or "")}
            ok = r["exit"] == 6 and a.get("status") == "ERROR" and bool(err.get("dependencyUnavailable")) and not a.get("answer")
        elif kind == "golden":
            a = self.answer_of(chk["step"])
            m = score_case(self.golden[chk["case"]], a, self.contexts()) if a else {}
            keep = ("status", "statusOk", "preferredStatus", "keyFactCoverage", "keyFactCoverageStrict", "forbiddenHits", "correct",
                    "correctStrict", "citations", "hit", "reciprocalRank", "docRecall", "contextPrecision", "citationPrecision", "proxy")
            obs = {k: m.get(k) for k in keep if k in m}
            ok = bool(m.get("correct"))
        elif kind == "keyfacts":
            a = self.answer_of(chk["step"])
            groups = self.golden[chk["case"]]["expected"]["must_include"]
            text = (a.get("answer") or "").lower()
            hits = [any(alt.lower() in text for alt in g) for g in groups]
            obs = {"status": a.get("status"), "groups": groups, "hits": hits}
            ok = bool(hits) and all(hits) and a.get("status") not in ("ABSTAINED", "ERROR")
        elif kind == "citations_valid":
            a = self.answer_of(chk["step"])
            row = self.engine_row(a.get("correlationId")) or {}
            obs = {"citations": len(a.get("citations") or []), "citations_valid": row.get("citations_valid"), "citations_total": row.get("citations_total")}
            ok = obs["citations"] >= 1 and row.get("citations_valid") == row.get("citations_total") == obs["citations"]
        elif kind == "honest_label":
            a = self.answer_of(chk["step"])
            obs = {"qualifying": a.get("qualifying"), "generationConfigured": ((a.get("models") or {}).get("generation") or {}).get("configured")}
            ok = a.get("qualifying") is False and obs["generationConfigured"] == "extractive (baseline)"
        elif kind == "session_turns":
            turns = [(self.answer_of(s).get("session") or {}) for s in chk["steps"]]
            obs = {"turns": [t.get("turn") for t in turns], "sessionIds": sorted({str(t.get("id")) for t in turns})}
            ok = obs["turns"] == list(range(1, len(turns) + 1)) and len(obs["sessionIds"]) == 1
        elif kind == "rewrite":
            rq = (self.answer_of(chk["step"]).get("retrieval") or {}).get("retrievalQuery")
            obs = {"retrievalQuery": rq}
            ok = bool(rq)
        elif kind == "memory_used":
            mem = self.answer_of(chk["memoryStep"]) or {}
            mem_id = mem.get("memory_id") or mem.get("id")
            used = {s: self.answer_of(s).get("memoryUsed") for s in chk["steps"]}
            obs = {"memoryId": mem_id, "memoryUsed": used}
            ok = bool(mem_id) and any(mem_id in (u or []) for u in used.values())
        elif kind == "persisted":
            data = self.answer_of(chk["step"])
            msgs = data.get("messages") or []
            corr = [self.answer_of(s).get("correlationId") for s in chk["steps"]]
            assistant = [m for m in msgs if m.get("role") == "assistant"]
            obs = {"messages": len(msgs), "user": sum(1 for m in msgs if m.get("role") == "user"), "assistant": len(assistant),
                   "assistantQueryIds": [m.get("query_id") for m in assistant], "cliCorrelationIds": corr}
            ok = obs["user"] == len(chk["steps"]) and obs["assistant"] == len(chk["steps"]) and obs["assistantQueryIds"] == corr
        elif kind == "distinct_docs":
            a = self.answer_of(chk["step"])
            docs = [c.get("document") for c in a.get("citations") or []]
            sel = set((a.get("retrieval") or {}).get("selected") or [])
            sel_docs = sorted({c.get("document") for c in (a.get("retrieval") or {}).get("candidates") or [] if c.get("chunkId") in sel})
            obs = {"citedDocuments": sorted(set(docs)), "citations": len(docs), "selectedContextDocuments": sel_docs}
            ok = len(set(docs)) >= int(chk.get("min", 2))
        elif kind == "no_secrets":
            hits: Dict[str, List[str]] = {}
            for s in chk["steps"]:
                text = text_of(self.ctx[s]["raw"])
                for name, pattern in SECRET_PATTERNS.items():
                    if re.search(pattern, text):
                        hits.setdefault(s, []).append(name)
            obs = {"patterns": list(SECRET_PATTERNS), "hits": hits}
            ok = not hits
        elif kind == "no_prompt_leak":
            leaks = {s: [p for p in PROMPT_MARKERS if p.lower() in (self.answer_of(s).get("answer") or "").lower()] for s in chk["steps"]}
            leaks = {k: v for k, v in leaks.items() if v}
            obs = {"markers": list(PROMPT_MARKERS), "leaks": leaks}
            ok = not leaks
        elif kind == "citations_kept":
            rows = {s: {"status": self.answer_of(s).get("status"), "citations": len(self.answer_of(s).get("citations") or [])} for s in chk["steps"]}
            answered = {s: r for s, r in rows.items() if r["status"] in ("GROUNDED", "PARTIAL", "CONFLICTED")}
            obs = {"answers": rows, "vacuous": not answered}
            ok = all(r["citations"] >= 1 for r in answered.values())
        elif kind == "conflict_flagged":
            a = self.answer_of(chk["step"])
            obs = {"status": a.get("status"), "conflict": a.get("conflict"), "warnings": a.get("warnings")}
            ok = a.get("status") == "CONFLICTED" and bool(a.get("conflict"))
        elif kind == "compaction_occurred":
            comps = self.answer_of(chk["step"]).get("compactions") or []
            first = comps[0] if comps else {}
            obs = {"compactions": len(comps), "first": first}
            ok = bool(comps) and int(first.get("tokens_before") or 0) > 900 and int(first.get("tokens_after") or 0) < int(first.get("tokens_before") or 0)
        elif kind == "summary_contains":
            summary = (self.answer_of(chk["step"]).get("session") or {}).get("summary") or ""
            found = [t for t in chk["terms"] if t.lower() in summary.lower()]
            obs = {"summaryChars": len(summary), "termsFound": found}
            ok = bool(found)
        elif kind == "refused":
            r = self.ctx[chk["step"]]
            text = text_of(r["raw"])
            obs = {"exit": r["exit"], "contains": chk["contains"], "found": chk["contains"].lower() in text.lower()}
            ok = r["exit"] != 0 and obs["found"]
        elif kind == "rate_limited":
            r = self.ctx[chk["step"]]
            obs = {k: r.get(k) for k in ("burst", "answeredInBurst", "rateLimitedInBurst", "retryAfterS")}
            ok = r.get("answeredInBurst") == r.get("burst", 0) - 1 and r.get("rateLimitedInBurst") == 1 and r.get("retryAfterS") is not None
        elif kind == "recovered":
            r = self.ctx[chk["step"]]
            after = r.get("after") or []
            obs = {"after": after}
            ok = bool(after) and all(x["answered"] == 1 and x["rateLimited"] == 0 for x in after)
        elif kind == "exit":
            obs = {"exit": self.ctx[chk["step"]]["exit"], "expected": chk["in"]}
            ok = obs["exit"] in chk["in"]
        elif kind == "eval_completed":
            r = self.ctx[chk["step"]]
            path = Path(self.substitute("{EVAL}/rec-s09/run.json"))
            cases = len(json.loads(path.read_text())["cases"]) if path.exists() else None
            obs = {"exit": r["exit"], "runJson": str(path), "cases": cases}
            ok = r["exit"] == 0 and cases == chk["cases"]
        elif kind == "eval_repro":
            p = self.answer_of(chk["step"])
            ident = p.get("identical") or {}
            obs = {"common": p.get("common"), "identical": ident, "differences": p.get("differences"), "onlyNew": p.get("onlyNew"), "onlyBaseline": p.get("onlyBaseline")}
            ok = bool(p.get("common")) and ident.get("status") == p.get("common") and ident.get("correct") == p.get("common") and not p.get("onlyNew") and not p.get("onlyBaseline")
        elif kind == "bench_completed":
            r = self.ctx[chk["step"]]
            obs = {"exit": r["exit"], "stamp": chk["stamp"], "found": chk["stamp"] in text_of(r["raw"])}
            ok = r["exit"] == 0 and obs["found"]
        else:
            obs = {"error": f"unknown check type {kind}"}
        return {"id": chk["id"], "type": kind, "description": chk["description"], "onFail": chk["onFail"],
                "outcome": "PASS" if ok else "FAIL", "observed": obs}

    # ---------------------------------------------------------------- run
    def header(self) -> None:
        sc = self.scenario
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        tracked_dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        self.git = {"commit": git, "branch": branch, "trackedFilesClean": not tracked_dirty}
        bar = "═" * (self.cols - 9)
        self.say("REC", bar)
        self.say("REC", f"Lear RAG agent · session recording {sc['id']}/09 · {sc['title']}")
        self.say("REC", f"run id {self.run_id} · started {iso(self.started)} · recorder asciinema 2.4.0 → terminal/{sc['file']}.cast")
        self.say("REC", f"repository aviunion1348-cloud/rag-formation @ {git[:12]} ({branch}) · tracked files {'clean' if not tracked_dirty else 'MODIFIED'} · host {platform.system()} {platform.machine()} · Python {platform.python_version()} · {os.cpu_count()} vCPU · no GPU")
        self.say("REC", f"plan scripts/session_recordings/scenario_plan.json sha256 {sha256(self.plan_bytes)[:16]}… (registered {self.plan['registeredAt']}, before execution)")
        self.say("REC", f"isolated data dir {self.data} · configured LLM {self.plan['environmentFacts']['configuredLLM']}")
        self.say("REC", "legend: ▌ lines come from the recording harness · \"$ …\" is the exact command executed · everything else is the unmodified output of that command")
        self.say("REC", bar)
        self.say("NOTE", f"objective: {sc['objective']}")
        self.say("NOTE", f"selection: {sc['selection']}")
        if sc.get("cap"):
            self.say("NOTE", f"known limit: {sc['cap']['reason']}")

    def run(self) -> int:
        self.header()
        steps = [s for s in self.scenario["steps"] if s["kind"] != "note"]
        for note in (s for s in self.scenario["steps"] if s["kind"] == "note"):
            self.say("NOTE", self.substitute(note["text"]))
        for index, step in enumerate(steps, 1):
            self.step_title(index, len(steps), step)
            kind = step["kind"]
            try:
                if kind == "cli":
                    result = self.do_cli(step)
                elif kind == "ask":
                    result = self.do_ask(step)
                elif kind == "chat":
                    result = self.do_chat(step)
                elif kind == "scan_corpus":
                    result = self.do_scan(step)
                elif kind == "compare_eval":
                    result = self.do_compare(step)
                else:
                    raise ValueError(f"unknown step kind {kind}")
            except Exception as exc:  # recorded, never hidden
                self.say("EXIT", f"HARNESS ERROR in step {step['id']}: {type(exc).__name__}: {exc}", style=OUTCOME_STYLE["FAIL"])
                result = {"argv": [], "exit": None, "startedAt": iso(utcnow()), "endedAt": iso(utcnow()), "ms": 0.0,
                          "out": "", "outBytes": 0, "outSha256": None, "raw": b"", "json": None, "harnessError": f"{type(exc).__name__}: {exc}"}
            self.ctx[step["id"]] = result
            self.finish_step(step, result)
            if kind == "ask":
                try:
                    self.view(step, result)
                    if isinstance(result.get("json"), dict):
                        self.export_trace(step["id"], result["json"])
                except Exception as exc:  # recorded, never hidden; the step's raw output is already saved
                    self.say("EXIT", f"HARNESS DISPLAY ERROR in step {step['id']}: {type(exc).__name__}: {exc}", style=OUTCOME_STYLE["FAIL"])
                    result["harnessDisplayError"] = f"{type(exc).__name__}: {exc}"
        checks = []
        for chk in self.scenario.get("checks", []):
            try:
                checks.append(self.check(chk))
            except Exception as exc:
                checks.append({"id": chk["id"], "type": chk["type"], "description": chk["description"], "onFail": chk["onFail"],
                               "outcome": "FAIL", "observed": {"harnessError": f"{type(exc).__name__}: {exc}"}})
        self.say("STEP", f"pre-registered acceptance checks · {hms(utcnow())}")
        for c in checks:
            obs = json.dumps(c["observed"], ensure_ascii=False, default=str)
            self.say("CHECK", f"{c['outcome']} {c['id']} {c['description']}", style=OUTCOME_STYLE[c["outcome"]])
            self.say("CHECK", f"      observed: {obs[:600]}")
        status = "PASS"
        for c in checks:
            if c["outcome"] == "FAIL" and SEVERITY[c["onFail"]] > SEVERITY[status]:
                status = c["onFail"]
        cap = self.scenario.get("cap")
        if cap and SEVERITY[cap["status"]] > SEVERITY[status]:
            status = cap["status"]
        ended = utcnow()
        passed = sum(1 for c in checks if c["outcome"] == "PASS")
        self.say("RESULT", f"scenario {self.scenario['id']} {self.scenario['name']}: {status} · checks passed {passed}/{len(checks)}" + (f" · cap {cap['status']}: {cap['reason']}" if cap else ""))
        self.say("RESULT", f"run id {self.run_id} · ended {iso(ended)} · evidence traces/{self.scenario['id']}/ and evidence/results/{self.scenario['id']}.json")
        self.write(checks, status, ended)
        return 0

    def write(self, checks: List[Dict[str, Any]], status: str, ended: dt.datetime) -> None:
        with (self.traces / "steps.jsonl").open("w", encoding="utf-8") as fh:
            for row in self.step_rows:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        with (self.traces / "events.jsonl").open("w", encoding="utf-8") as fh:
            for ev in self.events:
                fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        answers = {}
        for sid, r in self.ctx.items():
            a = r.get("json") if isinstance(r.get("json"), dict) else None
            if a and "status" in a and "timings" in a:
                t = a.get("timings") or {}
                itl = t.get("itlMs") or {}
                answers[sid] = {"question": r.get("question"), "questionSource": r.get("questionSource"), "status": a.get("status"),
                                "backend": a.get("backend"), "qualifying": a.get("qualifying"), "correlationId": a.get("correlationId"),
                                "sessionId": (a.get("session") or {}).get("id"), "turn": (a.get("session") or {}).get("turn"),
                                "citations": [{"document": c.get("document"), "lines": c.get("lines"), "chunkId": c.get("chunkId")} for c in a.get("citations") or []],
                                "generation": ((a.get("models") or {}).get("generation") or {}), "embedding": ((a.get("models") or {}).get("embedding") or {}),
                                "metrics": {"total_latency_ms": t.get("totalMs"), "ttft_ms": t.get("ttftMs"), "itl_median_ms": itl.get("p50"),
                                            "itl_p95_ms": itl.get("p95"), "stream_events": t.get("streamEvents"),
                                            "output_tokens": t.get("completionTokens"), "output_tokens_estimated": t.get("tokensEstimated"),
                                            "tokens_per_second": t.get("tokensPerSec"), "stages": t.get("stages")},
                                "error": a.get("error"), "abstainReason": a.get("abstainReason"), "warnings": a.get("warnings")}
        result = {"schema": "lear-session-recording-result-v1", "runId": self.run_id, "scenario": self.scenario["id"],
                  "name": self.scenario["name"], "title": self.scenario["title"], "status": status, "cap": self.scenario.get("cap"),
                  "startedAt": iso(self.started), "endedAt": iso(ended), "git": getattr(self, "git", {}),
                  "planSha256": sha256(self.plan_bytes), "cast": f"terminal/{self.scenario['file']}.cast",
                  "steps": [{k: v for k, v in r.items() if k not in ("raw", "json")} | {"stepId": sid} for sid, r in self.ctx.items()],
                  "answers": answers, "checks": checks, "events": f"traces/{self.scenario['id']}/events.jsonl",
                  "sessionIds": sorted({str(a["sessionId"]) for a in answers.values() if a.get("sessionId")} |
                                       {str(r["sessionId"]) for r in self.ctx.values() if r.get("sessionId")}),
                  "correlationIds": [a["correlationId"] for a in answers.values() if a.get("correlationId")] +
                                    [c for r in self.ctx.values() for c in r.get("correlationIds") or []]}
        (self.results_dir / f"{self.scenario['id']}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--pkg", default=str(REPO / "artifacts" / "agent_session_recordings"))
    ap.add_argument("--plan", default=str(Path(__file__).with_name("scenario_plan.json")))
    return Harness(ap.parse_args()).run()


if __name__ == "__main__":
    sys.exit(main())
