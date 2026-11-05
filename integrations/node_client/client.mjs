// Node 18+ client for the RagFormation HTTP API (no dependencies).
//
// Same contract as the Python client: identity comes from a credential or a
// host principal header, errors are structured, reads retry with backoff,
// writes do not.
//
//   import { Client } from "./client.mjs";
//   const client = new Client("http://127.0.0.1:8000");
//   console.log((await client.ask("How long are audit logs retained?")).status);

export class RagFormationError extends Error {
  constructor(message, { code = "client_error", status = 0, requestId = null, hint = null, payload = {} } = {}) {
    super(message);
    this.name = "RagFormationError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.hint = hint;
    this.payload = payload;
  }

  get retryable() {
    return [429, 502, 503, 504].includes(this.status) || ["rate_limited", "unavailable"].includes(this.code);
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export class Client {
  constructor(baseUrl = "http://127.0.0.1:8000", options = {}) {
    this.baseUrl = String(baseUrl).replace(/\/+$/, "");
    this.apiKey = options.apiKey ?? null;
    this.principal = options.principal ?? null;
    this.principalHeader = options.principalHeader ?? "x-rag-principal";
    this.timeoutMs = options.timeoutMs ?? 30000;
    this.maxRetries = Math.max(0, options.maxRetries ?? 2);
  }

  headers(extra = {}) {
    const headers = { accept: "application/json", "user-agent": "ragformation-node-client/0.2.0", ...extra };
    if (this.apiKey) headers["x-api-key"] = this.apiKey;
    if (this.principal) headers[this.principalHeader] = JSON.stringify(this.principal);
    return headers;
  }

  async request(method, path, { body, query, retry } = {}) {
    let url = this.baseUrl + path;
    if (query) {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== null) params.append(key, String(value));
      }
      const encoded = params.toString();
      if (encoded) url += `?${encoded}`;
    }
    const attempts = (retry === undefined ? this.maxRetries : Number(retry)) + 1;
    let lastError = null;

    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const response = await fetch(url, {
          method,
          headers: this.headers(body === undefined ? {} : { "content-type": "application/json" }),
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: controller.signal,
        });
        const text = await response.text();
        let payload = {};
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch {
            payload = { message: text.slice(0, 200) };
          }
        }
        if (!response.ok) {
          const error = new RagFormationError(payload.message || `HTTP ${response.status}`, {
            code: payload.code || payload.error || "http_error",
            status: response.status,
            requestId: payload.requestId ?? null,
            hint: payload.hint ?? null,
            payload,
          });
          if (!error.retryable || attempt === attempts - 1) throw error;
          lastError = error;
        } else {
          return payload;
        }
      } catch (error) {
        if (error instanceof RagFormationError) {
          if (!error.retryable || attempt === attempts - 1) throw error;
          lastError = error;
        } else {
          const wrapped = new RagFormationError(
            `transport error: ${error.name === "AbortError" ? "request timed out" : error.message}`,
            { code: error.name === "AbortError" ? "timeout" : "transport_error" },
          );
          if (attempt === attempts - 1) throw wrapped;
          lastError = wrapped;
        }
      } finally {
        clearTimeout(timer);
      }
      await sleep(Math.min(2000, 250 * 2 ** attempt));
    }
    throw lastError ?? new RagFormationError("request failed");
  }

  health() {
    return this.request("GET", "/api/v1/health");
  }

  ready() {
    return this.request("GET", "/api/v1/ready");
  }

  config() {
    return this.request("GET", "/api/v1/config");
  }

  async sources(limit = 100) {
    const body = await this.request("GET", "/api/v1/sources", { query: { limit } });
    return body.sources ?? [];
  }

  async chunks(sourceId) {
    const body = await this.request("GET", `/api/v1/sources/${encodeURIComponent(sourceId)}/chunks`);
    return body.chunks ?? [];
  }

  trace(queryId) {
    return this.request("GET", `/api/v1/traces/${encodeURIComponent(queryId)}`);
  }

  ingestText(filename, text) {
    return this.request("POST", "/api/v1/sources", { body: { filename, text }, retry: false });
  }

  async ingestFile(file) {
    // `file` is a Blob/File (Node 20) or Buffer + filename.
    const form = new FormData();
    if (typeof Blob !== "undefined" && file instanceof Blob) {
      form.append("file", file, file.name ?? "upload.bin");
    } else {
      form.append("file", new Blob([file.data]), file.filename);
    }
    const response = await fetch(`${this.baseUrl}/api/v1/sources`, {
      method: "POST",
      headers: this.headers(), // let the runtime set the multipart boundary
      body: form,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new RagFormationError(payload.message || `HTTP ${response.status}`, {
        code: payload.code || "upload_failed",
        status: response.status,
        requestId: payload.requestId ?? null,
        payload,
      });
    }
    return payload;
  }

  deleteSource(sourceId) {
    return this.request("DELETE", `/api/v1/sources/${encodeURIComponent(sourceId)}`, { retry: false });
  }

  ask(question, { topK, mode, allowedSources } = {}) {
    const body = { question };
    if (topK !== undefined) body.topK = topK;
    if (mode !== undefined) body.mode = mode;
    if (allowedSources !== undefined) body.allowedSources = allowedSources;
    return this.request("POST", "/api/v1/query", { body });
  }

  async recentEvents(since = 0) {
    const body = await this.request("GET", "/api/v1/events/recent", { query: { since } });
    return body.events ?? [];
  }

  /** Async iterator over server-sent events (Node 18+ has ReadableStream). */
  async *streamEvents({ traceId } = {}) {
    const query = traceId ? `?trace_id=${encodeURIComponent(traceId)}` : "";
    const response = await fetch(`${this.baseUrl}/api/v1/events${query}`, {
      headers: this.headers({ accept: "text/event-stream" }),
    });
    if (!response.ok || !response.body) {
      throw new RagFormationError(`stream failed with HTTP ${response.status}`, { status: response.status });
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const chunk = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = chunk
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("\n");
        if (data) {
          try {
            yield JSON.parse(data);
          } catch {
            /* a comment or heartbeat */
          }
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  }
}
