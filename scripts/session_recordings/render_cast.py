#!/usr/bin/env python3
"""Render an asciicast v2 terminal recording to a 1920x1080 H.264 MP4.

Adapted from scripts/render_demo_video.py. The recorded output bytes are fed,
at their recorded times, into a ``pyte`` terminal emulator; the emulated screen
is drawn cell by cell in DejaVu Sans Mono and piped to the ffmpeg binary shipped
by ``imageio-ffmpeg``. Nothing is added, removed or re-timed: frame k shows the
screen state after every event with time <= k / fps. The only additions are a
dark margin around the terminal grid (to make an exact 16:9 frame) and a final
hold of ``--tail`` seconds on the last screen.

Tooling only (not dependencies of RAG Formation):
  python3 -m venv /tmp/recvenv && /tmp/recvenv/bin/pip install asciinema==2.4.0 pyte Pillow imageio-ffmpeg

  /tmp/recvenv/bin/python scripts/session_recordings/render_cast.py terminal/01_x.cast 01_x.mp4
  /tmp/recvenv/bin/python scripts/session_recordings/render_cast.py terminal/01_x.cast --frames-at 5,10 --frames-dir /tmp/f --no-video
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
BG, FG = (24, 26, 33), (214, 218, 224)
PALETTE = {
    "black": (40, 44, 52), "red": (232, 96, 96), "green": (122, 200, 120), "brown": (229, 192, 100),
    "yellow": (229, 192, 100), "blue": (97, 175, 239), "magenta": (205, 132, 230), "cyan": (86, 200, 214),
    "white": (214, 218, 224), "brightblack": (128, 134, 146), "brightred": (255, 120, 120),
    "brightgreen": (150, 230, 140), "brightbrown": (255, 214, 120), "brightyellow": (255, 214, 120),
    "brightblue": (130, 195, 255), "brightmagenta": (225, 160, 245), "brightcyan": (120, 225, 235),
    "brightwhite": (250, 250, 250),
}
ENCODE = ["-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]


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
    def __init__(self, cols: int, rows: int, width: int, height: int, font_size: int) -> None:
        self.cols, self.rows, self.width, self.height = cols, rows, width, height
        self.regular = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), font_size)
        self.bold = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono-Bold.ttf"), font_size)
        self.cw = int(round(self.regular.getlength("M")))
        self.ch = int(round(font_size * 1.3))
        grid_w, grid_h = cols * self.cw, rows * self.ch
        if grid_w > width or grid_h > height:
            raise SystemExit(f"terminal grid {grid_w}x{grid_h} does not fit {width}x{height}; lower --font-size")
        self.ox, self.oy = (width - grid_w) // 2, (height - grid_h) // 2
        self.tiles: Dict[tuple, Image.Image] = {}

    def tile(self, char: str, bold: bool, fg: tuple, bg: tuple) -> Image.Image:
        key = (char, bold, fg, bg)
        img = self.tiles.get(key)
        if img is None:
            img = Image.new("RGB", (self.cw, self.ch), bg)
            if char.strip():
                ImageDraw.Draw(img).text((0, 2), char, font=self.bold if bold else self.regular, fill=fg)
            self.tiles[key] = img
        return img

    def draw(self, screen: pyte.Screen) -> Image.Image:
        img = Image.new("RGB", (self.width, self.height), BG)
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
            ImageDraw.Draw(img).rectangle([cx, cy + 2, cx + self.cw - 1, cy + self.ch - 2], outline=(200, 200, 200))
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cast")
    ap.add_argument("mp4", nargs="?")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--font-size", type=int, default=20)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--crf", type=int, default=22)
    ap.add_argument("--tail", type=float, default=3.0)
    ap.add_argument("--frames-at", default="", help="comma-separated times (s): also save those frames as PNG")
    ap.add_argument("--frames-dir", default=None)
    ap.add_argument("--no-video", action="store_true")
    args = ap.parse_args()

    header, events = load_cast(Path(args.cast))
    cols, rows = int(header["width"]), int(header["height"])
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    painter = Painter(cols, rows, args.width, args.height, args.font_size)
    last = events[-1][0] if events else 0.0
    total = last + args.tail
    frames = int(total * args.fps) + 1
    wanted = sorted(float(x) for x in args.frames_at.split(",") if x.strip())
    if wanted and args.frames_dir:
        Path(args.frames_dir).mkdir(parents=True, exist_ok=True)
    proc = None if args.no_video else ffmpeg_writer(args.mp4, args.width, args.height, args.fps, args.crf)
    ei, frame_bytes, img, changes = 0, None, None, 0
    for k in range(frames):
        t = k / args.fps
        changed = False
        while ei < len(events) and events[ei][0] <= t:
            if events[ei][1] == "o":
                stream.feed(events[ei][2])
            ei += 1
            changed = True
        if changed or img is None:
            img = painter.draw(screen)
            frame_bytes = img.tobytes()
            changes += 1
        while wanted and t >= wanted[0]:
            img.save(Path(args.frames_dir) / f"frame_{wanted[0]:09.3f}.png")
            wanted.pop(0)
        if proc is not None:
            proc.stdin.write(frame_bytes)
    code = 0
    if proc is not None:
        proc.stdin.close()
        code = proc.wait()
    print(json.dumps({"cast": args.cast, "mp4": args.mp4, "frames": frames, "fps": args.fps, "seconds": round(frames / args.fps, 3),
                      "castLastEventS": round(last, 3), "redraws": changes, "size": [args.width, args.height],
                      "grid": [cols, rows], "cell": [painter.cw, painter.ch], "ffmpeg": code}))
    return code


if __name__ == "__main__":
    sys.exit(main())
