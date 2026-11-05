"""Render an asciicast v2 recording of ``lear demo`` to an H.264 MP4.

The terminal is emulated with ``pyte`` and drawn with ``Pillow`` in DejaVu
Sans Mono; frames are piped to the ffmpeg binary shipped by
``imageio-ffmpeg``. Timing is the recording's real timing. These three
packages are tooling for this script only: they are NOT dependencies of
RAG Formation and are not needed to run the demo.

  python3 -m venv /tmp/vidvenv && /tmp/vidvenv/bin/pip install pyte Pillow imageio-ffmpeg
  /tmp/vidvenv/bin/python scripts/render_demo_video.py \
      docs/lear_agent/demo_video/lear_demo_terminal.cast docs/lear_agent/demo_video/lear_demo_terminal.mp4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import pyte
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
BG, FG = (24, 26, 33), (214, 218, 224)
PALETTE = {
    "black": (40, 44, 52), "red": (232, 96, 96), "green": (122, 200, 120), "brown": (229, 192, 100),
    "blue": (97, 175, 239), "magenta": (205, 132, 230), "cyan": (86, 200, 214), "white": (214, 218, 224),
    "brightblack": (128, 134, 146), "brightred": (255, 120, 120), "brightgreen": (150, 230, 140),
    "brightbrown": (255, 214, 120), "brightyellow": (255, 214, 120), "brightblue": (130, 195, 255),
    "brightmagenta": (225, 160, 245), "brightcyan": (120, 225, 235), "brightwhite": (250, 250, 250),
}


def colour(name: str, default: tuple) -> tuple:
    if name in ("default", None, ""):
        return default
    if name in PALETTE:
        return PALETTE[name]
    try:
        return tuple(int(name[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cast")
    ap.add_argument("mp4")
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--font-size", type=int, default=14)
    ap.add_argument("--crf", type=int, default=28)
    ap.add_argument("--poster", default=None, help="also save one PNG frame (at --poster-at seconds)")
    ap.add_argument("--poster-at", type=float, default=None)
    args = ap.parse_args()

    lines = Path(args.cast).read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    events = [json.loads(x) for x in lines[1:] if x.strip()]
    cols, rows = int(header["width"]), int(header["height"])
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)

    regular = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), args.font_size)
    bold = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono-Bold.ttf"), args.font_size)
    cw = round(regular.getlength("M"))
    ch = round(args.font_size * 1.3)
    pad = 12
    width = (cols * cw + 2 * pad + 1) // 2 * 2
    height = (rows * ch + 2 * pad + 1) // 2 * 2

    def draw() -> Image.Image:
        img = Image.new("RGB", (width, height), BG)
        d = ImageDraw.Draw(img)
        for y in range(rows):
            line = screen.buffer[y]
            x = 0
            while x < cols:
                c = line[x]
                key = (c.fg, c.bg, c.bold, c.reverse)
                start = x
                text = []
                while x < cols and (line[x].fg, line[x].bg, line[x].bold, line[x].reverse) == key:
                    text.append(line[x].data or " ")
                    x += 1
                fg, bg = colour(c.fg, FG), colour(c.bg, BG)
                if c.reverse:
                    fg, bg = bg, fg
                px, py = pad + start * cw, pad + y * ch
                if bg != BG:
                    d.rectangle([px, py, px + len(text) * cw - 1, py + ch - 1], fill=bg)
                s = "".join(text)
                if s.strip():
                    font = bold if c.bold else regular
                    for i, glyph in enumerate(s):  # per cell: keeps the grid exact
                        if glyph != " ":
                            d.text((px + i * cw, py + 2), glyph, font=font, fill=fg)
        if not screen.cursor.hidden:
            cx, cy = pad + screen.cursor.x * cw, pad + screen.cursor.y * ch
            d.rectangle([cx, cy + 2, cx + cw - 1, cy + ch - 2], outline=(200, 200, 200))
        return img

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
                             "-r", str(args.fps), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-tune", "stillimage",
                             "-crf", str(args.crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart", args.mp4],
                            stdin=subprocess.PIPE)
    total = (events[-1][0] if events else 0) + 2.0
    frames = int(total * args.fps) + 1
    ei, frame, poster_done = 0, None, False
    for k in range(frames):
        t = k / args.fps
        changed = False
        while ei < len(events) and events[ei][0] <= t:
            if events[ei][1] == "o":
                stream.feed(events[ei][2])
            ei += 1
            changed = True
        if changed or frame is None:
            img = draw()
            frame = img.tobytes()
        if args.poster and args.poster_at is not None and not poster_done and t >= args.poster_at:
            img.save(args.poster, optimize=True)  # the frame on screen at --poster-at
            poster_done = True
        proc.stdin.write(frame)
    proc.stdin.close()
    code = proc.wait()
    print(json.dumps({"mp4": args.mp4, "frames": frames, "seconds": round(total, 1), "size": [width, height], "ffmpeg": code}))
    return code


if __name__ == "__main__":
    sys.exit(main())
