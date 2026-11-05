"""Soundtrack for the walkthrough: voiceover + synthesized music bed + sound effects on the visual cue timeline.

Everything is generated here with numpy/scipy (no sample libraries). Output: 48 kHz stereo 16-bit WAV.
"""
import json
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, resample_poly, sosfilt

SR = 48000
plan = json.loads(Path("/tmp/vfxbuild/plan.json").read_text())
D = json.loads(Path("/tmp/vfxdata/data.json").read_text())
T = plan["duration"] + 0.6
N = int(T * SR)
rng = np.random.default_rng(20261106)
C = {s["name"]: s for s in plan["scenes"]}
cue = lambda s, k: C[s]["cues"][k]
sec = lambda t: int(round(t * SR))


def filt(x, kind, f, order=2):
    nyq = SR / 2 - 200
    if kind == "band":
        lo, hi = max(20, f[0]), min(nyq, f[1])
        sos = butter(order, [lo, hi], btype="band", fs=SR, output="sos")
    else:
        sos = butter(order, min(f, nyq), btype=kind, fs=SR, output="sos")
    return sosfilt(sos, x)


def norm(y):
    m = np.abs(y).max()
    return y / m if m > 0 else y


# ------------------------------------------------------------------ sound effects
def whoosh(dur=0.9):
    n = sec(dur); x = rng.standard_normal(n); t = np.linspace(0, 1, n); out = np.zeros(n)
    for fc, peak in ((320, 0.28), (850, 0.42), (2100, 0.54), (5000, 0.64)):
        out += filt(x, "band", (fc * 0.7, fc * 1.45)) * np.exp(-((t - peak) ** 2) / (2 * 0.13 ** 2))
    return norm(out * np.sin(np.pi * t) ** 0.8)


def impact():
    n = sec(1.6); tt = np.arange(n) / SR
    f = 32 + 95 * np.exp(-tt / 0.07); sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt / 0.45)
    crack = filt(rng.standard_normal(n), "high", 1800) * np.exp(-tt / 0.03) * 0.5
    tail = filt(rng.standard_normal(n), "low", 900) * np.exp(-tt / 0.6) * 0.12
    return norm(sub + crack + tail)


def riser(dur=2.0):
    n = sec(dur); tt = np.linspace(0, 1, n)
    noise = filt(rng.standard_normal(n), "high", 400) * tt ** 2.2
    f = 180 + 1600 * tt ** 2; ph = 2 * np.pi * np.cumsum(f) / SR
    tone = (np.sin(ph) + 0.4 * np.sin(2 * ph)) * tt ** 2 * 0.35
    return norm((noise * 0.6 + tone) * np.minimum(1, (1 - tt) * 40 + 0.02))


def tick(freq=3200, dur=0.012):
    n = sec(dur + 0.03); tt = np.arange(n) / SR
    return np.sin(2 * np.pi * freq * tt) * np.exp(-tt / (dur / 3))


def blip(f0=900, f1=1500, dur=0.07):
    n = sec(dur); tt = np.arange(n) / SR
    ph = 2 * np.pi * np.cumsum(np.linspace(f0, f1, n)) / SR
    return np.sin(ph) * np.exp(-tt / (dur / 3)) * np.minimum(1, tt * SR / 60)


def chime(f=880, dur=1.2):
    n = sec(dur); tt = np.arange(n) / SR
    y = sum(a * np.sin(2 * np.pi * f * r * tt) * np.exp(-tt / (dur * d)) for r, a, d in ((1, 1, 0.35), (2.76, 0.45, 0.18), (5.4, 0.22, 0.09), (2.0, 0.3, 0.25)))
    return norm(y * np.minimum(1, tt * SR / 40))


def click():
    n = sec(0.025); tt = np.arange(n) / SR
    y = filt(rng.standard_normal(n), "band", (1800, 6500)) * np.exp(-tt / 0.004) + np.sin(2 * np.pi * 180 * tt) * np.exp(-tt / 0.006) * 0.4
    return norm(y)


def glitch(dur=0.35):
    n = sec(dur); y = np.zeros(n); i = 0
    while i < n:
        seg = int(SR * rng.uniform(0.01, 0.04)); m = min(seg, n - i); tt = np.arange(m) / SR
        f = rng.choice([110, 220, 440, 880, 1760, 3520]) * rng.uniform(0.9, 1.1)
        y[i:i + m] = rng.standard_normal(m) * 0.6 if rng.random() < 0.3 else np.sign(np.sin(2 * np.pi * f * tt)) * rng.uniform(0.3, 1.0)
        i += seg
    return np.round(y * 6) / 6 * np.exp(-np.linspace(0, 3, n))


def shimmer(dur=1.5, density=60, lo=2500, hi=7000):
    n = sec(dur); y = np.zeros(n)
    for _ in range(int(density * dur)):
        gl = int(SR * rng.uniform(0.02, 0.06)); st = int(rng.uniform(0, max(1, n - gl))); f = rng.uniform(lo, hi)
        tt = np.arange(gl) / SR; y[st:st + gl] += np.sin(2 * np.pi * f * tt) * np.hanning(gl) * rng.uniform(0.2, 1)
    return norm(y * np.sin(np.pi * np.linspace(0, 1, n)) ** 0.5)


def buzz(dur=0.16, f=110):
    n = sec(dur); tt = np.arange(n) / SR
    y = np.sign(np.sin(2 * np.pi * f * tt)) * 0.6 + np.sin(2 * np.pi * f * 2 * tt) * 0.3
    return norm(filt(y, "low", 1600) * np.exp(-tt / 0.08))


def success():
    out = np.zeros(sec(1.5))
    for k, f in enumerate((440, 554.37, 659.25, 880)):
        c = chime(f, 0.9); st = sec(0.08 * k); out[st:st + len(c)] += c[:len(out) - st] * 0.6
    return norm(out)


def low_hit():
    n = sec(2.0); tt = np.arange(n) / SR
    f = 40 + 40 * np.exp(-tt / 0.1)
    return norm(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt / 0.7) + filt(rng.standard_normal(n), "low", 300) * np.exp(-tt / 0.4) * 0.4)


def thud():
    n = sec(0.4); tt = np.arange(n) / SR
    f = 60 + 120 * np.exp(-tt / 0.03)
    return norm(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt / 0.09) + filt(rng.standard_normal(n), "band", (1500, 4000)) * np.exp(-tt / 0.01) * 0.4)


GEN = {"whoosh": whoosh, "impact": impact, "riser": riser, "tick": tick, "blip": blip, "chime": chime, "click": click, "glitch": glitch,
       "shimmer": shimmer, "buzz": buzz, "success": success, "low_hit": low_hit, "thud": thud}
EV = []


def ev(t, kind, gain=1.0, pan=0.0, **kw):
    EV.append((float(t), kind, gain, pan, kw))


def ev_end(t_end, kind, gain, dur, pan=0.0):
    ev(t_end - dur, kind, gain, pan, dur=dur)


# ------------------------------------------------------------------ cue sheet (mirrors render.js timings)
ev(0.15, "shimmer", 0.22, dur=2.5); ev_end(2.88, "riser", 0.45, 2.2); ev(2.84, "impact", 0.85); ev(2.8, "whoosh", 0.35, dur=0.8)
t0 = C["open"]["t0"]
for i, ch in enumerate("LEAR LOCAL"):
    if ch != " ":
        ev(t0 + 0.05 + i * 0.06, "blip", 0.11, -0.6 + i * 0.13, f0=700 + i * 60, f1=1100 + i * 80)
ev(t0 + 1.25, "whoosh", 0.22, dur=1.0); ev(cue("open", "backend-only") - 0.35, "shimmer", 0.10, dur=0.9)
ev(cue("open", "take-home") - 0.2, "blip", 0.18, f0=1200, f1=1600)
ev(cue("open", "every claim") - 0.15, "chime", 0.2, f=880); ev(cue("open", "every number") - 0.15, "chime", 0.2, f=1108.73); ev(cue("open", "every number") + 1.0, "chime", 0.2, f=1318.51)
for s in plan["scenes"][1:]:
    ev(s["t0"] - 0.45, "whoosh", 0.42, -0.3, dur=0.9)
    if s["name"] in ("kb", "agent", "measure", "outro"):
        ev(s["t0"] + 0.05, "impact", 0.32)
    if s["name"] == "honest":
        ev(s["t0"] + 0.05, "low_hit", 0.45)
# brief
bt0 = C["brief"]["t0"]; ev(bt0 + 0.15, "thud", 0.2)
for k, (key, n) in enumerate((("accurate knowledge base", 4), ("A local agent", 6), ("And measurement", 6))):
    ts = cue("brief", key) - 0.3
    ev(ts, "whoosh", 0.18, -0.5 + k * 0.5, dur=0.6); ev(ts + 0.1, "thud", 0.22, -0.5 + k * 0.5)
    for j in range(n):
        ev(ts + 0.35 + j * 0.2, "tick", 0.10, -0.5 + k * 0.5, freq=2400 + j * 120)
hh = cue("brief", "Here is how"); ev_end(hh - 0.1, "riser", 0.22, 0.9); ev(hh - 0.1, "impact", 0.28)
for k in range(3):
    ev(hh + 0.2 + k * 0.3, "chime", 0.12, -0.5 + k * 0.5, f=987.77 + k * 120)
# knowledge base
S = "kb"; ev(C[S]["t0"] + 0.1, "thud", 0.2); ev(cue(S, "generated straight") - 0.3, "blip", 0.16)
t454 = cue(S, "four hundred fifty-four") - 0.7; ev(t454, "shimmer", 0.24, -0.5, dur=1.8)
for i in range(18):
    ev(t454 + 1.6 * (1 - (1 - i / 18) ** 2), "tick", 0.06, -0.5, freq=3000)
ev(cue(S, "eighty-seven") - 0.2, "blip", 0.2, f0=800, f1=1400); ev(cue(S, "eighty-seven"), "shimmer", 0.12, dur=3.8, lo=1800, hi=4200)
ev(cue(S, "thirty-eight") - 0.15, "blip", 0.18, f0=600, f1=1000); ev(cue(S, "seventy-nine") - 0.15, "blip", 0.18, f0=700, f1=1200)
ev(cue(S, "seventy-nine"), "shimmer", 0.1, dur=1.8)
ev(cue(S, "A verifier") - 0.2, "blip", 0.18, 0.5, f0=1000, f1=1700)
tv, te = cue(S, "A verifier") + 0.4, cue(S, "eleven of eleven")
for i in range(11):
    ev(tv + i * ((te - tv) / 11), "chime", 0.075, 0.5, f=1318.51 + i * 40, dur=0.6)
ev(cue(S, "three hundred forty") - 0.9, "shimmer", 0.2, 0.5, dur=2.4, lo=3000, hi=8000)
ev(te + 0.1, "success", 0.3, 0.4)
ev(cue(S, "split by heading") - 0.5, "whoosh", 0.3, dur=0.9); ev(cue(S, "split by heading") - 0.1, "shimmer", 0.22, dur=1.8, lo=1500, hi=5000)
ev(cue(S, "embedded") - 0.2, "glitch", 0.12); ev(cue(S, "embedded"), "shimmer", 0.12, dur=1.2, lo=4000, hi=9000)
for i in range(13):
    ev(cue(S, "embedded") + i * 0.05, "blip", 0.05, -0.6 + i * 0.1, f0=1500 + i * 50, f1=2200 + i * 60, dur=0.05)
ev(cue(S, "SQLite") - 0.4, "whoosh", 0.3, 0.6, dur=1.4); ev(cue(S, "SQLite") + 1.0, "impact", 0.3, 0.6)
# agent
S = "agent"; ev(C[S]["t0"] + 0.1, "thud", 0.2)
cmd = f'$ python3 -m ragformation lear ask "{D["S06"]["question"]}"'
tq = cue(S, "Ask a question") - 0.2
for i, ch in enumerate(cmd):
    if ch != " ":
        ev(tq + i * 0.03, "click", 0.075, rng.uniform(-0.2, 0.2))
nodes_t = [cue(S, "dense vectors"), cue(S, "BM25"), cue(S, "reciprocal rank fusion"), cue(S, "reranks"), cue(S, "reranks") + 0.45, cue(S, "reranks") + 0.9]
for i, tt in enumerate(nodes_t):
    ev(tt - 0.2, "blip", 0.18, -0.7 + i * 0.28, f0=600 + i * 120, f1=1000 + i * 160)
ev(cue(S, "dense vectors"), "shimmer", 0.07, dur=6.0, lo=2000, hi=5000)
for i in range(10):
    ev(cue(S, "reranks") + 1.1 + i * 0.12, "tick", 0.09, -0.6, freq=2200 + i * 90)
for i in range(2):
    ev(cue(S, "eighteen-hundred") + 0.4 + i * 0.2, "buzz", 0.06, -0.6, f=98)
ev_end(cue(S, "eighteen-hundred") + 1.2, "riser", 0.14, 1.3, 0.5)
ev(cue(S, "cites its evidence") - 0.3, "whoosh", 0.15, 0.5, dur=0.6); ev(cue(S, "cites its evidence") - 0.1, "chime", 0.18, 0.4, f=1046.5)
for i in range(4):
    ev(cue(S, "exact lines") - 0.4 + i * 0.35, "blip", 0.18, 0.2 + i * 0.15, f0=900 + i * 200, f1=1400 + i * 250)
ev(cue(S, "Sessions persist") - 0.5, "whoosh", 0.28, dur=0.8)
for i in range(len(D["S07"]["turns"])):
    ev(cue(S, "Sessions persist") - 0.2 + i * 0.22, "blip", 0.14, -0.7 + i * 0.12, f0=800, f1=1300)
for i in range(3):
    ev(cue(S, "compacted") - 0.2 + i * 0.3, "blip", 0.13, f0=1300, f1=700, dur=0.12)
ev(cue(S, "memory is scoped") - 0.2, "buzz", 0.1, 0.6, f=123); ev(cue(S, "memory is scoped") + 0.1, "chime", 0.14, 0.6, f=1174.66); ev(cue(S, "memory is scoped") + 0.4, "blip", 0.1, 0.6)
tg, tge = cue(S, "guardrails") - 0.2, cue(S, "rate-limit") + 0.2
for i, st in enumerate(D["S08_steps"]):
    tt = tg + i * ((tge - tg) / len(D["S08_steps"]))
    ev(tt, "thud", 0.12, -0.4 if i < 5 else 0.4)
    ev(tt + 0.05, "chime" if st["verdict"] == "MATCH" else "buzz", 0.09 if st["verdict"] == "MATCH" else 0.07, -0.4 if i < 5 else 0.4, **({"f": 1567.98, "dur": 0.5} if st["verdict"] == "MATCH" else {"f": 110}))
# measure
S = "measure"; ev(C[S]["t0"] + 0.1, "thud", 0.2)
tl = cue(S, "thirteen lifecycle") - 0.4
for i in range(13):
    ev(tl + i * 0.12, "tick", 0.09, -0.6 + i * 0.1, freq=1800 + i * 150)
ev(cue(S, "A load test") - 0.3, "whoosh", 0.25, dur=0.8)
for li, lv in enumerate(D["bench"]):
    for u in range(lv["users"]):
        ev(cue(S, "A load test") + li * 0.35 + u * 0.05, "blip", 0.05, -0.8 + u * 0.05, f0=1400, f1=1900, dur=0.04)
    for q in range(lv["requests"]):
        ev(cue(S, "A load test") + 0.6 + li * 0.35 + q * 0.07 + 0.85, "tick", 0.035, -0.2, freq=2600)
for li in range(4):
    for ci in range(8):
        ev(cue(S, "time to first token") - 0.2 + li * 0.25 + ci * 0.08, "tick", 0.035, 0.3 + ci * 0.05, freq=3400)
ev(cue(S, "mean, p50") + 0.3, "thud", 0.28, 0.6)
for i in range(4):
    ev(cue(S, "At ten users") - 0.3 + i * 0.15, "blip", 0.14, -0.7, f0=500 + i * 150, f1=900 + i * 200)
ev(cue(S, "At ten users") + 0.4, "chime", 0.2, -0.6, f=1318.51)
for i in range(D["benchRL"][0]["requests"]):
    t_i = cue(S, "admits ten") - 0.4 + i * 0.09
    if i % 2 == 0:
        ev(t_i + 0.9, "blip", 0.07, 0.7, f0=1600, f1=2100, dur=0.05)
    else:
        ev(t_i + 0.45, "buzz", 0.06, 0.2, f=92, dur=0.1)
ev(cue(S, "eighty questions") - 0.4, "whoosh", 0.25, dur=0.8); ev(cue(S, "eighty questions") - 0.2, "shimmer", 0.16, -0.5, dur=1.6)
for i in range(5):
    ev(cue(S, "scored on citations") + i * 0.35, "blip", 0.12, 0.4, f0=600 + i * 100, f1=1100 + i * 140)
ev(cue(S, "scored on citations") + 2.0, "thud", 0.25, 0.6)
for ri in range(4):
    ev(cue(S, "Three evaluate") - 0.2 + ri * 0.45, "shimmer", 0.1, -0.7 + ri * 0.3, dur=0.5, lo=2000, hi=4500)
for i in range(4):
    ev(cue(S, "forty-five") - 0.2 + i * 0.4, "blip", 0.17, 0.3 + i * 0.1, f0=700 + i * 150, f1=1200 + i * 200)
ev(cue(S, "forty-five") + 1.6, "blip", 0.12, 0.5)
ev(cue(S, "zero regressions") - 0.1, "success", 0.32); ev(cue(S, "the holdout") + 0.2, "impact", 0.25); ev(cue(S, "the holdout") + 0.25, "chime", 0.2, f=1760)
# honest
S = "honest"; ev(cue(S, "refuse to fake") - 0.3, "glitch", 0.22, dur=0.5)
ev(cue(S, "Ollama could not") - 0.1, "low_hit", 0.45); ev(cue(S, "Ollama could not") - 0.1, "glitch", 0.2); ev(cue(S, "Ollama could not"), "thud", 0.35, 0.6)
for i in range(5):
    ev(cue(S, "offline baseline") - 0.2 + i * 0.22, "tick", 0.08, -0.5, freq=2600)
    ev(cue(S, "not qualifying") - 0.2 + i * 0.22, "buzz", 0.045, 0.5, f=104, dur=0.08)
ev(cue(S, "deterministic proxies") - 0.2, "blip", 0.14, f0=900, f1=700, dur=0.1)
ev(cue(S, "models are configured") - 0.5, "whoosh", 0.22, dur=0.8)
for i in range(3):
    ev(cue(S, "models are configured") - 0.1 + i * 0.25, "thud", 0.14, -0.6 + i * 0.6)
ev(cue(S, "doctor enforces") - 0.1, "blip", 0.14, f0=1000, f1=1500)
for i in range(8):
    ev(cue(S, "eight documented") - 0.6 + i * 0.16, "tick", 0.08, -0.4 if i < 4 else 0.4, freq=2000 + i * 100)
# outro
S = "outro"; ev(cue(S, "Everything is runnable") - 0.2, "whoosh", 0.22, dur=0.7)
ocmd = "$ python3 -m ragformation lear demo replay"
for i, ch in enumerate(ocmd):
    if ch != " ":
        ev(cue(S, "One command") + i * 0.035, "click", 0.075, rng.uniform(-0.2, 0.2))
ev(cue(S, "One command") + len(ocmd) * 0.035 + 0.3, "chime", 0.18, f=1046.5)
pitch = {"PASS": 2600, "PARTIAL": 1900, "BLOCKED": 1200}
for i, r in enumerate(D["requirements"]):
    ev(cue(S, "One command") + 2.2 + i * 0.09, "tick", 0.06, -0.7 + (i % 5) * 0.3, freq=pitch.get(r[2], 1800))
ev(cue(S, "One command") + 2.2 + 25 * 0.09 + 0.2, "thud", 0.28)
for i in range(3):
    ev(cue(S, "one click away") - 0.2 + i * 0.25, "blip", 0.15, 0.6, f0=900 + i * 200, f1=1500 + i * 200)
ev_end(cue(S, "Lear Local:") - 0.1, "riser", 0.38, 1.6); ev(cue(S, "Lear Local:") - 0.1, "impact", 0.75); ev(cue(S, "Lear Local:") - 0.1, "shimmer", 0.22, dur=2.8)
ev(cue(S, "every claim traced") - 0.1, "chime", 0.22, -0.4, f=880); ev(cue(S, "every claim traced") + 0.8, "chime", 0.22, 0.0, f=1108.73); ev(cue(S, "nothing faked") - 0.1, "chime", 0.24, 0.4, f=1318.51)
ev(cue(S, "nothing faked") + 1.2, "blip", 0.12, f0=800, f1=1200)

# ------------------------------------------------------------------ render sfx bus
sfx = np.zeros((N, 2))


def add(buf, t, sig, gain, pan):
    i = sec(t)
    if i >= N or i + len(sig) <= 0:
        return
    if i < 0:
        sig, i = sig[-i:], 0
    sig = sig[: N - i]
    l, r = np.cos((pan + 1) * np.pi / 4) * 1.4142, np.sin((pan + 1) * np.pi / 4) * 1.4142
    buf[i:i + len(sig), 0] += sig * gain * l
    buf[i:i + len(sig), 1] += sig * gain * r


for t, kind, gain, pan, kw in EV:
    add(sfx, t, GEN[kind](**kw), gain, pan)

# ------------------------------------------------------------------ voice bus
voice = np.zeros(N)
for s in plan["scenes"]:
    w = wave.open(s["voice"]); x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0
    x = resample_poly(x, 160, 147) if w.getframerate() == 44100 else x
    x = filt(x, "high", 70)
    i = sec(s["voiceT0"]); voice[i:i + len(x)] += x[: N - i]
voice = voice / np.abs(voice).max() * 0.70

# ------------------------------------------------------------------ music bus
BPM = 96.0; BEAT = 60.0 / BPM; BAR = 4 * BEAT
mtof = lambda m: 440.0 * 2 ** ((m - 69) / 12)
PROG = [(33, [57, 60, 64, 69]), (29, [53, 57, 60, 65]), (36, [55, 60, 64, 67]), (31, [55, 59, 62, 67])]
names = [s["name"] for s in plan["scenes"]]
LAYER = {  # pad, sub, kick(0 none,1 half,2 full), hats, arp, brightness
    "open": (0.85, 0.5, 0, 0.0, 0.0, 0.55), "brief": (0.8, 0.6, 0, 0.3, 0.0, 0.6), "kb": (0.7, 0.7, 1, 0.45, 0.2, 0.7),
    "agent": (0.7, 0.75, 2, 0.5, 0.35, 0.75), "measure": (0.7, 0.8, 2, 0.6, 0.5, 0.85), "honest": (0.6, 0.35, 0, 0.0, 0.0, 0.3),
    "outro": (0.85, 0.7, 2, 0.5, 0.4, 0.8)}
tgrid = np.arange(N) / SR


def layer_curve(idx):
    pts_t, pts_v = [0.0, plan["intro"] - 0.4], [0.35 if idx == 0 else 0.0, 0.35 if idx == 0 else 0.0]
    for s in plan["scenes"]:
        v = LAYER[s["name"]][idx]
        if idx == 2:
            v = 1.0 if v else 0.0
        pts_t += [s["t0"] + 0.6, s["t1"] - 0.4]; pts_v += [v, v]
    return np.interp(tgrid, pts_t, pts_v)


pad_g, sub_g, kick_on, hat_g, arp_g, bright = (layer_curve(i) for i in range(6))
outro_cut = cue("outro", "Lear Local:") - 0.1
fade = np.clip((T - 0.6 - tgrid) / 4.5, 0, 1)

pad = np.zeros(N); sub = np.zeros(N)
nchords = int(np.ceil(T / BAR)) + 1
for c in range(nchords):
    root, notes = PROG[c % 4]
    a, b = c * BAR - 0.25, (c + 1) * BAR + 0.25
    i0, i1 = max(0, sec(a)), min(N, sec(b))
    if i0 >= i1:
        continue
    tt = tgrid[i0:i1]; seg = np.zeros(i1 - i0); u = (tt - a) / (b - a)
    env = np.clip(np.minimum(u / 0.12, (1 - u) / 0.12), 0, 1)
    br = bright[i0:i1]
    for m in notes:
        f = mtof(m)
        for det in (-0.0035, 0.0035):
            ph0 = rng.uniform(0, 6.283)
            for k in range(1, 8):
                fc = 400 + 2600 * br
                amp = (1.0 / k ** 1.15) / (1 + (f * k / fc) ** 2)
                seg += amp * np.sin(2 * np.pi * f * (1 + det) * k * tt + ph0 * k)
    pad[i0:i1] += seg * env
    sub[i0:i1] += np.sin(2 * np.pi * mtof(root) * tt) * env + 0.25 * np.sin(2 * np.pi * mtof(root) * 2 * tt) * env
pad = filt(pad, "low", 5000); pad = pad / np.abs(pad).max()
sub = sub / np.abs(sub).max()

drums = np.zeros(N); arp = np.zeros(N); pump = np.ones(N)
kick_n = sec(0.45); kt = np.arange(kick_n) / SR
KICK = np.sin(2 * np.pi * np.cumsum(45 + 115 * np.exp(-kt / 0.04)) / SR) * np.exp(-kt / 0.16)
KICK = norm(KICK + filt(rng.standard_normal(kick_n), "band", (2000, 6000)) * np.exp(-kt / 0.004) * 0.25)
nbeats = int(T / BEAT)
for bi in range(nbeats):
    bt = bi * BEAT
    if bt >= outro_cut - 0.05 and bt < outro_cut + 3.0:
        continue
    i = sec(bt); on = kick_on[min(i, N - 1)]
    mode = None
    for s in plan["scenes"]:
        if s["t0"] + 0.4 <= bt < s["t1"] - 0.2:
            mode = LAYER[s["name"]][2]
    if on > 0.5 and mode and (mode == 2 or bi % 2 == 0):
        seg = KICK[: N - i]; drums[i:i + len(seg)] += seg * 0.9
        pn = min(N - i, sec(0.35)); pump[i:i + pn] = np.minimum(pump[i:i + pn], 1 - 0.45 * np.exp(-np.arange(pn) / (0.11 * SR)))
    hb = bt + BEAT / 2; hi_ = sec(hb); hg = hat_g[min(hi_, N - 1)]
    if hg > 0.02 and hi_ < N:
        hn = sec(0.05); h = filt(rng.standard_normal(hn), "high", 7000) * np.exp(-np.arange(hn) / (0.012 * SR))
        drums[hi_:hi_ + hn] += h[: N - hi_] * 0.35 * hg
    ag = arp_g[min(i, N - 1)]
    if ag > 0.02:
        root, notes = PROG[int(bt // BAR) % 4]
        for q in range(4):
            qt = bt + q * BEAT / 4; qi = sec(qt)
            if qi >= N:
                break
            m = notes[(bi * 4 + q) % 4] + 12; f = mtof(m); an = sec(0.22); at = np.arange(an) / SR
            tone = (np.sin(2 * np.pi * f * at) + 0.3 * np.sin(2 * np.pi * 3 * f * at)) * np.exp(-at / 0.07)
            arp[qi:qi + an] += tone[: N - qi] * 0.28 * ag

music = (pad * pad_g * 0.55 + sub * sub_g * 0.45) * pump + arp * pump + drums * 0.6
drone_on = np.interp(tgrid, [C["honest"]["t0"], C["honest"]["t0"] + 1.0, C["honest"]["t1"] - 1.0, C["honest"]["t1"]], [0, 1, 1, 0])
music += np.sin(2 * np.pi * 55 * tgrid) * 0.18 * drone_on + filt(rng.standard_normal(N), "low", 200) * 0.05 * drone_on
music = music / np.abs(music).max() * fade

# duck music under the voice (attack ~15 ms, release ~280 ms)
env = np.abs(voice)
win = sec(0.015); env = np.convolve(env, np.ones(win) / win, mode="same")
rel = np.exp(-1.0 / (0.28 * SR)); out = np.empty_like(env); acc = 0.0
step = 64
for j in range(0, N, step):  # coarse envelope follower, then upsample
    m = env[j:j + step].max()
    acc = m if m > acc else acc * (rel ** step)
    out[j:j + step] = acc
duck = 1 - 0.76 * np.clip(out / 0.10, 0, 1)
music_st = np.stack([music * duck * 0.38, music * duck * 0.38], axis=1)
# widen pad slightly
music_st[:, 0] = music_st[:, 0] * 0.98 + np.roll(music_st[:, 0], sec(0.008)) * 0.02
sfx_duck = (1 - 0.35 * np.clip(out / 0.12, 0, 1))[:, None]
mix = music_st + sfx * 0.55 * sfx_duck + voice[:, None] * 1.0
pre = mix
mix = np.tanh(1.15 * mix) / np.tanh(1.15)
scale = 0.89 / np.abs(mix).max(); mix = mix * scale
_r = lambda a: 20 * np.log10(np.sqrt(np.mean(a ** 2)) + 1e-12)
stem = {}
for sc in plan["scenes"]:
    a, b = sec(sc["voiceT0"]) + SR, sec(sc["voiceT0"] + sc["voiceDur"]) - SR // 2
    vv = voice[a:b] * scale; bed = (music_st[a:b, 0] + sfx[a:b, 0] * 0.55 * sfx_duck[a:b, 0]) * scale
    mm = music_st[a:b, 0] * scale; ss = sfx[a:b, 0] * 0.55 * sfx_duck[a:b, 0] * scale
    sb = lambda z: filt(z, "band", (300, 4000))
    stem[sc["name"]] = {"v/music": round(_r(vv) - _r(mm), 1), "v/sfx": round(_r(vv) - _r(ss), 1), "speechband v/music": round(_r(sb(vv)) - _r(sb(mm)), 1), "speechband v/sfx": round(_r(sb(vv)) - _r(sb(ss)), 1)}
print(json.dumps(stem))
pcm = (mix * 32767).astype(np.int16)
with wave.open("/tmp/vfxbuild/soundtrack.wav", "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
rms = lambda x: 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)
print(json.dumps({"seconds": round(N / SR, 2), "events": len(EV), "kinds": {k: sum(1 for e in EV if e[1] == k) for k in GEN},
                  "rms_db": round(rms(mix), 1), "voice_rms_db": round(rms(voice[voice != 0]), 1)}))
