"""Versioned, data-driven effect registry for ``lear demo``.

An effect *recipe* = one **layout** (how real data becomes terminal lines) +
one **motion** (how those lines appear over time) + one **information
purpose** (which real pipeline datum or event it shows). Recipes are rows of
data; the renderer is the small set of layout and motion functions below, not
one giant animation function.

Rules enforced by ``validate_registry`` (and by tests):

* every recipe id is unique and every information purpose is unique, so no
  recipe is a renamed copy of another;
* every layout and motion referenced by a recipe exists, and every recipe
  renders its example in plain mode (ASCII, no ANSI);
* the static (reduced-motion) form is the exact final frame of the animated
  form: ``play`` returns the same lines with motion on or off;
* nothing is random: the flap and count motions roll deterministically;
* the ``type_stream`` motion types text only for a real model stream.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
import textwrap
from typing import Any, Callable, Dict, List, Optional, Sequence

from .demo_render import RESET, Renderer

REGISTRY_VERSION = "lear-demo-effects-v2"
_NUM = re.compile(r"\d+(?:\.\d+)?")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 1000 else f"{value:.0f}"
    return str(value)


def _delta(r: Renderer, before: Any, after: Any) -> str:
    try:
        b, a = float(before), float(after)
    except (TypeError, ValueError):
        return ""
    d = a - b
    glyph = r.g["up"] if d > 0 else (r.g["down"] if d < 0 else r.g["same"])
    return f"{glyph} {d:+.4g}"


def _wrap(text: str, width: int) -> List[str]:
    return textwrap.wrap(str(text), width=max(8, width), break_on_hyphens=False) or [""]


# ------------------------------------------------------------------ layouts
# Each layout turns real data into the final, static lines (the reduced-motion
# form). Layouts never invent values: missing data raises KeyError, and the
# scene shows "(visual unavailable)".
def L_registration(r: Renderer, d: Dict[str, Any]) -> List[str]:
    g = r.g
    return [r.style(f"{g['tl']}{g['hr'] * 3} {d['id']}  {d['title']}  ", "bold") + r.badge(d.get("provenance", "UNAVAILABLE"))]


def L_ledger(r: Renderer, d: Dict[str, Any]) -> List[str]:
    rows = d["rows"]
    kw = min(max((len(str(k)) for k, _ in rows), default=0), 34)
    return [f"  {k} {r.g['lead'] * max(2, kw + 2 - len(str(k)))} {_num(v)}" for k, v in rows]


def L_bars(r: Renderer, d: Dict[str, Any]) -> List[str]:
    rows = d["rows"]
    vmax = float(d.get("max") or max([float(v or 0) for _, v in rows] or [1]) or 1)
    lw = min(max((len(str(label)) for label, _ in rows), default=0), 28)
    bw = max(8, min(30, r.caps.width - lw - 18))
    return [f"  {str(label)[:lw].ljust(lw)} {r.bar(v, vmax, bw)} {_num(v)}{d.get('unit', '')}" for label, v in rows]


def L_ladder(r: Renderer, d: Dict[str, Any]) -> List[str]:
    rows = sorted(d["rows"], key=lambda x: -float(x[1] or 0))
    vmax = max([float(x[1] or 0) for x in rows] or [1]) or 1.0
    out = []
    for rank, row in enumerate(rows, 1):
        mark = r.g["on"] if len(row) > 2 and row[2] else r.g["off"]
        out.append(f"  {rank:>2} {mark} {r.bar(row[1], vmax, 16)} {_num(row[1])}  {row[0]}")
    return out


def L_strip(r: Renderer, d: Dict[str, Any]) -> List[str]:
    values = [max(0, int(v or 0)) for v in d["values"]]
    total = sum(values) or 1
    width = max(10, min(r.caps.width - 4, 96))
    segments = "".join((r.g["full"] if i % 2 == 0 else r.g["alt"]) * max(1, round(width * v / total)) for i, v in enumerate(values))
    return ["  " + segments[:width], f"  {len(values)} segments, total {sum(values)}{d.get('unit', '')}"]


def L_tree(r: Renderer, d: Dict[str, Any]) -> List[str]:
    return ["  " + "   " * depth + (r.g["end"] + " " if depth else "") + str(name) for depth, name in enumerate(d["path"])]


def L_stamp(r: Renderer, d: Dict[str, Any]) -> List[str]:
    verdict = str(d["verdict"])
    return [f"  {r.style('[ ' + verdict + ' ]', r.tone_for(verdict), 'bold')} {d.get('text', '')}"]


def L_seal(r: Renderer, d: Dict[str, Any]) -> List[str]:
    value = str(d.get("value") or "")
    groups = " ".join(value[i:i + 8] for i in range(0, min(len(value), 64), 8)) or "n/a"
    return [f"  {r.g['gate']} {d.get('label', 'seal')}: {groups}"]


def L_ruler(r: Renderer, d: Dict[str, Any]) -> List[str]:
    marks = d["marks"]
    vmax = float(d.get("max") or max([float(v) for _, v in marks] or [1]) or 1)
    width = max(20, min(60, r.caps.width - 24))
    line = [r.g["hr"]] * (width + 1)
    for i in range(0, width + 1, max(1, width // 10)):
        line[i] = r.g["mark"]
    out = [f"  0 {''.join(line)} {_num(vmax)}{d.get('unit', '')}"]
    for label, value in marks:
        pos = int(round(width * min(float(value), vmax) / vmax))
        out.append("    " + " " * pos + f"{r.g['up']} {label}={_num(value)}{d.get('unit', '')}")
    return out


def L_spark(r: Renderer, d: Dict[str, Any]) -> List[str]:
    values = [float(v) for v in d["values"] if v is not None]
    if not values:
        return ["  not measured"]
    return [f"  {r.spark(values)}  min {_num(min(values))} max {_num(max(values))} n={len(values)} {d.get('unit', '')}".rstrip()]


def L_timeline(r: Renderer, d: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    rows = d["rows"]
    for i, (label, text) in enumerate(rows):
        out.append(f"  {r.g['on']} {label}  {text}")
        if i < len(rows) - 1:
            out.append(f"  {r.g['vr']}")
    return out


def L_lanes(r: Renderer, d: Dict[str, Any]) -> List[str]:
    out = []
    for label, outcomes in d["rows"]:
        cells = "".join(r.g["tick"] if o == "ok" else (r.g["same"] if o in ("rate_limited", "429") else r.g["cross"]) for o in outcomes)
        out.append(f"  {str(label):<12} {cells}  {sum(1 for o in outcomes if o == 'ok')}/{len(outcomes)} ok")
    return out


def L_gauge(r: Renderer, d: Dict[str, Any]) -> List[str]:
    value, limit = float(d["value"] or 0), float(d["limit"] or 0)
    width = 30
    pos = min(width, int(round(width * value / limit))) if limit else 0
    pct = f"{round(100 * value / limit)}%" if limit else "n/a"
    return [f"  {d.get('label', '')} [{r.g['full'] * pos}{r.g['light'] * (width - pos)}] {_num(d['value'])}/{_num(d['limit'])}{d.get('unit', '')} ({pct})"]


def L_diff(r: Renderer, d: Dict[str, Any]) -> List[str]:
    return [f"  {d.get('label', '')}: {_num(d.get('before'))} {r.g['arrow']} {_num(d.get('after'))}  {_delta(r, d.get('before'), d.get('after'))}{d.get('unit', '')}"]


def L_grid(r: Renderer, d: Dict[str, Any]) -> List[str]:
    cols = [str(c) for c in d["cols"]]
    cw = max([len(c) for c in cols] + [len(strip_ansi(str(x))) for row in d["cells"] for x in row] + [3])
    rw = max([len(str(x)) for x in d["rows"]] + [4])
    out = ["  " + " " * rw + " " + " ".join(c.center(cw) for c in cols)]
    for name, cells in zip(d["rows"], d["cells"]):
        out.append("  " + str(name).ljust(rw) + " " + " ".join(str(c).center(cw) for c in cells))
    return out


def L_waterfall(r: Renderer, d: Dict[str, Any]) -> List[str]:
    rows = d["rows"]
    end = max([float(s or 0) + float(m or 0) for _, s, m in rows] or [1]) or 1.0
    width = max(10, min(36, r.caps.width - 50))
    nw = min(max([len(str(n)) for n, _, _ in rows] + [4]), 30)
    out = []
    for name, start, ms in rows:
        a = min(width - 1, int(width * float(start or 0) / end))
        b = max(1, int(round(width * float(ms or 0) / end)))
        out.append(f"  {str(name)[:nw].ljust(nw)} {' ' * a}{r.g['full'] * b}{' ' * max(0, width - a - b)} {_num(ms)} ms")
    return out


def L_slots(r: Renderer, d: Dict[str, Any]) -> List[str]:
    cap, used = int(d["capacity"]), int(d["used"])
    cells = "".join(r.g["box_on"] if i < used else r.g["box_off"] for i in range(min(cap, 60)))
    return [f"  {d.get('label', '')} [{cells}] {used}/{cap}{d.get('unit', '')}"]


def L_window(r: Renderer, d: Dict[str, Any]) -> List[str]:
    limit, used = int(d["limit"]), int(d["used"])
    shown = min(limit, 40)
    cells = "".join(r.g["box_on"] if i < min(used, shown) else r.g["box_off"] for i in range(shown))
    refused = max(0, used - limit)
    return [f"  {d.get('label', 'window')} {d.get('window_s', 60)}s [{cells}{'...' if limit > shown else ''}] {min(used, limit)}/{limit}"
            + (f"  refused {refused}" if refused else "")]


def L_chain(r: Renderer, d: Dict[str, Any]) -> List[str]:
    return ["  " + f" {r.g['arrow']} ".join(str(x) for x in d["items"])]


def _box(r: Renderer, title: str, lines: Sequence[str], fill: str) -> List[str]:
    g = r.g
    width = max(30, min(r.caps.width, 100))
    inner = width - 4
    title = r.tr(title)
    out = [g["tl"] + fill + f" {title} " + fill * max(0, width - len(title) - 5) + g["tr"]]
    for line in lines:
        for piece in _wrap(r.tr(line), inner):
            out.append(f"{g['vr']} {piece.ljust(inner)} {g['vr']}")
    out.append(g["bl"] + fill * (width - 2) + g["br"])
    return out


def L_box(r: Renderer, d: Dict[str, Any]) -> List[str]:
    return [r.style(x, d.get("tone", "cyan")) for x in _box(r, d["title"], d["lines"], r.g["hr"])]


def L_hatch(r: Renderer, d: Dict[str, Any]) -> List[str]:
    return [r.style(x, "amber") for x in _box(r, d["title"], d["lines"], r.g["hatch"])]


def L_checklist(r: Renderer, d: Dict[str, Any]) -> List[str]:
    out = []
    for status, text in d["rows"]:
        s = str(status)
        up = s.upper()
        glyph = r.g["tick"] if up in ("PASS", "OK", "MATCH", "GROUNDED", "VALID", "YES", "TRUE") else (
            r.g["cross"] if up in ("FAIL", "MISMATCH", "ERROR", "BLOCKED", "NO", "FALSE") else r.g["dot"])
        out.append(f"  {glyph} {r.style(s, r.tone_for(s))} {text}")
    return out


def L_counter(r: Renderer, d: Dict[str, Any]) -> List[str]:
    value = d["value"]
    shown = f"{int(value):06d}" if isinstance(value, int) or (isinstance(value, float) and value.is_integer()) else _num(value)
    return [f"  {d.get('label', '')} {r.g['vr']}{shown}{r.g['vr']} {d.get('unit', '')}".rstrip()]


def L_histogram(r: Renderer, d: Dict[str, Any]) -> List[str]:
    rows = d["rows"]
    vmax = max([float(v or 0) for _, v in rows] or [1]) or 1.0
    height = 5
    out = ["  " + " ".join((r.g["full"] if float(v or 0) / vmax * height >= level - 0.5 else " ") * 4 for _, v in rows)
           for level in range(height, 0, -1)]
    out.append("  " + " ".join(str(label)[:4].ljust(4) for label, _ in rows))
    out.append("  " + " ".join(_num(v)[:4].ljust(4) for _, v in rows))
    return out


def L_axis(r: Renderer, d: Dict[str, Any]) -> List[str]:
    values = [float(v) for v in d["values"]]
    labels = list(d.get("labels") or [f"d{i}" for i in range(len(values))])
    lw = max([len(str(x)) for x in labels] + [4])
    vmax = max([abs(v) for v in values] + [1e-9])
    half = 12
    out = []
    for label, v in zip(labels, values):
        n = int(round(half * abs(v) / vmax))
        left = (r.g["full"] * n).rjust(half) if v < 0 else " " * half
        right = (r.g["full"] * n).ljust(half) if v > 0 else " " * half
        out.append(f"  {str(label):<{lw}} {left}{r.g['vr']}{right} {v:+.4f}")
    return out


def L_split(r: Renderer, d: Dict[str, Any]) -> List[str]:
    cw = max(18, (min(r.caps.width, 110) - 7) // 2)
    (lt, ll), (rt, rl) = d["left"], d["right"]
    left = list(itertools.chain.from_iterable(_wrap(r.tr(x), cw) for x in [lt, *ll]))
    right = list(itertools.chain.from_iterable(_wrap(r.tr(x), cw) for x in [rt, *rl]))
    return [f"  {a.ljust(cw)} {r.g['vr']} {b}".rstrip() for a, b in itertools.zip_longest(left, right, fillvalue="")]


def L_margin(r: Renderer, d: Dict[str, Any]) -> List[str]:
    gw = max([len(str(g)) for g, _ in d["rows"]] + [3])
    return [f"  {str(g).rjust(gw)} {r.g['vr']} {t}" for g, t in d["rows"]]


def L_delimiters(r: Renderer, d: Dict[str, Any]) -> List[str]:
    rows = d["rows"]
    out = []
    for i, (name, size, note) in enumerate(rows):
        joint = r.g["tl"] if i == 0 else (r.g["bl"] if i == len(rows) - 1 else r.g["tee"])
        out.append(f"  {joint}{r.g['hr']} {str(name).upper()} {r.g['hr']} {size}" + (f"  ({note})" if note else ""))
    return out


def L_flap(r: Renderer, d: Dict[str, Any]) -> List[str]:
    return [f"  {d.get('label', '')} " + "".join(f"[{c}]" for c in str(d["value"]))]


def L_trace(r: Renderer, d: Dict[str, Any]) -> List[str]:
    return ["  " + "  " * i + (r.g["end"] + r.g["arrow"] + " " if i else "") + str(item) for i, item in enumerate(d["items"])]


def L_table(r: Renderer, d: Dict[str, Any]) -> List[str]:
    headers, rows = d["headers"], d["rows"]
    cells = [[r.tr(str("n/a" if h is None else h)) for h in headers]] + [[r.tr(_num(c)) for c in row] for row in rows]
    ncol = len(headers)
    widths = [min(40, max(len(row[i]) if i < len(row) else 0 for row in cells)) for i in range(ncol)]
    budget = r.caps.width - 3 * (ncol - 1) - 2
    while sum(widths) > budget and max(widths) > 6:
        widths[widths.index(max(widths))] -= 1
    out = []
    for n, row in enumerate(cells):
        wrapped = [_wrap(row[i] if i < len(row) else "", widths[i]) for i in range(ncol)]
        for k in range(max(len(w) for w in wrapped)):
            parts = [(w[k] if k < len(w) else "").ljust(widths[i]) for i, w in enumerate(wrapped)]
            line = "  " + (" " + r.g["vr"] + " ").join(parts).rstrip()
            out.append(r.style(line, "bold") if n == 0 else line)
        if n == 0:
            out.append("  " + r.style((r.g["hr"] * 3).join(r.g["hr"] * w for w in widths), "grey"))
    return out


LAYOUTS: Dict[str, Callable[[Renderer, Dict[str, Any]], List[str]]] = {
    name[2:]: fn for name, fn in globals().items() if name.startswith("L_") and callable(fn)
}


# ------------------------------------------------------------------ motions
def _can_animate(r: Renderer, lines: Sequence[str]) -> bool:
    return (r.caps.animate and r.caps.tty and r.speed > 0
            and all(len(strip_ansi(r.tr(x))) < r.caps.width for x in lines))


def _frames(r: Renderer, frames: Sequence[str], final: str, delay: float) -> None:
    for frame in frames:
        r.out.write("\r" + frame + "\x1b[K")
        r.out.flush()
        r.pause(delay)
    r.out.write("\r\x1b[K")
    r.write(final, wrap=False)


def _indent(line: str) -> str:
    return " " * (len(line) - len(line.lstrip(" ")))


def M_instant(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    for line in lines:
        r.write(line, indent=_indent(line))


def M_feed(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    for line in lines:
        r.write(line, indent=_indent(line))
        r.pause(0.035)


def _type(r: Renderer, lines: Sequence[str]) -> None:
    if not _can_animate(r, lines[:1]):
        M_instant(r, lines, {})
        return
    first = strip_ansi(r.tr(lines[0]))
    step_n = max(1, len(first) // 40)
    _frames(r, [first[:i] for i in range(0, len(first), step_n)], lines[0], 0.015)
    M_instant(r, lines[1:], {})


def M_type_input(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    _type(r, lines)


def M_type_stream(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    # Typing implies generation: only a real model stream may be typed.
    if d.get("realStream"):
        _type(r, lines)
    else:
        M_instant(r, lines, d)


def M_grow(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    if not _can_animate(r, lines):
        return M_instant(r, lines, d)
    for line in lines:
        plain = strip_ansi(r.tr(line))
        _frames(r, [plain[: int(len(plain) * k / 8)] for k in range(1, 8)], line, 0.03)


def M_flap(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    if not _can_animate(r, lines):
        return M_instant(r, lines, d)
    for line in lines:
        plain = strip_ansi(r.tr(line))
        frames = [re.sub(r"\d", lambda m, f=f: str((int(m.group(0)) + 6 - f) % 10), plain) for f in range(6)]
        _frames(r, frames, line, 0.04)


def M_count(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    if not _can_animate(r, lines):
        return M_instant(r, lines, d)

    def scaled(text: str, k: int) -> str:
        def sub(m: "re.Match[str]") -> str:
            raw = m.group(0)
            value = float(raw) * k / 8
            return (f"{value:.{len(raw.split('.')[1])}f}" if "." in raw else str(int(value))).rjust(len(raw))
        return _NUM.sub(sub, text)

    for line in lines:
        plain = strip_ansi(r.tr(line))
        _frames(r, [scaled(plain, k) for k in range(1, 8)], line, 0.03)


def M_stamp(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    if not _can_animate(r, lines):
        return M_instant(r, lines, d)
    for line in lines:
        plain = strip_ansi(r.tr(line))
        _frames(r, [re.sub(r"\S", ".", plain)], line, 0.08)


def M_sweep(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    if not _can_animate(r, lines):
        return M_instant(r, lines, d)
    for line in lines:
        plain = strip_ansi(r.tr(line))
        if not plain:
            r.write(line)
            continue
        frames = []
        for k in range(10):
            pos = min(len(plain) - 1, int(len(plain) * k / 10))
            frames.append(plain[:pos] + r.g["full"] + plain[pos + 1:])
        _frames(r, frames, line, 0.02)


def M_settle(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    """Rows arrive bottom-up, then settle into their final order in place."""

    if not _can_animate(r, lines) or len(lines) > 30:
        return M_instant(r, lines, d)
    for line in reversed(lines):
        r.out.write(strip_ansi(r.tr(line)) + "\x1b[K\n")
        r.out.flush()
        r.pause(0.02)
    r.out.write(f"\x1b[{len(lines)}A")
    for line in lines:
        r.out.write("\r\x1b[K")
        r.write(line, wrap=False)


def M_scan(r: Renderer, lines: Sequence[str], d: Dict[str, Any]) -> None:
    """Print the block, then pass one highlighted scan line over it."""

    if not _can_animate(r, lines) or not r.caps.color or len(lines) > 30:
        return M_instant(r, lines, d)
    for line in lines:
        r.write(line, wrap=False)
    n = len(lines)
    for i, line in enumerate(lines):
        up = n - i
        r.out.write(f"\x1b[{up}A\r\x1b[7m{strip_ansi(r.tr(line))}{RESET}\x1b[K\x1b[{up}B\r")
        r.out.flush()
        r.pause(0.03)
        r.out.write(f"\x1b[{up}A\r\x1b[K{r.tr(line)}\x1b[{up}B\r")
    r.out.flush()


MOTIONS: Dict[str, Callable[[Renderer, Sequence[str], Dict[str, Any]], None]] = {
    name[2:]: fn for name, fn in globals().items() if name.startswith("M_") and callable(fn)
}

MOTION_POLICY = {
    "instant": ("no motion", "none", "low"),
    "feed": ("one line per 35 ms", "lines printed one by one", "low"),
    "type_input": ("~40 frames x 15 ms on the first line", "single carriage-return line", "low"),
    "type_stream": ("real stream only; otherwise instant", "single carriage-return line", "low"),
    "grow": ("7 frames x 30 ms per line", "carriage-return redraw per line", "low"),
    "flap": ("6 deterministic digit frames x 40 ms", "carriage-return redraw per line", "low"),
    "count": ("7 frames x 30 ms, numbers scale 1/8..8/8", "carriage-return redraw per line", "low"),
    "stamp": ("1 placeholder frame x 80 ms", "carriage-return redraw per line", "low"),
    "sweep": ("10 frames x 20 ms marker pass", "carriage-return redraw per line", "low"),
    "settle": ("arrival frame, then in-place reorder", "cursor-up redraw of <= 30 lines", "medium"),
    "scan": ("one reverse-video pass, 30 ms per line", "cursor-up redraw of <= 30 lines; needs colour", "medium"),
}

LAYOUT_ACCESS = {
    "bars": "every bar is followed by its number",
    "ladder": "rank number and value printed; selection is a glyph and the order",
    "gauge": "value, limit and percentage printed",
    "stamp": "verdict spelled out in words",
    "checklist": "status spelled out next to its glyph",
    "lanes": "ok count printed after the lane",
    "strip": "segment count and total printed",
    "histogram": "values printed under columns",
    "spark": "min, max and n printed",
    "axis": "signed value printed per dimension",
    "ruler": "every mark printed as label=value",
    "waterfall": "duration printed per stage",
    "slots": "used/capacity printed",
    "window": "used/limit and refusals printed",
}


@dataclasses.dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    family: str
    layout: str
    motion: str
    trigger: str
    purpose: str
    scene: str
    example: Optional[Dict[str, Any]] = None

    @property
    def timing(self) -> str:
        return MOTION_POLICY[self.motion][0]

    @property
    def mechanism(self) -> str:
        return MOTION_POLICY[self.motion][1]

    @property
    def cost(self) -> str:
        return MOTION_POLICY[self.motion][2]

    @property
    def reduced(self) -> str:
        return f"static {self.layout} lines (the final frame), printed at once"

    @property
    def accessibility(self) -> str:
        return LAYOUT_ACCESS.get(self.layout, "plain text carries the full meaning; colour only repeats the status")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "family": self.family, "layout": self.layout, "motion": self.motion,
            "trigger": self.trigger, "transformation": f"{self.layout} layout + {self.motion} motion",
            "timing": self.timing, "purpose": self.purpose, "scene": self.scene,
            "interrupt": "interruptible between frames; Ctrl-C ends the demo with exit 130 after an ANSI reset",
            "terminal": "motion needs a TTY, --speed > 0 and no --plain/--no-animate/CI; otherwise static" + (
                "; needs colour" if self.motion == "scan" else ""),
            "reduced": self.reduced, "noColour": "same text without ANSI styles", "cost": self.cost,
            "cleanup": "each frame ends with erase-to-end-of-line; styles are closed per line; the cursor is never hidden",
            "seed": "deterministic: no randomness", "test": "tests/test_lear_demo.py::EffectRegistryTests",
            "accessibility": self.accessibility, "mechanism": self.mechanism,
        }


def R(*args: Any, example: Optional[Dict[str, Any]] = None) -> Recipe:
    return Recipe(*args, example=example)


# fmt: off
RECIPES: List[Recipe] = [
    # id, name, family, layout, motion, trigger, purpose (real datum/event), scene
    R("scene_registration", "Scene registration marks", "precision text reveal", "registration", "stamp", "a scene starts", "scene id, title and provenance label of the scene being rendered", "all"),
    R("narrative_reveal", "Conceptual explanation feed", "precision text reveal", "box", "feed", "director mode shows a scene's explanation", "the conceptual explanation text, labelled as such", "all"),
    R("provenance_legend", "Provenance legend", "precision text reveal", "ledger", "feed", "the demo starts", "the meaning of LIVE, RECORDED, ILLUSTRATIVE, UNAVAILABLE, BLOCKED and NOT QUALIFYING", "S00"),
    R("question_entry", "Question entry", "typewriter/teletype", "chain", "type_input", "the golden question is introduced", "the golden question text that will be asked", "S00"),
    R("answer_stream", "Model answer teletype", "typewriter/teletype", "margin", "type_stream", "an answer is shown", "answer text, typed only when it came from a real model stream", "S06"),
    R("command_echo", "Reproduction command echo", "typewriter/teletype", "margin", "type_input", "examiner mode lists commands", "the exact CLI commands that reproduce the scene", "all"),
    R("docs_check_feed", "Docs verification feed", "line-by-line document registration", "checklist", "feed", "docs verify returns", "status and summary of every docs-verify check", "S01"),
    R("docs_page_register", "Page register", "line-by-line document registration", "ledger", "feed", "docs verify counts pages", "narrative and generated page counts", "S01"),
    R("refs_by_file_bars", "References per source file", "line-by-line document registration", "bars", "grow", "docs verify reports reference counts", "number of path:line references per Lear source file", "S01"),
    R("chunk_heading_tree", "Chunk heading path", "heading hierarchy expansion", "tree", "feed", "a sample chunk is inspected", "heading path of one manifest chunk", "S02"),
    R("document_outline_tree", "Document outline", "heading hierarchy expansion", "tree", "instant", "a document's sections are listed", "distinct section headings of one generated document", "S02"),
    R("chunk_token_strip", "Chunk token strip", "chunk-boundary tracing", "strip", "grow", "the index is built", "token counts of consecutive manifest chunks", "S02"),
    R("chunk_line_margin", "Chunk line span", "chunk-boundary tracing", "margin", "feed", "a sample chunk is inspected", "start and end line of one chunk in its source document", "S02"),
    R("chunk_token_histogram", "Chunk size histogram", "chunk-boundary tracing", "histogram", "instant", "index stats are shown", "distribution of chunk token counts in buckets", "S02"),
    R("neighbour_chain", "Neighbouring chunks", "overlap/adjacency demonstration", "chain", "feed", "a chunk's neighbours are inspected", "the previous, current and next chunk ids of one document", "S02"),
    R("overlap_policy_ledger", "Chunking policy", "overlap/adjacency demonstration", "ledger", "instant", "chunking settings are shown", "configured chunk size, overlap and splitter", "S02"),
    R("document_chunk_bars", "Chunks per document", "page and section indexing", "bars", "grow", "index stats are shown", "number of chunks per source document", "S02"),
    R("line_precision_ledger", "Line precision", "page and section indexing", "ledger", "feed", "index stats are shown", "count of chunks with exact line spans versus section-level spans", "S02"),
    R("coverage_gauge", "Module coverage gauge", "page and section indexing", "gauge", "grow", "docs verify measures coverage", "Lear modules cited by narrative pages out of all modules", "S01"),
    R("embedded_chunk_counter", "Embedded chunk counter", "embedding batch progress using real counts", "counter", "count", "the index rebuild finishes", "number of chunks embedded and stored", "S02"),
    R("embedding_batch_slots", "Embedding batches", "embedding batch progress using real counts", "slots", "feed", "the index rebuild finishes", "embedding batches completed for the corpus", "S02"),
    R("query_vector_axis", "Query vector readout", "vector axis/coordinate readout", "axis", "feed", "the query is embedded", "first dimensions of the query vector from the configured embedder", "S05"),
    R("vector_shape_ledger", "Vector shape", "vector axis/coordinate readout", "ledger", "instant", "embedding settings are shown", "embedding provider, dimension, normalisation and metric", "S05"),
    R("score_support_split", "Fused score vs support", "similarity/distance comparison", "split", "instant", "the top candidates are compared", "fused rank score next to evidence support for the top candidates", "S05"),
    R("support_spark", "Support distribution", "similarity/distance comparison", "spark", "instant", "candidates are scored", "evidence support values across all candidates", "S05"),
    R("candidate_fanout", "Candidate arrival", "candidate arrival and rank ordering", "margin", "feed", "retrieval returns candidates", "candidate chunk ids in the order the engine returned them", "S05"),
    R("candidate_rank_ladder", "Rank ladder", "candidate arrival and rank ordering", "ladder", "settle", "candidates are ranked", "fused score and selection of each candidate", "S05"),
    R("scope_term_checklist", "Scope guard terms", "lexical hit marking", "checklist", "feed", "the scope guard runs", "named terms of the question found or missing in the corpus vocabulary", "S08"),
    R("salient_terms_ledger", "Salient terms", "lexical hit marking", "ledger", "instant", "a question is analysed", "salient terms extracted from the question", "S05"),
    R("dense_stage_bar", "Dense retrieval time", "dense retrieval candidate marking", "bars", "grow", "the semantic stage finishes", "measured duration of the semantic retrieval stage", "S05"),
    R("channel_rank_unavailable", "Per-channel ranks unavailable", "dense retrieval candidate marking", "stamp", "stamp", "per-channel ranks are requested", "the fact that the core does not expose dense/BM25/RRF ranks", "S05"),
    R("hybrid_stage_chain", "Hybrid stage chain", "hybrid merge", "chain", "feed", "retrieval stages are listed", "the retrieval stages that ran, in order", "S05"),
    R("retrieval_mode_stamp", "Retrieval mode", "hybrid merge", "stamp", "stamp", "retrieval completes", "retrieval mode reported by the engine", "S05"),
    R("rrf_definition", "RRF definition", "RRF rank reconciliation", "ledger", "instant", "fusion is explained", "the reciprocal rank fusion formula and its k", "S05"),
    R("fused_score_bars", "Fused scores", "RRF rank reconciliation", "bars", "grow", "fusion completes", "fused score of each selected chunk", "S05"),
    R("support_bars", "Rerank support", "rerank comparison", "bars", "feed", "reranking completes", "heuristic support score of each candidate", "S05"),
    R("rerank_method_stamp", "Reranker identity", "rerank comparison", "stamp", "stamp", "reranking is explained", "that the reranker is heuristic-coverage-v1, not a model", "S05"),
    R("selection_checklist", "Selected vs excluded", "MMR diversity selection", "checklist", "feed", "MMR selection completes", "which candidates were selected or excluded", "S05"),
    R("mmr_lambda_gauge", "MMR balance", "MMR diversity selection", "gauge", "instant", "MMR is explained", "the configured MMR relevance/diversity balance", "S05"),
    R("context_budget_gauge", "Context budget", "context-budget packing", "gauge", "grow", "context is assembled", "context tokens used against the configured budget", "S05"),
    R("context_chunk_strip", "Context packing strip", "context-budget packing", "strip", "grow", "context is assembled", "token count of each selected chunk in context order", "S05"),
    R("exclusion_checklist", "Exclusions", "context exclusion/overflow", "checklist", "instant", "candidates are excluded", "excluded candidates and the engine's reasons", "S05"),
    R("overflow_counter", "Candidates left out", "context exclusion/overflow", "counter", "count", "selection completes", "number of candidates not selected for context", "S05"),
    R("prompt_delimiters", "Prompt sections", "prompt-section delimiting", "delimiters", "feed", "the prompt is assembled", "sizes of system, history, memory, evidence and user sections", "S05"),
    R("prompt_version_flap", "Prompt version", "prompt-section delimiting", "flap", "flap", "the prompt is assembled", "the system prompt version identifier", "S05"),
    R("quarantine_hatch", "Quarantine hatch", "quarantine isolation", "hatch", "instant", "a retrieved chunk is quarantined", "chunk id and injection rules that quarantined it", "S08"),
    R("quarantine_rule_grid", "Quarantine rules", "quarantine isolation", "grid", "instant", "quarantine results are summarised", "which injection rules matched which quarantined chunks", "S08"),
    R("citation_stamp", "Citation stamp", "citation marker resolution", "stamp", "stamp", "a citation is validated", "whether one citation resolves to a retrieved manifest chunk", "S06"),
    R("marker_chain", "Marker to evidence", "citation marker resolution", "chain", "feed", "a citation marker is resolved", "marker number, evidence id and chunk id", "S06"),
    R("source_traceback", "Source-line traceback", "source-line traceback", "trace", "feed", "a citation is traced", "chunk, document lines and pinned Lear source lines of a citation", "S06"),
    R("ref_line_margin", "Source ref check", "source-line traceback", "margin", "feed", "source refs are checked", "each cited Lear path:line and the file's line count at the pin", "S06"),
    R("conflict_split", "Conflict expectation", "conflict highlighting", "split", "instant", "a conflict case is run", "expected CONFLICTED status next to the observed status", "S08"),
    R("conflict_scope_ledger", "Conflict detector scope", "conflict highlighting", "ledger", "instant", "conflict detection is explained", "units the core conflict detector compares", "S08"),
    R("abstention_gate", "Abstention gate", "abstention gate", "box", "instant", "an answer abstains", "abstention reason and the evidence threshold", "S08"),
    R("abstain_counter", "Recorded abstentions", "abstention gate", "counter", "count", "evaluation runs are summarised", "number of ABSTAINED answers in a recorded run", "S09"),
    R("session_timeline", "Session timeline", "session timeline", "timeline", "feed", "a session is replayed", "status and retrieval rewrite of each session turn", "S07"),
    R("turn_role_grid", "Turn roles", "session timeline", "grid", "instant", "session messages are listed", "user and assistant messages stored per turn", "S07"),
    R("followup_rewrite_chain", "Follow-up rewrite", "session timeline", "chain", "type_input", "a follow-up is rewritten", "a follow-up question and its retrieval-only rewrite", "S07"),
    R("session_isolation_stamp", "Session isolation", "session timeline", "stamp", "stamp", "another user opens the session", "the exit code returned when another user opens the session", "S07"),
    R("compaction_token_diff", "Compaction tokens", "history compaction", "diff", "count", "history is compacted", "estimated history tokens before and after one compaction", "S07"),
    R("compaction_fold_slots", "Compaction fold", "history compaction", "slots", "feed", "history is compacted", "messages kept verbatim versus folded into the summary", "S07"),
    R("memory_gate_checklist", "Memory gate", "memory selection/expiry", "checklist", "feed", "memory is tested", "outcomes of the outdated-memory, guard and scoping checks", "S07"),
    R("memory_expiry_counter", "Memory expiry", "memory selection/expiry", "counter", "count", "a memory item is stored", "time-to-live of the stored memory item in seconds", "S07"),
    R("admission_window", "Rate window", "rate-limit admission/refusal", "window", "count", "the rate limiter admits or refuses", "requests used in a sliding window and refusals", "S08"),
    R("rate_limited_bars", "Rate-limited requests", "rate-limit admission/refusal", "bars", "grow", "bench levels finish", "number of 429 responses per bench level", "S10"),
    R("concurrency_slots", "Concurrency slots", "concurrency queue", "slots", "flap", "requests are admitted", "peak in-flight requests against the concurrency limit", "S04"),
    R("queue_timeout_gauge", "Queue timeout", "concurrency queue", "gauge", "instant", "admission policy is shown", "configured queue timeout against the request budget", "S04"),
    R("dependency_block", "Dependency blocked", "model readiness/blocking", "box", "stamp", "the model gate blocks", "doctor exit code, blocked reason and the fix", "S03"),
    R("model_card", "Model card", "model readiness/blocking", "ledger", "feed", "doctor reports models", "configured model tags, caps and whether they were verified", "S03"),
    R("loopback_lamp", "Loopback lamp", "model readiness/blocking", "stamp", "stamp", "settings are loaded", "whether the Ollama endpoint is loopback", "S00"),
    R("doctor_checklist", "Doctor probes", "model readiness/blocking", "checklist", "feed", "doctor finishes", "status of every doctor probe", "S03"),
    R("stream_event_counter", "Stream events", "token stream display", "counter", "count", "a model stream ends", "number of streamed token events", "S06"),
    R("no_stream_stamp", "No model stream", "token stream display", "stamp", "stamp", "a baseline answer is shown", "that no model stream exists for this answer", "S06"),
    R("ttft_itl_ledger", "TTFT and ITL", "TTFT/ITL readout", "ledger", "instant", "an answer finishes", "TTFT, ITL and decode tokens/sec with their definitions", "S06"),
    R("itl_spark", "Inter-token latency", "TTFT/ITL readout", "spark", "instant", "a model stream ends", "inter-token latencies of a real stream", "S06"),
    R("lifecycle_waterfall", "Request lifecycle spans", "TTFT/ITL readout", "waterfall", "grow", "an ask completes", "measured start and duration of each agent lifecycle stage", "S04"),
    R("percentile_ruler", "Percentile ruler", "percentile ruler", "ruler", "sweep", "a latency summary is shown", "p50, p85 and p99 of total latency on one scale", "S10"),
    R("sample_size_stamp", "Sample-size flag", "percentile ruler", "stamp", "stamp", "percentiles come from a small sample", "the sample size behind each percentile", "S10"),
    R("round_table", "Round scores", "evaluation comparison", "table", "feed", "evaluation runs load", "correctness, strict correctness and MRR of each recorded run", "S09"),
    R("round_delta", "Round delta", "evaluation comparison", "diff", "count", "two rounds are compared", "change in correctness between the first and last round", "S09"),
    R("split_strip", "Golden splits", "evaluation comparison", "strip", "instant", "the golden set is validated", "number of cases per split", "S09"),
    R("judge_blocked_stamp", "Judge blocked", "evaluation comparison", "stamp", "stamp", "judge state is shown", "that no LLM-judge scores exist", "S09"),
    R("regression_ledger", "Fixed and regressed", "regression detection", "ledger", "feed", "rounds are compared", "case ids fixed and regressed between consecutive rounds", "S09"),
    R("case_flip_grid", "Case flips", "regression detection", "grid", "instant", "rounds are compared", "per-case correctness across rounds for flipped cases", "S09"),
    R("request_lanes", "Dummy-user lanes", "dummy-user lanes", "lanes", "feed", "a bench level finishes", "outcome of every request per bench level", "S10"),
    R("throughput_bars", "Throughput", "dummy-user lanes", "bars", "grow", "bench levels finish", "requests per second per bench level", "S10"),
    R("wall_time_bars", "Wall time", "dummy-user lanes", "bars", "instant", "bench levels finish", "wall-clock seconds per bench level", "S10"),
    R("exit_code_ledger", "Exit codes", "error/retry/recovery", "ledger", "instant", "guardrail steps finish", "exit code observed for each guardrail step", "S08"),
    R("error_panel", "Error panel", "error/retry/recovery", "box", "stamp", "a step fails", "error message and remediation hint of a failed step", "S08"),
    R("guardrail_grid", "Guardrail verdicts", "error/retry/recovery", "grid", "instant", "guardrail steps finish", "verdict of each guardrail step", "S08"),
    R("status_exit_stamp", "Status to exit code", "error/retry/recovery", "stamp", "stamp", "an answer is mapped", "answer status and its process exit code", "S06"),
    R("bundle_seal", "Replay checksum", "checksum/replay verification", "seal", "flap", "a replay bundle loads", "sha256 of the replay bundle body", "replay"),
    R("evidence_checklist", "Evidence integrity", "checksum/replay verification", "checklist", "feed", "a replay bundle loads", "per-file evidence hash status", "replay"),
    R("source_pin_seal", "Source pin seal", "checksum/replay verification", "seal", "flap", "the source pin is loaded", "the pinned Lear commit id", "S01"),
    R("golden_seal", "Golden set seal", "checksum/replay verification", "seal", "stamp", "the golden set is validated", "sha256 of the frozen golden set", "S09"),
    R("correlation_seal", "Correlation id", "checksum/replay verification", "flap", "flap", "an ask completes", "the correlation id of the request", "S04"),
    R("readiness_card", "Readiness card", "final evidence/report assembly", "box", "feed", "the report is assembled", "overall readiness and the steps to qualify", "S11"),
    R("evidence_index", "Evidence index", "final evidence/report assembly", "margin", "feed", "a scene lists evidence", "evidence files that back the scene", "all"),
    R("qualify_checklist", "Qualification steps", "final evidence/report assembly", "checklist", "feed", "readiness is BLOCKED", "the remaining steps to qualify the local model path", "S11"),
    R("registry_family_bars", "Registry families", "final evidence/report assembly", "bars", "instant", "the effect registry is listed", "number of recipes per effect family", "effects"),
    R("report_runs_counter", "Report runs", "final evidence/report assembly", "counter", "count", "the report is built", "number of evaluation runs listed by the report", "S11"),
    R("bench_table", "Bench table", "percentile ruler", "table", "feed", "bench results load", "count, mean and percentiles per bench level", "S10"),
    R("scene_step_table", "Step verdicts", "error/retry/recovery", "table", "instant", "a scene's steps finish", "expected versus observed result of each demonstrated step", "all"),
    R("git_commit_flap", "rag-formation commit", "precision text reveal", "flap", "flap", "the demo starts", "the rag-formation git commit being demonstrated", "S00"),
    R("doc_rebuild_gauge", "Docs rebuild identity", "line-by-line document registration", "gauge", "grow", "docs are rebuilt from the pin", "generated files byte-identical to the committed docs", "S01"),
    R("stage_timing_table", "Core stage table", "hybrid merge", "table", "instant", "the core reports stages", "status and duration of each core retrieval stage", "S05"),
    R("memory_used_slots", "Memory used", "memory selection/expiry", "slots", "instant", "memory is selected for a turn", "memory items used against the per-turn limit", "S07"),
    R("history_budget_gauge", "History budget", "history compaction", "gauge", "instant", "compaction settings are shown", "configured history token budget against the summary size", "S07"),
    R("injection_split", "Injected page outcome", "quarantine isolation", "split", "instant", "the injected corpus is queried", "quarantine count next to whether the answer obeyed the page", "S08"),
    R("provider_failure_chain", "Provider failure path", "error/retry/recovery", "chain", "feed", "the model provider is unreachable", "the path from provider error to ERROR status and exit 6", "S08"),
    R("level_status_grid", "Status per level", "dummy-user lanes", "grid", "instant", "bench levels finish", "answer statuses observed per bench level", "S10"),
    R("partial_counter", "Recorded partial answers", "abstention gate", "counter", "instant", "partial coverage is checked", "number of PARTIAL answers across recorded runs", "S08"),
    R("readiness_scan", "Readiness scan", "final evidence/report assembly", "ledger", "scan", "the final verdict is shown", "doctor state, recorded decision and overall readiness", "S11"),
    R("chunk_content_seal", "Chunk content hash", "chunk-boundary tracing", "seal", "flap", "a sample chunk is inspected", "content hash of one manifest chunk", "S02"),
    R("lifecycle_result_table", "Lifecycle results", "TTFT/ITL readout", "table", "feed", "an ask completes", "what each agent lifecycle stage did, with its measured duration", "S04"),
    R("core_substage_bars", "Inside the core request", "TTFT/ITL readout", "bars", "grow", "an ask completes", "retrieval, generation and the derived remainder inside the core request", "S04"),
    R("five_dimension_table", "Five evaluation dimensions", "evaluation comparison", "table", "instant", "evaluation runs load", "five deterministic proxy dimensions per recorded run", "S09"),
    R("failure_explorer", "Failure explorer", "regression detection", "table", "feed", "evaluation runs load", "incorrect round-3 cases with status, key-fact coverage, citation precision and reciprocal rank", "S09"),
]
# fmt: on

BY_ID: Dict[str, Recipe] = {r.id: r for r in RECIPES}

LAYOUT_EXAMPLES: Dict[str, Dict[str, Any]] = {
    "registration": {"id": "S00", "title": "Boot and trust boundary", "provenance": "ILLUSTRATIVE"},
    "ledger": {"rows": [["backend", "baseline"], ["chunks", 355]]},
    "bars": {"rows": [["users=1", 27.0], ["users=10", 32.0]], "unit": " req/s"},
    "ladder": {"rows": [["src-a#c001", 0.031, True], ["src-b#c004", 0.027, False]]},
    "strip": {"values": [101, 255, 70, 171], "unit": " tokens"},
    "tree": {"path": ["Lear backend", "Permission engine", "Modes"]},
    "stamp": {"verdict": "MATCH", "text": "example"},
    "seal": {"label": "sha256", "value": "56050cf2689383ca0123456789abcdef"},
    "ruler": {"max": 810.3, "marks": [["p50", 63.3], ["p85", 428.7], ["p99", 810.3]], "unit": " ms"},
    "spark": {"values": [0.51, 0.42, 0.30, 0.28, 0.11]},
    "timeline": {"rows": [["turn 1", "GROUNDED"], ["turn 2", "GROUNDED (rewritten)"]]},
    "lanes": {"rows": [["users=2", ["ok", "ok", "rate_limited", "ok"]]]},
    "gauge": {"label": "context", "value": 1684, "limit": 1800, "unit": " tok"},
    "diff": {"label": "correct", "before": 0.452, "after": 0.548},
    "grid": {"rows": ["round-0", "round-3"], "cols": ["lear-005", "lear-028"], "cells": [["x", "x"], ["+", "+"]]},
    "waterfall": {"rows": [["validate", 0.0, 0.02], ["engine", 0.5, 40.1], ["persist", 40.7, 1.2]]},
    "slots": {"label": "in flight", "capacity": 2, "used": 1},
    "window": {"label": "session", "limit": 1, "used": 2, "window_s": 60},
    "chain": {"items": ["question", "retrieval", "context", "answer"]},
    "box": {"title": "EXAMPLE", "lines": ["one line of real text per row"]},
    "hatch": {"title": "QUARANTINED", "lines": ["chunk src-x#c000: instruction_override"]},
    "checklist": {"rows": [["PASS", "links"], ["WARN", "pin not checked"]]},
    "counter": {"label": "chunks", "value": 355, "unit": "embedded"},
    "histogram": {"rows": [["0-99", 120], ["100", 140], ["200", 80], ["300", 15]]},
    "axis": {"values": [0.12, -0.08, 0.0, 0.31]},
    "split": {"left": ["expected", ["CONFLICTED"]], "right": ["observed", ["GROUNDED"]]},
    "margin": {"rows": [["L32", "first line"], ["L34", "last line"]]},
    "delimiters": {"rows": [["system", "1 prompt", "trusted"], ["evidence", "10 chunks", "untrusted"], ["user", "47 chars", ""]]},
    "flap": {"label": "prompt", "value": "lear-assistant-v1"},
    "trace": {"items": ["[4] src-0aa7#c004", "api_reference.md lines 32-34", "prash/cli.py:L1251"]},
    "table": {"headers": ["run", "correct"], "rows": [["round-0", 0.452], ["round-3", 0.548]]},
}


def play(r: Renderer, recipe_id: str, data: Dict[str, Any]) -> List[str]:
    """Render ``data`` with a recipe; return the final (static) lines."""

    recipe = BY_ID[recipe_id]
    lines = LAYOUTS[recipe.layout](r, data)
    MOTIONS[recipe.motion](r, lines, data)
    return lines


def example_for(recipe: Recipe) -> Dict[str, Any]:
    return recipe.example or LAYOUT_EXAMPLES[recipe.layout]


def validate_registry() -> List[str]:
    problems = []
    ids = [r.id for r in RECIPES]
    if len(ids) != len(set(ids)):
        problems.append("duplicate recipe ids")
    purposes = [r.purpose for r in RECIPES]
    if len(purposes) != len(set(purposes)):
        problems.append("duplicate information purposes (renamed copies)")
    for r in RECIPES:
        if r.layout not in LAYOUTS:
            problems.append(f"{r.id}: unknown layout {r.layout}")
        if r.motion not in MOTIONS:
            problems.append(f"{r.id}: unknown motion {r.motion}")
        for field in ("name", "family", "trigger", "purpose", "scene"):
            if not getattr(r, field):
                problems.append(f"{r.id}: empty {field}")
    return problems
