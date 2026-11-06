"""Timeline plan for the walkthrough: voice placement, pause-snapped word timing, subtitles, cues."""
import json
import re
import wave
from pathlib import Path

import numpy as np

FPS = 24
SEG = [
    ("open", "/tmp/vo/n1_open.wav",
     "This is Lear Local. A backend-only RAG agent that answers questions about the Lear backend, built for the take-home brief, where every claim is traced to source, and every number is measured.",
     ["Lear Local", "backend-only", "take-home", "every claim", "every number"]),
    ("brief", "/tmp/vo/n2_brief.wav",
     "The brief has three parts. An accurate knowledge base. A local agent with sessions, memory and guardrails. And measurement, with an evaluation set and three rounds of improvement. Here is how each part is met.",
     ["three parts", "accurate knowledge base", "A local agent", "And measurement", "Here is how"]),
    ("kb", "/tmp/vo/n3_kb.wav",
     "Part one: the knowledge base. The documentation is generated straight from the Lear backend at one pinned commit: four hundred fifty-four source files, eighty-seven API routes, thirty-eight environment variables, and seventy-nine modules. A verifier checks every claim against the source code: three hundred forty out of three hundred forty, with eleven of eleven checks passing. The markdown is then split by heading into three hundred fifty-five chunks, embedded, and stored in a SQLite vector index.",
     ["Part one", "generated straight", "four hundred fifty-four", "eighty-seven", "thirty-eight", "seventy-nine", "A verifier",
      "three hundred forty", "eleven of eleven", "split by heading", "embedded", "SQLite"]),
    ("agent", "/tmp/vo/n4_agent.wav",
     "Part two: the agent. Ask a question in the CLI, and retrieval runs dense vectors and BM25 side by side, fuses them with reciprocal rank fusion, then reranks and diversifies. The best chunks fill an eighteen-hundred-token context budget, and the answer cites its evidence, traced back to exact lines of Lear source. Sessions persist, long histories are compacted, memory is scoped to each user, and guardrails refuse out-of-scope questions, strip invented citations, quarantine injected instructions, and rate-limit abuse.",
     ["Part two", "Ask a question", "dense vectors", "BM25", "reciprocal rank fusion", "reranks", "eighteen-hundred", "cites its evidence",
      "exact lines", "Sessions persist", "compacted", "memory is scoped", "guardrails", "strip invented", "quarantine", "rate-limit"]),
    ("measure", "/tmp/vo/n5_measure.wav",
     "Part three: measurement, where most of the work went. Every request is timed across thirteen lifecycle stages. A load test with one to ten dummy users reports time to first token, inter-token latency, tokens per second, mean, p50, p85 and p99. At ten users the pipeline sustains thirty-two requests per second, and the rate limiter admits ten requests and blocks ten. The evaluation set has eighty questions with golden answers, scored on citations, groundedness, answer relevance, context precision and context recall. Three evaluate-and-improve rounds lifted correctness from forty-five to fifty-five percent, with zero regressions, and the holdout, used exactly once, scored sixty-four percent.",
     ["Part three", "thirteen lifecycle", "A load test", "time to first token", "mean, p50", "At ten users", "admits ten",
      "eighty questions", "scored on citations", "Three evaluate", "forty-five", "zero regressions", "the holdout"]),
    ("honest", "/tmp/vo/n6_honest.wav",
     "And here is what we refuse to fake. Ollama could not be installed in the recording sandbox, so every number you just saw comes from the offline baseline, labelled not qualifying, and the judge scores are deterministic proxies. The local models are configured, the doctor enforces the brief's size limits the moment Ollama runs, and the full qualification run is eight documented steps away.",
     ["refuse to fake", "Ollama could not", "offline baseline", "not qualifying", "deterministic proxies", "models are configured",
      "doctor enforces", "eight documented"]),
    ("outro", "/tmp/vo/n7_outro.wav",
     "Everything is runnable. One command replays the recorded run on any machine, and the code, the evidence and this video are one click away. Lear Local: every claim traced, every number measured, nothing faked.",
     ["Everything is runnable", "One command", "one click away", "Lear Local:", "every claim traced", "nothing faked"]),
]
INTRO, LEAD, TAIL, HOLD = 3.0, 0.7, 0.9, 5.5


def load(path):
    w = wave.open(path)
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return x, w.getframerate()


def envelope(x, sr, win=0.02, hop=0.01):
    n, h = int(sr * win), int(sr * hop)
    frames = 1 + max(0, (len(x) - n) // h)
    idx = np.arange(n)[None, :] + h * np.arange(frames)[:, None]
    return np.sqrt((x[idx] ** 2).mean(axis=1) + 1e-12), hop


def speech_bounds_and_gaps(x, sr):
    env, hop = envelope(x, sr)
    thr = max(env.max() * 0.06, np.percentile(env, 20) * 1.5)
    voiced = env > thr
    on = np.flatnonzero(voiced)
    start, end = on[0] * hop, (on[-1] + 1) * hop
    gaps, run = [], None
    for i, v in enumerate(voiced):
        t = i * hop
        if not v and start < t < end:
            run = t if run is None else run
        elif run is not None:
            if t - run >= 0.12:
                gaps.append((run, t))
            run = None
    return start, end, gaps


PAUSE = {".": 9.0, ":": 7.0, "?": 9.0, "!": 9.0, ",": 3.5, ";": 5.0}


def char_times(text, s0, s1, gaps):
    weights = np.array([PAUSE.get(ch, 1.0) for ch in text], dtype=float)
    cum = np.concatenate([[0.0], np.cumsum(weights)])
    est = s0 + (s1 - s0) * cum / cum[-1]          # time at each char boundary
    anchors = [(0, s0), (len(text), s1)]
    used = set()
    for i, ch in enumerate(text):
        if ch in PAUSE and PAUSE[ch] >= 3.5 and i + 1 < len(text):
            window = 1.1 if PAUSE[ch] >= 7 else 0.6
            cands = [(abs((g0 + g1) / 2 - est[i + 1]), k, g0, g1) for k, (g0, g1) in enumerate(gaps) if k not in used]
            cands = [c for c in cands if c[0] <= window]
            if cands:
                _, k, g0, g1 = min(cands)
                used.add(k)
                anchors.append((i + 1, (g0 + g1) / 2))
    anchors.sort()
    clean = [anchors[0]]
    for a in anchors[1:]:
        if a[1] > clean[-1][1] and a[0] > clean[-1][0]:
            clean.append(a)
    xs, ys = zip(*clean)
    # piecewise-linear by weighted position between anchors
    out = np.interp(cum, [cum[x] for x in xs], ys)
    return out


def captions(text, times, t_off):
    parts, start = [], 0
    for m in re.finditer(r"[.:?!](\s|$)|,\s", text):
        end = m.end()
        seg = text[start:end].strip()
        if len(seg) < 28 and m.group(0).startswith(","):
            continue
        if seg:
            parts.append((start, end, seg))
        start = end
    if text[start:].strip():
        parts.append((start, len(text), text[start:].strip()))
    merged = []
    for p in parts:
        if merged and len(merged[-1][2]) + len(p[2]) < 64 and not merged[-1][2].endswith((".", ":")):
            a = merged.pop()
            merged.append((a[0], p[1], a[2] + " " + p[2]))
        else:
            merged.append(p)
    return [{"t0": round(t_off + times[a], 3), "t1": round(t_off + times[b], 3), "text": s} for a, b, s in merged]


def main():
    t = INTRO
    scenes, subs = [], []
    for i, (name, path, text, keys) in enumerate(SEG):
        x, sr = load(path)
        dur = len(x) / sr
        s0, s1, gaps = speech_bounds_and_gaps(x, sr)
        times = char_times(text, s0, s1, gaps)
        scene_t0 = t
        voice_t0 = t + LEAD
        cues = {}
        for k in keys:
            pos = text.find(k)
            assert pos >= 0, (name, k)
            cues[k] = round(voice_t0 + times[pos], 3)
        scene_t1 = voice_t0 + dur + (TAIL if i < len(SEG) - 1 else HOLD)
        scenes.append({"index": i, "name": name, "t0": round(scene_t0, 3), "t1": round(scene_t1, 3), "voice": path,
                       "voiceT0": round(voice_t0, 3), "voiceDur": round(dur, 3), "speech": [round(voice_t0 + s0, 3), round(voice_t0 + s1, 3)],
                       "cues": cues, "gaps": len(gaps)})
        subs.extend(captions(text, times, voice_t0))
        t = scene_t1
    plan = {"fps": FPS, "duration": round(t, 3), "frames": int(round(t * FPS)), "intro": INTRO, "scenes": scenes, "subtitles": subs}
    Path("/tmp/vfxbuild/plan.json").write_text(json.dumps(plan, indent=1))
    print("duration", plan["duration"], "frames", plan["frames"], "subtitles", len(subs))
    for s in scenes:
        print(s["name"], s["t0"], "->", s["t1"], "voice", s["voiceT0"], "+", s["voiceDur"], "gaps", s["gaps"], "| first cues:", list(s["cues"].items())[:3])


if __name__ == "__main__":
    main()
