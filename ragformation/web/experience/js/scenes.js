// Scene script for the S3 Experience.
//
// Each scene declares:
//   id / number   stable identifiers used by the timeline and by event mapping
//   title         headline
//   caption       what the viewer is looking at (plain language, no marketing)
//   detail        engineering note: what actually happens in the engine
//   stages        engine event stages that drive this scene in live mode
//   palette       colour key for the particle system
//   camera        { position: [x,y,z], target: [x,y,z], fov }
//   formation     procedural geometry builder name + options
//
// Everything here is data. Rendering, timing and interpolation live in
// renderer.js / timeline.js, so scenes can be re-ordered or tested headlessly.

export const PALETTE = {
  ink: [0.06, 0.07, 0.09],
  paper: [0.93, 0.92, 0.89],
  amber: [0.98, 0.72, 0.28],
  cyan: [0.29, 0.82, 0.87],
  violet: [0.62, 0.55, 0.98],
  teal: [0.31, 0.86, 0.66],
  blue: [0.36, 0.62, 0.98],
  gold: [0.95, 0.84, 0.42],
  green: [0.55, 0.9, 0.58],
  red: [0.98, 0.42, 0.4],
  white: [0.98, 0.98, 0.96],
};

export const SCENES = [
  {
    id: "problem",
    number: "00",
    title: "A model without your documents",
    caption:
      "A question arrives with nothing behind it. Without retrieval, an answer is generated from parameters alone — fluent, unverifiable, and sometimes simply wrong.",
    detail:
      "Nothing is indexed yet. The engine has no source to ground a claim, so the only honest output would be an abstention.",
    stages: ["query.received"],
    palette: "red",
    formation: { kind: "dust", count: 1.0, spread: 6.2, hole: 1.15 },
    camera: { position: [0, 0.7, 6.4], target: [0, 0, 0], fov: 48 },
  },
  {
    id: "sources",
    number: "01",
    title: "The source universe",
    caption:
      "Documents enter as data, not as instructions. Each one keeps its provenance: origin, type, hash and revision.",
    detail:
      "Files are parsed in memory, hashed and registered. Retrieved text is treated as untrusted evidence for the rest of its life.",
    stages: ["ingestion.source.accepted"],
    palette: "amber",
    formation: { kind: "cards", count: 1.0, radius: 2.5, cards: 14 },
    camera: { position: [0, 2.4, 5.6], target: [0, 0.1, 0], fov: 46 },
  },
  {
    id: "extraction",
    number: "02",
    title: "Extraction and normalisation",
    caption:
      "A document opens into structure: headings, paragraphs, tables, rows, page anchors. Formatting noise is stripped; structure is kept.",
    detail:
      "Markdown, HTML, DOCX, CSV and JSON each map to blocks with page and section provenance. Normalisation is conservative: no stemming at this stage, no silent deletion.",
    stages: ["ingestion.extracted"],
    palette: "gold",
    formation: { kind: "rows", count: 1.0, rows: 22, width: 5.4, card: 1 },
    camera: { position: [0, 1.2, 4.6], target: [0, 0.2, 0], fov: 44 },
  },
  {
    id: "chunking",
    number: "03",
    title: "Chunking",
    caption:
      "The document becomes heading-aware chunks with overlap. Chunk boundaries decide what can ever be cited, so they are recorded, not guessed.",
    detail:
      "Chunks carry a stable id, token count, section path and content hash. Re-ingesting unchanged content is a no-op; changed content replaces the revision and drops stale chunks.",
    stages: ["ingestion.chunking"],
    palette: "cyan",
    formation: { kind: "blocks", count: 1.0, blocks: 5, perBlock: 90, gap: 0.55 },
    camera: { position: [0, 0.9, 5.0], target: [0, 0, 0], fov: 46 },
  },
  {
    id: "embeddings",
    number: "04",
    title: "Embeddings",
    caption:
      "Each chunk becomes a vector. The badge above tells you which provider produced it — a pretrained model, a corpus-trained LSA model, or a lexical hash fallback.",
    detail:
      "The index fingerprint covers provider, model, dimension, normalisation, similarity metric and preprocessing. Vectors from different fingerprints are never mixed.",
    stages: ["ingestion.embedding"],
    palette: "violet",
    formation: { kind: "torus", count: 1.0, radius: 2.1, tube: 0.75, turns: 5 },
    camera: { position: [0, 1.6, 5.2], target: [0, 0, 0], fov: 42 },
  },
  {
    id: "index",
    number: "05",
    title: "The persistent index",
    caption:
      "Vectors, lexical terms and provenance commit in one transaction. Restart the process and the evidence is still there.",
    detail:
      "SQLite holds vectors, an FTS5 index and source metadata. Ingestion is atomic: a crash leaves the previous revision intact, and an interrupted job is recorded as failed, never as complete.",
    stages: ["ingestion.index.committed", "ingestion.duplicate"],
    palette: "teal",
    formation: { kind: "lattice", count: 1.0, size: 2.4, spacing: 0.42 },
    camera: { position: [2.2, 1.8, 3.6], target: [0, 0, 0], fov: 44 },
  },
  {
    id: "query",
    number: "06",
    title: "The query arrives",
    caption:
      "A question enters the same space. It is embedded with the same provider, and it inherits the caller's permission scope before any search runs.",
    detail:
      "Filters from the client can only narrow the caller's scope. Retrieval never sees a chunk the principal may not read.",
    stages: ["query.received", "query.embedding"],
    palette: "white",
    formation: { kind: "beam", count: 1.0, length: 6.0, radius: 0.35 },
    camera: { position: [0, -1.1, 5.4], target: [0, 0.2, 0], fov: 46 },
  },
  {
    id: "retrieval",
    number: "07",
    title: "Hybrid retrieval",
    caption:
      "Two channels run in parallel: vector similarity and BM25 over the lexical index. Their scores are different quantities and are labelled as such.",
    detail:
      "The dense channel ranks by cosine similarity; the lexical channel by BM25 over FTS5. Each returns a bounded candidate pool.",
    stages: ["retrieval.semantic", "retrieval.lexical"],
    palette: "blue",
    formation: { kind: "branches", count: 1.0, arms: 2, length: 3.4, spread: 0.75 },
    camera: { position: [0, 2.0, 5.0], target: [0, 0, 0], fov: 46 },
  },
  {
    id: "fusion",
    number: "08",
    title: "Fusion and ranking",
    caption:
      "Ranks, not raw scores, are fused with reciprocal rank fusion. Then a cheap reranker orders the survivors by how well they support the question.",
    detail:
      "RRF (k = 60) makes incomparable scores comparable. The reranker is heuristic and swappable; its identity is reported in the trace, and if reranking is disabled the event says 'skipped' rather than pretending.",
    stages: ["retrieval.fusion", "retrieval.rerank", "retrieval.mmr"],
    palette: "gold",
    formation: { kind: "bars", count: 1.0, bars: 7, height: 2.4, spread: 0.6 },
    camera: { position: [0, 0.8, 4.8], target: [0, 0.1, 0], fov: 44 },
  },
  {
    id: "context",
    number: "09",
    title: "Context assembly",
    caption:
      "Evidence is packed into a bounded context window. Chunks that do not fit are recorded with a reason, so you can see what was left out and why.",
    detail:
      "Assembly respects a token budget, removes near-duplicates, keeps provenance, and only ever contains authorised evidence.",
    stages: ["context.assembly"],
    palette: "green",
    formation: { kind: "container", count: 1.0, width: 3.6, height: 2.2, layers: 6 },
    camera: { position: [0, 0.4, 5.0], target: [0, 0, 0], fov: 42 },
  },
  {
    id: "generation",
    number: "10",
    title: "Generation",
    caption:
      "The selected path is shown for what it is: a language model writing prose, or the extractive fallback quoting evidence. Streaming timings are measured, not estimated.",
    detail:
      "In LLM mode the provider streams tokens; TTFT and inter-token latency are recorded per query. In extractive mode no model is involved, and the answer is a quote bound to its chunk.",
    stages: ["generation", "generation.first_token"],
    palette: "white",
    formation: { kind: "stream", count: 1.0, length: 6.4, jitter: 0.18 },
    camera: { position: [0, -0.6, 4.6], target: [0, 0, 0], fov: 46 },
  },
  {
    id: "citation",
    number: "11",
    title: "Citation validation",
    caption:
      "Every marker is mapped back to a stored chunk on the server. A marker that does not resolve is removed; an answer with no resolvable citation is downgraded, not shipped.",
    detail:
      "The model chooses which numbers to write. The server decides which sources those numbers may refer to, and checks that the claim overlaps the cited chunk.",
    stages: ["citation.validation"],
    palette: "cyan",
    formation: { kind: "constellation", count: 1.0, nodes: 12, radius: 2.4 },
    camera: { position: [1.6, 1.4, 4.2], target: [0, 0, 0], fov: 44 },
  },
  {
    id: "decision",
    number: "12",
    title: "Abstention and conflicts",
    caption:
      "When evidence is too thin the answer is an abstention. When two sources disagree the answer is marked for review with both values — never averaged into one confident number.",
    detail:
      "Abstention uses a calibrated support threshold plus the model's own refusal signal. Conflict detection is scoped to same-unit numeric disagreement across two sources with overlapping subjects.",
    stages: ["abstention", "response.ready"],
    palette: "amber",
    formation: { kind: "orbit", count: 1.0, radius: 2.2, clusters: 2 },
    camera: { position: [0, 1.1, 5.2], target: [0, 0, 0], fov: 46 },
  },
  {
    id: "security",
    number: "13",
    title: "Trust boundaries",
    caption:
      "Instruction-like text inside a document is data. It is quarantined, excluded from the prompt and reported as a warning — it cannot grant itself authority.",
    detail:
      "Retrieved text is wrapped and labelled untrusted. Credentials in output are redacted, unverified URLs are neutralised, and a guessed citation id cannot resolve a chunk outside the caller's scope.",
    stages: ["guardrail.scan"],
    palette: "red",
    formation: { kind: "cage", count: 1.0, radius: 2.0, bars: 18 },
    camera: { position: [0, 0.8, 5.0], target: [0, 0, 0], fov: 44 },
  },
  {
    id: "lifecycle",
    number: "14",
    title: "Update, delete, recover",
    caption:
      "A changed document replaces its revision and its old chunks disappear from retrieval. A rebuild stages new vectors and swaps them atomically, keeping the previous index for rollback.",
    detail:
      "Delete removes chunks, vectors and lexical rows in one transaction. Rebuild writes to a staging table, validates counts and dimensions, then swaps; the previous vectors stay available for rollback.",
    stages: ["source.delete", "index.rebuild"],
    palette: "teal",
    formation: { kind: "lattice", count: 1.0, size: 2.2, spacing: 0.5, drift: 0.5 },
    camera: { position: [-2.0, 1.6, 3.8], target: [0, 0, 0], fov: 44 },
  },
  {
    id: "answer",
    number: "15",
    title: "The finished answer",
    caption:
      "One question, one grounded answer, citations that resolve to real chunks, and a trace you can inspect stage by stage.",
    detail:
      "The complete path — ingest, index, retrieve, rerank, assemble, generate, validate — runs in a single process and is visible end to end.",
    stages: ["response.ready"],
    palette: "white",
    formation: { kind: "system", count: 1.0 },
    camera: { position: [0, 2.6, 6.2], target: [0, 0, 0], fov: 48 },
  },
];

export const STAGE_TO_SCENE = (() => {
  const map = new Map();
  for (const scene of SCENES) {
    for (const stage of scene.stages) {
      map.set(stage, scene.id);
    }
  }
  // Additional real stages that should light up an existing scene.
  map.set("ingestion.embedding", "embeddings");
  map.set("ingestion.index_committed", "index");
  map.set("ingestion.index.committed", "index");
  map.set("retrieval.rerank", "fusion");
  map.set("retrieval.mmr", "fusion");
  map.set("generation.started", "generation");
  map.set("generation.completed", "generation");
  map.set("citation.validation", "citation");
  map.set("decision.abstain", "decision");
  map.set("decision.conflict", "decision");
  // Older builds emitted underscores; keep the alias so replays of old traces
  // still land on the right scene.
  map.set("ingestion.source_accepted", "sources");
  map.set("ingestion.index_committed", "index");
  map.set("citation_validation", "citation");
  map.set("context_assembly", "context");
  map.set("response_ready", "decision");
  return map;
})();

export function sceneById(id) {
  return SCENES.find((scene) => scene.id === id) || SCENES[0];
}

export function sceneIndex(id) {
  const index = SCENES.findIndex((scene) => scene.id === id);
  return index < 0 ? 0 : index;
}

export function sceneForStage(stage) {
  const id = STAGE_TO_SCENE.get(stage);
  return id ? sceneById(id) : null;
}
