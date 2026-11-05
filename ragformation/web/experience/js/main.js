// S3 Experience entry point.
//
// Three modes share one stage:
//   director  — deterministic walkthrough of recorded data (labelled demo)
//   live      — follows the real event stream of a running server
//   explorer  — pauses the film and lets you read the real index
//
// Honesty rules enforced here:
//   * the mode badge always states which data is on screen;
//   * embedding/generation badges are filled from the server's own config, and
//     say "unknown" when the server cannot be reached — never a guess;
//   * demo mode never claims to be live.

import { SCENES, sceneForStage, sceneIndex } from "./scenes.js";
import { createRenderer } from "./renderer.js";
import { createTimeline, demoPlan, transcript, DEFAULT_DWELL_MS } from "./timeline.js";
import { createStreamClient } from "./events.js";
import { createExplorer } from "./explorer.js";

const $ = (id) => document.getElementById(id);

const DWELL_MS = DEFAULT_DWELL_MS;

const state = {
  mode: "director",
  reducedMotion: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  quality: "high",
  degraded: 0,
  demo: null,
  plan: null,
  serverConfig: null,
  serverHealth: null,
  liveEvents: new Map(),
  stream: null,
  explorer: null,
  frameSamples: [],
};

const refs = {
  canvas: $("stage"),
  badgeMode: $("badge-mode"),
  badgeEmbed: $("badge-embed"),
  badgeGen: $("badge-gen"),
  badgeStream: $("badge-stream"),
  number: $("scene-number"),
  title: $("scene-title"),
  caption: $("scene-caption"),
  detail: $("scene-detail"),
  readout: $("readout"),
  grid: document.querySelector(".stage-grid"),
  side: $("side"),
  sideHint: $("side-hint"),
  sourceList: $("source-list"),
  sourceCount: $("source-count"),
  chunkList: $("chunk-list"),
  chunkCount: $("chunk-count"),
  trace: $("trace"),
  answer: $("answer"),
  askForm: $("ask-form"),
  askInput: $("ask-input"),
  play: $("play"),
  prev: $("prev"),
  next: $("next"),
  position: $("position"),
  scrub: $("scrub"),
  speed: $("speed"),
  replay: $("replay"),
  motion: $("motion"),
  fullscreen: $("fullscreen"),
  transcriptList: $("transcript-list"),
  tabs: [...document.querySelectorAll("[data-mode]")],
};

const renderer = { current: null };

function createRendererForQuality() {
  if (renderer.current) renderer.current.dispose();
  renderer.current = createRenderer(refs.canvas, {
    quality: state.quality,
    reducedMotion: state.reducedMotion,
  });
  return renderer.current;
}

/* ------------------------------------------------------------- formatting */
function pretty(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") {
    if (Number.isInteger(value)) return value.toLocaleString("en-US");
    return Number(value).toFixed(2);
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return Object.keys(value).length ? JSON.stringify(value).slice(0, 60) : "—";
  return String(value);
}

function renderReadout(items) {
  refs.readout.textContent = "";
  for (const [label, value] of items.slice(0, 4)) {
    const wrapper = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = pretty(value);
    wrapper.append(dt, dd);
    refs.readout.append(wrapper);
  }
}

const SCENE_READOUT = {
  problem: () => [["indexed sources", state.demo?.summary?.sources ?? "—"], ["retrieval", "not started"]],
  sources: () => [["sources", state.demo?.summary?.sources ?? "—"], ["chunks", state.demo?.summary?.chunks ?? "—"]],
  extraction: () => [["blocks parsed", state.demo?.summary?.blocks ?? "—"], ["transform", "structure kept"]],
  chunking: () => [["chunks", state.demo?.summary?.chunks ?? "—"], ["overlap", "heading-aware"]],
  embeddings: () => [
    ["provider", state.demo?.labelling?.embeddingProvider ?? "—"],
    ["semantic", state.demo?.labelling?.semantic ? "yes" : "no"],
    ["dimension", state.demo?.summary?.dimension ?? "—"],
  ],
  index: () => [["vectors", state.demo?.summary?.chunks ?? "—"], ["store", "sqlite + fts5"]],
  query: () => [["question", state.demo?.query ?? "—"], ["scope", "server-enforced"]],
  retrieval: () => [
    ["dense candidates", state.demo?.summary?.denseCandidates ?? "—"],
    ["lexical candidates", state.demo?.summary?.lexicalCandidates ?? "—"],
  ],
  fusion: () => [
    ["fused candidates", state.demo?.summary?.fused ?? "—"],
    ["rerank", state.demo?.summary?.rerankStatus ?? "—"],
  ],
  context: () => [
    ["context chunks", state.demo?.summary?.contextChunks ?? "—"],
    ["context tokens", state.demo?.summary?.contextTokens ?? "—"],
  ],
  generation: () => [
    ["path", state.demo?.labelling?.generationProvider ?? "—"],
    ["ttft", state.demo?.summary?.ttftMs ? `${state.demo.summary.ttftMs.toFixed(1)} ms` : "—"],
  ],
  citation: () => [
    ["citations", state.demo?.summary?.citations ?? "—"],
    ["unresolved", state.demo?.summary?.unresolvedCitations ?? 0],
  ],
  decision: () => [
    ["status", state.demo?.summary?.status ?? "—"],
    ["support", state.demo?.summary?.support ?? "—"],
  ],
  security: () => [["injected instructions", state.demo?.summary?.quarantined ?? "0"], ["policy", "data, never orders"]],
  lifecycle: () => [["revision", state.demo?.summary?.revision ?? "—"], ["rollback", "previous kept"]],
  answer: () => [
    ["answer", state.demo?.summary?.status ?? "—"],
    ["latency", state.demo?.summary?.latencyMs ? `${state.demo.summary.latencyMs.toFixed(1)} ms` : "—"],
  ],
};

/* --------------------------------------------------------------- demo data */
async function loadDemo() {
  try {
    const response = await fetch("./demo_trace.json", { headers: { accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    if (body.kind !== "demo" || body.labelling?.mode !== "demo") {
      throw new Error("demo fixture is not labelled as a demo");
    }
    state.demo = body;
    state.plan = demoPlan(body.events || []);
    return body;
  } catch (error) {
    state.demo = null;
    state.plan = null;
    console.warn("[experience] demo fixture unavailable:", error.message);
    return null;
  }
}

/* ------------------------------------------------------------------ stream */
function ensureStream() {
  if (state.stream) return state.stream;
  state.stream = createStreamClient({
    onEvent: (event) => {
      state.liveEvents.set(event.stage, event);
      const scene = sceneForStage(event.stage);
      if (scene && state.mode === "live") {
        timeline.advanceTo(scene.id);
        applyReadout(scene);
      }
    },
    onState: (info) => {
      if (!refs.badgeStream) return;
      refs.badgeStream.hidden = false;
      const label = `${info.transport}/${info.state}`;
      refs.badgeStream.textContent = `stream: ${label}`;
      refs.badgeStream.classList.toggle("badge--live", info.state === "open");
      refs.badgeStream.classList.toggle("badge--warn", info.state.includes("error") || info.state === "rejected");
    },
    onGap: () => {
      if (!refs.badgeStream) return;
      refs.badgeStream.classList.add("badge--warn");
      refs.badgeStream.textContent = "stream: missed events";
    },
  });
  state.stream.connect();
  return state.stream;
}

/* ---------------------------------------------------------------- readout */
function applyReadout(scene) {
  if (state.mode === "demo" || state.mode === "director") {
    const builder = SCENE_READOUT[scene.id];
    renderReadout(builder ? builder() : []);
    return;
  }
  if (state.mode === "live") {
    const items = [];
    for (const stage of scene.stages) {
      const event = state.liveEvents.get(stage);
      if (!event) continue;
      for (const [key, value] of Object.entries(event.counts || {}).slice(0, 2)) items.push([key, value]);
      for (const [key, value] of Object.entries(event.metrics || {}).slice(0, 1)) items.push([key, value]);
      if (!items.length) items.push([stage, event.status || "seen"]);
      break;
    }
    if (!items.length) items.push(["awaiting traffic", "ask a question in the console"]);
    renderReadout(items);
    return;
  }
  const health = state.serverHealth;
  renderReadout(
    health
      ? [
          ["sources", health.sources],
          ["chunks", health.chunks],
          ["embedding", health.embedding?.provider],
          ["generation", health.generation?.provider],
        ]
      : [["server", "not reachable"]],
  );
}

/* ------------------------------------------------------------------- stage */
function applyScene(scene, index) {
  refs.number.textContent = scene.number;
  refs.title.textContent = scene.title;
  refs.caption.textContent = scene.caption;
  refs.detail.textContent = scene.detail;
  refs.position.textContent = `${scene.number} / ${String(SCENES.length - 1).padStart(2, "0")}`;
  refs.scrub.value = String(index);
  renderer.current?.setScene(scene.id);
  applyReadout(scene);
}

const timeline = createTimeline({
  dwell: DWELL_MS,
  onScene: (scene, index) => applyScene(scene, index),
  onProgress: (progress) => {
    if (document.activeElement !== refs.scrub) {
      refs.scrub.value = String(progress.index + progress.fraction);
    }
  },
  onState: (info) => {
    refs.play.textContent = info.playing ? "❙❙" : "▶";
    refs.play.setAttribute("aria-label", info.playing ? "Pause" : "Play");
    refs.play.setAttribute("aria-pressed", String(info.playing));
  },
  onEnd: () => {
    if (state.mode === "director") refs.play.textContent = "↺";
  },
});

/* -------------------------------------------------------------------- modes */
function setMode(mode, { autoplay = true } = {}) {
  if (!["director", "live", "explorer"].includes(mode)) return;
  state.mode = mode;
  for (const tab of refs.tabs) {
    tab.setAttribute("aria-selected", String(tab.dataset.mode === mode));
  }
  if (refs.grid) refs.grid.dataset.mode = mode;
  refs.side.hidden = mode !== "explorer";
  document.body.dataset.mode = mode;

  if (mode === "director") {
    refs.badgeMode.textContent = state.demo ? "demo · recorded run" : "demo · scripted";
    refs.badgeMode.className = state.demo ? "badge" : "badge badge--warn";
    refs.badgeMode.title = state.demo?.labelling?.note || "No recorded fixture loaded; showing the script only.";
    if (autoplay) timeline.replay();
  } else if (mode === "live") {
    refs.badgeMode.textContent = "live";
    refs.badgeMode.className = "badge badge--live";
    refs.badgeMode.title = "Events on this screen come from the running server's stream.";
    ensureStream();
    refreshServerBadges();
    timeline.advanceTo("query");
    if (autoplay) timeline.play();
  } else {
    refs.badgeMode.textContent = "explorer · real index";
    refs.badgeMode.className = "badge";
    timeline.pause();
    renderer.current?.setScene("index", { instant: true });
    refreshServerBadges();
    state.explorer?.loadSources();
  }
}

async function refreshServerBadges() {
  const config = await state.explorer.loadConfig();
  state.serverConfig = config?.error ? null : config;
  state.serverHealth = await state.explorer.loadHealth();
  if (!refs.badgeEmbed) return;
  if (config?.error) {
    refs.badgeEmbed.textContent = "embedding: server unreachable";
    refs.badgeEmbed.className = "badge badge--quiet badge--warn";
    refs.badgeGen.textContent = "generated by: unknown";
    refs.badgeGen.className = "badge badge--quiet badge--warn";
    return;
  }
  const embedding = config.providers || config.config?.embedding || {};
  const generation = config.generation || {};
  refs.badgeEmbed.textContent = `embedding: ${embedding.provider || "unknown"}${
    embedding.semantic === false ? " (lexical)" : ""
  }`;
  refs.badgeEmbed.title = embedding.dimension ? `${embedding.dimension}-dim, ${embedding.metric || "cosine"}` : "";
  refs.badgeGen.textContent = `generated by: ${generation.provider || "unknown"}${
    generation.model ? ` · ${generation.model}` : ""
  }`;
  refs.badgeEmbed.className = "badge badge--quiet";
  refs.badgeGen.className = "badge badge--quiet";
}

/* ---------------------------------------------------------------- controls */
function wireControls() {
  refs.play.addEventListener("click", () => {
    if (timeline.state.finished) timeline.replay();
    else timeline.toggle();
  });
  refs.prev.addEventListener("click", () => timeline.previous());
  refs.next.addEventListener("click", () => timeline.next());
  refs.replay.addEventListener("click", () => timeline.replay());
  refs.speed.addEventListener("change", () => timeline.setSpeed(Number(refs.speed.value)));
  refs.scrub.addEventListener("input", () => {
    const value = Number(refs.scrub.value);
    const index = Math.min(SCENES.length - 1, Math.floor(value));
    timeline.seek(index, value - index);
  });
  refs.tabs.forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));

  refs.motion.setAttribute("aria-pressed", String(state.reducedMotion));
  refs.motion.addEventListener("click", () => {
    state.reducedMotion = !state.reducedMotion;
    refs.motion.setAttribute("aria-pressed", String(state.reducedMotion));
    document.body.dataset.reducedMotion = String(state.reducedMotion);
    renderer.current?.setReducedMotion(state.reducedMotion);
  });

  refs.fullscreen.addEventListener("click", async () => {
    try {
      if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
      else await document.exitFullscreen();
    } catch (error) {
      console.warn("[experience] fullscreen unavailable:", error.message);
    }
  });

  refs.askForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = refs.askInput.value;
    if (question.trim()) state.explorer.ask(question);
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
    switch (event.key) {
      case " ":
        event.preventDefault();
        timeline.toggle();
        break;
      case "ArrowRight":
        timeline.next();
        break;
      case "ArrowLeft":
        timeline.previous();
        break;
      case "r":
        timeline.replay();
        break;
      case "m":
        refs.motion.click();
        break;
      case "f":
        refs.fullscreen.click();
        break;
      case "1":
        setMode("director");
        break;
      case "2":
        setMode("live");
        break;
      case "3":
        setMode("explorer");
        break;
      default:
        break;
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) timeline.pause();
  });
}

/* --------------------------------------------------------------- transcript */
function buildTranscript() {
  const fragment = document.createDocumentFragment();
  for (const entry of transcript(SCENES)) {
    const li = document.createElement("li");
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = entry.number;
    const heading = document.createElement("h3");
    heading.textContent = entry.title;
    const caption = document.createElement("p");
    caption.textContent = entry.caption;
    const detail = document.createElement("p");
    detail.className = "eng";
    detail.textContent = entry.detail;
    li.append(num, heading, caption, detail);
    fragment.append(li);
  }
  refs.transcriptList.append(fragment);
}

/* -------------------------------------------------------------------- loop */
function frame(now) {
  const last = frame.last || now;
  const delta = Math.min(80, now - last);
  frame.last = now;
  timeline.tick(delta);
  renderer.current?.render(delta / 1000);

  if (state.mode !== "explorer" && state.quality !== "low") {
    state.frameSamples.push(delta);
    if (state.frameSamples.length > 90) {
      const average = state.frameSamples.reduce((sum, value) => sum + value, 0) / state.frameSamples.length;
      state.frameSamples = [];
      if (average > 34 && state.degraded < 2) {
        state.degraded += 1;
        state.quality = state.degraded === 1 ? "medium" : "low";
        createRendererForQuality();
        if (refs.badgeMode) refs.badgeMode.title = `Adaptive quality: ${state.quality} (device could not sustain 30fps)`;
      }
    }
  }
  requestAnimationFrame(frame);
}

/* -------------------------------------------------------------------- boot */
function boot() {
  document.body.dataset.reducedMotion = String(state.reducedMotion);
  buildTranscript();
  createRendererForQuality();
  refs.scrub.max = String(SCENES.length - 1);
  refs.position.textContent = `00 / ${String(SCENES.length - 1).padStart(2, "0")}`;

  state.explorer = createExplorer(refs, {
    onAsk: (body) => {
      const scene = body.status === "abstained" ? "decision" : body.warnings?.length ? "decision" : "citation";
      renderer.current?.setScene(scene);
    },
  });

  wireControls();

  const params = new URLSearchParams(window.location.search);
  const requested = params.get("mode");
  const mode = ["director", "live", "explorer"].includes(requested) ? requested : "director";

  loadDemo().then(() => {
    renderer.current?.setScene(SCENES[0].id, { instant: true });
    timeline.start();
    setMode(mode);
    if (mode === "director") {
      // Give the first frame a beat before the timeline starts counting.
      window.setTimeout(() => timeline.play(), 600);
    }
  });

  requestAnimationFrame(frame);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}

// Exposed for the smoke test in scripts/ and for debugging in the browser.
window.__experience = { state, timeline, renderer, SCENES, sceneIndex };
