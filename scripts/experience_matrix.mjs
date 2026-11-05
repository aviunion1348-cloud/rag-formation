#!/usr/bin/env node
/**
 * Experience renderer + accessibility matrix (Stage H).
 *
 * A headless sandbox has no GPU, so this does **not** claim a physical-GPU pass.
 * What it does claim, and measures, is that every renderer path the page can
 * take is exercised against the real modules:
 *
 *   1. WebGL2 available              -> mode "webgl", 16 scenes render
 *   2. WebGL2 missing, WebGL1 there  -> mode "webgl" (same path, older context)
 *   3. no WebGL, canvas 2D available -> mode "canvas2d", 16 scenes render
 *   4. no context at all             -> mode "none", page still boots and the
 *                                       DOM transcript still carries the story
 *
 * plus the accessibility contract: transcript text for every scene, the scene
 * captions a screen reader reads, keyboard-reachable controls, reduced-motion
 * and forced-colour handling, and no third-party requests.
 *
 * Usage: node scripts/experience_matrix.mjs [--json]
 */

import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

// jsdom is resolved relative to *this file*, so the runner copies this script
// into a scratch directory that has node_modules and points RAG_ROOT back here.
const ROOT = process.env.RAG_ROOT
  ? path.resolve(process.env.RAG_ROOT)
  : path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EXPERIENCE = path.join(ROOT, 'ragformation/web/experience');

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  if (!process.argv.includes('--json')) console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
}

/** A WebGL context that answers every call the renderer makes, plausibly. */
function fakeGL(kind) {
  const constants = {
    COMPILE_STATUS: 0x8b81,
    LINK_STATUS: 0x8b82,
    VERTEX_SHADER: 0x8b31,
    FRAGMENT_SHADER: 0x8b30,
    ARRAY_BUFFER: 0x8892,
    STATIC_DRAW: 0x88e4,
    POINTS: 0x0000,
    FLOAT: 0x1406,
    COLOR_BUFFER_BIT: 0x4000,
    DEPTH_BUFFER_BIT: 0x0100,
    BLEND: 0x0be2,
    SRC_ALPHA: 0x0302,
    ONE_MINUS_SRC_ALPHA: 0x0303,
  };
  const counters = { drawArrays: 0, bufferData: 0, calls: 0 };
  const handler = {
    get(target, prop) {
      if (prop in constants) return constants[prop];
      if (prop in target) return target[prop];
      if (prop === 'drawArrays') {
        return () => {
          counters.drawArrays += 1;
        };
      }
      if (prop === 'bufferData') {
        return () => {
          counters.bufferData += 1;
        };
      }
      if (prop === 'getShaderParameter' || prop === 'getProgramParameter') return () => true;
      if (prop === 'getShaderInfoLog' || prop === 'getProgramInfoLog') return () => '';
      if (prop === 'getUniformLocation') return () => ({ uniform: true });
      if (prop === 'getAttribLocation') return () => 0;
      if (prop === 'getExtension') return () => null;
      if (prop === 'getParameter') return () => 4096;
      if (prop === 'getShaderPrecisionFormat') return () => ({ precision: 23, rangeMin: 127, rangeMax: 127 });
      if (prop === 'canvas') return target.canvas;
      if (prop === 'drawingBufferWidth' || prop === 'drawingBufferHeight') return 800;
      return () => {
        counters.calls += 1;
        return {};
      };
    },
  };
  const gl = new Proxy({ canvas: null, __kind: kind, counters }, handler);
  return gl;
}

function fake2d() {
  const counters = { fill: 0, stroke: 0, fillText: 0, gradient: 0 };
  const ctx = new Proxy(
    {},
    {
      get(target, prop) {
        if (prop in target) return target[prop];
        if (prop === 'fillRect' || prop === 'rect' || prop === 'arc')
          return () => {
            counters.fill += 1;
          };
        if (prop === 'stroke' || prop === 'moveTo' || prop === 'lineTo')
          return () => {
            counters.stroke += 1;
          };
        if (prop === 'fillText')
          return () => {
            counters.fillText += 1;
          };
        if (prop === 'createLinearGradient' || prop === 'createRadialGradient')
          return () => {
            counters.gradient += 1;
            return { addColorStop: () => {} };
          };
        if (prop === 'canvas') return target.canvas;
        if (prop === 'measureText') return () => ({ width: 42 });
        if (
          [
            'fillStyle',
            'strokeStyle',
            'lineWidth',
            'globalAlpha',
            'font',
            'textAlign',
            'textBaseline',
            'shadowBlur',
            'shadowColor',
            'lineCap',
            'lineJoin',
          ].includes(prop)
        )
          return target[prop];
        return () => {};
      },
      set(target, prop, value) {
        target[prop] = value;
        return true;
      },
    },
  );
  return { ctx, counters };
}

async function renderCase({ name, gl2, gl1, ctx2d }) {
  const dom = new JSDOM('<!doctype html><canvas id="stage" width="800" height="450"></canvas>', {
    pretendToBeVisual: true,
    url: 'http://localhost/experience/',
  });
  const canvas = dom.window.document.getElementById('stage');
  const gl2Context = gl2 ? fakeGL('webgl2') : null;
  const gl1Context = gl1 ? fakeGL('webgl') : null;
  const two = ctx2d ? fake2d() : null;
  canvas.getContext = (kind) => {
    if (kind === 'webgl2') return gl2Context;
    if (kind === 'webgl') return gl1Context;
    if (kind === '2d') return two ? two.ctx : null;
    return null;
  };
  // jsdom exposes clientWidth/clientHeight as getters; define them so the
  // renderer can size its drawing buffer the way it does in a browser.
  Object.defineProperty(canvas, 'clientWidth', { value: 800, configurable: true });
  Object.defineProperty(canvas, 'clientHeight', { value: 450, configurable: true });
  Object.defineProperty(canvas, 'width', { value: 800, writable: true, configurable: true });
  Object.defineProperty(canvas, 'height', { value: 450, writable: true, configurable: true });

  // The experience modules are browser modules: they read `window` directly, so
  // the jsdom window becomes the global for the duration of the case.
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
  globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
  const moduleUrl = pathToFileURL(path.join(EXPERIENCE, 'js/renderer.js')).href;
  const { createRenderer } = await import(moduleUrl);
  const renderer = createRenderer(canvas, { quality: 'high' });
  const modes = [renderer.mode];
  const errors = [];
  let rendered = 0;
  const scenes = (await import(pathToFileURL(path.join(EXPERIENCE, 'js/scenes.js')).href)).SCENES;
  for (const scene of scenes) {
    try {
      renderer.setScene(scene.id, { instant: true });
      renderer.render(16);
      rendered += 1;
    } catch (error) {
      errors.push(`${scene.id}: ${error.message}`);
    }
  }
  const gl = gl2Context || gl1Context;
  const draws = gl ? gl.counters.drawArrays : 0;
  const twoDee = two ? two.counters.fill + two.counters.stroke : 0;
  renderer.dispose();
  check(`${name}: renderer reports a mode`, Boolean(modes[0]), modes[0]);
  check(`${name}: every scene renders without throwing`, errors.length === 0, errors.slice(0, 2).join(' | '));
  check(`${name}: scenes rendered`, rendered === scenes.length, `${rendered}/${scenes.length}`);
  if (gl2 || gl1) {
    check(`${name}: GPU path issues draw calls`, draws > 0, `${draws} drawArrays`);
  } else if (ctx2d) {
    check(`${name}: canvas2d path issues draw operations`, twoDee > 0, `${twoDee} ops`);
  } else {
    check(`${name}: no-context path degrades without drawing`, draws === 0 && twoDee === 0, 'transcript only');
  }
  dom.window.close();
  return modes[0];
}

async function accessibilityChecks() {
  const html = readFileSync(path.join(EXPERIENCE, 'index.html'), 'utf8');
  const css = readFileSync(path.join(EXPERIENCE, 'styles.css'), 'utf8');
  const scenes = (await import(pathToFileURL(path.join(EXPERIENCE, 'js/scenes.js')).href)).SCENES;
  const timeline = await import(pathToFileURL(path.join(EXPERIENCE, 'js/timeline.js')).href);

  check('sixteen scenes are defined', scenes.length === 16, `${scenes.length}`);
  check(
    'every scene carries narration text for the transcript',
    scenes.every(
      (scene) =>
        typeof scene.caption === 'string' &&
        scene.caption.trim().length > 40 &&
        typeof scene.detail === 'string' &&
        scene.detail.trim().length > 40,
    ),
    'caption + detail per scene',
  );
  check(
    'every scene carries a caption and a title',
    scenes.every((scene) => scene.caption && scene.title && scene.title.trim().length > 0),
  );
  const transcript = timeline.transcript(scenes);
  check('transcript lists every scene', transcript.length === scenes.length, `${transcript.length}`);
  check(
    'transcript lines are text, not markup',
    transcript.every((line) => line && typeof line.caption === 'string' && !/[<>]/.test(line.caption + line.detail)),
  );
  check(
    'canvas is decorative and the transcript carries the meaning',
    /<canvas[^>]+aria-hidden="true"/i.test(html),
    'canvas aria-hidden',
  );
  check('page announces updates politely', /aria-live="polite"/i.test(html));
  check(
    'page exposes the transcript as a list to assistive tech',
    /<ol[^>]*id="transcript-list"/i.test(html) && /aria-labelledby="transcript-title"/i.test(html),
    'ordered list inside a labelled section',
  );
  check('page honours prefers-reduced-motion in CSS', /prefers-reduced-motion/.test(css));
  check('page honours forced colours', /forced-colors/.test(css));
  check('page keeps a visible focus ring', /:focus-visible/.test(css));
  check('page loads no third-party script or style', !/(src|href)="https?:\/\//i.test(html));
  check('page documents its own fallback', /fallback|transcript/i.test(html));
}

const modeA = await renderCase({ name: 'webgl2', gl2: true, gl1: false, ctx2d: true });
const modeB = await renderCase({ name: 'webgl1', gl2: false, gl1: true, ctx2d: true });
const modeC = await renderCase({ name: 'canvas2d', gl2: false, gl1: false, ctx2d: true });
const modeD = await renderCase({ name: 'no-context', gl2: false, gl1: false, ctx2d: false });
await accessibilityChecks();

check('webgl2 selects the GPU path', modeA === 'webgl', modeA);
check('webgl1 also selects the GPU path', modeB === 'webgl', modeB);
check('missing WebGL falls back to canvas2d', modeC === 'canvas2d', modeC);
check('missing every context degrades to none', modeD === 'none', modeD);

const failed = results.filter((result) => !result.ok);
if (process.argv.includes('--json')) {
  console.log(JSON.stringify({ checks: results.length, failed: failed.length, results }, null, 2));
} else {
  console.log(`\nexperience matrix: ${results.length - failed.length}/${results.length} checks passed`);
  console.log('physical GPU: NOT verified in this sandbox (no /dev/dri, no nvidia-smi) — blocked, not claimed');
}
process.exit(failed.length === 0 ? 0 : 1);
