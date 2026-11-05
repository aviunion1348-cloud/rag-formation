#!/usr/bin/env node
/**
 * Host fixture D — a Node 22 host with no Python in the request path.
 *
 * It exercises the HTTP contract the way a JavaScript service would:
 *
 *   1. `GET /healthz` for readiness,
 *   2. `POST /api/v1/ask` with an API key and a host-signed principal header,
 *   3. citation resolution: every citation points at a chunk that exists,
 *   4. `GET /api/v1/events`: the SSE stream opens as `text/event-stream` and
 *      delivers frames (this is the endpoint the server actually exposes;
 *      answer text itself is returned by the JSON call),
 *   5. refusal: an off-corpus question returns `abstained`, not an invention.
 *
 * Usage: RAG_BASE_URL=http://127.0.0.1:8787 RAG_API_KEY=... node node_host.mjs
 */

const BASE = (process.env.RAG_BASE_URL || 'http://127.0.0.1:8787').replace(/\/$/, '');
const KEY = process.env.RAG_API_KEY || '';
const PRINCIPAL = JSON.stringify({ subjectId: 'node-host', tenantId: 'local', roles: ['viewer', 'operator'] });

let failures = 0;
function check(name, ok, detail = '') {
  if (!ok) failures += 1;
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
}

const headers = { 'content-type': 'application/json', 'x-api-key': KEY, 'x-rag-principal': PRINCIPAL };

async function main() {
  const health = await fetch(`${BASE}/healthz`, { headers });
  const healthBody = await health.json();
  check('health endpoint reachable', health.ok, `status=${healthBody.status}`);

  const askResponse = await fetch(`${BASE}/api/v1/ask`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ question: 'How long are audit logs retained?', topK: 6 }),
  });
  const ask = await askResponse.json();
  check('ask returns 200', askResponse.ok, `status=${ask.status}`);
  check('ask is grounded in evidence', ask.evidence?.length > 0, `${ask.evidence?.length || 0} chunks`);
  check('answer states the fact', /400\s*days/i.test(ask.answer || ''), (ask.answer || '').slice(0, 80));
  check('citations present', (ask.citations || []).length > 0, `${(ask.citations || []).length}`);

  const citedIds = (ask.citations || []).map((c) => c.chunkId);
  const evidenceIds = new Set((ask.evidence || []).map((e) => e.chunkId));
  check(
    'every citation resolves to delivered evidence',
    citedIds.every((id) => evidenceIds.has(id)),
    citedIds.join(', '),
  );

  // ------------------------------------------------------------------ streaming
  const controller = new AbortController();
  const streamTimeout = setTimeout(() => controller.abort(), 15000);
  const streamResponse = await fetch(`${BASE}/api/v1/events`, { headers, signal: controller.signal });
  check('event stream returns 200', streamResponse.ok, `status=${streamResponse.status}`);
  check(
    'event stream is text/event-stream',
    (streamResponse.headers.get('content-type') || '').includes('text/event-stream'),
    streamResponse.headers.get('content-type') || '',
  );
  const reader = streamResponse.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let frames = 0;
  while (frames < 3) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    frames += parts.filter((part) => part.trim().length > 0).length;
  }
  clearTimeout(streamTimeout);
  controller.abort();
  check('event stream delivered frames', frames > 0, `${frames} frames`);

  // -------------------------------------------------------------------- refusal
  const off = await fetch(`${BASE}/api/v1/ask`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ question: 'Who won the 2014 football World Cup?' }),
  });
  const offBody = await off.json();
  check('off-corpus question abstains', offBody.status === 'abstained', `status=${offBody.status}`);

  // ------------------------------------------------------------- authorisation
  const unauthorised = await fetch(`${BASE}/api/v1/ask`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ question: 'How long are audit logs retained?' }),
  });
  check('missing API key is rejected', unauthorised.status === 401, `status=${unauthorised.status}`);

  const dropped = await fetch(`${BASE}/healthz`, {
    headers: { 'x-api-key': KEY },
  });
  check('healthz without a principal still works (liveness)', dropped.status === 200, `status=${dropped.status}`);

  console.log(`\nhost D: ${failures === 0 ? 'all checks passed' : `${failures} check(s) failed`}`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error('host D crashed:', error);
  process.exit(1);
});
