// Headless smoke test for the S3 Experience.
//
// There is no browser in CI, so this loads the real index.html in jsdom,
// imports the real ES modules, and asserts that the page boots, renders the
// transcript, and survives all three modes with fetch stubbed. It does not try
// to verify pixels — WebGL cannot run headless here, and the fallback path is
// what it exercises.
//
// Usage: node scripts/experience_smoke.mjs [--jsdom /path/to/node_modules]
// Requires `jsdom` to be resolvable (dev-only; not a runtime dependency).

import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import process from "node:process";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const web = path.join(root, "ragformation", "web", "experience");

const jsdomArgIndex = process.argv.indexOf("--jsdom");
const jsdomPath = jsdomArgIndex > -1 ? process.argv[jsdomArgIndex + 1] : null;
const { JSDOM } = jsdomPath
  ? await import(pathToFileURL(path.join(jsdomPath, "jsdom", "lib", "api.js")).href)
  : await import("jsdom");

const html = readFileSync(path.join(web, "index.html"), "utf8");
const demo = JSON.parse(readFileSync(path.join(web, "demo_trace.json"), "utf8"));

const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8000/experience/?mode=director",
  pretendToBeVisual: true,
  runScripts: "outside-only",
});
const { window } = dom;

// jsdom has no GPU and no canvas backend: force the documented fallback path.
window.HTMLCanvasElement.prototype.getContext = () => null;

const responses = {
  "./demo_trace.json": demo,
  "/api/v1/config": {
    config: { top_k: 6 },
    providers: { provider: "hashing", semantic: false, dimension: 512, model: "hashing-lexical-v1" },
    generation: { provider: "extractive", mode: "extractive", model: "extractive-synthesis-v1" },
    version: "0.2.0",
    apiVersion: "v1",
    eventSchemaVersion: "1.0",
  },
  "/api/v1/health": {
    ok: true,
    sources: 14,
    chunks: 59,
    vectors: 59,
    embedding: { provider: "hashing", semantic: false, dimension: 512 },
    generation: { provider: "extractive", mode: "extractive" },
  },
  "/api/v1/sources": {
    sources: [{ sourceId: "src-1", title: "access-control-policy", chunks: 5, mediaType: "text/markdown" }],
    count: 1,
  },
  "/api/v1/sources/src-1/chunks": {
    chunks: [{ chunkId: "src-1#c000", text: "Retention of 3 years.", section: "Retention", tokenCount: 42 }],
  },
  "/api/v1/query": {
    queryId: "q-test",
    status: "needs_review",
    answer: "Access review evidence is retained for 5 years. [1]",
    support: 0.6,
    citations: [{ sourceTitle: "evidence-retention-memo", section: "Retention", page: 1 }],
    warnings: ["conflicting_evidence"],
  },
  "/api/v1/traces/q-test": { trace: { stages: [{ stage: "retrieval", status: "completed", durationMs: 1.2 }] } },
};

const calls = [];
window.fetch = async (url, options = {}) => {
  const key = String(url).split("?")[0];
  calls.push(key);
  const body = responses[key] ?? responses[url] ?? { error: "not_found", message: `no stub for ${key}` };
  const ok = !body.error;
  return {
    ok,
    status: ok ? 200 : 404,
    json: async () => body,
    headers: { get: () => "application/json" },
  };
};

// Minimal EventSource stub: the live mode must connect without throwing.
class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    FakeEventSource.instances.push(this);
    setTimeout(() => this.emit("open", {}), 5);
  }
  addEventListener(type, handler) {
    const list = this.listeners.get(type) || [];
    list.push(handler);
    this.listeners.set(type, list);
  }
  emit(type, payload) {
    for (const handler of this.listeners.get(type) || []) handler(payload);
  }
  close() {}
}
FakeEventSource.instances = [];
window.EventSource = FakeEventSource;

const globals = [
  "window",
  "document",
  "navigator",
  "HTMLElement",
  "HTMLCanvasElement",
  "EventSource",
  "requestAnimationFrame",
  "cancelAnimationFrame",
  "getComputedStyle",
  "URLSearchParams",
];
for (const name of globals) {
  try {
    Object.defineProperty(globalThis, name, { value: window[name], configurable: true, writable: true });
  } catch (error) {
    console.warn(`[smoke] could not expose global ${name}: ${error.message}`);
  }
}
globalThis.fetch = window.fetch;
// main.js reads window.matchMedia at module scope.
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
globalThis.DeviceEventSource = FakeEventSource;

const watchdog = setTimeout(() => {
  console.error("experience smoke: timed out after 25s (the page never reached its checks)");
  process.exit(2);
}, 25000);
watchdog.unref?.();

const failures = [];
function check(label, condition, extra = "") {
  if (condition) {
    console.log(`  ok   ${label}`);
  } else {
    console.log(`  FAIL ${label}${extra ? ` — ${extra}` : ""}`);
    failures.push(label);
  }
}

await import(pathToFileURL(path.join(web, "js", "main.js")).href);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
await sleep(120);

const doc = window.document;
const api = window.__experience;

check("module booted and exposed its state", Boolean(api), "window.__experience missing");
check("demo fixture loaded", api?.state.demo?.kind === "demo");
check("scene title replaced the loading text", doc.getElementById("scene-title").textContent.length > 4);
check("transcript rendered 16 scenes", doc.querySelectorAll("#transcript-list li").length === 16);
check("readout populated", doc.querySelectorAll("#readout div").length > 0);
check("mode badge says demo", /demo/.test(doc.getElementById("badge-mode").textContent));
check("renderer reports a non-WebGL fallback", ["canvas2d", "none"].includes(api?.renderer.current?.mode), api?.renderer.current?.mode);
check("scrub max matches the scene count", doc.getElementById("scrub").max === "15");

// Director playback advances the timeline.
api.timeline.setSpeed(2);
api.timeline.play();
await sleep(60);
check("timeline runs in director mode", api.timeline.state.playing);

// Live mode: must connect the stream and read server config.
doc.getElementById("tab-live").click();
await sleep(140);
check("live mode selected", api.state.mode === "live");
check("EventSource used for live mode", FakeEventSource.instances.length > 0);
check("config requested in live mode", calls.includes("/api/v1/config"));
check("embedding badge reflects the server", /hashing/.test(doc.getElementById("badge-embed").textContent));
check("generation badge reflects the server", /extractive/.test(doc.getElementById("badge-gen").textContent));

// Push a real event through the stream and confirm the scene follows it.
FakeEventSource.instances[0].emit("rag", {
  data: JSON.stringify({
    contract: "ragformation.events",
    event_version: "1.0",
    stage: "retrieval.semantic",
    status: "completed",
    counts: { candidates: 24 },
    sequence: 1,
    timestamp: new Date().toISOString(),
  }),
});
await sleep(40);
check("live event moved the scene", api.timeline.scene.id === "retrieval", api.timeline.scene.id);

// An unsupported contract version must be rejected, not rendered.
FakeEventSource.instances[0].emit("rag", {
  data: JSON.stringify({ contract: "ragformation.events", event_version: "2.0", stage: "generation" }),
});
await sleep(20);
check("future contract version rejected", api.timeline.scene.id === "retrieval");

// Explorer mode: real reads plus one query.
doc.getElementById("tab-explorer").click();
await sleep(160);
check("explorer mode selected", api.state.mode === "explorer");
check("sources listed", doc.querySelectorAll("#source-list button").length === 1);
doc.querySelector("#source-list button").click();
await sleep(80);
check("chunks listed for the selected source", doc.querySelectorAll("#chunk-list button").length === 1);
doc.getElementById("ask-input").value = "How long is access review evidence retained?";
doc.getElementById("ask-form").dispatchEvent(new window.Event("submit"));
await sleep(120);
check("answer rendered with a status", /needs_review/.test(doc.getElementById("answer").textContent));
check("trace rendered", doc.querySelectorAll("#trace li").length >= 1);

// Keyboard contract.
doc.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
await sleep(20);
check("arrow key advances", api.timeline.scene.id === "fusion" || api.timeline.state.index > 0);

// Unreachable-server path: the page must say so rather than invent values.
state_offline: {
  window.fetch = async () => {
    throw new TypeError("network down");
  };
  globalThis.fetch = window.fetch;
  doc.getElementById("tab-live").click();
  await sleep(120);
  check("offline mode is reported", /unreachable|unknown/.test(doc.getElementById("badge-embed").textContent));
  break state_offline;
}

console.log("");
if (failures.length) {
  console.error(`${failures.length} smoke check(s) failed`);
} else {
  console.log("experience smoke: all checks passed");
}
// The animation loop keeps timers alive; the smoke test is done at this point.
dom.window.close();
process.exit(failures.length ? 1 : 0);
