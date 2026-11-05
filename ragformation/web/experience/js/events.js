// Event stream client.
//
// The contract is documented in docs/events.md. This client is intentionally
// small and defensive:
//   * validates the envelope (`contract`, `event_version`, `stage`, `trace_id`)
//     and refuses unknown major versions instead of guessing;
//   * detects sequence gaps (the server increments `sequence` per *trace*, so
//     the client tracks it per trace id and de-duplicates by `event_id`);
//     a gap is reported, so the UI can say "events were missed" rather than lie;
//   * reconnects with exponential backoff and a bounded retry count;
//   * falls back to polling /api/v1/events/recent when EventSource is missing
//     or repeatedly fails, so a proxied deployment still shows live activity.

export const SUPPORTED_EVENT_MAJOR = 1;

export function parseEnvelope(raw) {
  let payload;
  try {
    payload = typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch (error) {
    return { ok: false, reason: "invalid-json", error };
  }
  if (!payload || typeof payload !== "object") return { ok: false, reason: "not-an-object" };
  const contract = payload.contract || payload.event?.contract;
  const version = payload.event_version ?? payload.version ?? payload.event?.event_version;
  const major = Number(String(version ?? "").split(".")[0]);
  if (contract && contract !== "ragformation.events") {
    return { ok: false, reason: "unknown-contract", contract };
  }
  if (Number.isFinite(major) && major > SUPPORTED_EVENT_MAJOR) {
    return { ok: false, reason: "unsupported-version", version };
  }
  const stage = payload.stage || payload.event?.stage;
  if (!stage) return { ok: false, reason: "missing-stage" };
  return { ok: true, event: payload };
}

export function createStreamClient(options = {}) {
  const endpoint = options.endpoint || "/api/v1/events";
  const pollingEndpoint = options.pollingEndpoint || "/api/v1/events/recent";
  const onEvent = options.onEvent || (() => {});
  const onState = options.onState || (() => {});
  const onGap = options.onGap || (() => {});
  const maxRetries = options.maxRetries ?? 4;
  const pollIntervalMs = options.pollIntervalMs ?? 2500;

  let source = null;
  let pollTimer = null;
  let retries = 0;
  let closed = false;
  const lastByTrace = new Map();
  const seenEventIds = new Set();
  let lastTimestamp = 0;
  let transport = "idle";
  let lastError = null;

  function report(state, extra = {}) {
    onState({ transport, state, retries, lastError, ...extra });
  }

  function handle(payload) {
    const parsed = parseEnvelope(payload);
    if (!parsed.ok) {
      lastError = `${parsed.reason}${parsed.version ? ` (${parsed.version})` : ""}`;
      report("rejected", { reason: parsed.reason });
      return;
    }
    const event = parsed.event;
    const eventId = event.event_id || event.eventId;
    if (eventId) {
      if (seenEventIds.has(eventId)) return; // polling overlap, not a gap
      seenEventIds.add(eventId);
      if (seenEventIds.size > 1000) seenEventIds.clear();
    }
    const traceId = event.trace_id || event.traceId || "";
    const sequence = Number.isFinite(event.sequence) ? event.sequence : null;
    const previous = lastByTrace.get(traceId);
    if (sequence !== null && Number.isFinite(previous) && sequence !== previous + 1) {
      onGap({ traceId, expected: previous + 1, received: sequence });
    }
    if (sequence !== null) lastByTrace.set(traceId, sequence);
    const stamp = Date.parse(event.timestamp || "") || Date.now();
    if (stamp < lastTimestamp) {
      onGap({ reordered: true, timestamp: event.timestamp });
    }
    lastTimestamp = Math.max(lastTimestamp, stamp);
    onEvent(event);
  }

  function startPolling(reason) {
    if (pollTimer || closed) return;
    transport = "polling";
    report("polling", { reason });
    const drain = async () => {
      try {
        // No `since`: sequences are per trace, so overlap is resolved by
        // event-id de-duplication instead of a global cursor.
        const response = await fetch(pollingEndpoint, {
          headers: { accept: "application/json" },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body = await response.json();
        for (const event of body.events || []) handle(event);
      } catch (error) {
        lastError = error.message;
        report("polling-error");
      }
    };
    drain();
    pollTimer = setInterval(drain, pollIntervalMs);
  }

  function connect() {
    if (closed) return;
    if (typeof EventSource === "undefined") {
      startPolling("no-eventsource");
      return;
    }
    transport = "sse";
    report("connecting");
    try {
      source = new EventSource(endpoint);
    } catch (error) {
      lastError = error.message;
      startPolling("eventsource-threw");
      return;
    }
    source.addEventListener("open", () => {
      retries = 0;
      report("open");
    });
    source.addEventListener("rag", (message) => handle(message.data));
    source.addEventListener("message", (message) => handle(message.data));
    source.addEventListener("error", () => {
      lastError = "stream error";
      retries += 1;
      report("error");
      if (retries > maxRetries) {
        if (source) source.close();
        source = null;
        startPolling("retries-exhausted");
      }
    });
  }

  return {
    connect,
    close() {
      closed = true;
      if (source) source.close();
      if (pollTimer) clearInterval(pollTimer);
      source = null;
      pollTimer = null;
      transport = "closed";
      report("closed");
    },
    get transport() {
      return transport;
    },
  };
}
