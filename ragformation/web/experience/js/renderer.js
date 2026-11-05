// Particle renderer for the S3 Experience.
//
// Design constraints that shape this file:
//   * no third-party 3D library (the console must run offline, from the package);
//   * every visual is procedural and seeded, so demo mode is deterministic;
//   * quality is adaptive (particle count + pixel ratio) because the experience
//     has to stay smooth on integrated graphics;
//   * a canvas-2D fallback exists so the story is still told when WebGL is
//     unavailable, and `dispose()` frees every GPU object it created.

import { PALETTE, SCENES } from "./scenes.js";

const BASE_PARTICLES = 2600;

/* ------------------------------------------------------------------ helpers */
function mulberry32(seed) {
  let a = seed >>> 0;
  return function random() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

const mat4 = {
  identity() {
    return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  },
  perspective(out, fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2);
    out[0] = f / aspect; out[1] = 0; out[2] = 0; out[3] = 0;
    out[4] = 0; out[5] = f; out[6] = 0; out[7] = 0;
    out[8] = 0; out[9] = 0; out[10] = (far + near) / (near - far); out[11] = -1;
    out[12] = 0; out[13] = 0; out[14] = (2 * far * near) / (near - far); out[15] = 0;
    return out;
  },
  lookAt(out, eye, center, up = [0, 1, 0]) {
    let zx = eye[0] - center[0], zy = eye[1] - center[1], zz = eye[2] - center[2];
    let len = Math.hypot(zx, zy, zz) || 1;
    zx /= len; zy /= len; zz /= len;
    let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
    len = Math.hypot(xx, xy, xz) || 1;
    xx /= len; xy /= len; xz /= len;
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    out[0] = xx; out[1] = yx; out[2] = zx; out[3] = 0;
    out[4] = xy; out[5] = yy; out[6] = zy; out[7] = 0;
    out[8] = xz; out[9] = yz; out[10] = zz; out[11] = 0;
    out[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
    out[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
    out[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
    out[15] = 1;
    return out;
  },
  multiply(out, a, b) {
    for (let i = 0; i < 4; i += 1) {
      for (let j = 0; j < 4; j += 1) {
        out[i * 4 + j] =
          a[i * 4] * b[j] + a[i * 4 + 1] * b[4 + j] + a[i * 4 + 2] * b[8 + j] + a[i * 4 + 3] * b[12 + j];
      }
    }
    return out;
  },
};

/* --------------------------------------------------------------- formations */
// Each formation fills `count` particles. Positions are roughly in [-4, 4].
const FORMATIONS = {
  dust(count, random, options) {
    const out = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const r = options.spread * Math.cbrt(random());
      const theta = random() * Math.PI * 2;
      const phi = Math.acos(2 * random() - 1);
      let x = r * Math.sin(phi) * Math.cos(theta);
      let y = r * Math.sin(phi) * Math.sin(theta) * 0.6;
      let z = r * Math.cos(phi);
      const centre = Math.hypot(x, y, z);
      if (centre < options.hole) {
        x *= 1.9; y *= 1.9; z *= 1.9; // hollow centre: the missing knowledge
      }
      out.set([x, y, z], i * 3);
    }
    return out;
  },

  cards(count, random, options) {
    const out = new Float32Array(count * 3);
    const cards = options.cards;
    for (let i = 0; i < count; i += 1) {
      const card = i % cards;
      const angle = (card / cards) * Math.PI * 2;
      const radius = options.radius + (card % 2 === 0 ? 0.15 : -0.15);
      const cx = Math.cos(angle) * radius;
      const cz = Math.sin(angle) * radius;
      const cy = Math.sin(card * 1.7) * 0.35;
      const u = random() - 0.5;
      const v = random() - 0.5;
      const x = cx + u * 0.92;
      const y = cy + v * 1.18;
      const z = cz + u * 0.05;
      out.set([x, y, z], i * 3);
    }
    return out;
  },

  rows(count, random, options) {
    const out = new Float32Array(count * 3);
    const rows = options.rows;
    for (let i = 0; i < count; i += 1) {
      const row = i % rows;
      const y = (row / (rows - 1) - 0.5) * 3.1;
      const indent = row % 5 === 0 ? 0.0 : row % 3 === 0 ? 0.5 : 0.25;
      const width = (options.width - indent) * (0.55 + 0.45 * random());
      const x = -options.width / 2 + indent + width * random();
      out.set([x, y, (random() - 0.5) * 0.12], i * 3);
    }
    return out;
  },

  blocks(count, random, options) {
    const out = new Float32Array(count * 3);
    const blocks = options.blocks;
    const perBlock = Math.ceil(count / blocks);
    for (let i = 0; i < count; i += 1) {
      const block = Math.min(blocks - 1, Math.floor(i / perBlock));
      const cx = (block - (blocks - 1) / 2) * (1.05 + options.gap);
      const local = i % perBlock;
      const rows = 9;
      const row = Math.floor(local / 8) % rows;
      const col = local % 8;
      out.set(
        [
          cx + (col / 7 - 0.5) * 0.92,
          (row / (rows - 1) - 0.5) * 1.7 + (random() - 0.5) * 0.03,
          (random() - 0.5) * 0.16,
        ],
        i * 3,
      );
    }
    return out;
  },

  torus(count, random, options) {
    const out = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const t = random() * Math.PI * 2 * options.turns;
      const theta = t;
      const phi = random() * Math.PI * 2;
      const tube = options.tube * (0.35 + 0.65 * Math.sqrt(random()));
      const x = (options.radius + tube * Math.cos(phi)) * Math.cos(theta);
      const z = (options.radius + tube * Math.cos(phi)) * Math.sin(theta);
      const y = tube * Math.sin(phi) * 0.9;
      out.set([x, y, z], i * 3);
    }
    return out;
  },

  lattice(count, random, options) {
    const out = new Float32Array(count * 3);
    const n = Math.max(4, Math.round(Math.cbrt(count)));
    const spacing = options.spacing;
    const drift = options.drift || 0;
    for (let i = 0; i < count; i += 1) {
      const ix = i % n;
      const iy = Math.floor(i / n) % n;
      const iz = Math.floor(i / (n * n)) % n;
      const jitter = drift ? (random() - 0.5) * drift : 0;
      out.set(
        [
          (ix - (n - 1) / 2) * spacing + jitter * 0.2,
          (iy - (n - 1) / 2) * spacing + jitter * 0.2,
          (iz - (n - 1) / 2) * spacing + jitter * 0.2,
        ],
        i * 3,
      );
    }
    return out;
  },

  beam(count, random, options) {
    const out = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const t = random();
      const radius = options.radius * (1 - t * 0.55) * Math.sqrt(random());
      const angle = random() * Math.PI * 2;
      out.set(
        [
          Math.cos(angle) * radius,
          (t - 0.5) * options.length * 0.9,
          Math.sin(angle) * radius,
        ],
        i * 3,
      );
    }
    return out;
  },

  branches(count, random, options) {
    const out = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const arm = i % options.arms;
      const sign = arm === 0 ? -1 : 1;
      const t = random();
      const spread = options.spread * Math.sqrt(random()) * t;
      const angle = random() * Math.PI * 2;
      out.set(
        [
          sign * (0.45 + t * options.length * 0.55) + Math.cos(angle) * spread * 0.5,
          (random() - 0.5) * 0.9 + Math.sin(angle) * spread * 0.5,
          Math.sin(angle) * spread,
        ],
        i * 3,
      );
    }
    return out;
  },

  bars(count, random, options) {
    const out = new Float32Array(count * 3);
    const bars = options.bars;
    for (let i = 0; i < count; i += 1) {
      const bar = i % bars;
      const rank = 1 - bar / bars;
      const height = options.height * (0.22 + rank * 0.78);
      const x = (bar - (bars - 1) / 2) * options.spread;
      const y = random() * height - height / 2;
      out.set([x + (random() - 0.5) * 0.12, y, (random() - 0.5) * 0.4], i * 3);
    }
    return out;
  },

  container(count, random, options) {
    const out = new Float32Array(count * 3);
    const w = options.width;
    const h = options.height;
    for (let i = 0; i < count; i += 1) {
      const layer = i % options.layers;
      const perimeter = random();
      const t = perimeter * 4;
      let x;
      let y;
      if (t < 1) { x = -w / 2 + t * w; y = h / 2; }
      else if (t < 2) { x = w / 2; y = h / 2 - (t - 1) * h; }
      else if (t < 3) { x = w / 2 - (t - 2) * w; y = -h / 2; }
      else { x = -w / 2; y = -h / 2 + (t - 3) * h; }
      const inset = (layer / options.layers) * 0.35;
      out.set([x * (1 - inset), y * (1 - inset), (random() - 0.5) * 0.5], i * 3);
    }
    return out;
  },

  stream(count, random, options) {
    const out = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const t = i / count;
      out.set(
        [
          (t - 0.5) * options.length,
          (random() - 0.5) * options.jitter * 3,
          (random() - 0.5) * options.jitter,
        ],
        i * 3,
      );
    }
    return out;
  },

  constellation(count, random, options) {
    const out = new Float32Array(count * 3);
    const nodes = options.nodes;
    for (let i = 0; i < count; i += 1) {
      const node = i % nodes;
      const angle = (node / nodes) * Math.PI * 2;
      const radius = options.radius + (node % 3) * 0.12;
      const cx = Math.cos(angle) * radius;
      const cz = Math.sin(angle) * radius * 0.55;
      const cy = Math.sin(node * 2.1) * 0.7;
      const jitter = 0.16 * Math.sqrt(random());
      out.set(
        [cx + (random() - 0.5) * jitter, cy + (random() - 0.5) * jitter, cz + (random() - 0.5) * jitter],
        i * 3,
      );
    }
    return out;
  },

  orbit(count, random, options) {
    const out = new Float32Array(count * 3);
    const clusters = options.clusters;
    for (let i = 0; i < count; i += 1) {
      const cluster = i % clusters;
      const sign = cluster === 0 ? -1 : 1;
      const angle = random() * Math.PI * 2;
      const radius = options.radius * (0.5 + 0.5 * Math.sqrt(random()));
      out.set(
        [
          sign * 0.55 + Math.cos(angle) * radius * 0.35,
          Math.sin(angle) * radius * 0.55,
          (random() - 0.5) * 0.4,
        ],
        i * 3,
      );
    }
    return out;
  },

  cage(count, random, options) {
    const out = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const bar = i % options.bars;
      const angle = (bar / options.bars) * Math.PI * 2;
      const t = random();
      const radius = options.radius;
      out.set(
        [
          Math.cos(angle) * radius,
          (t - 0.5) * radius * 1.9,
          Math.sin(angle) * radius,
        ],
        i * 3,
      );
    }
    return out;
  },

  system(count, random) {
    const out = new Float32Array(count * 3);
    const bands = 5;
    for (let i = 0; i < count; i += 1) {
      const band = i % bands;
      const angle = random() * Math.PI * 2 + band * 0.7;
      const radius = 0.9 + band * 0.55 + (random() - 0.5) * 0.06;
      const tilt = (band / bands) * 0.5;
      out.set(
        [Math.cos(angle) * radius, Math.sin(angle) * radius * tilt, Math.sin(angle) * radius * 0.9],
        i * 3,
      );
    }
    return out;
  },
};

/* ------------------------------------------------------------------ buffers */
function buildSceneBuffers(count) {
  const positions = new Float32Array(SCENES.length * count * 3);
  const colours = new Float32Array(SCENES.length * count * 3);
  const sorted = [...SCENES].sort((a, b) => Number(a.number) - Number(b.number));

  sorted.forEach((scene, sceneIndex) => {
    const random = mulberry32(1000 + sceneIndex * 77);
    const builder = FORMATIONS[scene.formation.kind] || FORMATIONS.dust;
    const block = builder(count, random, scene.formation);
    positions.set(block, sceneIndex * count * 3);

    const palette = PALETTE[scene.palette] || PALETTE.paper;
    for (let i = 0; i < count; i += 1) {
      const pulse = 0.55 + 0.45 * random();
      const offset = (sceneIndex * count + i) * 3;
      colours[offset] = palette[0] * pulse;
      colours[offset + 1] = palette[1] * pulse;
      colours[offset + 2] = palette[2] * pulse;
    }
  });

  const sizes = new Float32Array(count);
  const seeds = new Float32Array(count);
  const sizeRandom = mulberry32(4242);
  for (let i = 0; i < count; i += 1) {
    sizes[i] = 1.1 + sizeRandom() * 2.6;
    seeds[i] = sizeRandom();
  }
  return { positions, colours, sizes, seeds };
}

/* ----------------------------------------------------------------- renderer */
export function createRenderer(canvas, options = {}) {
  const quality = options.quality || "high";
  const count = Math.max(
    700,
    Math.round(
      quality === "high" ? BASE_PARTICLES
        : quality === "medium" ? BASE_PARTICLES * 0.6
        : BASE_PARTICLES * 0.35,
    ),
  );
  const gl =
    canvas.getContext("webgl2", { antialias: true, alpha: true, powerPreference: "high-performance" }) ||
    canvas.getContext("webgl", { antialias: true, alpha: true });

  // A canvas without a 2D context (headless DOM, denied context) still has to
  // boot: the page then falls back to the DOM transcript only.
  const ctx2d = gl ? null : (canvas.getContext ? canvas.getContext("2d") : null);

  const state = {
    count,
    sceneA: 0,
    sceneB: 0,
    mix: 1,
    time: 0,
    cameraA: SCENES[0].camera,
    cameraB: SCENES[0].camera,
    cameraMix: 1,
    quality,
    mode: gl ? "webgl" : ctx2d ? "canvas2d" : "none",
    disposed: false,
    forcedReduced: Boolean(options.reducedMotion),
  };

  const sorted = [...SCENES].sort((a, b) => Number(a.number) - Number(b.number));
  state.sceneById = new Map(sorted.map((scene, index) => [scene.id, index]));
  const buffers = buildSceneBuffers(count);

  const projection = mat4.identity();
  const view = mat4.identity();
  const viewProjection = mat4.identity();

  let program = null;
  let resources = null;

  function compile(source, type) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error(`shader compile failed: ${log}`);
    }
    return shader;
  }

  const VERTEX_300 = `#version 300 es
    in vec3 a_posA; in vec3 a_posB; in vec3 a_colA; in vec3 a_colB;
    in float a_size; in float a_seed;
    uniform mat4 u_viewProj; uniform float u_mix; uniform float u_time; uniform float u_scale;
    out vec3 v_colour; out float v_alpha;
    void main() {
      vec3 p = mix(a_posA, a_posB, u_mix);
      float breathe = sin(u_time * 0.7 + a_seed * 6.2831) * 0.022;
      p *= (1.0 + breathe);
      vec4 clip = u_viewProj * vec4(p, 1.0);
      gl_Position = clip;
      gl_PointSize = clamp(a_size * u_scale / max(0.35, clip.w), 1.0, 26.0);
      v_colour = mix(a_colA, a_colB, u_mix);
      v_alpha = clamp(1.75 - clip.w * 0.07, 0.12, 1.0);
    }`;

  const VERTEX_100 = `
    attribute vec3 a_posA; attribute vec3 a_posB; attribute vec3 a_colA; attribute vec3 a_colB;
    attribute float a_size; attribute float a_seed;
    uniform mat4 u_viewProj; uniform float u_mix; uniform float u_time; uniform float u_scale;
    varying vec3 v_colour; varying float v_alpha;
    void main() {
      vec3 p = mix(a_posA, a_posB, u_mix);
      float breathe = sin(u_time * 0.7 + a_seed * 6.2831) * 0.022;
      p *= (1.0 + breathe);
      vec4 clip = u_viewProj * vec4(p, 1.0);
      gl_Position = clip;
      gl_PointSize = clamp(a_size * u_scale / max(0.35, clip.w), 1.0, 26.0);
      v_colour = mix(a_colA, a_colB, u_mix);
      v_alpha = clamp(1.75 - clip.w * 0.07, 0.12, 1.0);
    }`;

  const FRAGMENT_300 = `#version 300 es
    precision mediump float;
    in vec3 v_colour; in float v_alpha; out vec4 outColour;
    void main() {
      vec2 d = gl_PointCoord - 0.5;
      float r2 = dot(d, d);
      if (r2 > 0.25) discard;
      float a = smoothstep(0.25, 0.02, r2);
      outColour = vec4(v_colour, a * v_alpha);
    }`;

  const FRAGMENT_100 = `
    precision mediump float;
    varying vec3 v_colour; varying float v_alpha;
    void main() {
      vec2 d = gl_PointCoord - 0.5;
      float r2 = dot(d, d);
      if (r2 > 0.25) discard;
      float a = smoothstep(0.25, 0.02, r2);
      gl_FragColor = vec4(v_colour, a * v_alpha);
    }`;

  if (gl) {
    try {
      const isGL2 = typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext;
      program = gl.createProgram();
      gl.attachShader(program, compile(isGL2 ? VERTEX_300 : VERTEX_100, gl.VERTEX_SHADER));
      gl.attachShader(program, compile(isGL2 ? FRAGMENT_300 : FRAGMENT_100, gl.FRAGMENT_SHADER));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error(gl.getProgramInfoLog(program) || "link failed");
      }
      gl.useProgram(program);

      const blockSize = count * 3 * 4;
      const positionBuffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, buffers.positions, gl.STATIC_DRAW);

      const colourBuffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, colourBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, buffers.colours, gl.STATIC_DRAW);

      const sizeBuffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, sizeBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, buffers.sizes, gl.STATIC_DRAW);

      const seedBuffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, seedBuffer);
      gl.bufferData(gl.ARRAY_BUFFER, buffers.seeds, gl.STATIC_DRAW);

      const attrib = (name) => gl.getAttribLocation(program, name);
      const setupVec3 = (buffer, location, stride, offset) => {
        if (location < 0) return;
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.enableVertexAttribArray(location);
        gl.vertexAttribPointer(location, 3, gl.FLOAT, false, stride, offset);
      };
      setupVec3(positionBuffer, attrib("a_posA"), 0, 0);
      setupVec3(positionBuffer, attrib("a_posB"), 0, blockSize);
      setupVec3(colourBuffer, attrib("a_colA"), 0, 0);
      setupVec3(colourBuffer, attrib("a_colB"), 0, blockSize);

      [["a_size", sizeBuffer], ["a_seed", seedBuffer]].forEach(([name, buffer]) => {
        const location = attrib(name);
        if (location < 0) return;
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.enableVertexAttribArray(location);
        gl.vertexAttribPointer(location, 1, gl.FLOAT, false, 0, 0);
      });

      gl.clearColor(0.03, 0.035, 0.045, 1.0);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      gl.disable(gl.DEPTH_TEST);

      resources = {
        program,
        buffers: [positionBuffer, colourBuffer, sizeBuffer, seedBuffer],
        uniforms: {
          viewProj: gl.getUniformLocation(program, "u_viewProj"),
          mix: gl.getUniformLocation(program, "u_mix"),
          time: gl.getUniformLocation(program, "u_time"),
          scale: gl.getUniformLocation(program, "u_scale"),
        },
      };
    } catch (error) {
      // A broken shader must degrade to the 2D fallback, never a blank screen.
      console.warn("[experience] WebGL setup failed, using canvas fallback:", error.message);
      state.mode = "canvas2d";
      program = null;
      resources = null;
    }
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, state.quality === "high" ? 2 : 1.5);
    const width = Math.max(1, Math.floor(canvas.clientWidth * dpr));
    const height = Math.max(1, Math.floor(canvas.clientHeight * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    if (gl) gl.viewport(0, 0, width, height);
    return { width, height, dpr };
  }

  function setScene(nextId, { instant = false } = {}) {
    const target = state.sceneById.has(nextId) ? state.sceneById.get(nextId) : 0;
    state.sceneA = state.sceneB;
    state.sceneB = target;
    state.mix = instant || state.forcedReduced ? 1 : 0;
    state.cameraA = state.cameraB;
    state.cameraB = sorted[target].camera;
    state.cameraMix = instant || state.forcedReduced ? 1 : 0;
  }

  function cameraFor(mix) {
    const a = state.cameraA;
    const b = state.cameraB;
    const t = mix;
    return {
      position: [
        lerp(a.position[0], b.position[0], t),
        lerp(a.position[1], b.position[1], t),
        lerp(a.position[2], b.position[2], t),
      ],
      target: [
        lerp(a.target[0], b.target[0], t),
        lerp(a.target[1], b.target[1], t),
        lerp(a.target[2], b.target[2], t),
      ],
      fov: lerp(a.fov, b.fov, t),
    };
  }

  function drawFallback(size) {
    if (!ctx2d) return;
    const { width, height } = size;
    ctx2d.clearRect(0, 0, width, height);
    const gradient = ctx2d.createRadialGradient(
      width * 0.5, height * 0.5, 0, width * 0.5, height * 0.5, Math.max(width, height) * 0.7,
    );
    gradient.addColorStop(0, "rgba(24,30,42,1)");
    gradient.addColorStop(1, "rgba(6,8,12,1)");
    ctx2d.fillStyle = gradient;
    ctx2d.fillRect(0, 0, width, height);

    const camera = cameraFor(state.cameraMix);
    const sceneIndex = state.mix < 0.5 ? state.sceneA : state.sceneB;
    const paletteScene = sorted[sceneIndex];
    const palette = PALETTE[paletteScene.palette] || PALETTE.paper;
    const scale = Math.min(width, height) * 0.32;
    for (let i = 0; i < count; i += 3) {
      const offset = (sceneIndex * count + i) * 3;
      const x = buffers.positions[offset];
      const y = buffers.positions[offset + 1];
      const z = buffers.positions[offset + 2] + camera.position[2] * 0.6;
      const depth = 1 / (1 + Math.max(0.2, z + 4));
      const sx = width / 2 + x * scale * depth * 2.2;
      const sy = height / 2 - y * scale * depth * 2.2;
      if (sx < 0 || sy < 0 || sx > width || sy > height) continue;
      const alpha = Math.min(0.9, depth * 1.5);
      ctx2d.fillStyle = `rgba(${Math.round(palette[0] * 255)},${Math.round(palette[1] * 255)},${Math.round(palette[2] * 255)},${alpha})`;
      ctx2d.fillRect(sx, sy, 1.6, 1.6);
    }
  }

  function render(deltaSeconds) {
    if (state.disposed) return;
    const size = resize();
    state.time += deltaSeconds;
    if (state.mix < 1) state.mix = Math.min(1, state.mix + deltaSeconds / 0.9);
    if (state.cameraMix < 1) state.cameraMix = Math.min(1, state.cameraMix + deltaSeconds / 1.4);

    if (!gl || !resources) {
      drawFallback(size);
      return;
    }
    const camera = cameraFor(state.cameraMix);
    mat4.perspective(projection, (camera.fov * Math.PI) / 180, size.width / size.height, 0.1, 100);
    mat4.lookAt(view, camera.position, camera.target);
    mat4.multiply(viewProjection, projection, view);

    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(resources.program);
    gl.uniformMatrix4fv(resources.uniforms.viewProj, false, viewProjection);
    gl.uniform1f(resources.uniforms.mix, state.mix);
    gl.uniform1f(resources.uniforms.time, state.time);
    gl.uniform1f(resources.uniforms.scale, size.dpr * (state.quality === "low" ? 1.2 : 1.6));
    gl.drawArrays(gl.POINTS, 0, count);
  }

  function dispose() {
    state.disposed = true;
    if (gl && resources) {
      resources.buffers.forEach((buffer) => gl.deleteBuffer(buffer));
      gl.deleteProgram(resources.program);
      resources = null;
    }
  }

  function setReducedMotion(reduced) {
    state.forcedReduced = Boolean(reduced);
    if (reduced) {
      state.mix = 1;
      state.cameraMix = 1;
    }
  }

  return {
    state,
    setScene,
    render,
    resize,
    dispose,
    setReducedMotion,
    get mode() {
      return state.mode;
    },
  };
}
