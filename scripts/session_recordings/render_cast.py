#!/usr/bin/env python3
"""Render an asciicast v2 terminal recording to a 1920x1080 H.264 MP4.

The recorded output bytes are fed into a ``pyte`` terminal emulator and the
emulated screen is drawn cell by cell in DejaVu Sans Mono, then piped to the
ffmpeg binary shipped by ``imageio-ffmpeg``. Terminal content is never added,
removed or edited.

The video has up to two parts, both rendered from the same cast and labelled on
screen in a banner above the terminal:

1. REAL-TIME PLAYBACK: frame k shows the screen after every event whose
   recorded time is <= k / fps -- the recorded timing, unmodified.
2. PACED REPLAY: the same events again at 1x, except that playback pauses
   ("HELD FOR READING") before any output that would scroll a not-yet-held line
   off the screen, so every line is readable in at least one hold. During a
   hold the recorded clock shown in the banner stops; it always shows the true
   recorded time of the screen on display.

A last-screen hold ends each part. The banner below the terminal names the
source cast. ``--card`` renders an editorial chapter card (plain text, labelled
as editorial) with identical encoder settings so files can be concatenated with
``ffmpeg -f concat -c copy``.

Tooling only (not dependencies of RAG Formation):
  python3 -m venv /tmp/recvenv && /tmp/recvenv/bin/pip install asciinema==2.4.0 pyte Pillow imageio-ffmpeg

  /tmp/recvenv/bin/python scripts/session_recordings/render_cast.py terminal/01_x.cast 01_x.mp4 --label "..."
  /tmp/recvenv/bin/python scripts/session_recordings/render_cast.py --card card.json card.mp4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import imageio_ffmpeg
import pyte
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
BG, FG, BAND = (24, 26, 33), (214, 218, 224), (14, 15, 20)
PALETTE = {
    "black": (40, 44, 52), "red": (232, 96, 96), "green": (122, 200, 120), "brown": (229, 192, 100),
    "yellow": (229, 192, 100), "blue": (97, 175, 239), "magenta": (205, 132, 230), "cyan": (86, 200, 214),
    "white": (214, 218, 224), "brightblack": (128, 134, 146), "brightred": (255, 120, 120),
    "brightgreen": (150, 230, 140), "brightbrown": (255, 214, 120), "brightyellow": (255, 214, 120),
    "brightblue": (130, 195, 255), "brightmagenta": (225, 160, 245), "brightcyan": (120, 225, 235),
    "brightwhite": (250, 250, 250),
}
MODE_STYLE = {
    "realtime": ((122, 200, 120), "REAL-TIME PLAYBACK · recorded timing, 1x"),
    "paced": ((86, 200, 214), "PACED REPLAY · same recording at 1x, pauses to read"),
    "held": ((229, 192, 100), "PACED REPLAY · HELD FOR READING · recorded clock stopped"),
    "end": ((128, 134, 146), "END OF RECORDING · last screen held"),
    "card": ((205, 132, 230), "EDITORIAL CHAPTER CARD · not a recording"),
}
ENCODE = ["-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-tune", "stillimage", "-pix_fmt", "yuv420p",
          "-movflags", "+faststart"]
BAND_H = 40


def colour(name: Optional[str], default: Tuple[int, int, int]) -> Tuple[int, int, int]:
    if not name or name == "default":
        return default
    if name in PALETTE:
        return PALETTE[name]
    if len(name) == 6:
        try:
            return (int(name[0:2], 16), int(name[2:4], 16), int(name[4:6], 16))
        except ValueError:
            return default
    return default


class Painter:
    def __init__(self, cols: int, rows: int, width: int, height: int, font_size: int, cell_h: int) -> None:
        self.cols, self.rows, self.width, self.height = cols, rows, width, height
        self.regular = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), font_size)
        self.bold = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono-Bold.ttf"), font_size)
        self.band_font = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), 18)
        self.band_bold = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono-Bold.ttf"), 18)
        self.cw = int(round(self.regular.getlength("M")))
        self.ch = cell_h
        grid_w, grid_h = cols * self.cw, rows * self.ch
        if grid_w > width or grid_h > height - 2 * BAND_H:
            raise SystemExit(f"terminal grid {grid_w}x{grid_h} does not fit {width}x{height} with banners; lower --font-size")
        self.ox, self.oy = (width - grid_w) // 2, BAND_H + (height - 2 * BAND_H - grid_h) // 2
        self.tiles: Dict[tuple, Image.Image] = {}

    def tile(self, char: str, bold: bool, fg: tuple, bg: tuple) -> Image.Image:
        key = (char, bold, fg, bg)
        img = self.tiles.get(key)
        if img is None:
            img = Image.new("RGB", (self.cw, self.ch), bg)
            if char.strip():
                ImageDraw.Draw(img).text((0, 1), char, font=self.bold if bold else self.regular, fill=fg)
            self.tiles[key] = img
        return img

    def grid(self, screen: pyte.Screen) -> Image.Image:
        img = Image.new("RGB", (self.width, self.height), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, self.width, BAND_H - 1], fill=BAND)
        d.rectangle([0, self.height - BAND_H, self.width, self.height], fill=BAND)
        for y in range(self.rows):
            line = screen.buffer[y]
            for x in range(self.cols):
                c = line[x]
                fg, bg = colour(c.fg, FG), colour(c.bg, BG)
                if c.reverse:
                    fg, bg = bg, fg
                char = c.data or " "
                if char == " " and bg == BG:
                    continue
                img.paste(self.tile(char, bool(c.bold), fg, bg), (self.ox + x * self.cw, self.oy + y * self.ch))
        if not screen.cursor.hidden:
            cx, cy = self.ox + screen.cursor.x * self.cw, self.oy + screen.cursor.y * self.ch
            d.rectangle([cx, cy + 2, cx + self.cw - 1, cy + self.ch - 2], outline=(200, 200, 200))
        return img

    def banners(self, base: Image.Image, mode: str, top_right: str, bottom_left: str, bottom_right: str) -> Image.Image:
        img = base.copy()
        d = ImageDraw.Draw(img)
        col, text = MODE_STYLE[mode]
        d.rectangle([12, 9, 28, 30], fill=col)
        d.text((40, 9), text, font=self.band_bold, fill=col)
        d.text((self.width - 12 - d.textlength(top_right, font=self.band_font), 9), top_right, font=self.band_font, fill=FG)
        y = self.height - BAND_H + 10
        right_w = d.textlength(bottom_right, font=self.band_font)
        room = self.width - 24 - right_w - 36  # never let the two texts touch
        while bottom_left and d.textlength(bottom_left, font=self.band_font) > room:
            bottom_left = bottom_left[:-2] + "…"
        d.text((12, y), bottom_left, font=self.band_font, fill=(150, 156, 168))
        d.text((self.width - 12 - right_w, y), bottom_right, font=self.band_font, fill=(150, 156, 168))
        return img


def load_cast(path: Path) -> Tuple[dict, List[list]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    events = [json.loads(x) for x in lines[1:] if x.strip()]
    return header, events


def ffmpeg_writer(out: str, width: int, height: int, fps: int, crf: int) -> subprocess.Popen:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    return subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
                             "-r", str(fps), "-i", "-", *ENCODE, "-crf", str(crf), "-r", str(fps), out], stdin=subprocess.PIPE)


class Writer:
    """Collects frames for ffmpeg and the optional PNG key frames."""

    def __init__(self, proc: Optional[subprocess.Popen], fps: int) -> None:
        self.proc, self.fps, self.count = proc, fps, 0

    def emit(self, frame: bytes, n: int = 1) -> None:
        for _ in range(n):
            if self.proc is not None:
                self.proc.stdin.write(frame)
            self.count += 1

    @property
    def seconds(self) -> float:
        return self.count / self.fps


def chunks(events: List[list]) -> List[Tuple[float, str]]:
    """Output events split after each newline (escape sequences never contain one)."""
    out: List[Tuple[float, str]] = []
    for t, kind, data in events:
        if kind != "o":
            continue
        start = 0
        for i, ch in enumerate(data):
            if ch == "\n":
                out.append((t, data[start:i + 1]))
                start = i + 1
        if start < len(data):
            out.append((t, data[start:]))
    return out


def render_cast(args: argparse.Namespace) -> Dict[str, object]:
    header, events = load_cast(Path(args.cast))
    cols, rows = int(header["width"]), int(header["height"])
    painter = Painter(cols, rows, args.width, args.height, args.font_size, args.cell_height)
    last = events[-1][0] if events else 0.0
    proc = None if args.no_video else ffmpeg_writer(args.mp4, args.width, args.height, args.fps, args.crf)
    w = Writer(proc, args.fps)
    src = f"source {args.source or Path(args.cast).name} · asciicast v2 {cols}x{rows} · no content added"
    keydir = Path(args.keyframes) if args.keyframes else None
    if keydir:
        keydir.mkdir(parents=True, exist_ok=True)
    report: Dict[str, object] = {"cast": args.cast, "mp4": args.mp4, "fps": args.fps, "size": [args.width, args.height],
                                 "grid": [cols, rows], "cell": [painter.cw, painter.ch], "castLastEventS": round(last, 3)}

    def clock(t: float) -> str:
        return f"recorded clock t = {t:7.3f} s of {last:.3f} s"

    # Part 1: real-time playback.
    if args.mode in ("both", "realtime"):
        screen, stream = pyte.Screen(cols, rows), None
        stream = pyte.Stream(screen)
        n = int(last * args.fps) + 1
        ei, base = 0, painter.grid(screen)
        for k in range(n):
            t = k / args.fps
            changed = False
            while ei < len(events) and events[ei][0] <= t:
                if events[ei][1] == "o":
                    stream.feed(events[ei][2])
                ei += 1
                changed = True
            if changed:
                base = painter.grid(screen)
            w.emit(painter.banners(base, "realtime", clock(min(t, last)), args.label, src).tobytes())
        while ei < len(events):  # events at the very end (after the last frame time)
            if events[ei][1] == "o":
                stream.feed(events[ei][2])
            ei += 1
        base = painter.grid(screen)
        w.emit(painter.banners(base, "end", clock(last), args.label, src).tobytes(), int(args.tail * args.fps))
        report["realtimeSeconds"] = round(w.seconds, 3)

    # Part 2: paced replay.
    if args.mode in ("both", "paced"):
        start_frames = w.count
        screen = pyte.Screen(cols, rows)
        stream = pyte.Stream(screen)
        base = painter.grid(screen)
        budget = rows - 2
        pending, cast_t, holds = 0, 0.0, []
        video_t = 0.0  # seconds of paced part emitted so far (frame-exact)
        held_s = 0.0

        def play_until(target: float) -> None:
            nonlocal video_t
            # emit frames while the cast clock (video_t - held_s) is before target
            while (video_t - held_s) + 1.0 / args.fps <= target:
                t = video_t - held_s
                w.emit(painter.banners(base, "paced", clock(t), args.label, src).tobytes())
                video_t += 1.0 / args.fps

        def hold(t: float, seconds: float, mode: str) -> None:
            nonlocal video_t, held_s
            img = painter.banners(base, mode, clock(t), args.label, src)
            n = int(round(seconds * args.fps))
            w.emit(img.tobytes(), n)
            video_t += n / args.fps
            held_s += n / args.fps
            holds.append(round(t, 3))
            if keydir:
                img.save(keydir / f"hold_{len(holds):03d}_t{t:08.3f}.png")

        for t, data in chunks(events):
            play_until(t)
            nl = data.count("\n")
            if pending and pending + nl > budget:
                hold(t, args.hold, "held")
                pending = 0
            stream.feed(data)
            base = painter.grid(screen)
            pending += nl
            cast_t = t
        hold(last, args.tail_paced, "end")
        report["pacedSeconds"] = round((w.count - start_frames) / args.fps, 3)
        report["holds"] = len(holds)
        report["holdAtRecordedS"] = holds
        report["holdSeconds"] = args.hold

    if proc is not None:
        proc.stdin.close()
        report["ffmpegExit"] = proc.wait()
    report["frames"] = w.count
    report["seconds"] = round(w.seconds, 3)
    return report


def render_card(args: argparse.Namespace) -> Dict[str, object]:
    spec = json.loads(Path(args.card).read_text(encoding="utf-8"))
    painter = Painter(158, 40, args.width, args.height, args.font_size, args.cell_height)
    img = Image.new("RGB", (args.width, args.height), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, args.width, BAND_H - 1], fill=BAND)
    d.rectangle([0, args.height - BAND_H, args.width, args.height], fill=BAND)
    title_font = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono-Bold.ttf"), 44)
    body_font = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), 26)
    y = 150
    d.text((120, y), spec["title"], font=title_font, fill=(250, 250, 250))
    y += 90
    for line in spec.get("lines", []):
        fill = {"warn": (229, 192, 100), "fail": (232, 96, 96), "ok": (122, 200, 120), "dim": (150, 156, 168)}.get(line.get("style"), FG)
        d.text((120, y), line["text"], font=body_font, fill=fill)
        y += 44
    frame = painter.banners(img, "card", spec.get("topRight", ""), spec.get("bottomLeft", ""), spec.get("bottomRight", ""))
    proc = None if args.no_video else ffmpeg_writer(args.mp4, args.width, args.height, args.fps, args.crf)
    w = Writer(proc, args.fps)
    w.emit(frame.tobytes(), int(round(spec.get("seconds", 5) * args.fps)))
    if args.keyframes:
        Path(args.keyframes).mkdir(parents=True, exist_ok=True)
        frame.save(Path(args.keyframes) / (Path(args.mp4 or "card").stem + ".png"))
    code = None
    if proc is not None:
        proc.stdin.close()
        code = proc.wait()
    return {"card": args.card, "mp4": args.mp4, "frames": w.count, "seconds": round(w.seconds, 3), "ffmpegExit": code}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cast", nargs="?")
    ap.add_argument("mp4", nargs="?")
    ap.add_argument("--card", default=None, help="render an editorial chapter card from this JSON spec instead of a cast")
    ap.add_argument("--mode", choices=["both", "realtime", "paced"], default="both")
    ap.add_argument("--label", default="", help="bottom-left banner text")
    ap.add_argument("--source", default=None, help="cast path shown in the bottom-right banner")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--font-size", type=int, default=20)
    ap.add_argument("--cell-height", type=int, default=25)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--crf", type=int, default=22)
    ap.add_argument("--tail", type=float, default=3.0, help="last-screen hold after the real-time part (s)")
    ap.add_argument("--hold", type=float, default=7.0, help="hold per screenful in the paced replay (s)")
    ap.add_argument("--tail-paced", type=float, default=6.0, help="last-screen hold after the paced replay (s)")
    ap.add_argument("--keyframes", default=None, help="save each paced-replay hold as a PNG in this directory")
    ap.add_argument("--no-video", action="store_true")
    args = ap.parse_args()
    if args.card:
        args.mp4 = args.mp4 or args.cast
        report = render_card(args)
    else:
        report = render_cast(args)
    print(json.dumps(report))
    return int(report.get("ffmpegExit") or 0)


if __name__ == "__main__":
    sys.exit(main())
