"""Terminal rendering for ``lear demo``: capability detection, styles, keys.

Rendering only. No RAG or business logic lives here. The effect recipes live in
``demo_effects`` (a versioned, data-driven registry); they draw data they are
handed and never fabricate a value. When motion is disabled (non-TTY,
``--plain``, ``--no-animate``, ``NO_COLOR``/CI, ``TERM=dumb``), every effect
prints its reduced-motion equivalent: the same final lines, at once.
"""

from __future__ import annotations

import contextlib
import dataclasses
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
PROVENANCE = ("LIVE", "RECORDED", "ILLUSTRATIVE", "UNAVAILABLE", "BLOCKED", "NOT QUALIFYING")
_PROV_TONE = {"LIVE": "green", "RECORDED": "cyan", "ILLUSTRATIVE": "magenta", "UNAVAILABLE": "amber",
              "BLOCKED": "red", "NOT QUALIFYING": "amber"}
PROVENANCE_MEANING = {
    "LIVE": "measured or returned by this execution",
    "RECORDED": "loaded from a checksum-verified recording (run id, capture time, commit)",
    "ILLUSTRATIVE": "explanation or example only, never a measurement",
    "UNAVAILABLE": "this capability or measurement cannot be observed here",
    "BLOCKED": "a dependency, model or environment prerequisite is missing",
    "NOT QUALIFYING": "a real result that does not meet the qualification conditions (baseline)",
}
_CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD", "JENKINS_URL", "TEAMCITY_VERSION")

_UNICODE = {
    "hr": "─", "vr": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "tee": "├", "end": "└",
    "full": "█", "light": "░", "arrow": "→", "ok": "✓", "bad": "✗", "dot": "·", "on": "●", "off": "○",
    "up": "▲", "down": "▼", "same": "■", "hatch": "╱", "spark": "▁▂▃▄▅▆▇█", "mark": "┼", "gate": "╪",
    "tick": "✓", "cross": "✗", "alt": "▓", "lead": "·", "box_on": "■", "box_off": "□",
}
_ASCII = {
    "hr": "-", "vr": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+", "tee": "|-", "end": "`-",
    "full": "#", "light": ".", "arrow": "->", "ok": "OK", "bad": "X", "dot": "*", "on": "(*)", "off": "( )",
    "up": "^", "down": "v", "same": "=", "hatch": "/", "spark": " .:-=+*#", "mark": "+", "gate": "#",
    "tick": "+", "cross": "x", "alt": "=", "lead": ".", "box_on": "#", "box_off": ".",
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

    def refresh_width(self) -> None:
        """Re-measure the terminal after a resize (called at every scene boundary)."""

        if self.caps.tty:
            cols = shutil.get_terminal_size((self.caps.width, 24)).columns
            self.caps = dataclasses.replace(self.caps, width=max(40, min(int(cols), 120)))

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


ARROWS = {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT", "OA": "UP", "OB": "DOWN", "OC": "RIGHT", "OD": "LEFT"}


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
            # Unbuffered os.read so select() sees every byte; arrow keys arrive
            # as ESC [ C / ESC [ D (or ESC O C) and are mapped to RIGHT/LEFT.
            if not select.select([fd], [], [], timeout)[0]:
                return None
            key = os.read(fd, 1).decode("utf-8", "replace")
            if key != "\x1b":
                return key
            seq = ""
            while len(seq) < 2 and select.select([fd], [], [], 0.05)[0]:
                seq += os.read(fd, 1).decode("utf-8", "replace")
            return ARROWS.get(seq, "ESC")

        yield read
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
