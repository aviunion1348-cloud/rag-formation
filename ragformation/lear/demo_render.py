"""Terminal rendering for ``lear demo``: capability detection, styles, motion.

Rendering only. No RAG or business logic lives here. Every effect draws data
it is handed; it never fabricates a value. When motion is disabled (non-TTY,
``--plain``, ``--no-animate``, ``NO_COLOR``/CI, ``TERM=dumb``), every effect
falls back to its reduced-motion equivalent, which prints the same
information at once.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, TextIO

RESET = "\x1b[0m"
_CODES = {
    "bold": "1", "dim": "2", "italic": "3", "underline": "4",
    "red": "31", "green": "32", "amber": "33", "blue": "34", "magenta": "35", "cyan": "36", "grey": "90",
}
PROVENANCE = ("LIVE", "RECORDED", "ILLUSTRATIVE", "UNAVAILABLE")
_PROV_TONE = {"LIVE": "green", "RECORDED": "cyan", "ILLUSTRATIVE": "magenta", "UNAVAILABLE": "amber"}
_CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD", "JENKINS_URL", "TEAMCITY_VERSION")

_UNICODE = {
    "hr": "─", "vr": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "tee": "├", "end": "└",
    "full": "█", "light": "░", "arrow": "→", "ok": "✓", "bad": "✗", "dot": "·", "on": "●", "off": "○",
    "up": "▲", "down": "▼", "same": "■", "hatch": "╱", "spark": "▁▂▃▄▅▆▇█", "mark": "┼", "gate": "╪",
    "tick": "✓", "cross": "✗", "alt": "▓",
}
_ASCII = {
    "hr": "-", "vr": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+", "tee": "|-", "end": "`-",
    "full": "#", "light": ".", "arrow": "->", "ok": "OK", "bad": "X", "dot": "*", "on": "(*)", "off": "( )",
    "up": "^", "down": "v", "same": "=", "hatch": "/", "spark": " .:-=+*#", "mark": "+", "gate": "#",
    "tick": "+", "cross": "x", "alt": "=",
}
# Plain/ASCII mode must not depend on Unicode: transliterate the few symbols the
# demo text uses so no information is lost.
_ASCII_MAP = str.maketrans({"→": "->", "←": "<-", "≤": "<=", "≥": ">=", "—": "-", "–": "-", "…": "...", "∝": "~",
                            "’": "'", "‘": "'", "“": '"', "”": '"', "§": "S.", "×": "x", "·": "*", "✓": "OK", "✗": "X"})


@dataclass
class TermCaps:
    tty: bool
    color: bool
    unicode: bool
    width: int
    animate: bool
    interactive: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"tty": self.tty, "color": self.color, "unicode": self.unicode, "width": self.width,
                "animate": self.animate, "interactive": self.interactive, "reasons": list(self.reasons)}


def detect_caps(
    stream: TextIO,
    *,
    plain: bool = False,
    no_animate: bool = False,
    json_mode: bool = False,
    env: Optional[Dict[str, str]] = None,
    stdin: Optional[TextIO] = None,
) -> TermCaps:
    """Decide colour, Unicode, width and motion from the real output stream."""

    env = dict(os.environ if env is None else env)
    stdin = sys.stdin if stdin is None else stdin
    reasons: List[str] = []
    tty = bool(getattr(stream, "isatty", lambda: False)())
    term = env.get("TERM", "")
    ci = any(env.get(k) for k in _CI_VARS)
    if not tty:
        reasons.append("output is not a TTY")
    if plain:
        reasons.append("--plain")
    if json_mode:
        reasons.append("--json")
    if env.get("NO_COLOR"):
        reasons.append("NO_COLOR")
    if term == "dumb":
        reasons.append("TERM=dumb")
    if ci:
        reasons.append("CI environment")
    if no_animate:
        reasons.append("--no-animate")
    color = tty and not plain and not json_mode and not env.get("NO_COLOR") and term != "dumb"
    if env.get("FORCE_COLOR") and not plain and not json_mode and not env.get("NO_COLOR"):
        color = True
    encoding = (getattr(stream, "encoding", "") or "").lower()
    unicode = (not plain) and ("utf" in encoding) and term != "dumb" and env.get("LEAR_DEMO_ASCII") != "1"
    width = shutil.get_terminal_size((100, 24)).columns if tty else 100
    width = max(40, min(int(width), 120))
    animate = tty and not plain and not json_mode and not no_animate and not ci and term != "dumb"
    interactive = animate and bool(getattr(stdin, "isatty", lambda: False)())
    return TermCaps(tty=tty, color=color, unicode=unicode, width=width, animate=animate, interactive=interactive, reasons=reasons)


class Renderer:
    """Line-oriented terminal writer with reduced-motion fallbacks."""

    def __init__(self, caps: TermCaps, out: Optional[TextIO] = None, *, speed: float = 1.0) -> None:
        self.caps = caps
        self.out = out or sys.stdout
        self.speed = max(0.0, float(speed))
        self.g = _UNICODE if caps.unicode else _ASCII
        self.dirty = False  # an ANSI style is open

    # ------------------------------------------------------------- basics
    def style(self, text: str, *names: str) -> str:
        if not self.caps.color or not names:
            return text
        codes = ";".join(_CODES[n] for n in names if n in _CODES)
        self.dirty = True
        return f"\x1b[{codes}m{text}{RESET}"

    def tr(self, text: Any) -> str:
        """Transliterate to ASCII when Unicode is off (measure *after* this)."""

        text = str(text)
        return text if self.caps.unicode else text.translate(_ASCII_MAP)

    def write(self, text: str = "", *, wrap: bool = True, indent: str = "") -> None:
        width = self.caps.width
        text = str(text)
        if not self.caps.unicode:
            text = text.translate(_ASCII_MAP)
        for raw in text.split("\n"):
            if wrap and _visible_len(raw) > width and "\x1b[" not in raw:
                lines = textwrap.wrap(raw, width=width, subsequent_indent=indent + "  ", break_long_words=True,
                                      break_on_hyphens=False) or [""]
            else:
                lines = [raw]
            for line in lines:
                self.out.write(line + "\n")
        self.out.flush()

    def pause(self, seconds: float) -> None:
        if self.caps.animate and self.speed > 0:
            time.sleep(seconds * self.speed)

    def reset(self) -> None:
        """Always leave the terminal in its default state."""

        if self.caps.color or self.dirty:
            try:
                self.out.write(RESET)
                self.out.flush()
            except (OSError, ValueError):  # pragma: no cover - closed stream
                pass
        self.dirty = False

    # ----------------------------------------------------------- elements
    def rule(self, title: str = "", tone: str = "grey") -> None:
        width = self.caps.width
        if title:
            head = f"{self.g['hr'] * 2} {title} "
            self.write(self.style(head + self.g["hr"] * max(0, width - _visible_len(head)), tone), wrap=False)
        else:
            self.write(self.style(self.g["hr"] * width, tone), wrap=False)

    def badge(self, provenance: str) -> str:
        label = provenance if provenance in PROVENANCE else "UNAVAILABLE"
        return self.style(f"[{label}]", _PROV_TONE[label], "bold")

    def tone_for(self, status: str) -> str:
        s = str(status).upper()
        if s in {"PASS", "OK", "GROUNDED", "VALID", "MATCH", "LIVE", "0"}:
            return "green"
        if s in {"WARN", "PARTIAL", "ABSTAINED", "SKIP", "UNVERIFIED", "LIMITATION", "CONFLICTED", "RECORDED"}:
            return "amber"
        if s in {"FAIL", "ERROR", "BLOCKED", "MISMATCH", "UNAVAILABLE"}:
            return "red"
        return "cyan"

    def panel(self, title: str, lines: Sequence[str], tone: str = "cyan") -> None:
        g, width = self.g, self.caps.width
        inner = width - 4
        title = self.tr(title)
        self.write(self.style(g["tl"] + g["hr"] + f" {title} " + g["hr"] * max(0, width - len(title) - 5) + g["tr"], tone), wrap=False)
        for line in lines:
            for piece in (textwrap.wrap(self.tr(line), width=inner, break_on_hyphens=False) or [""]):
                pad = " " * max(0, inner - _visible_len(piece))
                self.write(self.style(g["vr"], tone) + " " + piece + pad + " " + self.style(g["vr"], tone), wrap=False)
        self.write(self.style(g["bl"] + g["hr"] * (width - 2) + g["br"], tone), wrap=False)

    def table(self, headers: Sequence[str], rows: Sequence[Sequence[Any]], *, max_col: int = 60) -> None:
        cells = [[self.tr(_cell(h)) for h in headers]] + [[self.tr(_cell(c)) for c in row] for row in rows]
        ncol = len(headers)
        widths = [min(max_col, max(len(r[i]) if i < len(r) else 0 for r in cells)) for i in range(ncol)]
        budget = self.caps.width - 3 * (ncol - 1)
        while sum(widths) > budget and max(widths) > 8:
            widths[widths.index(max(widths))] -= 1
        for n, row in enumerate(cells):
            wrapped = [textwrap.wrap(row[i] if i < len(row) else "", width=widths[i], break_on_hyphens=False) or [""]
                       for i in range(ncol)]
            for k in range(max(len(w) for w in wrapped)):
                parts = [(w[k] if k < len(w) else "").ljust(widths[i]) for i, w in enumerate(wrapped)]
                line = (" " + self.g["vr"] + " ").join(parts).rstrip()
                self.write(self.style(line, "bold") if n == 0 else line, wrap=False)
            if n == 0:
                self.write(self.style((self.g["hr"] * 3).join(self.g["hr"] * w for w in widths), "grey"), wrap=False)

    def bar(self, value: Optional[float], vmax: float, width: int = 24) -> str:
        if value is None or vmax <= 0:
            return "not measured"
        filled = max(0, min(width, int(round(width * float(value) / vmax))))
        return self.g["full"] * filled + self.g["light"] * (width - filled)

    def spark(self, values: Sequence[float]) -> str:
        data = [float(v) for v in values if v is not None]
        if not data:
            return "not measured"
        ramp = self.g["spark"]
        lo, hi = min(data), max(data)
        span = (hi - lo) or 1.0
        return "".join(ramp[min(len(ramp) - 1, int((v - lo) / span * (len(ramp) - 1)))] for v in data)


def _cell(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value) < 1000 else f"{value:.0f}"
    return str(value)


def _visible_len(text: str) -> int:
    out, skip = 0, False
    for ch in text:
        if ch == "\x1b":
            skip = True
        elif skip and ch == "m":
            skip = False
        elif not skip:
            out += 1
    return out


# --------------------------------------------------------------- effects
@dataclass(frozen=True)
class Effect:
    name: str
    family: str
    trigger: str
    grammar: str
    steps: str
    purpose: str
    reduced: str
    data: str


EFFECTS: List[Effect] = [
    Effect("paper_feed", "archival", "a list of real pages/lines is shown", "lines advance one at a time like a printer feed", "1 line / 40 ms", "pace a ledger so it can be read", "all lines at once", "docs verify pages, transcript lines"),
    Effect("registration_marks", "archival", "a scene opens", "calibration marks frame the scene header", "1 frame", "anchor each scene visually", "plain header rule", "scene id/title"),
    Effect("heading_indent_reveal", "archival", "a chunk's heading path is shown", "each heading level is indented one step", "1 line / level", "show where a chunk sits in its document", "indented list", "manifest heading_path"),
    Effect("chunk_segmentation", "archival", "index statistics are shown", "a document strip split into segments sized by token counts", "1 frame", "show chunking granularity", "same strip, no delay", "manifest token counts"),
    Effect("citation_stamp", "archival", "a citation is validated", "[n] path:Lx-Ly followed by a stamped check or cross", "1 stamp / 60 ms", "tie each marker to a manifest entry", "same text", "answer citations + manifest"),
    Effect("hash_seal", "archival", "a fingerprint or checksum is shown", "the hash prints in groups of 8 then seals", "1 group / 30 ms", "make provenance identifiers legible", "full hash", "SOURCE_PIN, dataset sha256, index fingerprint"),
    Effect("quarantine_hatch", "archival", "a retrieved chunk is quarantined", "hatched bar around the chunk id with the matched rules", "1 frame", "show untrusted evidence was fenced off", "text label QUARANTINED", "retrieval.quarantined"),
    Effect("source_margin", "archival", "a fact is shown", "the provenance badge is printed beside the value", "none", "never show a number without its origin", "same", "fact provenance"),
    Effect("query_ribbon", "retrieval", "a question enters the pipeline", "the question with salient terms highlighted", "1 frame", "show what retrieval keys on", "terms listed", "agent.salient_terms"),
    Effect("candidate_fanout", "retrieval", "retrieval returns candidates", "tree branches fan out to each candidate", "1 branch / 40 ms", "show breadth of the candidate set", "indented list", "retrieval.candidates"),
    Effect("score_ladder", "retrieval", "candidate scores exist", "bars proportional to the real fused score", "1 rung / 40 ms", "compare candidates on the engine's own score", "numbers only in plain", "candidate score/support"),
    Effect("selection_rail", "retrieval", "context is assembled", "filled dot = selected, hollow = excluded with reason", "1 frame", "make selection decisions explicit", "SELECTED/EXCLUDED words", "retrieval.selected/excluded"),
    Effect("context_meter", "retrieval", "context tokens are known", "a packing meter: used tokens against the budget", "1 frame", "show context budget use", "numbers", "retrieval.contextTokens + settings"),
    Effect("stage_waterfall", "retrieval", "stage timings exist", "each stage bar offset by the time before it", "1 stage / 40 ms", "show where time goes", "table", "retrieval.stages ms"),
    Effect("teletype", "generation", "a REAL model stream exists", "characters print as the stream delivered them", "per token", "make streaming visible", "full text at once", "streamed tokens only (never for extractive output)"),
    Effect("abstention_gate", "generation", "an answer abstains", "a gate closes across the answer slot, naming the reason", "2 frames", "present refusal as a feature", "ABSTAINED: reason", "status + abstainReason"),
    Effect("status_stamp", "generation", "an answer completes", "status word stamped in a box with its exit code", "1 frame", "map status to the CLI contract", "status line", "status, exit_code_for"),
    Effect("claim_trace", "generation", "an answer has citations", "marker -> chunk -> source lines chain", "1 link / 40 ms", "trace answer to source", "list", "citations + manifest"),
    Effect("session_timeline", "sessions", "a session has turns", "turn nodes along a line with status", "1 node / 40 ms", "show conversation state", "numbered list", "session turns"),
    Effect("compaction_fold", "sessions", "compaction runs", "folded message count collapses into a summary block", "2 frames", "show what was folded", "before/after numbers", "compaction report"),
    Effect("token_counter", "sessions", "before/after token counts exist", "a mechanical counter rolls from before to after", "8 steps", "make the saving tangible", "before -> after", "tokens_before/after (estimated, labelled)"),
    Effect("memory_gate", "sessions", "memory is listed", "items visible to this user pass; others are not shown", "1 frame", "show scoping", "counts", "MemoryStore.list"),
    Effect("expiry_clock", "sessions", "an expiry timestamp exists", "remaining seconds computed from the real timestamp", "1 frame", "show lifecycle", "timestamp", "memory/session expires_at"),
    Effect("handshake_trace", "instrumentation", "doctor runs", "each probe prints with an arrow to its result", "1 check / 50 ms", "show exactly which probe failed", "table", "doctor checks"),
    Effect("loopback_lamp", "instrumentation", "the Ollama URL is shown", "a lamp: LOOPBACK or REFUSED", "1 frame", "show the local-only boundary", "word", "settings.ollama_url"),
    Effect("model_card", "instrumentation", "model configuration is shown", "card with role, tag, cap and verification state", "1 frame", "separate configured from verified", "table", "doctor summary"),
    Effect("percentile_plot", "instrumentation", "latency samples exist", "mean/p50/p85/p99 bars with n= and an instability warning", "1 bar / 40 ms", "read distributions, not single numbers", "table", "stats.describe over real samples"),
    Effect("concurrency_lanes", "instrumentation", "bench levels exist", "one lane per user level with outcome ticks", "1 lane / 40 ms", "show load outcomes", "table", "bench records"),
    Effect("admission_gate", "instrumentation", "rate limiting is shown", "accepted ticks pass the gate; refused ticks stop at it", "1 frame", "make limits visible", "counts", "limiter counters / RATE_LIMITED outcomes"),
    Effect("dependency_panel", "instrumentation", "a dependency is unavailable", "amber/red panel with the exit code and the fix", "1 frame", "fail honestly and usefully", "same text", "doctor / ProviderUnavailable"),
    Effect("round_ledger", "evaluation", "evaluation runs are listed", "one row per run with commit, scorer, judge state", "1 row / 40 ms", "keep runs separate, oldest first", "table", "report.evaluation_runs"),
    Effect("delta_arrows", "evaluation", "two comparable runs exist", "up/down/same arrows with signed deltas", "1 frame", "show change without hiding regressions", "signed numbers", "run summaries"),
    Effect("regression_ledger", "evaluation", "per-case results exist", "cases that flipped correct->wrong listed by id", "1 frame", "prove or disprove regressions", "list", "run.json cases"),
    Effect("split_ruler", "evaluation", "the golden set is shown", "a ruler divided by split sizes", "1 frame", "show split policy", "counts", "golden set splits"),
    Effect("exit_code_ledger", "evaluation", "guardrail steps finish", "step, expected exit, observed exit, verdict", "1 row / 40 ms", "prove the exit-code contract", "table", "real exit codes"),
    Effect("readiness_card", "evaluation", "the walkthrough ends", "boxed overall verdict with the steps to qualify", "1 frame", "state readiness without spin", "same text", "final_report + live gates"),
]
EFFECT_NAMES = {e.name for e in EFFECTS}


class Motion:
    """Effect implementations. Each prints real data; delays only when animating."""

    def __init__(self, r: Renderer) -> None:
        self.r = r

    def paper_feed(self, lines: Iterable[str], delay: float = 0.04) -> None:
        for line in lines:
            self.r.write(line)
            self.r.pause(delay)

    def registration_marks(self, scene_id: str, title: str, provenance: str) -> None:
        g, r = self.r.g, self.r
        mark = g["mark"]
        r.write("")
        r.write(r.style(f"{mark}{g['hr'] * 3}", "grey") + " " + r.style(f"{scene_id}  {title}", "bold") + "  " + r.badge(provenance), wrap=False)

    def heading_indent_reveal(self, heading_path: Sequence[str]) -> None:
        for depth, heading in enumerate(heading_path):
            self.r.write("  " * (depth + 1) + self.r.g["end"] + " " + heading)
            self.r.pause(0.03)

    def chunk_segmentation(self, token_counts: Sequence[int], width: Optional[int] = None) -> str:
        width = width or max(20, self.r.caps.width - 20)
        total = sum(token_counts) or 1
        out = []
        for n, tokens in enumerate(token_counts):
            seg = max(1, int(round(width * tokens / total)))
            out.append((self.r.g["full"] if n % 2 == 0 else self.r.g["alt"]) * seg)
        strip = "  " + "".join(out)[:width]
        self.r.write(strip, wrap=False)
        return strip

    def citation_stamp(self, label: str, ok: bool) -> None:
        mark = self.r.style(self.r.g["ok"], "green", "bold") if ok else self.r.style(self.r.g["bad"], "red", "bold")
        self.r.write(f"  {mark} {label}")
        self.r.pause(0.06)

    def hash_seal(self, label: str, value: Optional[str]) -> None:
        if not value:
            self.r.write(f"  {label}: not available")
            return
        groups = [value[i:i + 8] for i in range(0, len(value), 8)]
        self.r.write(f"  {label}: " + " ".join(groups))
        self.r.pause(0.03 * min(len(groups), 8))

    def quarantine_hatch(self, chunk_id: str, rules: Sequence[str]) -> None:
        h = self.r.g["hatch"] * 4
        self.r.write(self.r.style(f"  {h} QUARANTINED {chunk_id} {h}", "amber") + f"  rules: {', '.join(rules) or 'n/a'}")

    def candidate_fanout(self, items: Sequence[str]) -> None:
        for n, item in enumerate(items):
            branch = self.r.g["end"] if n == len(items) - 1 else self.r.g["tee"]
            self.r.write(f"  {branch} {item}")
            self.r.pause(0.04)

    def score_ladder(self, rows: Sequence[Dict[str, Any]], key: str = "score") -> None:
        values = [float(x[key]) for x in rows if isinstance(x.get(key), (int, float))]
        vmax = max(values) if values else 0.0
        for x in rows:
            mark = self.r.g["on"] if x.get("selected") else self.r.g["off"]
            value = x.get(key)
            shown = f"{value:.4f}" if isinstance(value, (int, float)) else "n/a"
            bar = self.r.bar(value if isinstance(value, (int, float)) else None, vmax, 18)
            self.r.write(f"  {mark} {bar} {shown}  {x.get('label', '')}")
            self.r.pause(0.04)

    def context_meter(self, used: Optional[int], budget: int) -> None:
        if used is None:
            self.r.write("  context tokens: not instrumented for this answer")
            return
        self.r.write(f"  context {self.r.bar(used, budget, 30)} {used}/{budget} tokens (estimated, ~4 chars/token)")

    def stage_waterfall(self, stages: Sequence[Dict[str, Any]]) -> None:
        timed = [s for s in stages if isinstance(s.get("ms"), (int, float))]
        total = sum(s["ms"] for s in timed) or 0.0
        if not timed:
            self.r.write("  stage timings: not instrumented")
            return
        offset = 0.0
        width = 30
        for s in stages:
            ms = s.get("ms")
            if not isinstance(ms, (int, float)):
                self.r.write(f"  {s['name']:<28} not instrumented")
                continue
            start = int(width * offset / total) if total else 0
            span = max(1, int(round(width * ms / total))) if total else 1
            self.r.write(f"  {s['name']:<28} " + " " * start + self.r.g["full"] * span + f" {ms} ms")
            offset += ms
            self.r.pause(0.04)

    def teletype(self, text: str, real_stream: bool) -> None:
        if not real_stream or not self.r.caps.animate:
            self.r.write(text)
            return
        for ch in text:
            self.r.out.write(ch)
            self.r.out.flush()
            self.r.pause(0.004)
        self.r.out.write("\n")

    def abstention_gate(self, reason: str) -> None:
        g = self.r.g
        self.r.write(self.r.style(f"  {g['gate'] * 3} GATE CLOSED {g['gate'] * 3}", "amber", "bold") + f"  {reason}")

    def status_stamp(self, status: str, exit_code: int) -> None:
        tone = self.r.tone_for(status)
        text = f" {status}  exit {exit_code} "
        g = self.r.g
        self.r.write(self.r.style(g["tl"] + g["hr"] * len(text) + g["tr"], tone), wrap=False)
        self.r.write(self.r.style(g["vr"], tone) + self.r.style(text, tone, "bold") + self.r.style(g["vr"], tone), wrap=False)
        self.r.write(self.r.style(g["bl"] + g["hr"] * len(text) + g["br"], tone), wrap=False)

    def session_timeline(self, turns: Sequence[Dict[str, Any]]) -> None:
        g = self.r.g
        for t in turns:
            self.r.write(f"  {g['on']}{g['hr'] * 2} turn {t.get('turn')}: {t.get('status')}  {t.get('note', '')}")
            self.r.pause(0.04)

    def token_counter(self, before: Optional[int], after: Optional[int], label: str) -> None:
        if before is None or after is None:
            self.r.write(f"  {label}: not measured")
            return
        if self.r.caps.animate and self.r.caps.tty:
            for k in range(1, 9):
                value = int(round(before + (after - before) * k / 8))
                self.r.out.write(f"\r  {label}: {before} {self.r.g['arrow']} {value:>6}")
                self.r.out.flush()
                self.r.pause(0.03)
            self.r.out.write("\n")
        else:
            self.r.write(f"  {label}: {before} {self.r.g['arrow']} {after}")

    def admission_gate(self, accepted: int, refused: int) -> None:
        g = self.r.g
        self.r.write(f"  accepted {self.r.style(g['ok'] * min(accepted, 30), 'green')} {accepted}")
        self.r.write(f"  refused  {self.r.style(g['bad'] * min(refused, 30), 'red')} {refused}")

    def dependency_panel(self, lines: Sequence[str]) -> None:
        self.r.panel("BLOCKED", lines, tone="red")

    def delta(self, before: Optional[float], after: Optional[float]) -> str:
        if before is None or after is None:
            return "n/a"
        diff = after - before
        arrow = self.r.g["up"] if diff > 1e-9 else self.r.g["down"] if diff < -1e-9 else self.r.g["same"]
        return f"{arrow} {diff:+.3f}"

    def split_ruler(self, splits: Dict[str, int]) -> None:
        total = sum(splits.values()) or 1
        width = max(20, self.r.caps.width - 10)
        parts = []
        for name, count in splits.items():
            seg = max(len(name) + 2, int(round(width * count / total)))
            parts.append(f"{name}:{count}".center(seg - 1, self.r.g["hr"]) + self.r.g["vr"])
        self.r.write("  " + "".join(parts), wrap=True)


@contextlib.contextmanager
def raw_keys(enabled: bool) -> Iterator[Callable[[Optional[float]], Optional[str]]]:
    """Single-key reads in cbreak mode; the terminal mode is restored in ``finally``."""

    if not enabled:
        yield lambda timeout=None: None
        return
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)

        def read(timeout: Optional[float] = None) -> Optional[str]:
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            return sys.stdin.read(1) if ready else None

        yield read
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
