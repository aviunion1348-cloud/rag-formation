"""Record a real terminal session of ``lear demo`` as an asciicast v2 file.

Every byte after a command line is the real output of the real process,
captured from a pseudo-terminal with its real timing; key presses are written
into the same pseudo-terminal, exactly as a person at the keyboard would.
Only the shell prompt, the typed command line and the ``echo $?`` line are
written by this recorder (there is no shell); the exit codes they show are
the real ones.

Stdlib only. Render the result with scripts/render_demo_video.py.

Usage:
  python3 scripts/record_demo_video.py --out docs/lear_agent/demo_video/lear_demo_terminal.cast \
      --source ../lear-backend
"""

from __future__ import annotations

import argparse
import codecs
import fcntl
import json
import os
import pty
import select
import struct
import sys
import termios
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
PROMPT = "\x1b[1;32mexaminer@lear-demo\x1b[0m:\x1b[1;34m~/rag-formation\x1b[0m$ "


class Recorder:
    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.t0 = time.monotonic()
        self.events: List[list] = []

    def emit(self, data: str) -> None:
        if data:
            self.events.append([round(time.monotonic() - self.t0, 4), "o", data])

    def type_line(self, text: str) -> None:
        self.emit(PROMPT)
        time.sleep(0.8)
        for ch in text:
            self.emit(ch)
            time.sleep(0.03)
        time.sleep(0.5)
        self.emit("\r\n")

    def run(self, argv: Sequence[str], keys: Sequence[Tuple[str, float]] = (), idle_s: float = 0.8,
            timeout_s: float = 900.0) -> int:
        pid, fd = pty.fork()
        if pid == 0:  # child: the pty is its controlling terminal
            os.environ.update({"TERM": "xterm-256color", "COLUMNS": str(self.width), "LINES": str(self.height),
                               "PYTHONUNBUFFERED": "1"})
            os.environ.pop("NO_COLOR", None)
            os.environ.pop("CI", None)
            os.chdir(str(REPO))
            os.execvp(argv[0], list(argv))
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.height, self.width, 0, 0))
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = list(keys)
        last_out = last_key = time.monotonic()
        started = time.monotonic()
        while True:
            ready, _, _ = select.select([fd], [], [], 0.05)
            now = time.monotonic()
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                self.emit(decoder.decode(data))
                last_out = now
                continue
            if pending and now - last_out >= idle_s and now - last_key >= pending[0][1]:
                key, _ = pending.pop(0)
                os.write(fd, key.encode())
                last_key = now
            if now - started > timeout_s:
                os.kill(pid, 9)
                break
        _, status = os.waitpid(pid, 0)
        os.close(fd)
        return os.waitstatus_to_exitcode(status)

    def show_exit(self, code: int) -> None:
        self.type_line("echo $?")
        self.emit(f"{code}\r\n")

    def save(self, path: Path, title: str) -> None:
        header = {"version": 2, "width": self.width, "height": self.height, "timestamp": int(time.time()),
                  "title": title, "env": {"TERM": "xterm-256color", "SHELL": "none (recorder)"}}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(header) + "\n")
            for event in self.events:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(REPO / "docs" / "lear_agent" / "demo_video" / "lear_demo_terminal.cast"))
    ap.add_argument("--source", default=None, help="Lear checkout for full docs verification (optional)")
    ap.add_argument("--width", type=int, default=120)
    ap.add_argument("--height", type=int, default=45)
    ap.add_argument("--dwell", type=float, default=9.0, help="seconds to stay on each scene before pressing Enter")
    args = ap.parse_args(argv)

    py = sys.executable
    src = ["--source", args.source] if args.source else []
    shown_src = f" --source {args.source}" if args.source else ""
    rec = Recorder(args.width, args.height)
    # 1. live mode on the default (Ollama) backend: must stop at the model gate with exit 6
    rec.type_line(f"python3 -m ragformation lear demo live{shown_src}")
    code_live = rec.run([py, "-m", "ragformation", "lear", "demo", "live", *src])
    rec.show_exit(code_live)
    time.sleep(2.0)
    # 2. the director on the offline baseline: real pipeline, navigated with real key presses
    rec.type_line(f"python3 -m ragformation lear --backend baseline demo director{shown_src}")
    keys = [("?", args.dwell - 2.0)] + [("\r", 5.0)] + [("\r", args.dwell)] * 11
    code_dir = rec.run([py, "-m", "ragformation", "lear", "--backend", "baseline", "demo", "director", *src], keys=keys)
    rec.show_exit(code_dir)
    time.sleep(3.0)
    rec.emit("")
    rec.save(Path(args.out), "RAG Formation / Lear Local Agent: lear demo live (exit 6) + director (baseline)")
    print(json.dumps({"cast": args.out, "events": len(rec.events), "seconds": rec.events[-1][0] if rec.events else 0,
                      "liveExit": code_live, "directorExit": code_dir}))
    return 0 if (code_live, code_dir) == (6, 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
