// Deterministic timeline for the S3 Experience.
//
// The timeline is deliberately DOM-free so it can be unit-tested in Node and so
// live mode can reuse it: live events call `advanceTo(sceneId)` and the same
// playback machinery handles scrubbing, replay and speed changes.

import { SCENES } from "./scenes.js";

export const DEFAULT_DWELL_MS = 5200;

export function createTimeline(options = {}) {
  const scenes = options.scenes || SCENES;
  const dwell = options.dwell || DEFAULT_DWELL_MS;
  const listeners = {
    scene: options.onScene || (() => {}),
    progress: options.onProgress || (() => {}),
    state: options.onState || (() => {}),
    end: options.onEnd || (() => {}),
  };

  const state = {
    index: 0,
    elapsed: 0,
    playing: false,
    speed: 1,
    dwell,
    visited: new Set([0]),
    finished: false,
  };

  function emitScene(reason) {
    const scene = scenes[state.index];
    listeners.scene(scene, state.index, reason);
    listeners.progress({
      index: state.index,
      fraction: Math.min(1, state.elapsed / state.dwell),
      elapsed: state.elapsed,
      dwell: state.dwell,
      playing: state.playing,
      speed: state.speed,
      total: scenes.length,
    });
  }

  function emitState() {
    listeners.state({
      index: state.index,
      playing: state.playing,
      speed: state.speed,
      finished: state.finished,
      visited: [...state.visited],
      total: scenes.length,
    });
  }

  function goTo(index, reason = "seek", { resetProgress = true } = {}) {
    const bounded = Math.max(0, Math.min(scenes.length - 1, index));
    state.index = bounded;
    if (resetProgress) state.elapsed = 0;
    state.visited.add(bounded);
    state.finished = false;
    emitScene(reason);
    emitState();
  }

  function tick(deltaMs) {
    if (!state.playing) return;
    state.elapsed += Math.max(0, deltaMs) * state.speed;
    // A long frame (background tab, slow device) may span several dwell times:
    // consume them in a loop so the film cannot fall behind real time.
    let advanced = false;
    while (state.elapsed >= state.dwell && state.playing) {
      state.elapsed -= state.dwell;
      if (state.index >= scenes.length - 1) {
        state.playing = false;
        state.finished = true;
        state.elapsed = state.dwell;
        emitScene("end");
        emitState();
        listeners.end();
        return;
      }
      goTo(state.index + 1, "auto", { resetProgress: false });
      advanced = true;
    }
    if (advanced) state.elapsed = Math.min(state.elapsed, state.dwell);
    listeners.progress({
      index: state.index,
      fraction: Math.min(1, state.elapsed / state.dwell),
      elapsed: state.elapsed,
      dwell: state.dwell,
      playing: state.playing,
      speed: state.speed,
      total: scenes.length,
    });
  }

  const api = {
    state,
    get scene() {
      return scenes[state.index];
    },
    get length() {
      return scenes.length;
    },
    play() {
      state.playing = true;
      state.finished = false;
      emitState();
    },
    pause() {
      state.playing = false;
      emitState();
    },
    toggle() {
      if (state.playing) api.pause();
      else api.play();
    },
    next() {
      goTo(state.index + 1, "next");
    },
    previous() {
      goTo(state.index - 1, "previous");
    },
    seek(index, fraction = 0) {
      goTo(index, "seek");
      state.elapsed = Math.max(0, Math.min(1, fraction)) * state.dwell;
      api.play();
    },
    replay() {
      goTo(0, "replay");
      api.play();
    },
    setSpeed(speed) {
      state.speed = Math.max(0.25, Math.min(3, speed));
      emitState();
    },
    setDwell(ms) {
      state.dwell = Math.max(1200, ms);
    },
    /** Live mode: jump to the scene for an event without ending playback. */
    advanceTo(sceneId, reason = "live") {
      const target = scenes.findIndex((scene) => scene.id === sceneId);
      if (target < 0 || target === state.index) return false;
      goTo(target, reason);
      return true;
    },
    tick,
    start() {
      emitScene("start");
      emitState();
    },
  };
  return api;
}

/** Director-mode transcript: one entry per scene, used for the accessible list. */
export function transcript(scenes = SCENES) {
  return scenes.map((scene) => ({
    id: scene.id,
    number: scene.number,
    title: scene.title,
    caption: scene.caption,
    detail: scene.detail,
  }));
}

/** Map a recorded demo trace (array of engine events) onto scene dwell times. */
export function demoPlan(events, scenes = SCENES) {
  const byStage = new Map();
  for (const event of events || []) {
    if (event.stage) byStage.set(event.stage, event);
  }
  return scenes.map((scene) => {
    const matched = scene.stages.map((stage) => byStage.get(stage)).filter(Boolean);
    const counts = {};
    const metrics = {};
    for (const event of matched) {
      Object.assign(counts, event.counts || {});
      Object.assign(metrics, event.metrics || {});
    }
    return {
      id: scene.id,
      dwellMs: DEFAULT_DWELL_MS,
      matchedStages: matched.map((event) => event.stage),
      counts,
      metrics,
      mode: matched[0]?.mode || "demo",
    };
  });
}
