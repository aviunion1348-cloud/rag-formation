'use strict';
// Lear Local walkthrough renderer: 1920x1080 design space, rendered at SCALE (2 = native 3840x2160).
// Every visual element is registered on one timeline (ANIMS) so the animation count is auditable.
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { createCanvas, GlobalFonts } = require('/tmp/vfxnode/node_modules/@napi-rs/canvas');

const plan = JSON.parse(fs.readFileSync('/tmp/vfxbuild/plan.json', 'utf8'));
const D = JSON.parse(fs.readFileSync('/tmp/vfxdata/data.json', 'utf8'));
const SCALE = Number(process.env.SCALE || 2);
const W = 1920, H = 1080, FPS = plan.fps, TOTAL = plan.duration;
const FONTS = '/tmp/fonts/';
[['BebasNeue.ttf', 'Bebas'], ['Inter-Regular.ttf', 'InterR'], ['Inter-SemiBold.ttf', 'InterSB'], ['Inter-Bold.ttf', 'InterB'],
 ['Inter-ExtraBold.ttf', 'InterXB'], ['Inter-Black.ttf', 'InterBlk'], ['JBMono-Regular.ttf', 'Mono'], ['JBMono-Bold.ttf', 'MonoB'],
 ['JBMono-ExtraBold.ttf', 'MonoXB']].forEach(([f, n]) => GlobalFonts.registerFromPath(FONTS + f, n));

// ---------------------------------------------------------------- palette + math
const BG = '#04050b', CYAN = '#22E4FF', VIOLET = '#8B5CFF', MAGENTA = '#FF3D9A', AMBER = '#FFB547',
  GREEN = '#2EE6A6', RED = '#FF4D6D', WHITE = '#F2F6FF', MUTED = '#8A94B8', DIM = '#2A3150', BLUE = '#4D8DFF';
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const lerp = (a, b, t) => a + (b - a) * t;
const E = {
  lin: t => t, outCubic: t => 1 - Math.pow(1 - t, 3), inOutCubic: t => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  outBack: t => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
  outExpo: t => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t)), outQuart: t => 1 - Math.pow(1 - t, 4), inCubic: t => t * t * t,
};
function hex2rgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function rgba(h, a) { const [r, g2, b] = hex2rgb(h); return `rgba(${r},${g2},${b},${clamp(a)})`; }
function mixHex(a, b, t) { const A = hex2rgb(a), B = hex2rgb(b); const c = A.map((v, i) => Math.round(lerp(v, B[i], clamp(t)))); return '#' + c.map(v => v.toString(16).padStart(2, '0')).join(''); }
function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const fmt = n => Math.round(n).toLocaleString('en-US');

// ---------------------------------------------------------------- timeline registry
const ANIMS = [];
const DECOR = new Set(['particle-converge', 'ring-segment', 'flash', 'flow-particle', 'burst-particle', 'orbit-ring', 'title-sweep', 'scene-transition']);
const TEXT = new Set(['title-letter', 'subtitle-word', 'chip', 'tagline-chip', 'heading', 'banner', 'subtitle', 'panel', 'stamp', 'typed-char', 'callout']);
function reg(scene, type, t0, dur, src, extra) {
  const a = Object.assign({ id: ANIMS.length, scene, type, t0: +t0.toFixed(3), dur: +Math.max(0.05, dur).toFixed(3), src: src || null }, extra || {});
  a.kind = DECOR.has(type) ? 'decor' : (TEXT.has(type) ? 'text' : 'data');
  ANIMS.push(a); return a;
}
const pr = (a, t, ease) => { const p = clamp((t - a.t0) / a.dur); return ease ? ease(p) : p; };
const SC = {}; plan.scenes.forEach(s => { SC[s.name] = s; });
const cue = (s, k) => { const v = SC[s].cues[k]; if (v === undefined) throw new Error(`missing cue ${s}/${k}`); return v; };

// ---------------------------------------------------------------- canvas + helpers
const canvas = createCanvas(W * SCALE, H * SCALE);
const g = canvas.getContext('2d');
let SA = 1;
const alpha = a => { g.globalAlpha = clamp(SA * a); };
function txt(s, x, y, f, color, o = {}) {
  g.save(); alpha(o.a == null ? 1 : o.a); g.font = f; g.fillStyle = color; g.textAlign = o.align || 'left'; g.textBaseline = o.base || 'alphabetic';
  if (o.glow) { g.shadowColor = o.glow; g.shadowBlur = (o.blur || 18) * SCALE; }
  g.fillText(s, x, y); g.restore();
}
function rr(x, y, w, h, r) { r = Math.min(r, w / 2, h / 2); g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
function panel(x, y, w, h, o = {}) {
  const a = o.a == null ? 1 : o.a, r = o.r == null ? 16 : o.r; if (a <= 0.001) return;
  g.save(); alpha(a); rr(x, y, w, h, r); g.fillStyle = o.fill || 'rgba(9,13,30,0.70)'; g.fill();
  const gr = g.createLinearGradient(x, y, x + w, y + h);
  gr.addColorStop(0, rgba(o.c1 || CYAN, 0.85)); gr.addColorStop(0.5, rgba(o.c2 || VIOLET, 0.45)); gr.addColorStop(1, rgba(o.c3 || MAGENTA, 0.75));
  g.strokeStyle = gr; g.lineWidth = o.lw || 1.6;
  if (o.glow) { g.shadowColor = o.glow; g.shadowBlur = 22 * SCALE; }
  g.stroke(); g.shadowBlur = 0;
  alpha(a * 0.45); g.beginPath(); g.moveTo(x + r, y + 1.5); g.lineTo(x + w - r, y + 1.5); g.strokeStyle = 'rgba(255,255,255,0.5)'; g.lineWidth = 1; g.stroke();
  g.restore();
}
function pill(s, x, y, color, o = {}) {
  const size = o.size || 16, f = `${size}px ${o.fam || 'MonoB'}`;
  g.save(); g.font = f; const tw = g.measureText(s).width; const padX = o.padX == null ? 12 : o.padX, h = o.h || Math.round(size * 1.9);
  const w = tw + padX * 2; const x0 = o.align === 'center' ? x - w / 2 : (o.align === 'right' ? x - w : x);
  alpha(o.a == null ? 1 : o.a); rr(x0, y - h / 2, w, h, h / 2);
  if (o.filled) { g.fillStyle = color; g.fill(); } else { g.fillStyle = rgba(color, 0.13); g.fill(); g.strokeStyle = rgba(color, 0.95); g.lineWidth = 1.5; g.stroke(); }
  g.fillStyle = o.filled ? '#05060d' : color; g.textBaseline = 'middle'; g.textAlign = 'left';
  if (o.glow) { g.shadowColor = color; g.shadowBlur = 14 * SCALE; }
  g.fillText(s, x0 + padX, y + 1); g.restore(); return w;
}
function wrap(s, f, maxW) {
  g.save(); g.font = f; const words = s.split(/\s+/); const lines = []; let cur = '';
  for (const w of words) { const t = cur ? cur + ' ' + w : w; if (g.measureText(t).width > maxW && cur) { lines.push(cur); cur = w; } else cur = t; }
  if (cur) lines.push(cur); g.restore(); return lines;
}
function measure(s, f) { g.save(); g.font = f; const w = g.measureText(s).width; g.restore(); return w; }
function trunc(s, f, maxW) { if (measure(s, f) <= maxW) return s; let t = s; while (t.length > 3 && measure(t + '…', f) > maxW) t = t.slice(0, -1); return t + '…'; }
function line(x1, y1, x2, y2, color, w, a) { g.save(); alpha(a == null ? 1 : a); g.strokeStyle = color; g.lineWidth = w || 1; g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke(); g.restore(); }
const SPRITES = {};
function glow(color) {
  if (SPRITES[color]) return SPRITES[color];
  const sz = 64 * SCALE, c = createCanvas(sz, sz), x = c.getContext('2d'), gr = x.createRadialGradient(sz / 2, sz / 2, 0, sz / 2, sz / 2, sz / 2);
  gr.addColorStop(0, rgba(color, 1)); gr.addColorStop(0.18, rgba(color, 0.65)); gr.addColorStop(0.5, rgba(color, 0.12)); gr.addColorStop(1, rgba(color, 0));
  x.fillStyle = gr; x.fillRect(0, 0, sz, sz); SPRITES[color] = c; return c;
}
function dot(x, y, r, color, a) { g.save(); alpha(a == null ? 1 : a); g.globalCompositeOperation = 'lighter'; g.drawImage(glow(color), x - r, y - r, 2 * r, 2 * r); g.restore(); }
function counter(a, t, value) { return Math.round(value * pr(a, t, E.outCubic)); }
function heading(num, title, sub, t, a0) {
  const k = pr(a0, t, E.outCubic); if (k <= 0) return;
  txt(num, 110 - (1 - k) * 30, 150, '92px Bebas', rgba(CYAN, 0.95), { a: k, glow: CYAN, blur: 26 });
  txt(title, 220 - (1 - k) * 30, 148, '72px Bebas', WHITE, { a: k });
  if (sub) txt(sub, 222, 182, '20px InterSB', MUTED, { a: k * 0.95 });
}

// ---------------------------------------------------------------- background, sweeps, HUD, subtitles
const DUST = (() => { const r = rng(7); return Array.from({ length: 150 }, () => ({ x: r() * W, y: r() * H, z: 0.25 + r() * 0.75, ph: r() * 6.28, s: 0.6 + r() * 1.5 })); })();
function honestMix(t) { const s = SC.honest; return clamp(Math.min((t - s.t0 + 0.4) / 1.2, (s.t1 + 0.4 - t) / 1.2)); }
function background(t) {
  g.globalAlpha = 1; g.fillStyle = BG; g.fillRect(0, 0, W, H);
  const hon = honestMix(t);
  const cols = [mixHex(CYAN, RED, hon), mixHex(VIOLET, AMBER, hon * 0.7), mixHex(MAGENTA, RED, hon), AMBER];
  g.globalCompositeOperation = 'lighter';
  for (let i = 0; i < 4; i++) {
    const cx = W * (0.5 + 0.38 * Math.sin(t * 0.045 + i * 1.9)), cy = H * (0.45 + 0.3 * Math.cos(t * 0.037 + i * 2.3));
    const r = 560 + 150 * Math.sin(t * 0.06 + i);
    const gr = g.createRadialGradient(cx, cy, 0, cx, cy, r);
    gr.addColorStop(0, rgba(cols[i], i === 3 ? 0.05 : 0.12)); gr.addColorStop(1, rgba(cols[i], 0));
    g.fillStyle = gr; g.fillRect(cx - r, cy - r, 2 * r, 2 * r);
  }
  g.globalCompositeOperation = 'source-over';
  const hy = 690, gc = mixHex(CYAN, RED, hon);
  g.strokeStyle = rgba(gc, 0.07); g.lineWidth = 1;
  for (let k = -18; k <= 18; k++) { g.beginPath(); g.moveTo(W / 2 + k * 28, hy); g.lineTo(W / 2 + k * 250, H); g.stroke(); }
  for (let j = 0; j < 12; j++) { const z = ((j + t * 0.35) % 12) / 12; const y = hy + (H - hy) * Math.pow(z, 2.2); g.strokeStyle = rgba(gc, 0.11 * z); g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }
  g.fillStyle = '#d6ecff';
  for (const p of DUST) {
    const x = (p.x + t * 14 * p.z) % W, y = p.y + Math.sin(t * 0.3 + p.ph) * 6;
    g.globalAlpha = (0.18 + 0.32 * p.z) * (0.6 + 0.4 * Math.sin(t * 1.3 + p.ph)); g.fillRect(x, y, p.s * p.z * 1.5, p.s * p.z * 1.5);
  }
  g.globalAlpha = 1;
}
function sweeps(t) {
  for (const s of plan.scenes.slice(1)) {
    const d = t - s.t0; if (Math.abs(d) > 0.55) continue;
    const x = lerp(-500, W + 500, (d + 0.55) / 1.1);
    g.save(); g.globalCompositeOperation = 'lighter';
    const gr = g.createLinearGradient(x - 260, 0, x + 260, 0);
    gr.addColorStop(0, 'rgba(120,230,255,0)'); gr.addColorStop(0.5, 'rgba(160,235,255,0.20)'); gr.addColorStop(1, 'rgba(255,80,180,0)');
    g.fillStyle = gr; g.transform(1, 0, -0.35, 1, 0, 0); g.fillRect(x - 260 + 0.35 * H / 2, 0, 520, H); g.restore();
  }
}
const TITLES = ['OPENING', 'THE BRIEF', 'KNOWLEDGE BASE', 'THE AGENT', 'MEASUREMENT', "WHAT WE WON'T FAKE", 'RUN IT'];
function sceneAt(t) { let s = plan.scenes[0]; for (const x of plan.scenes) if (t >= x.t0) s = x; return s; }
function timecode(t) { const f = Math.floor(t * FPS); const ss = Math.floor(f / FPS); return `${String(Math.floor(ss / 60)).padStart(2, '0')}:${String(ss % 60).padStart(2, '0')}:${String(f % FPS).padStart(2, '0')}`; }
function hud(t) {
  if (t < plan.intro - 0.3) return;
  const a = clamp((t - plan.intro + 0.3) / 0.8);
  g.save(); g.globalAlpha = 0.9 * a; g.strokeStyle = rgba(CYAN, 0.55); g.lineWidth = 2;
  const m = 34, L = 44;
  for (const [x, y, dx, dy] of [[m, m, 1, 1], [W - m, m, -1, 1], [m, H - m, 1, -1], [W - m, H - m, -1, -1]]) { g.beginPath(); g.moveTo(x, y + dy * L); g.lineTo(x, y); g.lineTo(x + dx * L, y); g.stroke(); }
  g.font = '15px MonoB'; g.textBaseline = 'middle'; g.fillStyle = rgba(MUTED, 1); g.textAlign = 'left';
  g.fillText('LEAR LOCAL  //  TAKE-HOME WALKTHROUGH', 96, 60);
  const s = sceneAt(t); g.textAlign = 'right'; g.fillStyle = rgba(WHITE, 0.92);
  g.fillText(`${String(s.index + 1).padStart(2, '0')} / 07   ${TITLES[s.index]}`, W - 96, 60);
  const p = clamp((t - s.t0) / (s.t1 - s.t0));
  g.fillStyle = rgba(DIM, 1); g.fillRect(W - 420, 76, 324, 2); g.fillStyle = rgba(CYAN, 0.9); g.fillRect(W - 420, 76, 324 * p, 2);
  g.font = '13px Mono'; g.textAlign = 'left'; g.fillStyle = rgba(MUTED, 0.95);
  g.fillText('DATA: rag-formation evidence @ 954c8d6  ·  Lear-backend-Avi @ 60cf9abf  ·  offline baseline  ·  NOT QUALIFYING', 96, H - 42);
  g.textAlign = 'right'; g.fillText(timecode(t), W - 96, H - 42); g.restore();
}
plan.subtitles.forEach((s, i) => reg('subtitles', 'subtitle', s.t0, s.t1 - s.t0, 'narration', { text: s.text }));
function subtitles(t) {
  const s = plan.subtitles.find(x => t >= x.t0 - 0.05 && t < x.t1 + 0.22); if (!s) return;
  const a = clamp(Math.min((t - s.t0 + 0.05) / 0.14, (s.t1 + 0.22 - t) / 0.18));
  g.save(); g.font = '30px InterSB'; const tw = g.measureText(s.text).width;
  g.globalAlpha = 0.78 * a; rr(W / 2 - tw / 2 - 24, 962, tw + 48, 52, 14); g.fillStyle = 'rgba(3,5,12,0.86)'; g.fill();
  g.globalAlpha = a; g.fillStyle = WHITE; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(s.text, W / 2, 989); g.restore();
}
function vignette() {
  const gr = g.createRadialGradient(W / 2, H / 2, H * 0.35, W / 2, H / 2, H * 0.95);
  gr.addColorStop(0, 'rgba(0,0,0,0)'); gr.addColorStop(1, 'rgba(0,0,0,0.62)'); g.globalAlpha = 1; g.fillStyle = gr; g.fillRect(0, 0, W, H);
}

// ---------------------------------------------------------------- scenes
const SCENES = [];
function scene(name, t0, t1, build, draw) { const s = { name, t0, t1, build, draw }; SCENES.push(s); return s; }
const vis = s => t => clamp(Math.min((t - s.t0 + 0.3) / 0.6, (s.t1 + 0.3 - t) / 0.6));

// ---- intro sting
scene('intro', 0, plan.intro, function () {
  const r = rng(11); this.parts = [];
  for (let i = 0; i < 160; i++) {
    const a0 = r() * Math.PI * 2, a1 = (i / 160) * Math.PI * 2;
    this.parts.push({ a: reg('intro', 'particle-converge', 0.15 + r() * 0.5, 1.9 + r() * 0.5, 'decor'), a0, a1, r0: 700 + r() * 500, c: [CYAN, VIOLET, MAGENTA][i % 3] });
  }
  this.segs = Array.from({ length: 48 }, (_, k) => reg('intro', 'ring-segment', 1.35 + k * 0.022, 0.45, 'decor'));
  this.flash = reg('intro', 'flash', 2.82, 0.6, 'decor');
}, function (t) {
  const cx = W / 2, cy = 500;
  for (const p of this.parts) {
    const k = pr(p.a, t, E.inOutCubic); if (k <= 0) continue;
    const ang = lerp(p.a0, p.a1, k) + (1 - k) * 2.4, rad = lerp(p.r0, 232, k);
    const x = cx + Math.cos(ang) * rad, y = cy + Math.sin(ang) * rad * 0.92;
    const k2 = pr(p.a, t - 0.06, E.inOutCubic), ang2 = lerp(p.a0, p.a1, k2) + (1 - k2) * 2.4, rad2 = lerp(p.r0, 232, k2);
    line(cx + Math.cos(ang2) * rad2, cy + Math.sin(ang2) * rad2 * 0.92, x, y, rgba(p.c, 0.55), 1.6, 0.6 * k);
    dot(x, y, 9, p.c, 0.5 + 0.5 * k);
  }
  g.save(); g.lineWidth = 5; g.lineCap = 'round';
  this.segs.forEach((s, k) => { const q = pr(s, t, E.outCubic); if (q <= 0) return; alpha(q); g.strokeStyle = k % 2 ? CYAN : VIOLET; g.beginPath(); g.arc(cx, cy, 232, (k / 48) * 6.283 + 0.02, ((k + 0.82 * q) / 48) * 6.283); g.stroke(); });
  g.restore();
  const f = pr(this.flash, t); if (f > 0 && f < 1) {
    const gr = g.createRadialGradient(cx, cy, 0, cx, cy, 900); gr.addColorStop(0, `rgba(255,255,255,${0.85 * (1 - f) * (1 - f)})`); gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.save(); alpha(1); g.fillStyle = gr; g.fillRect(0, 0, W, H); g.restore();
    line(0, cy, W, cy, 'rgba(180,240,255,0.9)', 3 * (1 - f), 1 - f);
  }
});

// ---- 01 opening
scene('open', SC.open.t0, SC.open.t1, function () {
  const t0 = SC.open.t0; this.title = 'LEAR LOCAL';
  this.letters = [...this.title].map((ch, i) => (ch === ' ' ? null : reg('open', 'title-letter', t0 + 0.05 + i * 0.06, 0.75, 'title', { ch })));
  this.sweep = reg('open', 'title-sweep', t0 + 1.25, 1.0, 'decor');
  this.orbits = [0, 1, 2].map(i => reg('open', 'orbit-ring', t0 + 0.2 + i * 0.15, 1.2, 'decor'));
  const words = 'A BACKEND-ONLY RAG AGENT FOR THE LEAR BACKEND'.split(' ');
  this.words = words.map((w, i) => ({ w, a: reg('open', 'subtitle-word', cue('open', 'backend-only') - 0.35 + i * 0.09, 0.55, 'brief') }));
  this.chip = reg('open', 'chip', cue('open', 'take-home') - 0.2, 0.6, 'brief');
  this.tags = [['EVERY CLAIM TRACED TO SOURCE', CYAN, cue('open', 'every claim') - 0.15], ['EVERY NUMBER MEASURED', MAGENTA, cue('open', 'every number') - 0.15],
    ['NOTHING FAKED', AMBER, cue('open', 'every number') + 1.0]].map(([s, c, tt]) => ({ s, c, a: reg('open', 'tagline-chip', tt, 0.55, 'principle') }));
}, function (t) {
  const cx = W / 2, cy = 500;
  g.save(); g.lineWidth = 2;
  this.orbits.forEach((o, i) => { const k = pr(o, t, E.outCubic); if (k <= 0) return; alpha(0.35 * k); g.strokeStyle = [CYAN, VIOLET, MAGENTA][i];
    const rad = 330 + i * 70, rot = t * (0.15 + i * 0.07) * (i % 2 ? -1 : 1);
    for (let s = 0; s < 6; s++) { g.beginPath(); g.arc(cx, cy, rad * k, rot + s * 1.047, rot + s * 1.047 + 0.62); g.stroke(); } });
  g.restore();
  const f = '250px Bebas'; const total = measure(this.title, f); let x = cx - total / 2;
  [...this.title].forEach((ch, i) => {
    const w = measure(ch, f); const a = this.letters[i];
    if (a && pr(a, t) > 0) {
      const k = pr(a, t, E.outBack), k1 = pr(a, t);
      const y = 590 + (1 - k) * 90;
      if (k1 < 1) { g.save(); g.globalCompositeOperation = 'lighter'; txt(ch, x - 16 * (1 - k1), y, f, rgba(MAGENTA, 0.7 * (1 - k1)), {}); txt(ch, x + 16 * (1 - k1), y, f, rgba(CYAN, 0.7 * (1 - k1)), {}); g.restore(); }
      txt(ch, x, y, f, WHITE, { a: k1, glow: CYAN, blur: 34 });
    }
    x += w;
  });
  const sw = pr(this.sweep, t); if (sw > 0 && sw < 1) {
    const sx = lerp(cx - total / 2 - 200, cx + total / 2 + 200, E.inOutCubic(sw));
    g.save(); g.globalCompositeOperation = 'lighter'; alpha(0.55 * Math.sin(sw * Math.PI));
    const gr = g.createLinearGradient(sx - 80, 0, sx + 80, 0); gr.addColorStop(0, 'rgba(255,255,255,0)'); gr.addColorStop(0.5, 'rgba(255,255,255,0.9)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gr; g.fillRect(sx - 80, 380, 160, 240); g.restore();
  }
  const kc = pr(this.chip, t, E.outCubic); if (kc > 0) pill('TAKE-HOME BRIEF  ·  BUILD A LOCAL RAG AGENT', cx, 345 - (1 - kc) * 16, CYAN, { a: kc, align: 'center', size: 17 });
  const wf = '38px InterSB'; const gap = 14; const widths = this.words.map(o => measure(o.w, wf)); let wx = cx - (widths.reduce((a, b) => a + b, 0) + gap * (widths.length - 1)) / 2;
  this.words.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); txt(o.w, wx, 668 + (1 - k) * 22, wf, i >= 3 && i <= 3 ? CYAN : rgba(WHITE, 0.92), { a: k }); wx += widths[i] + gap; });
  let tx = cx; const tw = this.tags.map(o => measure(o.s, '17px MonoB') + 24 + 22); tx = cx - (tw.reduce((a, b) => a + b, 0) - 22) / 2;
  this.tags.forEach((o, i) => { const k = pr(o.a, t, E.outBack); if (k > 0) pill(o.s, tx, 760 + (1 - k) * 20, o.c, { a: clamp(k), size: 17, glow: true }); tx += tw[i]; });
});

// ---- 02 the brief
const BRIEF = [
  ['01', 'KNOWLEDGE BASE', CYAN, 'accurate knowledge base', 'docgen + verifier (11/11 checks) · heading chunker · embeddings · SQLite vector index', ['Accurate docs for the Lear backend repo', 'Chunk markdown by heading or topic', 'Generate embeddings', 'Store chunks in a vector store']],
  ['02', 'LOCAL AGENT', VIOLET, 'A local agent', 'hybrid retrieval + rerank · CLI · sessions · compaction · memory · guardrails · Ollama models BLOCKED here', ['Ollama only: LLM ≤ 4B, embedder ≤ 200M', 'Retrieval + basic reranking', 'CLI: send a question, see the answer', 'Sessions, context, compaction', 'Memory + guardrails', 'Shared memory via MCP or CLI']],
  ['03', 'MEASUREMENT', MAGENTA, 'And measurement', 'bench at 1–10 users · rate limiter · 80-question eval · 5 metrics · 3 rounds · LLM judge BLOCKED here', ['TTFT · ITL · mean latency', 'p50 / p85 / p99 with dummy users', 'Tokens per second · rate limiting', '50–100 golden questions', 'Citations, groundedness, relevance, context precision + recall', 'Separate LLM judge · 3+ improve rounds']],
];
scene('brief', SC.brief.t0, SC.brief.t1, function () {
  const t0 = SC.brief.t0; this.head = reg('brief', 'heading', t0 + 0.15, 0.7, 'brief');
  this.cards = BRIEF.map(([n, title, c, key, metBy, items], k) => {
    const ts = cue('brief', key) - 0.3;
    return { n, title, c, metBy, met: reg('brief', 'met-summary', cue('brief', 'Here is how') + 0.2 + k * 0.3, 0.6, 'requirements_matrix.md'), card: reg('brief', 'panel', ts, 0.75, 'brief'), items: items.map((s, j) => ({ s, a: reg('brief', 'requirement-bullet', ts + 0.35 + j * 0.2, 0.45, 'brief'), m: reg('brief', 'met-marker', cue('brief', 'Here is how') + k * 0.25 + j * 0.06, 0.4, 'brief') })) };
  });
  this.banner = reg('brief', 'banner', cue('brief', 'Here is how') - 0.15, 0.7, 'brief');
}, function (t) {
  const kh = pr(this.head, t, E.outCubic);
  if (kh > 0) { txt('THE BRIEF', 110, 168 - (1 - kh) * 20, '96px Bebas', WHITE, { a: kh }); txt('backend-only  ·  no GUI  ·  no image or uploaded-document features', 112, 206, '21px InterSB', MUTED, { a: kh }); }
  this.cards.forEach((c, k) => {
    const kc = pr(c.card, t, E.outCubic); if (kc <= 0) return;
    const x = 110 + k * 590, y = 250 + (1 - kc) * 70, w = 520, h = 610;
    const met = pr(c.items[0].m, t);
    panel(x, y, w, h, { a: kc, c1: c.c, c2: c.c, c3: VIOLET, glow: met > 0 ? c.c : null });
    txt(c.n, x + 30, y + 78, '74px Bebas', c.c, { a: kc, glow: c.c, blur: 20 });
    txt(c.title, x + 112, y + 70, '30px InterXB', WHITE, { a: kc });
    line(x + 30, y + 104, x + w - 30, y + 104, rgba(c.c, 0.5), 1, kc);
    let yy = y + 150;
    c.items.forEach(it => {
      const ki = pr(it.a, t, E.outCubic), km = pr(it.m, t, E.outBack);
      const lines = wrap(it.s, '21px InterSB', w - 110);
      if (ki > 0) {
        g.save(); alpha(ki); g.beginPath(); g.arc(x + 44, yy - 7, 9, 0, 6.283); g.strokeStyle = c.c; g.lineWidth = 2; g.stroke();
        if (km > 0) { g.fillStyle = c.c; g.globalAlpha = clamp(SA * ki * km); g.beginPath(); g.arc(x + 44, yy - 7, 9 * clamp(km), 0, 6.283); g.fill(); }
        g.restore();
        lines.forEach((ln, li) => txt(ln, x + 70 + (1 - ki) * 20, yy + li * 28, '21px InterSB', rgba(WHITE, 0.92), { a: ki }));
      }
      yy += lines.length * 28 + 22;
    });
    const km = pr(c.met, t, E.outCubic);
    if (km > 0) {
      line(x + 30, y + 446, x + w - 30, y + 446, rgba(c.c, 0.5), 1, kc * km);
      pill('MET BY', x + 30, y + 476, c.c, { a: kc * km, size: 13, filled: true, h: 24 });
      wrap(c.metBy, '18px InterSB', w - 60).slice(0, 4).forEach((ln, li) => txt(ln, x + 30, y + 520 + li * 25, '18px InterSB', rgba(WHITE, 0.9), { a: kc * km }));
    }
  });
  const kb = pr(this.banner, t, E.outCubic);
  if (kb > 0) { const w = pill('HERE IS HOW EACH PART IS MET  →', W / 2, 912 - (1 - kb) * 14, WHITE, { a: kb, align: 'center', size: 20, glow: true }); }
});

// ---- 03 knowledge base
const KIND_COL = { py: CYAN, md: VIOLET, ts: BLUE, json: AMBER, yaml: GREEN, sh: MAGENTA, web: '#40E0D0', cfg: '#B0B8FF', txt: '#C8D0E8', other: '#5A6488' };
const DOC_PAL = [CYAN, VIOLET, MAGENTA, AMBER, GREEN, BLUE, '#40E0D0', '#FF8A5B', '#B388FF', '#7CF29C', '#FFD166', '#6EC1FF', '#FF6FB5', '#9AE6B4', '#F6AD55', '#90CDF4', '#D6BCFA', '#FBB6CE', '#C6F6D5'];
scene('kb', SC.kb.t0, SC.kb.t1, function () {
  const S = 'kb', t0 = SC.kb.t0;
  this.head = reg(S, 'heading', t0 + 0.1, 0.7, 'scene');
  this.repo = reg(S, 'chip', cue(S, 'generated straight') - 0.3, 0.6, 'docs/lear_backend/SOURCE_PIN.json');
  const t454 = cue(S, 'four hundred fifty-four') - 0.7;
  this.files = D.learFiles.map(([p, kind], i) => { const col = i % 27, row = Math.floor(i / 27); return { kind, col, row, a: reg(S, 'repo-file-tile', t454 + (col + row) * 0.022, 0.5, 'git ls-files @ 60cf9abf', { path: p }) }; });
  this.fileCount = reg(S, 'counter', t454, 1.6, 'git ls-files');
  this.docsHead = reg(S, 'heading', cue(S, 'generated straight') + 0.2, 0.6, 'scene');
  this.routes = D.routes.map(([m, p], i) => ({ m, p, a: reg(S, 'api-route', cue(S, 'eighty-seven') - 0.2 + i * 0.045, 0.4, 'docs/lear_backend/reference/http_api.md') }));
  this.routeCount = reg(S, 'counter', cue(S, 'eighty-seven') - 0.2, 1.2, 'http_api.md');
  this.envs = D.envVars.map((n, i) => ({ n, a: reg(S, 'env-var', cue(S, 'thirty-eight') - 0.15 + i * 0.035, 0.35, 'docs/lear_backend/reference/configuration_env.md') }));
  this.envCount = reg(S, 'counter', cue(S, 'thirty-eight') - 0.15, 1.0, 'configuration_env.md');
  this.mods = D.modules.map((n, i) => ({ n: n.replace('prash/', ''), a: reg(S, 'python-module', cue(S, 'seventy-nine') - 0.15 + i * 0.022, 0.35, 'docs/lear_backend/reference/modules.md') }));
  this.modCount = reg(S, 'counter', cue(S, 'seventy-nine') - 0.15, 1.0, 'modules.md');
  this.verHead = reg(S, 'heading', cue(S, 'A verifier') - 0.2, 0.6, 'scene');
  const labels = []; [['implemented', CYAN], ['configured', VIOLET], ['inferred', AMBER], ['tested', GREEN]].forEach(([k, c]) => { for (let i = 0; i < D.claimLabels[k]; i++) labels.push([k, c]); });
  const t340 = cue(S, 'three hundred forty') - 0.9;
  this.claims = labels.map(([k, c], i) => ({ c, a: reg(S, 'claim-dot', t340 + i * 0.0065, 0.35, 'docs_verify claim_labels', { label: k }) }));
  this.claimCount = reg(S, 'counter', t340, 2.4, 'docs_verify');
  const tv = cue(S, 'A verifier') + 0.4, te = cue(S, 'eleven of eleven');
  this.checks = D.verifyChecks.map(([n, st, sum], i) => ({ n, st, a: reg(S, 'verify-check', tv + i * ((te - tv) / 11), 0.4, 'docs_verify_2026-11-05.json') }));
  this.allPass = reg(S, 'stamp', te + 0.1, 0.6, 'docs_verify');
  this.phaseD = reg(S, 'scene-transition', cue(S, 'split by heading') - 0.5, 0.7, 'decor');
  const docs = [...new Set(D.chunks.map(c => c[0]))]; this.docs = docs;
  const tc = cue(S, 'split by heading') - 0.1; const maxTok = Math.max(...D.chunks.map(c => c[1]));
  this.chunks = D.chunks.map((c, i) => ({ doc: c[0], tok: c[1], col: i % 25, row: Math.floor(i / 25), ci: docs.indexOf(c[0]), rel: c[1] / maxTok,
    a: reg(S, 'chunk-tile', tc + i * 0.0045, 0.5, 'index manifest (scratch rebuild)', { tokens: c[1] }),
    e: reg(S, 'chunk-embed', cue(S, 'embedded') - 0.2 + i * 0.003, 0.4, 'index manifest'),
    s: reg(S, 'chunk-to-index', cue(S, 'SQLite') - 0.4 + i * 0.0035, 0.6, 'index manifest') }));
  this.chunkHead = reg(S, 'heading', tc, 0.6, 'scene');
  this.db = reg(S, 'panel', cue(S, 'SQLite') - 0.4, 0.7, 'index stats');
  const qv = D.S05.queryVector; this.qv = qv.index.map((ix, i) => ({ ix, v: qv.first[i], a: reg(S, 'vector-dimension', cue(S, 'embedded') + i * 0.05, 0.4, 'S05 queryVector') }));
}, function (t) {
  const S = 'kb';
  heading('01', 'KNOWLEDGE BASE', 'docs generated from the Lear backend, verified, chunked by heading, embedded, indexed', t, this.head);
  const colA = 1 - pr(this.phaseD, t, E.inOutCubic);
  if (colA > 0.001) {
    const save = SA; SA *= colA;
    const kr = pr(this.repo, t, E.outCubic);
    if (kr > 0) { txt('LEAR BACKEND REPO', 110, 262, '30px Bebas', WHITE, { a: kr }); pill('Lear-backend-Avi @ 60cf9abf', 300, 252, CYAN, { a: kr, size: 14 }); }
    for (const f of this.files) {
      const k = pr(f.a, t, E.outCubic); if (k <= 0) continue;
      const x = 110 + f.col * 19.6, y = 290 + f.row * 19.6; const c = KIND_COL[f.kind] || MUTED;
      g.globalAlpha = clamp(SA * k); g.fillStyle = k < 1 ? WHITE : rgba(c, 0.92); g.fillRect(x + (1 - k) * 8, y + (1 - k) * 8, 16 * k, 16 * k);
    }
    g.globalAlpha = 1;
    const kf = pr(this.fileCount, t, E.outCubic);
    if (kf > 0) {
      txt(fmt(counter(this.fileCount, t, D.learFiles.length)), 110, 712, '84px Bebas', WHITE, { a: kf, glow: CYAN, blur: 22 });
      txt('TRACKED FILES AT THE PIN', 260, 690, '20px InterB', MUTED, { a: kf });
      const counts = {}; D.learFiles.forEach(([, k]) => { counts[k] = (counts[k] || 0) + 1; });
      let lx = 260; Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6).forEach(([k, n]) => { g.save(); alpha(kf); g.fillStyle = KIND_COL[k]; g.fillRect(lx, 704, 10, 10); g.restore(); txt(`${k} ${n}`, lx + 14, 714, '14px Mono', MUTED, { a: kf }); lx += 14 + measure(`${k} ${n}`, '14px Mono') + 16; });
    }
    const kd = pr(this.docsHead, t, E.outCubic);
    if (kd > 0) { txt('GENERATED DOCS', 700, 262, '30px Bebas', WHITE, { a: kd }); txt('python -m ragformation lear docs build', 880, 258, '14px Mono', MUTED, { a: kd }); }
    const blocks = [[this.routeCount, D.routes.length, 'API ROUTES', 'http_api.md · matches the AST of prash/server.py', 290, CYAN],
      [this.envCount, D.envVars.length, 'ENV VARIABLES', 'configuration_env.md · read via os.environ / getenv', 470, AMBER],
      [this.modCount, D.modules.length, 'PYTHON MODULES', 'modules.md · prash/*.py excl. __init__.py', 650, VIOLET]];
    blocks.forEach(([a, n, label, sub, y, c]) => {
      const k = pr(a, t, E.outCubic); if (k <= 0) return;
      panel(700, y, 500, 160, { a: k, c1: c, c2: c, c3: VIOLET });
      txt(String(counter(a, t, n)), 724, y + 78, '76px Bebas', c, { a: k, glow: c, blur: 18 });
      txt(label, 836, y + 52, '26px InterXB', WHITE, { a: k }); txt(sub, 836, y + 78, '14px InterSB', MUTED, { a: k });
    });
    // live tickers inside the blocks
    const visRoutes = this.routes.filter(r => pr(r.a, t) > 0).slice(-3);
    visRoutes.forEach((r, i) => { const k = pr(r.a, t, E.outCubic); const mc = r.m === 'GET' ? GREEN : (r.m === 'POST' ? AMBER : MAGENTA); txt(r.m === 'WEBSOCKET' ? 'WS' : r.m, 724, 392 + i * 19, '14px MonoB', mc, { a: k }); txt(trunc(r.p, '14px Mono', 380), 800, 392 + i * 19, '14px Mono', rgba(WHITE, 0.85), { a: k }); });
    let ex = 724, ey = 568; this.envs.forEach(e => { const k = pr(e.a, t, E.outCubic); if (k <= 0) return; const w = measure(e.n, '11px MonoB') + 12; if (ex + w > 1180) { ex = 724; ey += 20; } if (ey > 612) return; pill(e.n, ex, ey, AMBER, { a: k, size: 11, padX: 6, h: 16 }); ex += w + 5; });
    const visMods = this.mods.filter(m => pr(m.a, t) > 0);
    visMods.slice(-12).forEach((m, i) => { const k = pr(m.a, t, E.outCubic); txt(trunc(m.n, '12px Mono', 150), 724 + (i % 3) * 158, 748 + Math.floor(i / 3) * 16, '12px Mono', rgba(VIOLET, 0.95), { a: k }); });
    const kv = pr(this.verHead, t, E.outCubic);
    if (kv > 0) { txt('VERIFIER', 1260, 262, '30px Bebas', WHITE, { a: kv }); txt('lear docs verify --source <Lear>', 1372, 258, '14px Mono', MUTED, { a: kv }); }
    for (let i = 0; i < this.claims.length; i++) {
      const c = this.claims[i]; const k = pr(c.a, t, E.outBack); if (k <= 0) continue;
      const x = 1266 + (i % 20) * 16, y = 296 + Math.floor(i / 20) * 16;
      g.globalAlpha = clamp(SA * clamp(k)); g.fillStyle = c.c; g.beginPath(); g.arc(x, y, 5.5 * clamp(k, 0, 1.3), 0, 6.283); g.fill();
    }
    g.globalAlpha = 1;
    const kcl = pr(this.claimCount, t, E.outCubic);
    if (kcl > 0) {
      txt(`${counter(this.claimCount, t, 340)} / 340`, 1600, 330, '58px Bebas', WHITE, { a: kcl, glow: CYAN, blur: 18 });
      txt('CLAIMS LABELLED', 1600, 356, '15px InterB', MUTED, { a: kcl }); txt('WITH EVIDENCE', 1600, 376, '15px InterB', MUTED, { a: kcl });
      [['implemented', 255, CYAN], ['configured', 32, VIOLET], ['inferred', 30, AMBER], ['tested', 23, GREEN]].forEach(([n, v, c], i) => { g.save(); alpha(kcl); g.fillStyle = c; g.beginPath(); g.arc(1606, 412 + i * 22, 5, 0, 6.283); g.fill(); g.restore(); txt(`${n} ${v}`, 1618, 417 + i * 22, '14px Mono', MUTED, { a: kcl }); });
    }
    this.checks.forEach((c, i) => {
      const k = pr(c.a, t, E.outCubic); if (k <= 0) return; const y = 600 + i * 24;
      txt('✓', 1266, y, '18px InterB', GREEN, { a: k, glow: GREEN, blur: 10 }); txt(c.n, 1292, y, '15px MonoB', rgba(WHITE, 0.9), { a: k }); pill('PASS', 1520, y - 5, GREEN, { a: k, size: 11, h: 17, padX: 7 });
    });
    const kp = pr(this.allPass, t, E.outBack);
    if (kp > 0) { pill('11 / 11 CHECKS PASS', 1590, 640, GREEN, { a: clamp(kp), size: 20, glow: true, filled: true }); txt('1,237 source refs · 0 invalid', 1592, 690, '15px MonoB', MUTED, { a: clamp(kp) }); txt('87 routes = AST of server.py', 1592, 712, '15px MonoB', MUTED, { a: clamp(kp) }); }
    SA = save;
  }
  // phase D/E: chunks -> embeddings -> index
  const kh = pr(this.chunkHead, t, E.outCubic);
  if (kh > 0) {
    txt('19 DOCS  →  355 CHUNKS', 110, 262, '44px Bebas', WHITE, { a: kh }); txt('48,676 tokens  ·  split by heading  ·  tables and code kept whole', 470, 254, '18px InterSB', MUTED, { a: kh });
    const ks = this.chunks[0].s;
  }
  let gone = 0;
  for (const c of this.chunks) {
    const k = pr(c.a, t, E.outBack); if (k <= 0) continue;
    const ke = pr(c.e, t, E.outCubic), ks = pr(c.s, t, E.inOutCubic);
    let x = 110 + c.col * 62, y = 300 + c.row * 34 - (1 - clamp(k)) * 120;
    const tx = 1700, ty = 560;
    if (ks > 0) { x = lerp(x, tx, ks); y = lerp(y, ty, ks) - Math.sin(ks * Math.PI) * 120; }
    const w = lerp(56, 6, ks), h = lerp(26, 6, ks);
    const col = DOC_PAL[c.ci % DOC_PAL.length];
    g.globalAlpha = clamp(SA * clamp(k) * (1 - 0.6 * ks)); g.fillStyle = rgba(col, 0.25 + 0.6 * c.rel); g.fillRect(x, y, w, h);
    if (ke > 0 && ks < 0.2) { g.globalAlpha = clamp(SA * ke * (1 - ks * 5)); g.strokeStyle = rgba(WHITE, 0.8); g.lineWidth = 1; g.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1); for (let b = 0; b < 4; b++) { g.fillStyle = rgba(WHITE, 0.55); const bh = 4 + ((c.tok * (b + 3)) % 14); g.fillRect(x + 8 + b * 11, y + h - 4 - bh * ke, 5, bh * ke); } }
    if (ks >= 1) gone++;
  }
  g.globalAlpha = 1;
  const kdb = pr(this.db, t, E.outCubic);
  if (kdb > 0) {
    const x = 1610, y = 420; panel(x - 20, y - 40, 300, 330, { a: kdb, c1: GREEN, c2: CYAN, c3: VIOLET, glow: GREEN });
    g.save(); alpha(kdb); g.strokeStyle = GREEN; g.lineWidth = 3; g.fillStyle = rgba(GREEN, 0.10);
    for (let i = 0; i < 3; i++) { const yy = y + 40 + i * 52; g.beginPath(); g.ellipse(x + 130, yy, 90, 22, 0, 0, 6.283); g.fill(); g.stroke(); }
    g.restore();
    txt('SQLITE VECTOR INDEX', x + 130, y + 210, '30px Bebas', WHITE, { a: kdb, align: 'center' });
    txt(`${fmt(Math.min(355, gone))} vectors × 512 dims`, x + 130, y + 236, '16px MonoB', GREEN, { a: kdb, align: 'center' });
    txt('FTS5 + vectors · fingerprinted', x + 130, y + 258, '14px Mono', MUTED, { a: kdb, align: 'center' });
  }
  if (this.qv.length && pr(this.qv[0].a, t) > 0) {
    const x0 = 110, y0 = 870, wv = 1180; const ka = pr(this.qv[0].a, t, E.outCubic);
    line(x0, y0, x0 + wv, y0, rgba(MUTED, 0.6), 1, ka);
    txt('query vector (real): 512 dims, 13 non-zero  ·  baseline embedder = feature hashing (NOT semantic)  ·  nomic-embed-text configured for Ollama', x0, y0 + 56, '14px Mono', MUTED, { a: ka });
    this.qv.forEach(q => { const k = pr(q.a, t, E.outBack); if (k <= 0) return; const x = x0 + (q.ix / 512) * wv; const hh = q.v * 120 * clamp(k, 0, 1.2); line(x, y0, x, y0 - hh, q.v > 0 ? CYAN : MAGENTA, 3, 1); dot(x, y0 - hh, 8, q.v > 0 ? CYAN : MAGENTA, 0.9); });
  }
});

// ---- 04 the agent
scene('agent', SC.agent.t0, SC.agent.t1, function () {
  const S = 'agent', t0 = SC.agent.t0;
  this.head = reg(S, 'heading', t0 + 0.1, 0.7, 'scene');
  this.cmd = `$ python3 -m ragformation lear ask "${D.S06.question}"`;
  const tq = cue(S, 'Ask a question') - 0.2;
  this.term = reg(S, 'panel', tq - 0.3, 0.5, 'cli');
  this.chars = [...this.cmd].map((ch, i) => reg(S, 'typed-char', tq + i * 0.03, 0.05, 'golden lear-012 (dev)'));
  this.result = reg(S, 'callout', cue(S, 'cites its evidence') - 0.1, 0.5, 'S06');
  const nodes = [['DENSE', 'cosine · hashing baseline', 'dense vectors', 'retrieval.semantic'], ['BM25', 'lexical · FTS5', 'BM25', 'retrieval.lexical'], ['RRF', 'k = 60', 'reciprocal rank fusion', 'retrieval.fusion'], ['RERANK', 'heuristic-coverage-v1', 'reranks', 'retrieval.rerank'], ['MMR', 'diversify', 'reranks', 'retrieval.mmr'], ['TOP-10', 'to context', 'reranks', null]];
  this.nodes = nodes.map(([n, sub, key, stage], i) => ({ n, sub, stage, ms: stage ? (D.S05.stages.find(s => s.name === stage) || {}).ms : null, a: reg(S, 'pipeline-node', cue(S, key) - 0.2 + (i >= 4 ? (i - 3) * 0.45 : 0), 0.5, 'S05 stages') }));
  const r = rng(5); this.flow = Array.from({ length: 64 }, (_, i) => ({ lane: i % 2, off: r(), sp: 0.25 + r() * 0.2, a: reg(S, 'flow-particle', cue(S, 'dense vectors') + r() * 2, 0.4, 'decor') }));
  this.phaseC = reg(S, 'scene-transition', cue(S, 'reranks') + 0.9, 0.5, 'decor');
  this.cands = D.S05.candidates.map((c, i) => ({ c, a: reg(S, 'candidate-row', cue(S, 'reranks') + 1.1 + i * 0.12, 0.45, 'S05 candidates') }));
  this.excl = D.S05.excluded.map((c, i) => ({ c, a: reg(S, 'excluded-row', cue(S, 'eighteen-hundred') + 0.4 + i * 0.2, 0.45, 'S05 excluded') }));
  this.gauge = reg(S, 'context-gauge', cue(S, 'eighteen-hundred') - 0.2, 1.4, 'S05 contextTokens');
  this.segs = D.S05.context.map((c, i) => ({ c, a: reg(S, 'context-segment', cue(S, 'eighteen-hundred') - 0.1 + i * 0.12, 0.4, 'S05 context') }));
  this.answer = reg(S, 'panel', cue(S, 'cites its evidence') - 0.3, 0.6, 'S06 answer');
  this.answerWords = D.S06.answer.split(' ').slice(0, 34).map((w, i) => ({ w, a: reg(S, 'answer-word', cue(S, 'cites its evidence') + i * 0.035, 0.3, 'S06 answer (extractive baseline)') }));
  const cit = D.S06.citations[0];
  this.chain = [[`[${cit.id}] ${cit.chunkId.slice(0, 22)}…`, CYAN], [`${cit.document.split('/').pop()}  L${cit.lines}`, VIOLET], [`${cit.refs[0].ref}  ✓ resolved`, GREEN], ['pinned @ 60cf9abf', AMBER]].map(([s, c], i) => ({ s, c, a: reg(S, 'citation-hop', cue(S, 'exact lines') - 0.4 + i * 0.35, 0.45, 'S06 citations') }));
  this.phaseE = reg(S, 'scene-transition', cue(S, 'Sessions persist') - 0.5, 0.6, 'decor');
  this.turns = D.S07.turns.map((tt, i) => ({ tt, a: reg(S, 'session-turn', cue(S, 'Sessions persist') - 0.2 + i * 0.22, 0.4, 'S07 turns') }));
  this.comps = D.S07.compactions.map((c, i) => ({ c, a: reg(S, 'compaction-bar', cue(S, 'compacted') - 0.2 + i * 0.3, 0.6, 'S07 compactions') }));
  this.mem = [reg(S, 'memory-card', cue(S, 'memory is scoped') - 0.2, 0.5, 'S07'), reg(S, 'memory-card', cue(S, 'memory is scoped') + 0.4, 0.5, 'S07')];
  const tg = cue(S, 'guardrails') - 0.2, tge = cue(S, 'rate-limit') + 0.2;
  this.guards = D.S08_steps.map((s, i) => ({ s, a: reg(S, 'guardrail-row', tg + i * ((tge - tg) / D.S08_steps.length), 0.45, 'S08 steps') }));
}, function (t) {
  const S = 'agent';
  heading('02', 'THE AGENT', 'local CLI agent: hybrid retrieval, reranking, cited answers, sessions, memory, guardrails', t, this.head);
  const kt = pr(this.term, t, E.outCubic);
  if (kt > 0) {
    panel(110, 215, 1700, 70, { a: kt, r: 12, c1: CYAN, c2: VIOLET, c3: VIOLET });
    [RED, AMBER, GREEN].forEach((c, i) => { g.save(); alpha(kt); g.fillStyle = c; g.beginPath(); g.arc(136 + i * 18, 250, 5, 0, 6.283); g.fill(); g.restore(); });
    const n = this.chars.filter(a => t >= a.t0).length; const s = this.cmd.slice(0, n);
    txt(s, 200, 257, '22px MonoB', WHITE, { a: kt }); if (n < this.cmd.length && Math.floor(t * 3) % 2 === 0) { const w = measure(s, '22px MonoB'); g.save(); alpha(kt); g.fillStyle = CYAN; g.fillRect(202 + w, 238, 12, 26); g.restore(); }
    const kr = pr(this.result, t, E.outCubic); if (kr > 0) pill('GROUNDED · exit 0 · 1 citation', 1790, 250, GREEN, { a: kr, align: 'right', size: 15 });
  }
  const pa = 1 - pr(this.phaseE, t, E.inOutCubic);
  if (pa > 0.001) {
    const save = SA; SA *= pa;
    const pos = [[260, 360], [260, 470], [700, 415], [980, 415], [1250, 415], [1560, 415]];
    const k0 = pr(this.nodes[0].a, t);
    if (k0 > 0) {
      g.save(); g.lineWidth = 2;
      [[0, 2], [1, 2], [2, 3], [3, 4], [4, 5]].forEach(([a, b]) => { const kb = pr(this.nodes[b].a, t); if (kb <= 0) return; alpha(0.6 * kb); g.strokeStyle = rgba(CYAN, 0.6); g.beginPath(); g.moveTo(pos[a][0] + 95, pos[a][1]); g.lineTo(pos[b][0] - 95, pos[b][1]); g.stroke(); });
      g.restore();
      this.flow.forEach(p => { if (t < p.a.t0) return; const ph = ((t - p.a.t0) * p.sp + p.off) % 1; const pts = [pos[p.lane], pos[2], pos[3], pos[4], pos[5]]; const seg = ph * 4; const si = Math.min(3, Math.floor(seg)); const u = seg - si; const ok = pr(this.nodes[si + 1 < 2 ? 2 : si + 1].a, t); if (ok <= 0) return;
        const x = lerp(pts[si][0] + 95, pts[si + 1][0] - 95, u), y = lerp(pts[si][1], pts[si + 1][1], u); dot(x, y, 7, p.lane ? AMBER : CYAN, 0.9); });
    }
    this.nodes.forEach((nd, i) => { const k = pr(nd.a, t, E.outBack); if (k <= 0) return; const [x, y] = pos[i]; const c = i === 0 ? CYAN : (i === 1 ? AMBER : (i === 5 ? GREEN : VIOLET));
      panel(x - 95, y - 40, 190, 80, { a: clamp(k), c1: c, c2: c, c3: c, glow: c, r: 14 });
      txt(nd.n, x, y - 2, '30px Bebas', WHITE, { a: clamp(k), align: 'center' }); txt(nd.sub, x, y + 22, '12px MonoB', MUTED, { a: clamp(k), align: 'center' });
      if (nd.ms != null) txt(`${nd.ms.toFixed(3)} ms`, x, y + 62, '14px MonoB', c, { a: clamp(k), align: 'center' }); });
    // ladder
    this.cands.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; const y = 590 + i * 30; const sc = o.c.score / D.S05.candidates[0].score;
      txt(String(i + 1).padStart(2, '0'), 110, y + 14, '15px MonoB', MUTED, { a: k });
      g.save(); alpha(k); g.fillStyle = rgba(o.c.selected ? CYAN : MUTED, 0.85); g.fillRect(146, y + 2, 220 * sc * k, 16); g.restore();
      txt(o.c.score.toFixed(4), 376, y + 15, '13px Mono', MUTED, { a: k });
      const lab = o.c.label.split('  ').slice(1).join(' ') || o.c.chunkId; txt(trunc(lab, '14px InterSB', 520), 440, y + 15, '14px InterSB', rgba(WHITE, 0.88), { a: k }); });
    this.excl.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; txt(`EXCLUDED  ${o.c.document.split('/').pop()}  ·  context_token_budget`, 146, 893 + i * 20, '13px MonoB', rgba(AMBER, 0.9), { a: k }); });
    const kg = pr(this.gauge, t, E.outCubic);
    if (kg > 0) {
      panel(1020, 575, 790, 92, { a: kg, r: 12, c1: CYAN, c2: VIOLET, c3: GREEN });
      txt(`CONTEXT  ${fmt(counter(this.gauge, t, D.S05.contextTokens))} / ${fmt(D.S05.contextBudget)} TOKENS`, 1044, 606, '22px InterXB', WHITE, { a: kg });
      let x = 1044; const wbar = 740; g.save(); alpha(kg); g.fillStyle = rgba(DIM, 1); g.fillRect(1044, 626, wbar, 22); g.restore();
      this.segs.forEach((s, i) => { const k = pr(s.a, t, E.outCubic); const w = (s.c[1] / D.S05.contextBudget) * wbar; if (k > 0) { g.save(); alpha(k); g.fillStyle = DOC_PAL[i % DOC_PAL.length]; g.fillRect(x, 626, w * k - 2, 22); g.restore(); } x += w; });
    }
    const ka = pr(this.answer, t, E.outCubic);
    if (ka > 0) {
      panel(1020, 690, 790, 240, { a: ka, r: 12, c1: GREEN, c2: CYAN, c3: VIOLET });
      txt('ANSWER  ·  BASELINE EXTRACTIVE (NOT QUALIFYING)', 1044, 722, '15px MonoB', AMBER, { a: ka });
      const shown = this.answerWords.filter(o => t >= o.a.t0).map(o => o.w).join(' ');
      wrap(shown, '17px InterSB', 740).slice(0, 4).forEach((ln, i) => txt(ln, 1044, 752 + i * 24, '17px InterSB', rgba(WHITE, 0.92), { a: ka }));
      let cx = 1044; this.chain.forEach((c, i) => { const k = pr(c.a, t, E.outBack); if (k <= 0) return; const w = pill(c.s, cx, 900, c.c, { a: clamp(k), size: 12, padX: 8, h: 24 }); if (i < 3) txt('→', cx + w + 4, 905, '16px InterB', MUTED, { a: clamp(k) }); cx += w + 24; });
    }
    SA = save;
  }
  const kE = pr(this.phaseE, t, E.inOutCubic);
  if (kE > 0) {
    const save = SA; SA *= kE;
    const cols = [['SESSIONS', CYAN, 'per user + session · persisted · TTL'], ['COMPACTION', VIOLET, 'lear-compaction-v1 · never grows the history'], ['MEMORY', MAGENTA, 'scoped per user · cannot override evidence']];
    cols.forEach(([n, c, sub], i) => { const x = 110 + i * 575; panel(x, 330, 545, 250, { a: 1, c1: c, c2: c, c3: VIOLET }); txt(n, x + 24, 374, '34px Bebas', WHITE); txt(sub, x + 24, 398, '14px InterSB', MUTED); });
    this.turns.forEach((o, i) => { const k = pr(o.a, t, E.outBack); if (k <= 0) return; const x = 150 + i * 100, y = 470; dot(x, y, 22, o.tt.status === 'GROUNDED' ? GREEN : AMBER, clamp(k)); txt(`T${o.tt.turn}`, x, y + 44, '15px MonoB', WHITE, { a: clamp(k), align: 'center' }); txt(o.tt.note && o.tt.note.startsWith('retrieval rewrite') ? 'rewrite' : (o.tt.note || '').slice(0, 9), x, y + 64, '12px Mono', MUTED, { a: clamp(k), align: 'center' }); if (i) line(x - 78, y, x - 22, y, rgba(CYAN, 0.6), 2, clamp(k)); });
    this.comps.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; const y = 440 + i * 42, x = 710; const b = o.c.tokens_before, a2 = o.c.tokens_after;
      g.save(); alpha(k); g.fillStyle = rgba(MUTED, 0.5); g.fillRect(x, y, (b / 650) * 330, 14); g.fillStyle = VIOLET; g.fillRect(x, y + 16, (lerp(b, a2, k) / 650) * 330, 14); g.restore();
      txt(`${b} → ${Math.round(lerp(b, a2, k))}`, x + 345, y + 24, '16px MonoB', WHITE, { a: k }); });
    const km1 = pr(this.mem[0], t, E.outCubic), km2 = pr(this.mem[1], t, E.outCubic);
    if (km1 > 0) { txt('outdated note "50"  ✗', 1300, 460, '19px MonoB', RED, { a: km1 }); txt('docs say "5"  ✓ evidence wins', 1300, 490, '19px MonoB', GREEN, { a: km1 }); }
    if (km2 > 0) { txt('another user sees: 0 of your notes', 1300, 530, '16px MonoB', MUTED, { a: km2 }); }
    // guardrails table
    if (this.guards.some(o => t >= o.a.t0)) { txt('GUARDRAILS  ·  9 LIVE CHECKS', 110, 628, '30px Bebas', WHITE); txt('expected vs observed, from the recorded run', 420, 624, '15px InterSB', MUTED); }
    this.guards.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; const col = i < 5 ? 0 : 1; const x = 110 + col * 860, y = 660 + (i % 5) * 52;
      panel(x, y, 830, 44, { a: k, r: 10, c1: o.s.verdict === 'MATCH' ? GREEN : AMBER, c2: VIOLET, c3: VIOLET, lw: 1 });
      txt(trunc(o.s.name, '15px InterSB', 400), x + 16, y + 28, '15px InterSB', WHITE, { a: k });
      txt(trunc(`${o.s.observed}`, '13px Mono', 240), x + 430, y + 27, '13px Mono', MUTED, { a: k });
      pill(o.s.verdict, x + 814, y + 22, o.s.verdict === 'MATCH' ? GREEN : AMBER, { a: k, align: 'right', size: 12, h: 22 }); });
    SA = save;
  }
});

// ---- 05 measurement
scene('measure', SC.measure.t0, SC.measure.t1, function () {
  const S = 'measure', t0 = SC.measure.t0;
  this.head = reg(S, 'heading', t0 + 0.1, 0.7, 'scene');
  const tl = cue(S, 'thirteen lifecycle') - 0.4;
  this.spans = D.S04.spans.map((s, i) => ({ s, a: reg(S, 'lifecycle-span', tl + i * 0.12, 0.5, 'S04 timings.lifecycle') }));
  this.phaseB = reg(S, 'scene-transition', cue(S, 'A load test') - 0.3, 0.6, 'decor');
  const levels = D.bench; this.levels = levels;
  this.users = []; let ui = 0;
  levels.forEach((lv, li) => { for (let u = 0; u < lv.users; u++) { this.users.push({ li, u, a: reg(S, 'dummy-user', cue(S, 'A load test') + li * 0.35 + u * 0.05, 0.35, 'bench levels') }); ui++; } });
  this.pulses = []; levels.forEach((lv, li) => { for (let q = 0; q < lv.requests; q++) this.pulses.push({ li, q, a: reg(S, 'request-pulse', cue(S, 'A load test') + 0.6 + li * 0.35 + q * 0.07, 0.9, 'bench requests') }); });
  this.cells = []; levels.forEach((lv, li) => { [lv.totalMs.mean, lv.totalMs.p50, lv.totalMs.p85, lv.totalMs.p99, lv.ttftMs.p50, lv.itlMs.p50, lv.tokensPerSec.p50, lv.throughputRps].forEach((v, ci) => this.cells.push({ li, ci, v, a: reg(S, 'bench-cell', cue(S, 'time to first token') - 0.2 + li * 0.25 + ci * 0.08, 0.4, 'bench_baseline_users_1_2_5_10') })); });
  this.stamp = reg(S, 'stamp', cue(S, 'mean, p50') + 0.3, 0.5, 'bench label');
  this.rps = levels.map((lv, i) => ({ lv, a: reg(S, 'throughput-bar', cue(S, 'At ten users') - 0.3 + i * 0.15, 0.6, 'bench throughputRps') }));
  const rl = D.benchRL[0]; this.rl = Array.from({ length: rl.requests }, (_, i) => ({ ok: i % 2 === 0, a: reg(S, 'rate-limit-token', cue(S, 'admits ten') - 0.4 + i * 0.09, 0.9, 'bench_baseline_ratelimit_session2') }));
  this.phaseD = reg(S, 'scene-transition', cue(S, 'eighty questions') - 0.4, 0.6, 'decor');
  this.gold = D.golden.map(([id, split], i) => ({ id, split, a: reg(S, 'eval-case', cue(S, 'eighty questions') - 0.2 + i * 0.018, 0.4, 'golden_lear_v1.json') }));
  const P = D.runs['round-3'].proxy;
  this.metrics = [['CITATIONS', P.citation_quality], ['GROUNDEDNESS', P.groundedness], ['ANSWER RELEVANCE', P.answer_relevance], ['CONTEXT PRECISION', P.context_precision], ['CONTEXT RECALL', P.context_recall]]
    .map(([n, v], i) => ({ n, v, a: reg(S, 'metric-bar', cue(S, 'scored on citations') + i * 0.35, 0.7, 'round-3 run.json proxyScores') }));
  this.judge = reg(S, 'stamp', cue(S, 'scored on citations') + 2.0, 0.5, 'run.json judge');
  const rounds = ['round-0', 'round-1', 'round-2', 'round-3'];
  this.rcells = []; rounds.forEach((rn, ri) => D.runs[rn].cases.forEach((c, ci) => this.rcells.push({ ri, ci, ok: c[2], a: reg(S, 'round-case', cue(S, 'Three evaluate') - 0.2 + ri * 0.45 + ci * 0.006, 0.35, `${rn}/run.json`) })));
  this.points = [['R0', D.runs['round-0'].summary.correct], ['R1', D.runs['round-1'].summary.correct], ['R2', D.runs['round-2'].summary.correct], ['R3', D.runs['round-3'].summary.correct]]
    .map(([n, v], i) => ({ n, v, a: reg(S, 'round-point', cue(S, 'forty-five') - 0.2 + i * 0.4, 0.5, 'run.json summary.correct') }));
  this.test = reg(S, 'round-point', cue(S, 'forty-five') + 1.6, 0.5, 'round-0-test / round-3-test');
  this.zero = reg(S, 'stamp', cue(S, 'zero regressions') - 0.1, 0.5, 'run.json flips');
  this.hold = reg(S, 'stamp', cue(S, 'the holdout') + 0.2, 0.6, 'final-holdout/run.json');
}, function (t) {
  const S = 'measure';
  heading('03', 'MEASUREMENT', 'timed, load-tested, rate-limited, evaluated: every number from the repo evidence files', t, this.head);
  const kb = pr(this.phaseB, t, E.inOutCubic), kd = pr(this.phaseD, t, E.inOutCubic);
  if (kb < 1) {
    const save = SA; SA *= (1 - kb);
    const tot = D.S04.totalMs; const x0 = 420, wv = 1300;
    if (pr(this.spans[0].a, t) > 0) { txt(`REQUEST LIFECYCLE  ·  13 CONTIGUOUS SPANS  ·  ${tot.toFixed(1)} MS  ·  ${D.S04.correlationId}`, 110, 262, '30px Bebas', WHITE); }
    this.spans.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; const y = 290 + i * 46;
      txt(o.s.stage, 110, y + 22, '18px MonoB', rgba(WHITE, 0.9), { a: k }); txt(trunc(o.s.label, '13px InterSB', 250), 110, y + 40, '13px InterSB', MUTED, { a: k });
      const x = x0 + (o.s.startMs / tot) * wv, w = Math.max(3, (o.s.ms / tot) * wv) * k;
      g.save(); alpha(k); const gr = g.createLinearGradient(x, 0, x + w, 0); gr.addColorStop(0, CYAN); gr.addColorStop(1, VIOLET); g.fillStyle = gr; g.fillRect(x, y + 8, w, 22); g.restore();
      txt(`${o.s.ms.toFixed(3)} ms`, Math.min(x + w + 10, 1730), y + 25, '14px MonoB', CYAN, { a: k }); });
    SA = save;
  }
  if (kb > 0 && kd < 1) {
    const save = SA; SA *= kb * (1 - kd);
    txt('LOAD TEST  ·  DUMMY USERS 1 / 2 / 5 / 10', 110, 262, '30px Bebas', WHITE);
    const rowY = li => 320 + li * 62;
    this.levels.forEach((lv, li) => txt(`${lv.users} user${lv.users > 1 ? 's' : ''}`, 110, rowY(li) + 6, '16px MonoB', MUTED));
    this.users.forEach(o => { const k = pr(o.a, t, E.outBack); if (k <= 0) return; dot(210 + o.u * 26, rowY(o.li), 12, CYAN, clamp(k)); });
    const busy = this.pulses.filter(o => { const k = pr(o.a, t); return k > 0.85 && k < 1; }).length;
    g.save(); alpha(1); panel(600, 290, 70, 230, { a: 1, r: 10, c1: GREEN, c2: GREEN, c3: CYAN, glow: busy ? GREEN : null }); g.restore();
    if (busy) dot(635, 405, 40 + busy * 6, GREEN, 0.5); txt('AGENT', 635, 540, '13px MonoB', MUTED, { align: 'center' });
    this.pulses.forEach(o => { const k = pr(o.a, t, E.inOutCubic); if (k <= 0 || k >= 1) return; const lv = this.levels[o.li]; const sx = 210 + (o.q % lv.users) * 26; dot(lerp(sx, 630, k), rowY(o.li) + Math.sin(k * Math.PI) * -18, 7, AMBER, 1 - k * 0.3); });
    const heads = ['users', 'mean', 'p50', 'p85', 'p99', 'TTFT p50', 'ITL p50', 'tok/s p50', 'req/s'];
    const cx = [720, 820, 930, 1040, 1150, 1270, 1400, 1530, 1680];
    if (this.cells.some(c => t >= c.a.t0)) { heads.forEach((h, i) => txt(h, cx[i], 312, '15px MonoB', MUTED)); line(720, 322, 1790, 322, rgba(MUTED, 0.5), 1, 1); }
    this.levels.forEach((lv, li) => { if (t >= this.cells[li * 8].a.t0) txt(String(lv.users), cx[0], 352 + li * 40, '19px MonoB', WHITE); });
    this.cells.forEach(o => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; const v = o.v; const s = o.ci === 6 ? fmt(v) : (o.ci === 7 ? v.toFixed(1) : (o.ci === 5 ? v.toFixed(3) : v.toFixed(1)));
      txt(s, cx[o.ci + 1], 352 + o.li * 40, '19px MonoB', o.ci === 3 ? MAGENTA : (o.ci === 7 ? GREEN : WHITE), { a: k }); });
    txt('ms, pipeline only', 820, 520, '13px Mono', MUTED, { a: pr(this.stamp, t) });
    const ks = pr(this.stamp, t, E.outBack); if (ks > 0) pill('PIPELINE-ONLY  ·  OFFLINE BASELINE  ·  NOT MODEL LATENCY', 1790, 520, AMBER, { a: clamp(ks), align: 'right', size: 15, glow: true });
    // throughput + rate limit
    if (t >= this.rps[0].a.t0) txt('THROUGHPUT (REQ/S)', 110, 612, '26px Bebas', WHITE);
    this.rps.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; const v = o.lv.throughputRps; const h = (v / 34) * 240 * k; const x = 130 + i * 130;
      g.save(); alpha(1); const gr = g.createLinearGradient(0, 900 - h, 0, 900); gr.addColorStop(0, i === 3 ? GREEN : CYAN); gr.addColorStop(1, rgba(VIOLET, 0.4)); g.fillStyle = gr; g.fillRect(x, 900 - h, 80, h); g.restore();
      txt(v.toFixed(1), x + 40, 890 - h, '22px MonoB', i === 3 ? GREEN : WHITE, { align: 'center', a: k, glow: i === 3 ? GREEN : null }); txt(`${o.lv.users}u`, x + 40, 925, '15px MonoB', MUTED, { align: 'center' }); });
    if (t >= this.rl[0].a.t0) { txt('RATE LIMITER  ·  SAME SESSION, 20 REQUESTS', 720, 612, '26px Bebas', WHITE); g.save(); alpha(1); g.fillStyle = rgba(WHITE, 0.85); g.fillRect(1240, 650, 6, 250); g.restore(); txt('GATE', 1243, 930, '13px MonoB', MUTED, { align: 'center' }); }
    let okN = 0, blkN = 0;
    this.rl.forEach((o, i) => { const k = pr(o.a, t, E.inOutCubic); if (k <= 0) return; const sy = 670 + (i % 10) * 23;
      let x, y; if (o.ok) { x = lerp(740, 1700, k); y = lerp(sy, 690 + Math.floor(i / 2) * 20, k); } else { const kk = k < 0.5 ? k * 2 : 1; x = k < 0.5 ? lerp(740, 1225, kk) : lerp(1225, 1300, (k - 0.5) * 2); y = k < 0.5 ? sy : lerp(sy, 760 + Math.floor(i / 2) * 14, (k - 0.5) * 2); }
      if (o.ok && k >= 1) okN++; if (!o.ok && k >= 1) blkN++;
      dot(x, y, 9, o.ok ? GREEN : RED, 1); });
    if (t >= this.rl[0].a.t0 + 1.0) { txt(`ADMITTED ${this.rl.filter(o => o.ok && t >= o.a.t0 + o.a.dur).length}`, 1560, 660, '22px MonoB', GREEN); txt(`BLOCKED ${this.rl.filter(o => !o.ok && t >= o.a.t0 + o.a.dur).length}  ·  exit 7 RATE_LIMITED`, 1290, 900, '18px MonoB', RED); }
    SA = save;
  }
  if (kd > 0) {
    const save = SA; SA *= kd;
    txt('EVALUATION SET  ·  80 GOLDEN QUESTIONS', 110, 262, '30px Bebas', WHITE);
    const splitCol = { calibration: CYAN, dev: VIOLET, test: AMBER, holdout: MAGENTA };
    this.gold.forEach((o, i) => { const k = pr(o.a, t, E.outBack); if (k <= 0) return; const x = 110 + (i % 16) * 34, y = 290 + Math.floor(i / 16) * 34; g.save(); alpha(clamp(k)); g.fillStyle = rgba(splitCol[o.split], 0.85); rr(x, y, 28 * clamp(k, 0, 1.1), 28 * clamp(k, 0, 1.1), 6); g.fill(); g.restore(); });
    if (t >= this.gold[0].a.t0) { let lx = 110; [['calibration', 10], ['dev', 32], ['test', 24], ['holdout', 14]].forEach(([n, v]) => { g.save(); alpha(1); g.fillStyle = splitCol[n]; g.fillRect(lx, 476, 12, 12); g.restore(); txt(`${n} ${v}`, lx + 18, 487, '15px MonoB', MUTED); lx += 18 + measure(`${n} ${v}`, '15px MonoB') + 22; }); }
    if (t >= this.metrics[0].a.t0) txt('FIVE DIMENSIONS  ·  ROUND 3  ·  0–4 SCALE', 720, 262, '30px Bebas', WHITE);
    this.metrics.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; const y = 300 + i * 40; txt(o.n, 720, y + 18, '16px MonoB', rgba(WHITE, 0.9), { a: k });
      g.save(); alpha(k); g.fillStyle = rgba(DIM, 1); g.fillRect(960, y + 4, 600, 18); const gr = g.createLinearGradient(960, 0, 1560, 0); gr.addColorStop(0, CYAN); gr.addColorStop(1, MAGENTA); g.fillStyle = gr; g.fillRect(960, y + 4, 600 * (o.v / 4) * k, 18); g.restore();
      txt((o.v * k).toFixed(2), 1580, y + 20, '17px MonoB', WHITE, { a: k }); });
    const kj = pr(this.judge, t, E.outBack); if (kj > 0) pill('DETERMINISTIC PROXIES  ·  LLM JUDGE BLOCKED (NO OLLAMA)', 1790, 506, AMBER, { a: clamp(kj), align: 'right', size: 14, glow: true });
    if (t >= this.rcells[0].a.t0) txt('THREE EVALUATE-AND-IMPROVE ROUNDS  ·  42 CALIBRATION + DEV CASES', 110, 568, '28px Bebas', WHITE);
    this.rcells.forEach(o => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; const x = 110 + o.ri * 230 + (o.ci % 7) * 28, y = 600 + Math.floor(o.ci / 7) * 28; g.save(); alpha(k); g.fillStyle = o.ok ? GREEN : rgba(MUTED, 0.35); rr(x, y, 22, 22, 5); g.fill(); g.restore(); });
    ['R0', 'R1', 'R2', 'R3'].forEach((n, i) => { if (t >= this.rcells[i * 42].a.t0) txt(n, 110 + i * 230 + 90, 790, '18px MonoB', MUTED, { align: 'center' }); });
    const cx0 = 1060, cy0 = 900, cw = 600, ch = 280;
    if (t >= this.points[0].a.t0) { line(cx0, cy0, cx0 + cw, cy0, rgba(MUTED, 0.5), 1, 1); txt('correct (lenient)', cx0, 610, '14px MonoB', MUTED); }
    const pts = this.points.map((o, i) => ({ x: cx0 + 40 + i * 170, y: cy0 - (o.v - 0.35) / 0.35 * ch, o }));
    for (let i = 1; i < pts.length; i++) { const k = pr(pts[i].o.a, t, E.outCubic); if (k <= 0) continue; line(pts[i - 1].x, pts[i - 1].y, lerp(pts[i - 1].x, pts[i].x, k), lerp(pts[i - 1].y, pts[i].y, k), GREEN, 4, 1); }
    pts.forEach(p => { const k = pr(p.o.a, t, E.outBack); if (k <= 0) return; dot(p.x, p.y, 16, GREEN, clamp(k)); txt(`${p.o.n} ${(p.o.v * 100).toFixed(1)}%`, p.x, p.y - 22, '17px MonoB', WHITE, { a: clamp(k), align: 'center' }); });
    const ktest = pr(this.test, t, E.outCubic); if (ktest > 0) txt(`test split: ${(D.runs['round-0-test'].summary.correct * 100).toFixed(1)}% → ${(D.runs['round-3-test'].summary.correct * 100).toFixed(1)}%`, cx0 + 40, 640, '17px MonoB', AMBER, { a: ktest });
    const kz = pr(this.zero, t, E.outBack); if (kz > 0) pill('0 REGRESSIONS', 760, 820, GREEN, { a: clamp(kz), size: 20, filled: true, glow: true });
    const kh2 = pr(this.hold, t, E.outBack); if (kh2 > 0) { pill(`HOLDOUT · USED ONCE · ${(D.runs['final-holdout'].summary.correct * 100).toFixed(1)}%`, 760, 870, MAGENTA, { a: clamp(kh2), size: 18, glow: true }); }
    SA = save;
  }
});

// ---- 06 what we won't fake
scene('honest', SC.honest.t0, SC.honest.t1, function () {
  const S = 'honest';
  this.title = reg(S, 'heading', cue(S, 'refuse to fake') - 0.3, 0.7, 'scene');
  this.blocked = reg(S, 'stamp', cue(S, 'Ollama could not') - 0.1, 0.6, 'audit_environment.md');
  this.real = ['Docs: 340 / 340 claims · 11 / 11 checks', 'Index: 355 chunks · 19 docs', 'Guardrails: 7 MATCH · 2 LIMITATION', 'Eval: 80 questions · 3 rounds · 0 regressions', 'Bench: 1–10 users · pipeline-only'].map((s, i) => ({ s, a: reg(S, 'evidence-row', cue(S, 'offline baseline') - 0.2 + i * 0.22, 0.4, 'requirements_matrix.md') }));
  this.block = ['Local LLM answers (qwen2.5:1.5b)', 'Semantic embeddings (nomic-embed-text)', 'LLM judge scores (llama3.2:1b)', 'Model TTFT · ITL · tokens per second', 'Shared-memory import (no source supplied)'].map((s, i) => ({ s, a: reg(S, 'blocked-row', cue(S, 'not qualifying') - 0.2 + i * 0.22, 0.4, 'requirements_matrix.md') }));
  this.proxy = reg(S, 'chip', cue(S, 'deterministic proxies') - 0.2, 0.5, 'run.json judge');
  this.phase2 = reg(S, 'scene-transition', cue(S, 'models are configured') - 0.5, 0.6, 'decor');
  this.models = [['LLM', 'qwen2.5:1.5b', 'cap ≤ 4B params'], ['EMBEDDER', 'nomic-embed-text', 'cap ≤ 200M params'], ['JUDGE', 'llama3.2:1b', 'separate from the LLM']].map((m, i) => ({ m, a: reg(S, 'model-card', cue(S, 'models are configured') - 0.1 + i * 0.25, 0.5, 'settings + doctor') }));
  this.doctor = reg(S, 'chip', cue(S, 'doctor enforces') - 0.1, 0.5, 'doctor');
  const short = ['Start Ollama on 127.0.0.1', 'Pull + verify the 3 models via /api/show', 'lear doctor → exit 0', 'index rebuild --full (model-backed)', 'eval run --suite calibration,dev --judge on', 'Recalibrate abstention on cal/dev only', 'Score test once · new versioned check set', 'bench --users 1,2,5 with model streaming'];
  this.steps = short.map((s, i) => ({ s, a: reg(S, 'runbook-step', cue(S, 'eight documented') - 0.6 + i * 0.16, 0.4, 'S11 readiness steps') }));
}, function (t) {
  const kt = pr(this.title, t, E.outCubic);
  if (kt > 0) {
    const gl = (1 - kt) * 18; const f = '96px Bebas';
    if (kt < 1) { g.save(); g.globalCompositeOperation = 'lighter'; txt("WHAT WE WON'T FAKE", 110 + gl, 170, f, rgba(RED, 0.6), {}); txt("WHAT WE WON'T FAKE", 110 - gl, 170, f, rgba(CYAN, 0.5), {}); g.restore(); }
    txt("WHAT WE WON'T FAKE", 110, 170, f, WHITE, { a: kt, glow: RED, blur: 24 });
  }
  const kb = pr(this.blocked, t, E.outBack);
  if (kb > 0) {
    panel(110, 205, 1700, 100, { a: clamp(kb), c1: RED, c2: AMBER, c3: RED, glow: RED });
    txt('OLLAMA COULD NOT BE INSTALLED IN THE RECORDING SANDBOX', 140, 250, '30px InterXB', WHITE, { a: clamp(kb) });
    txt('its download host is unreachable from the sandbox  ·  no local LLM, embedder or judge ran  ·  live model path: exit 6', 140, 282, '17px InterSB', rgba(WHITE, 0.75), { a: clamp(kb) });
    g.save(); alpha(clamp(kb)); g.translate(1640, 255); g.rotate(-0.08); const sc = lerp(1.6, 1, clamp(kb)); g.scale(sc, sc); pill('BLOCKED', 0, 0, RED, { align: 'center', size: 30, filled: true, glow: true }); g.restore();
  }
  const p2 = pr(this.phase2, t, E.inOutCubic);
  if (p2 < 1) {
    const save = SA; SA *= (1 - p2);
    if (t >= this.real[0].a.t0) { panel(110, 340, 830, 380, { c1: GREEN, c2: CYAN, c3: GREEN }); txt('MEASURED  ·  OFFLINE BASELINE  ·  NOT QUALIFYING', 140, 386, '24px Bebas', GREEN); }
    this.real.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; txt('✓', 140, 440 + i * 54, '24px InterB', GREEN, { a: k, glow: GREEN, blur: 10 }); txt(o.s, 176, 440 + i * 54, '22px InterSB', WHITE, { a: k }); });
    if (t >= this.block[0].a.t0) { panel(980, 340, 830, 380, { c1: RED, c2: AMBER, c3: RED }); txt('BLOCKED  ·  NEEDS OLLAMA', 1010, 386, '24px Bebas', RED); }
    this.block.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; txt('✗', 1010, 440 + i * 54, '24px InterB', RED, { a: k, glow: RED, blur: 10 }); txt(o.s, 1046, 440 + i * 54, '22px InterSB', WHITE, { a: k }); });
    const kp = pr(this.proxy, t, E.outBack); if (kp > 0) pill('JUDGE SCORES SHOWN EARLIER = DETERMINISTIC PROXIES, NOT AN LLM JUDGE', W / 2, 780, AMBER, { a: clamp(kp), align: 'center', size: 17, glow: true });
    SA = save;
  }
  if (p2 > 0) {
    const save = SA; SA *= p2;
    this.models.forEach((o, i) => { const k = pr(o.a, t, E.outBack); if (k <= 0) return; const x = 110 + i * 575; panel(x, 340, 545, 150, { a: clamp(k), c1: VIOLET, c2: CYAN, c3: VIOLET });
      txt(o.m[0], x + 24, 384, '26px Bebas', MUTED, { a: clamp(k) }); txt(o.m[1], x + 24, 426, '32px MonoXB', WHITE, { a: clamp(k) }); txt(o.m[2], x + 24, 462, '16px InterSB', CYAN, { a: clamp(k) });
      pill('UNVERIFIED UNTIL DOCTOR PASSES', x + 521, 384, AMBER, { a: clamp(k), align: 'right', size: 11, h: 20 }); });
    const kd = pr(this.doctor, t, E.outBack); if (kd > 0) pill('lear doctor enforces the size caps from Ollama /api/show metadata', W / 2, 525, CYAN, { a: clamp(kd), align: 'center', size: 16 });
    if (t >= this.steps[0].a.t0) txt('QUALIFICATION RUNBOOK  ·  8 DOCUMENTED STEPS', 110, 590, '30px Bebas', WHITE);
    this.steps.forEach((o, i) => { const k = pr(o.a, t, E.outCubic); if (k <= 0) return; const col = i < 4 ? 0 : 1, x = 110 + col * 860, y = 620 + (i % 4) * 72;
      panel(x, y, 830, 60, { a: k, r: 12, c1: CYAN, c2: VIOLET, c3: VIOLET, lw: 1 });
      txt(String(i + 1), x + 30, y + 40, '30px Bebas', CYAN, { a: k, align: 'center' }); txt(o.s, x + 62, y + 38, '20px InterSB', WHITE, { a: k });
      pill('TO DO', x + 812, y + 30, MUTED, { a: k, align: 'right', size: 11, h: 20 }); });
    SA = save;
  }
});

// ---- 07 run it
scene('outro', SC.outro.t0, SC.outro.t1, function () {
  const S = 'outro';
  this.title = reg(S, 'heading', cue(S, 'Everything is runnable') - 0.2, 0.6, 'scene');
  this.cmd = '$ python3 -m ragformation lear demo replay';
  this.term = reg(S, 'panel', cue(S, 'One command') - 0.3, 0.5, 'cli');
  this.chars = [...this.cmd].map((ch, i) => reg(S, 'typed-char', cue(S, 'One command') + i * 0.035, 0.05, 'demo replay'));
  this.out = reg(S, 'callout', cue(S, 'One command') + this.cmd.length * 0.035 + 0.3, 0.5, 'replay_plain.txt');
  this.reqs = D.requirements.map((r, i) => ({ r, a: reg(S, 'requirement-tile', cue(S, 'One command') + 2.2 + i * 0.09, 0.4, 'requirements_matrix.md') }));
  this.score = reg(S, 'stamp', cue(S, 'One command') + 2.2 + 25 * 0.09 + 0.2, 0.5, 'requirements_matrix.md totals');
  this.links = [['CODE + DOCS', 'one-click ZIP of the repository branch', CYAN], ['THIS VIDEO', '4K MP4 · 1080p MP4 · in the repo', MAGENTA], ['EVIDENCE', 'docs/lear_agent · transcripts · run.json', GREEN]].map((l, i) => ({ l, a: reg(S, 'link-card', cue(S, 'one click away') - 0.2 + i * 0.25, 0.5, 'repo') }));
  this.phaseF = reg(S, 'scene-transition', cue(S, 'Lear Local:') - 0.5, 0.7, 'decor');
  this.letters = [...'LEAR LOCAL'].map((ch, i) => (ch === ' ' ? null : reg(S, 'title-letter', cue(S, 'Lear Local:') - 0.2 + i * 0.05, 0.6, 'title')));
  this.tags = [['EVERY CLAIM TRACED', CYAN, cue(S, 'every claim traced')], ['EVERY NUMBER MEASURED', MAGENTA, cue(S, 'every claim traced') + 0.9], ['NOTHING FAKED', AMBER, cue(S, 'nothing faked')]].map(([s, c, tt]) => ({ s, c, a: reg(S, 'tagline-chip', tt - 0.1, 0.5, 'principle') }));
  const r = rng(99); this.burst = Array.from({ length: 140 }, () => ({ ang: r() * 6.283, sp: 200 + r() * 700, c: [CYAN, VIOLET, MAGENTA, AMBER][Math.floor(r() * 4)], a: reg(S, 'burst-particle', cue(S, 'Lear Local:') - 0.1 + r() * 0.3, 2.4 + r() * 1.2, 'decor') }));
  this.count = reg(S, 'callout', cue(S, 'nothing faked') + 1.2, 0.8, 'animation manifest');
}, function (t) {
  const S = 'outro';
  const pf = pr(this.phaseF, t, E.inOutCubic);
  if (pf < 1) {
    const save = SA; SA *= (1 - pf);
    const kt = pr(this.title, t, E.outCubic); if (kt > 0) txt('EVERYTHING IS RUNNABLE', 110, 168 - (1 - kt) * 16, '96px Bebas', WHITE, { a: kt, glow: CYAN, blur: 20 });
    const km = pr(this.term, t, E.outCubic);
    if (km > 0) {
      panel(110, 210, 1700, 110, { a: km, r: 12 }); const n = this.chars.filter(a => t >= a.t0).length;
      txt(this.cmd.slice(0, n), 140, 258, '26px MonoB', WHITE, { a: km });
      const ko = pr(this.out, t, E.outCubic); if (ko > 0) txt('✓ bundle sha256 verified  ·  12 scenes  ·  RECORDED REPLAY  ·  exit 0  ·  no Ollama needed', 140, 296, '18px MonoB', GREEN, { a: ko });
    }
    if (t >= this.reqs[0].a.t0) txt('TAKE-HOME REQUIREMENTS MATRIX  ·  25 ROWS', 110, 380, '30px Bebas', WHITE);
    const stc = { PASS: GREEN, PARTIAL: AMBER, BLOCKED: RED, FAIL: '#FF2020' };
    this.reqs.forEach((o, i) => { const k = pr(o.a, t, E.outBack); if (k <= 0) return; const x = 110 + (i % 5) * 172, y = 405 + Math.floor(i / 5) * 92; const c = stc[o.r[2]];
      panel(x, y, 162, 82, { a: clamp(k), r: 10, c1: c, c2: c, c3: VIOLET, lw: 1.2 });
      txt(String(o.r[0]).padStart(2, '0'), x + 12, y + 30, '24px Bebas', c, { a: clamp(k) }); pill(o.r[2], x + 150, y + 20, c, { a: clamp(k), align: 'right', size: 10, h: 18, padX: 6 });
      wrap(o.r[1], '11px InterSB', 134).slice(0, 2).forEach((ln, li) => txt(trunc(ln, '11px InterSB', 138), x + 12, y + 52 + li * 15, '11px InterSB', rgba(WHITE, 0.85), { a: clamp(k) })); });
    const ks = pr(this.score, t, E.outBack); if (ks > 0) { pill('9 PASS', 110, 890, GREEN, { a: clamp(ks), size: 16, filled: true }); pill('10 PARTIAL', 220, 890, AMBER, { a: clamp(ks), size: 16, filled: true }); pill('6 BLOCKED', 370, 890, RED, { a: clamp(ks), size: 16, filled: true }); pill('0 FAIL', 510, 890, WHITE, { a: clamp(ks), size: 16 }); }
    this.links.forEach((o, i) => { const k = pr(o.a, t, E.outBack); if (k <= 0) return; const y = 405 + i * 160; panel(1010, y, 800, 140, { a: clamp(k), c1: o.l[2], c2: VIOLET, c3: o.l[2], glow: o.l[2] });
      txt(o.l[0], 1040, y + 54, '40px Bebas', WHITE, { a: clamp(k) }); txt(o.l[1], 1040, y + 86, '18px InterSB', MUTED, { a: clamp(k) }); txt('github.com/aviunion1348-cloud/rag-formation', 1040, y + 116, '16px MonoB', o.l[2], { a: clamp(k) }); });
    SA = save;
  }
  if (pf > 0) {
    const save = SA; SA *= pf; const cx = W / 2;
    this.burst.forEach(b => { const k = pr(b.a, t, E.outExpo); if (k <= 0 || k >= 1) return; const d = b.sp * k; dot(cx + Math.cos(b.ang) * d, 470 + Math.sin(b.ang) * d * 0.8, 8, b.c, 1 - pr(b.a, t)); });
    const f = '230px Bebas'; const total = measure('LEAR LOCAL', f); let x = cx - total / 2;
    [...'LEAR LOCAL'].forEach((ch, i) => { const w = measure(ch, f); const a = this.letters[i]; if (a) { const k = pr(a, t, E.outBack), k1 = pr(a, t); txt(ch, x, 560 + (1 - k) * 70, f, WHITE, { a: k1, glow: CYAN, blur: 36 }); } x += w; });
    const tw = this.tags.map(o => measure(o.s, '20px MonoB') + 24 + 24); let tx = cx - (tw.reduce((a, b) => a + b, 0) - 24) / 2;
    this.tags.forEach((o, i) => { const k = pr(o.a, t, E.outBack); if (k > 0) pill(o.s, tx, 660, o.c, { a: clamp(k), size: 20, glow: true }); tx += tw[i]; });
    const kc = pr(this.count, t, E.outCubic);
    if (kc > 0) {
      const dataN = ANIMS.filter(a => a.kind === 'data').length;
      txt(`${fmt(ANIMS.length)} timeline animations  ·  ${fmt(dataN)} bound to real repo data  ·  manifest: docs/lear_agent/demo_video/walkthrough_animation_manifest.json`, cx, 760, '16px MonoB', MUTED, { a: kc, align: 'center' });
      txt('python3 -m ragformation lear demo replay', cx, 800, '22px MonoB', CYAN, { a: kc, align: 'center' });
    }
    SA = save;
  }
});

SCENES.forEach(s => s.build.call(s));
SCENES.forEach(s => { s.vis = vis(s); });

function frame(t) {
  g.setTransform(SCALE, 0, 0, SCALE, 0, 0); g.globalAlpha = 1; g.globalCompositeOperation = 'source-over';
  background(t); vignette();
  for (const s of SCENES) { const a = s.vis(t); if (a > 0.001) { SA = a; s.draw.call(s, t); } }
  SA = 1; g.globalAlpha = 1; g.globalCompositeOperation = 'source-over';
  sweeps(t); hud(t); subtitles(t);
}

function writeManifest(file) {
  const byType = {}, byScene = {}, byKind = {};
  ANIMS.forEach(a => { byType[a.type] = (byType[a.type] || 0) + 1; byScene[a.scene] = (byScene[a.scene] || 0) + 1; byKind[a.kind] = (byKind[a.kind] || 0) + 1; });
  fs.writeFileSync(file, JSON.stringify({ film: 'lear-local-walkthrough', fps: FPS, duration: TOTAL, total: ANIMS.length, byKind, byScene, byType,
    note: 'Each entry is one element with its own timed motion on the film timeline. kind=data: the element encodes a real value from the repository evidence (src); kind=text: typography/subtitles; kind=decor: decorative motion.',
    animations: ANIMS }, null, 0));
}

async function main() {
  writeManifest(process.env.MANIFEST || '/tmp/vfxbuild/walkthrough_animation_manifest.json');
  const stills = process.env.STILLS;
  if (stills) {
    fs.mkdirSync('/tmp/vfxbuild/stills', { recursive: true });
    for (const s of stills.split(',')) { const t = Number(s); frame(t); fs.writeFileSync(`/tmp/vfxbuild/stills/t${String(t).replace('.', '_')}.png`, canvas.toBuffer('image/png')); }
    console.log(JSON.stringify({ stills: stills.split(',').length, animations: ANIMS.length }));
    return;
  }
  const out = process.env.OUT || '/tmp/vfxbuild/walkthrough_4k_video.mp4';
  const start = Number(process.env.START || 0), end = Number(process.env.END || plan.frames);
  const ffArgs = ['-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', `${W * SCALE}x${H * SCALE}`, '-r', String(FPS), '-i', '-', '-an',
    '-c:v', 'libx264', '-preset', process.env.PRESET || 'faster', '-tune', 'animation', '-crf', process.env.CRF || '21', '-maxrate', process.env.MAXRATE || '1900k', '-bufsize', process.env.BUFSIZE || '3800k',
    '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-level', SCALE >= 2 ? '5.1' : '4.1', '-g', String(FPS * 2), '-movflags', '+faststart', out];
  const ff = spawn(process.env.FFMPEG, ffArgs, { stdio: ['pipe', 'inherit', 'inherit'] });
  const t0 = Date.now();
  for (let f = start; f < end; f++) {
    frame(f / FPS);
    const buf = Buffer.from(canvas.data());
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if (f % 96 === 0) console.log(JSON.stringify({ frame: f, of: end, fps: +((f - start + 1) / ((Date.now() - t0) / 1000)).toFixed(2) }));
  }
  ff.stdin.end();
  const code = await new Promise(r => ff.on('close', r));
  console.log(JSON.stringify({ done: true, code, seconds: (Date.now() - t0) / 1000, animations: ANIMS.length }));
}
main().catch(e => { console.error(e); process.exit(1); });
