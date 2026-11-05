// Explorer mode: everything on screen comes from the running server.
//
// Deliberate constraints:
//   * read-only by default (sources, chunks, traces) plus one bounded action (ask);
//   * no credentials are ever read or stored here — if the server needs an API
//     key this page says so and stops, rather than inventing a token;
//   * every failure is shown as text, never swallowed into an empty list.

const API = {
  config: "/api/v1/config",
  health: "/api/v1/health",
  sources: "/api/v1/sources",
  chunks: (id) => `/api/v1/sources/${encodeURIComponent(id)}/chunks`,
  query: "/api/v1/query",
  trace: (id) => `/api/v1/traces/${encodeURIComponent(id)}`,
};

async function getJSON(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { accept: "application/json", ...(options.headers || {}) },
  });
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null;
  }
  if (!response.ok) {
    const message = body?.message || body?.error || `HTTP ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

function describeError(error) {
  if (error.status === 401 || error.status === 403) {
    return "This server requires authorisation. The page deliberately does not read or store API keys — open the console with the key configured, or serve the experience behind the same authenticated proxy.";
  }
  if (error.status === 404) return "That endpoint is not available on this server build.";
  if (error.status === 429) return "Rate limited by the server. Wait a moment and retry.";
  if (error.status >= 500) return "The server reported an internal error. Check the server log; nothing was retried silently.";
  if (error instanceof TypeError) return "The server is not reachable from this page (network error).";
  return error.message || "Request failed.";
}

export function createExplorer(refs, hooks = {}) {
  const onAsk = hooks.onAsk || (() => {});
  const onSelectSource = hooks.onSelectSource || (() => {});
  let selectedSource = null;

  function setHint(text) {
    if (refs.hint) refs.hint.textContent = text;
  }

  function renderList(items, { onSelect, format }) {
    const list = refs.sourceList;
    list.textContent = "";
    if (!items.length) {
      const li = document.createElement("li");
      li.className = "list__empty";
      li.textContent = "no sources indexed yet";
      list.append(li);
      return;
    }
    for (const item of items) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      const { title, meta } = format(item);
      button.textContent = title;
      if (meta) {
        const span = document.createElement("span");
        span.className = "meta";
        span.textContent = meta;
        button.append(span);
      }
      button.addEventListener("click", () => onSelect(item, button));
      li.append(button);
      list.append(li);
    }
  }

  async function loadSources() {
    try {
      const body = await getJSON(API.sources);
      const sources = body.sources || [];
      renderList(sources, {
        format: (source) => ({
          title: source.title || source.sourceId || "untitled",
          meta: `${source.chunks ?? 0} chunks · ${source.mediaType || "document"}`,
        }),
        onSelect: async (source, button) => {
          refs.sourceList.querySelectorAll("button").forEach((el) => el.setAttribute("aria-current", "false"));
          button.setAttribute("aria-current", "true");
          selectedSource = source.sourceId;
          onSelectSource(selectedSource);
          await loadChunks(selectedSource);
        },
      });
      if (refs.sourceCount) refs.sourceCount.textContent = String(sources.length);
      setHint(
        sources.length
          ? `Connected. ${sources.length} sources indexed; select one to read its chunks.`
          : "Connected, but the index is empty. Ingest a document first — the console can do it.",
      );
      return sources;
    } catch (error) {
      const message = describeError(error);
      if (refs.sourceList) {
        refs.sourceList.textContent = "";
        const li = document.createElement("li");
        li.className = "list__empty";
        li.textContent = message;
        refs.sourceList.append(li);
      }
      setHint(message);
      return [];
    }
  }

  async function loadChunks(sourceId) {
    if (!sourceId) return [];
    try {
      const body = await getJSON(API.chunks(sourceId));
      const chunks = body.chunks || [];
      if (refs.chunkCount) refs.chunkCount.textContent = String(chunks.length);
      refs.chunkList.textContent = "";
      if (!chunks.length) {
        const li = document.createElement("li");
        li.className = "list__empty";
        li.textContent = "this source has no chunks";
        refs.chunkList.append(li);
        return [];
      }
      for (const chunk of chunks) {
        const li = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        const preview = (chunk.text || "").replace(/\s+/g, " ").slice(0, 140);
        button.textContent = preview || chunk.chunkId || "chunk";
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = `${chunk.chunkId} · ${chunk.section || "no section"} · ${chunk.tokenCount ?? "?"} tokens`;
        button.append(meta);
        li.append(button);
        refs.chunkList.append(li);
      }
      return chunks;
    } catch (error) {
      const message = describeError(error);
      refs.chunkList.textContent = "";
      const li = document.createElement("li");
      li.className = "list__empty";
      li.textContent = message;
      refs.chunkList.append(li);
      return [];
    }
  }

  async function ask(question) {
    const trimmed = (question || "").trim();
    if (!trimmed) return null;
    refs.answer.textContent = "";
    const pending = document.createElement("p");
    pending.textContent = "Asking the running index…";
    refs.answer.append(pending);
    try {
      const body = await getJSON(API.query, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
      });
      refs.answer.textContent = "";
      const status = document.createElement("span");
      status.className = "status";
      status.dataset.status = body.status || "unknown";
      status.textContent = `${body.status || "unknown"} · support ${Number(body.support ?? 0).toFixed(2)}`;
      const text = document.createElement("p");
      text.textContent = body.answer || "(empty answer)";
      refs.answer.append(status, text);
      const citations = body.citations || [];
      if (citations.length) {
        const list = document.createElement("ol");
        for (const citation of citations) {
          const li = document.createElement("li");
          li.textContent = `${citation.sourceTitle || citation.sourceId} — ${citation.section || "section"}${
            citation.page ? `, p.${citation.page}` : ""
          }`;
          list.append(li);
        }
        refs.answer.append(list);
      }
      if (body.queryId) await loadTrace(body.queryId);
      onAsk(body);
      return body;
    } catch (error) {
      refs.answer.textContent = "";
      const p = document.createElement("p");
      p.textContent = describeError(error);
      refs.answer.append(p);
      return null;
    }
  }

  async function loadTrace(queryId) {
    if (!queryId || !refs.trace) return null;
    try {
      const body = await getJSON(API.trace(queryId));
      const stages = body.trace?.stages || body.stages || body.events || [];
      refs.trace.textContent = "";
      if (!stages.length) {
        const li = document.createElement("li");
        li.className = "list__empty";
        li.textContent = "trace contains no stages";
        refs.trace.append(li);
        return body;
      }
      for (const stage of stages.slice(0, 24)) {
        const li = document.createElement("li");
        const name = document.createElement("span");
        name.textContent = stage.stage || stage.name || "stage";
        const state = document.createElement("span");
        state.className = stage.status === "ok" || stage.status === "completed" ? "ok" : "";
        state.textContent = stage.status || "";
        const ms = document.createElement("span");
        ms.className = "ms";
        ms.textContent = Number.isFinite(stage.durationMs) ? `${stage.durationMs.toFixed(1)}ms` : "";
        li.append(name, ms);
        li.insertBefore(state, ms);
        refs.trace.append(li);
      }
      return body;
    } catch (error) {
      refs.trace.textContent = "";
      const li = document.createElement("li");
      li.className = "list__empty";
      li.textContent = describeError(error);
      refs.trace.append(li);
      return null;
    }
  }

  async function loadHealth() {
    try {
      return await getJSON(API.health);
    } catch (error) {
      return null;
    }
  }

  async function loadConfig() {
    try {
      return await getJSON(API.config);
    } catch (error) {
      return { error: describeError(error) };
    }
  }

  return {
    loadSources,
    loadChunks,
    ask,
    loadTrace,
    loadConfig,
    loadHealth,
    get selectedSource() {
      return selectedSource;
    },
  };
}

export { API as EXPLORER_API };
