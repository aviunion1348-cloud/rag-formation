/* ragformation console.
   Vanilla JS, no build step, relative URLs only (works behind a proxy).
   Every number rendered here comes from the API response — nothing is faked. */

const state = {
  answer: null,
  sources: [],
  stats: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

/* ------------------------------------------------------------------ helpers */
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function toast(message, isError = false) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.toggle("error", isError);
  box.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { box.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let payload = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) {
    const message = (payload && (payload.message || payload.hint)) || `request failed (${response.status})`;
    throw new Error(message);
  }
  return payload;
}

const fmt = (value, digits = 1) =>
  value === null || value === undefined ? "—" : Number(value).toFixed(digits);
const ms = (value) => (value === null || value === undefined ? "—" : `${fmt(value, 1)} ms`);

/* -------------------------------------------------------------- health bar */
async function loadHealth() {
  const box = $("#providers");
  try {
    const health = await api("/api/health");
    const embedding = health.embedding;
    const generation = health.generation;
    const integrityOk = health.integrity.problems.length === 0;
    box.replaceChildren(
      el("span", {
        class: `chip ${embedding.semantic ? "ok" : "warn"}`,
        title: embedding.note,
        text: `embed ${embedding.provider}/${embedding.model} · ${embedding.semantic ? "semantic" : "lexical-only"}`,
      }),
      el("span", {
        class: `chip ${generation.neural ? "ok" : "warn"}`,
        title: generation.note,
        text: `llm ${generation.provider}/${generation.model}`,
      }),
      el("span", {
        class: `chip ${integrityOk ? "ok" : "bad"}`,
        title: integrityOk ? "index integrity ok" : JSON.stringify(health.integrity.problems),
        text: `${health.chunks} chunks · ${health.sources} sources`,
      })
    );
  } catch (error) {
    box.replaceChildren(el("span", { class: "chip bad", text: `offline: ${error.message}` }));
  }
}

/* ---------------------------------------------------------------- sources */
async function loadSources() {
  const list = $("#source-list");
  try {
    const data = await api("/api/sources");
    state.sources = data.sources || [];
    $("#source-count").textContent = `${state.sources.length} indexed`;
    if (!state.sources.length) {
      list.replaceChildren(
        el("li", {}, [
          el("p", { class: "note", text: "Nothing indexed yet. Use “Index corpus/” to load the sample documents." }),
        ])
      );
      return;
    }
    list.replaceChildren(
      ...state.sources.map((source) =>
        el("li", {}, [
          el("div", { class: "source-head" }, [
            el("div", {}, [
              el("div", { class: "source-title", text: source.title }),
              el("div", {
                class: "source-meta",
                text: `${source.chunks} chunks · ${source.tokens} tok · rev ${source.revision} · ${source.status}`,
              }),
            ]),
            el("div", { class: "source-actions" }, [
              el("button", {
                class: "icon-btn",
                type: "button",
                title: `Delete ${source.title}`,
                text: "delete",
                onclick: () => deleteSource(source),
              }),
            ]),
          ]),
        ])
      )
    );
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteSource(source) {
  if (!confirm(`Delete “${source.title}” and every derived chunk and vector?`)) return;
  try {
    const result = await api(`/api/sources/${encodeURIComponent(source.sourceId)}`, { method: "DELETE" });
    toast(`Removed ${result.removedChunks} chunks · index now ${result.indexSize}`);
    await Promise.all([loadSources(), loadHealth(), loadStats()]);
  } catch (error) {
    toast(error.message, true);
  }
}

async function ingestSample() {
  const button = $("#ingest-sample");
  button.disabled = true;
  button.textContent = "indexing…";
  try {
    const report = await api("/api/ingest", { method: "POST", body: JSON.stringify({ path: "corpus" }) });
    const failed = (report.results || []).filter((row) => row.status === "failed");
    toast(
      failed.length
        ? `${failed.length} file(s) failed: ${failed[0].error?.message || "unknown error"}`
        : `indexed ${report.indexed} · unchanged ${report.unchanged} · ${report.chunks} chunks total`,
      failed.length > 0
    );
    await Promise.all([loadSources(), loadHealth(), loadStats()]);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.replaceChildren("Index ", el("code", { text: "corpus/" }));
  }
}

async function uploadFile(file) {
  if (!file) return;
  const maxBytes = 8 * 1024 * 1024;
  if (file.size > maxBytes) {
    toast("File is larger than the 8 MiB ingest limit", true);
    return;
  }
  toast(`uploading ${file.name}…`);
  try {
    const buffer = await file.arrayBuffer();
    let binary = "";
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    const report = await api("/api/sources/upload", {
      method: "POST",
      body: JSON.stringify({ filename: file.name, contentBase64: btoa(binary) }),
    });
    const row = report.results[0];
    if (row.status === "failed") {
      toast(row.error?.message || "ingest failed", true);
    } else {
      toast(`${row.filename}: ${row.chunks} chunks indexed (rev ${row.revision})`);
    }
    await Promise.all([loadSources(), loadHealth()]);
  } catch (error) {
    toast(error.message, true);
  }
}

/* ------------------------------------------------------------------- ask */
function linkCitations(text, citations) {
  const byId = new Map(citations.map((citation) => [citation.evidenceId, citation]));
  const parts = String(text || "").split(/(\[\d{1,2}\])/g);
  return parts.map((part) => {
    const match = part.match(/^\[(\d{1,2})\]$/);
    if (!match) return document.createTextNode(part);
    const id = Number(match[1]);
    const citation = byId.get(id);
    return el("button", {
      class: "cite",
      type: "button",
      text: String(id),
      title: citation
        ? `${citation.sourceTitle} — ${citation.section} (p.${citation.page})`
        : `evidence ${id}`,
      onclick: () => focusEvidence(id),
    });
  });
}

function focusEvidence(id) {
  const tab = $$("#tabs .tab").find((node) => node.dataset.tab === "evidence");
  if (tab) selectTab(tab);
  const target = document.querySelector(`[data-evidence-id="${id}"]`);
  if (target) {
    target.classList.add("highlight");
    target.scrollIntoView({ block: "nearest", behavior: "smooth" });
    setTimeout(() => target.classList.remove("highlight"), 2200);
  }
}

function statusBadge(status) {
  return el("span", { class: `badge ${status}`, text: status.replace("_", " ") });
}

function renderAnswer(result) {
  state.answer = result;
  const region = $("#answer-region");
  region.replaceChildren();

  const card = el("div", { class: "answer-card" });
  const head = el("div", { class: "answer-head" }, [
    statusBadge(result.status),
    el("span", {
      class: "metric",
      html: `<b>${fmt(result.metrics.bestSupport, 2)}</b> retrieval support`,
    }),
    el("span", { class: "metric", html: `<b>${ms(result.metrics.totalMs)}</b> total` }),
    el("span", {
      class: "metric",
      html: `<b>${result.provider.used || result.provider.provider}</b> · ${result.provider.model}`,
    }),
  ]);
  card.append(head);

  const paragraphs = el("div", { class: "answer-text" });
  const blocks = String(result.answer || "").split(/\n{2,}/);
  for (const block of blocks) {
    if (!block.trim()) continue;
    paragraphs.append(el("p", {}, linkCitations(block.trim(), result.citations)));
  }
  if (!blocks.length) paragraphs.append(el("p", { text: "No answer text." }));
  card.append(paragraphs);

  if (result.citations?.length) {
    card.append(
      el("div", { class: "metric-strip", style: "margin-top:10px" },
        result.citations.map((citation) =>
          el("button", {
            class: "metric",
            type: "button",
            style: "cursor:pointer",
            title: citation.excerpt,
            html: `<b>[${citation.evidenceId}]</b> ${citation.sourceTitle} · p.${citation.page}`,
            onclick: () => focusEvidence(citation.evidenceId),
          })
        )
      )
    );
  }

  if (result.conflict) {
    card.append(
      el("p", { class: "note warn", text: `Conflicting evidence: ${JSON.stringify(result.conflict.statements)}` })
    );
  }
  if (result.warnings?.length) {
    card.append(el("p", { class: "note", text: `warnings: ${result.warnings.join(", ")}` }));
  }
  if (result.status === "needs_review" && result.abstainReason) {
    card.append(el("p", { class: "note warn", text: result.abstainReason }));
  }

  region.append(card);

  const feedback = el("div", { class: "metric-strip" }, [
    el("button", {
      class: "pill", type: "button", text: "helpful",
      onclick: () => sendFeedback(result.queryId, 1),
    }),
    el("button", {
      class: "pill", type: "button", text: "not helpful",
      onclick: () => sendFeedback(result.queryId, -1),
    }),
    el("span", { class: "source-meta", text: `query ${result.queryId}` }),
  ]);
  region.append(feedback);

  renderEvidence(result);
  renderTrace(result);
  renderQueryMetrics(result);
  renderSafety(result);

  $("#tabs").hidden = false;
  $("#tab-panels").hidden = false;
}

async function sendFeedback(queryId, rating) {
  try {
    await api("/api/feedback", { method: "POST", body: JSON.stringify({ queryId, rating }) });
    toast("feedback recorded");
  } catch (error) {
    toast(error.message, true);
  }
}

function renderEvidence(result) {
  const panel = document.querySelector('[data-panel="evidence"]');
  if (!result.evidence?.length) {
    panel.replaceChildren(el("p", { class: "note", text: "No passages were retrieved." }));
    return;
  }
  panel.replaceChildren(
    ...result.evidence.map((item) =>
      el("div", { class: "evidence-item", "data-evidence-id": item.evidenceId }, [
        el("div", { class: "evidence-head" }, [
          el("strong", { text: `[${item.evidenceId}] ${item.sourceTitle}` }),
          el("span", {
            class: "evidence-meta",
            text: `${item.section} · p.${item.pageStart} · support ${fmt(item.support, 2)} · ${item.channel}`,
          }),
        ]),
        el("p", { class: "evidence-text", text: item.text }),
        el("p", { class: "evidence-meta", text: item.chunkId }),
      ])
    )
  );
}

function renderTrace(result) {
  const panel = document.querySelector('[data-panel="trace"]');
  const rows = (result.trace || []).map((event) =>
    el("li", {}, [
      el("span", { class: "stage", text: event.stage }),
      el("span", { text: event.status }),
      el("span", { class: "dur", text: event.durationMs ? `${fmt(event.durationMs, 2)} ms` : "" }),
      el("span", { class: "detail", text: event.detail ? JSON.stringify(event.detail) : "" }),
    ])
  );
  panel.replaceChildren(el("ul", { class: "trace-list" }, rows));
}

function renderQueryMetrics(result) {
  const panel = document.querySelector('[data-panel="metrics"]');
  const metrics = result.metrics || {};
  const rows = [
    ["retrieval support (best)", fmt(metrics.bestSupport, 3)],
    ["total latency", ms(metrics.totalMs)],
    ["retrieval latency", ms(metrics.retrievalMs)],
    ["generation latency", ms(metrics.generationMs)],
    ["time to first token", ms(metrics.ttftMs)],
    ["inter-token latency", ms(metrics.itlMs)],
    ["context tokens (est.)", metrics.contextTokens ?? "—"],
    ["completion tokens", metrics.completionTokens ?? "—"],
    ["semantic candidates", metrics.semanticCandidates ?? "—"],
    ["lexical candidates", metrics.lexicalCandidates ?? "—"],
    ["reranked", metrics.reranked ? "yes" : "no"],
    ["claims with citations", fmt(metrics.claimCitationRate, 2)],
    ["invalid citation markers removed", metrics.citationsInvalid ?? 0],
  ];
  const table = el("table", { class: "metric-table" }, [
    el("thead", {}, [el("tr", {}, [el("th", { text: "metric" }), el("th", { text: "value" })])]),
    el("tbody", {}, rows.map(([label, value]) =>
      el("tr", {}, [el("td", { text: label }), el("td", { text: String(value) })])
    )),
  ]);

  const fleet = state.stats;
  const aggregate = fleet
    ? el("div", {}, [
        el("h3", { class: "evidence-meta", text: `Fleet-wide, last ${fleet.window} queries`, style: "margin:16px 0 6px" }),
        el("table", { class: "metric-table" }, [
          el("thead", {}, [el("tr", {}, [
            el("th", { text: "signal" }), el("th", { text: "mean" }), el("th", { text: "p50" }), el("th", { text: "p95" }),
          ])]),
          el("tbody", {}, [
            ["latency (ms)", fleet.latencyMs],
            ["retrieval (ms)", fleet.retrievalMs],
            ["generation (ms)", fleet.generationMs],
            ["ttft (ms)", fleet.ttftMs],
            ["itl (ms)", fleet.itlMs],
          ].map(([label, block]) => el("tr", {}, [
            el("td", { text: label }),
            el("td", { text: block.count ? fmt(block.mean) : "—" }),
            el("td", { text: block.count ? fmt(block.p50) : "—" }),
            el("td", { text: block.count ? fmt(block.p95) : "—" }),
          ]))),
        ]),
      ])
    : null;

  panel.replaceChildren(table);
  if (aggregate) panel.append(aggregate);
}

function renderSafety(result) {
  const panel = document.querySelector('[data-panel="safety"]');
  const blocks = [];

  const quarantined = result.quarantined || [];
  blocks.push(
    el("div", { class: "safety-block" }, [
      el("h3", { text: `Quarantined passages: ${quarantined.length}` }),
      quarantined.length
        ? el("ul", {}, quarantined.map((item) =>
            el("li", { text: `${item.sourceTitle} — rules: ${item.rules.join(", ")} — “${item.excerpt}”` })
          ))
        : el("p", { class: "note", text: "No instruction-like content was detected in the retrieved passages." }),
    ])
  );

  const excluded = result.excluded || [];
  blocks.push(
    el("div", { class: "safety-block" }, [
      el("h3", { text: `Candidates excluded from context: ${excluded.length}` }),
      excluded.length
        ? el("ul", {}, excluded.slice(0, 12).map((item) =>
            el("li", { text: `${item.sourceTitle} · ${item.reason} (support ${fmt(item.support, 2)})` })
          ))
        : el("p", { class: "note", text: "Every retrieved candidate fit inside the context budget." }),
    ])
  );

  blocks.push(
    el("div", { class: "safety-block" }, [
      el("h3", { text: "Policy applied to this answer" }),
      el("ul", {}, [
        el("li", { text: "Retrieved text is treated as data; instruction-like passages never enter the prompt." }),
        el("li", { text: "Citation markers are re-validated server-side; unknown ids are removed." }),
        el("li", { text: "URLs absent from the evidence are neutralised; credential-shaped strings are redacted." }),
        el("li", { text: `Answer status: ${result.status} — insufficient evidence produces an abstention, not a guess.` }),
      ]),
    ])
  );

  panel.replaceChildren(...blocks);
}

async function ask(question) {
  const button = $("#ask-button");
  const region = $("#answer-region");
  button.disabled = true;
  button.textContent = "thinking…";
  region.replaceChildren(el("div", { class: "placeholder", text: "Retrieving evidence, then generating a grounded answer…" }));
  try {
    const result = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({
        question,
        topK: Number($("#topk").value),
        mode: $("#mode").value,
        temperature: Number($("#temperature").value),
      }),
    });
    renderAnswer(result);
    await loadStats();
    renderQueryMetrics(result);
  } catch (error) {
    region.replaceChildren(el("div", { class: "placeholder", text: `request failed: ${error.message}` }));
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Ask";
  }
}

/* ------------------------------------------------------------------ stats */
async function loadStats() {
  try {
    state.stats = await api("/api/stats");
  } catch { /* metrics are non-critical for the page to work */ }
}

/* ------------------------------------------------------------------- tabs */
function selectTab(tab) {
  for (const node of $$("#tabs .tab")) node.classList.toggle("active", node === tab);
  for (const panel of $$(".tab-panel")) {
    panel.classList.toggle("active", panel.dataset.panel === tab.dataset.tab);
  }
}

/* ------------------------------------------------------------------- wiring */
function init() {
  $("#ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const question = $("#question").value.trim();
    if (question) ask(question);
  });

  $("#question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      $("#ask-form").requestSubmit();
    }
  });

  $("#topk").addEventListener("input", (event) => { $("#topk-out").textContent = event.target.value; });
  $("#temperature").addEventListener("input", (event) => { $("#temp-out").textContent = event.target.value; });
  $("#mode").addEventListener("change", (event) => {
    $("#mode-note").textContent = `${event.target.value} · rerank on`;
  });

  for (const pill of $$(".pill[data-q]")) {
    pill.addEventListener("click", () => {
      $("#question").value = pill.dataset.q;
      $("#question").focus();
      ask(pill.dataset.q);
    });
  }

  $("#ingest-sample").addEventListener("click", ingestSample);
  $("#refresh").addEventListener("click", () => { loadSources(); loadHealth(); loadStats(); });
  $("#rebuild").addEventListener("click", async () => {
    try {
      const result = await api("/api/rebuild", { method: "POST", body: JSON.stringify({}) });
      toast(`re-embedded ${result.chunks} chunks`);
      await Promise.all([loadHealth(), loadSources()]);
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#file-input").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    uploadFile(file);
  });

  $("#toggle-paste").addEventListener("click", (event) => {
    const form = $("#paste-form");
    form.hidden = !form.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!form.hidden));
    if (!form.hidden) $("#paste-title").focus();
  });
  $("#cancel-paste").addEventListener("click", () => {
    $("#paste-form").hidden = true;
    $("#toggle-paste").setAttribute("aria-expanded", "false");
  });

  $("#paste-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = $("#paste-title").value.trim();
    const text = $("#paste-text").value.trim();
    if (!text) { toast("nothing to index", true); return; }
    try {
      const report = await api("/api/sources/text", {
        method: "POST",
        body: JSON.stringify({ title: title || "pasted note", text }),
      });
      const row = report.results[0];
      toast(row.status === "failed" ? row.error.message : `indexed ${row.chunks} chunks`);
      $("#paste-text").value = "";
      $("#paste-form").hidden = true;
      await Promise.all([loadSources(), loadHealth()]);
    } catch (error) {
      toast(error.message, true);
    }
  });

  for (const tab of $$("#tabs .tab")) {
    tab.addEventListener("click", () => selectTab(tab));
  }

  $("#answer-region").append(
    el("div", { class: "placeholder", text: "Ask a question about the indexed corpus. Evidence, trace, metrics and safety findings appear here." })
  );

  Promise.all([loadHealth(), loadSources(), loadStats()]);
}

document.addEventListener("DOMContentLoaded", init);
