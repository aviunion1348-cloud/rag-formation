"""Measure the terminal renderer's overhead with motion off and on.

Renders the full recorded replay (all 12 scenes) into an in-memory TTY-like
stream three ways and prints JSON:

* ``static``    colour, no motion: pure layout + text cost;
* ``animated``  colour + motion with a near-zero speed, so every frame is
  generated but the deliberate pauses are ~0: frame-generation cost;
* ``pauses``    the deliberate pause time motion adds at --speed 1, summed
  from the renderer's own pause calls (no real sleeping).

Usage: python3 scripts/measure_demo_render.py [--bundle PATH] [--repeat 5]
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ragformation.lear import demo  # noqa: E402
from ragformation.lear.demo_render import Renderer, TermCaps  # noqa: E402


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _render(bundle: dict, *, animate: bool, speed: float) -> tuple:
    out = _Tty()
    caps = TermCaps(tty=True, color=True, unicode=True, width=110, animate=animate, interactive=False)
    r = Renderer(caps, out, speed=speed)
    sr = demo.SceneRenderer(r, examiner=False, replay_meta=bundle["meta"])
    started = time.perf_counter()
    for rec in bundle["scenes"]:
        sr.scene(rec)
    return (time.perf_counter() - started) * 1000.0, out.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(demo.default_bundle()))
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args()
    bundle, check = demo.load_bundle(Path(args.bundle))
    if not check["valid"]:
        print(json.dumps({"error": check["problems"]}))
        return 5
    static, animated = [], []
    for _ in range(args.repeat):
        ms, text_static = _render(bundle, animate=False, speed=1.0)
        static.append(ms)
        with mock.patch("time.sleep"):
            ms, text_anim = _render(bundle, animate=True, speed=1e-9)
        animated.append(ms)
    slept: list = []
    with mock.patch("time.sleep", side_effect=lambda s: slept.append(s)):
        _render(bundle, animate=True, speed=1.0)
    result = {
        "bundle": demo._rel(Path(args.bundle)), "scenes": len(bundle["scenes"]), "repeat": args.repeat,
        "staticMs": {"median": round(statistics.median(static), 2), "max": round(max(static), 2)},
        "animatedFrameGenerationMs": {"median": round(statistics.median(animated), 2), "max": round(max(animated), 2)},
        "outputBytes": {"static": len(text_static.encode()), "animated": len(text_anim.encode()),
                        "carriageReturnFrames": text_anim.count("\r")},
        "deliberatePauseAtSpeed1S": round(sum(slept), 2), "pauseCalls": len(slept),
        "note": "pauses are deliberate pacing, skipped by --speed 0, --no-animate, --plain, CI and non-TTY output",
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
