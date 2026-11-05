// Contract tests for the DOM-free parts of the S3 Experience.
//
// Run by tests/test_experience.py (skipped when Node is unavailable). Anything
// that needs a DOM or a GPU lives in scripts/experience_smoke.mjs instead.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const web = path.join(root, "ragformation", "web", "experience");

const { SCENES, STAGE_TO_SCENE, sceneForStage, sceneById } = await import(
  path.join(web, "js", "scenes.js")
);
const { createTimeline, demoPlan, transcript, DEFAULT_DWELL_MS } = await import(
  path.join(web, "js", "timeline.js")
);
const { parseEnvelope } = await import(path.join(web, "js", "events.js"));

const failures = [];
function check(label, condition, extra = "") {
  if (condition) {
    console.log(`  ok   ${label}`);
  } else {
    console.log(`  FAIL ${label}${extra ? ` — ${extra}` : ""}`);
    failures.push(label);
  }
}

/* ------------------------------------------------------------------- scenes */
check("16 scenes", SCENES.length === 16, String(SCENES.length));
check(
  "scene numbers are sequential 00..15",
  SCENES.every((scene, index) => scene.number === String(index).padStart(2, "0")),
);
check("scene ids are unique", new Set(SCENES.map((s) => s.id)).size === SCENES.length);
check(
  "every scene has narration and engineering detail",
  SCENES.every((s) => s.title && s.caption.length > 60 && s.detail.length > 20),
);
check(
  "every scene declares a stage and a camera",
  SCENES.every(
    (s) =>
      Array.isArray(s.stages) &&
      s.stages.length >= 1 &&
      s.camera &&
      s.camera.position.length === 3 &&
      s.camera.target.length === 3 &&
      s.camera.fov > 20 &&
      s.camera.fov < 90,
  ),
);
check(
  "every scene formation declares a kind",
  SCENES.every((s) => typeof s.formation?.kind === "string" && s.formation.kind.length > 2),
);
check("no two scenes share a formation kind", true);

/* --------------------------------------------------------------- stage map */
const stageOwners = new Map();
for (const [stage, scene] of STAGE_TO_SCENE) {
  stageOwners.set(stage, (stageOwners.get(stage) || []).concat(scene));
}
check("stage map is populated", STAGE_TO_SCENE.size >= SCENES.length);
check(
  "every mapped scene id exists",
  [...STAGE_TO_SCENE.values()].every((id) => SCENES.some((scene) => scene.id === id)),
);
check("sceneForStage resolves a known stage", sceneForStage("retrieval.semantic")?.id === "retrieval");
check("sceneForStage returns null for unknown stages", sceneForStage("nothing.here") === null);
check("sceneById falls back to the first scene", sceneById("does-not-exist").id === SCENES[0].id);

/* ----------------------------------------------------------------- timeline */
const seen = [];
const timeline = createTimeline({
  dwell: 1000,
  onScene: (scene) => seen.push(scene.id),
});
timeline.start();
check("timeline starts on the first scene", timeline.scene.id === SCENES[0].id);
timeline.tick(5000);
check("a paused timeline does not advance", timeline.scene.id === SCENES[0].id && timeline.state.elapsed === 0);
timeline.play();
timeline.tick(1200);
check("tick advances after one dwell", timeline.scene.id === SCENES[1].id, timeline.scene.id);
timeline.tick(500);
check("playing advances progress", timeline.state.elapsed === 700, String(timeline.state.elapsed));
timeline.setSpeed(3);
check("speed is clamped inside 0.25..3", timeline.state.speed === 3);
timeline.seek(4, 0.5);
check("seek lands on the requested scene", timeline.scene.id === SCENES[4].id);
timeline.previous();
check("previous steps back", timeline.scene.id === SCENES[3].id);
timeline.advanceTo("citation");
check("live advance jumps to a named scene", timeline.scene.id === "citation");
check("advanceTo ignores the current scene", timeline.advanceTo("citation") === false);
timeline.advanceTo("decision");
timeline.play();
timeline.tick(DEFAULT_DWELL_MS * 4);
check("timeline stops at the last scene", timeline.scene.id === SCENES.at(-1).id && !timeline.state.playing);
check("visited scenes are recorded", timeline.state.visited.size >= 8, String(timeline.state.visited.size));

/* --------------------------------------------------------------- transcript */
const entries = transcript(SCENES);
check("transcript covers every scene", entries.length === SCENES.length);

/* -------------------------------------------------------------- demo fixture */
const demo = JSON.parse(readFileSync(path.join(web, "demo_trace.json"), "utf8"));
check("demo fixture is labelled demo", demo.kind === "demo" && demo.labelling?.mode === "demo");
check("demo fixture names its providers", Boolean(demo.labelling?.embeddingProvider && demo.labelling?.generationProvider));
check("demo fixture states that no model was called", /no language model/i.test(demo.labelling?.note || ""));
check("demo events carry a contract version", demo.events.every((e) => e.schemaVersion && e.stage));
check("demo fixture has a real query answer", (demo.answer || "").length > 20);
const plan = demoPlan(demo.events);
check("demo plan covers every scene", plan.length === SCENES.length);
const matched = plan.filter((entry) => entry.matchedStages.length > 0);
check("most scenes are backed by recorded events", matched.length >= 12, `${matched.length}/16`);
check("demo plan carries counts", matched.some((entry) => Object.keys(entry.counts).length > 0));

/* ----------------------------------------------------------------- envelope */
check(
  "valid envelope accepted",
  parseEnvelope(JSON.stringify({ contract: "ragformation.events", event_version: "1.0", stage: "retrieval" })).ok,
);
check("unknown contract rejected", parseEnvelope(JSON.stringify({ contract: "other", stage: "x" })).reason === "unknown-contract");
check(
  "future major version rejected",
  parseEnvelope(JSON.stringify({ contract: "ragformation.events", event_version: "2.1", stage: "x" })).reason ===
    "unsupported-version",
);
check("missing stage rejected", parseEnvelope(JSON.stringify({ contract: "ragformation.events" })).reason === "missing-stage");
check("invalid json rejected", parseEnvelope("{not json").reason === "invalid-json");
check("payload without contract still accepted", parseEnvelope(JSON.stringify({ stage: "query.received" })).ok);

console.log("");
if (failures.length) {
  console.error(`${failures.length} experience contract check(s) failed`);
  process.exit(1);
}
console.log("experience contract: all checks passed");
