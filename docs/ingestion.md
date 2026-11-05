# Ingestion

Ingestion turns a file into addressable, provenance-carrying chunks, then writes
them, their vectors and their lexical rows in one transaction.

## 1. Supported formats

| Format | Parser | What is preserved | Notes |
|---|---|---|---|
| `.md` / `.markdown` | own block parser | headings (level), paragraphs, lists, tables, fenced code | the default corpus format |
| `.txt`, `.rst`, `.text` | paragraph splitter | paragraphs, list items | warns when no headings are found |
| `.html`, `.htm` | `html.parser` (stdlib) | `<title>`, `h1`–`h6`, paragraphs, list items, table cells; `<script>`/`<style>` ignored | no JavaScript execution |
| `.json` | `json` (stdlib) | each record flattened to `key: value` blocks | invalid JSON → `extraction_error` with line/column |
| `.csv` | `csv` (stdlib) | header + one record block per row | |
| `.docx` | `zipfile` + `ElementTree` (stdlib) | paragraphs, heading styles, table rows, document title | macros are never executed |
| `.pdf` | optional `pypdf` | text per page, page numbers in metadata | encrypted PDFs rejected; **OCR is not supported** — an image-only PDF is reported as a warning, not silently indexed |

Unsupported or binary content is rejected with a stable code
(`unsupported_format`, `extraction_error`, `source_too_large`) and a hint, not
half-parsed. Media type is decided by **content sniffing first** (PDF magic
bytes, ZIP signature, HTML tags, JSON braces), with the extension only as a hint.

## 2. What every source records

`source_id`, original `origin` (path or `upload:<name>`), title, media type, byte
size, SHA-256 of the raw bytes, revision number, tenant, tags, parser identity,
page count, block count, extraction warnings, created/updated timestamps.

Chunks additionally carry: `chunk_id`, ordinal, text, token estimate, section,
section path, page start/end, and a content hash of the chunk text.

## 3. Chunking rules

1. A heading always starts a new chunk and its text is kept inside that chunk, so
   lexical search can match the section name.
2. Blocks accumulate until the target size (default **180** estimated tokens,
   max **300**).
3. Overlap (default **40** tokens) is a measured sentence tail of the previous
   chunk, recorded as `overlapTokens` so it can be audited.
4. Oversized blocks are split on sentence boundaries, never mid-word.
5. A trailing chunk smaller than a quarter of the target is merged into its
   predecessor when both share a section path.
6. `page_start`/`page_end` are computed from the contributing blocks.

Chunking is fingerprinted (`settings.chunking_fingerprint()`); changing the
configuration makes `rebuild` warn that a full re-ingest from originals is
required, because re-embedding existing chunks cannot change their boundaries.

## 4. Identity, revisions and idempotency

| Situation | Behaviour |
|---|---|
| Same file ingested twice, unchanged | `status: unchanged`, no writes, no new vectors (short-circuit on content hash + embedding fingerprint + chunking fingerprint) |
| File edited | New revision: old chunks/vectors/FTS rows deleted, new inserted in one transaction, `revision` incremented |
| Same content, different filename | Indexed as a separate source (different `origin`). Documented; de-duplicating across names is not attempted |
| File deleted from disk | Nothing happens automatically; use `delete <source_id>` |
| Ingestion fails midway | Transaction rolls back; job marked `failed` with an error code; the previous revision keeps serving |
| Embedding model changed | `embedding_mismatch` refusal with a `rebuild` hint; existing index untouched |
| Source exceeds the size limit | `source_too_large`, no job effect beyond the failure record |

## 5. Lifecycle and observability

```
queued → chunking → embedding → indexing → complete
                       │            │
                       └────────────┴──→ failed (error_code, message, hint)
```

Every ingestion writes a `jobs` row with stage timings, chunk counts and the
failure reason. `store.recent_jobs()` and the API expose them. On engine start,
any job still marked `running` is flipped to `failed / interrupted` — a crash can
never look like a success.

## 6. Commands

```bash
python3 -m ragformation ingest corpus               # directory (recursive, skips dotfiles)
python3 -m ragformation ingest notes.md spec.docx   # individual files
python3 -m ragformation ingest corpus --force       # reindex even if unchanged
python3 -m ragformation sources                     # list with chunk counts and revisions
python3 -m ragformation chunks <source_id>          # inspect chunk boundaries and provenance
python3 -m ragformation delete <source_id>          # remove source + derived data
```

HTTP equivalents: `POST /api/sources/upload` (base64, parsed in memory),
`POST /api/sources/text` (paste a note), `POST /api/ingest` (server-side path;
disable with `RAG_ALLOW_PATH_INGEST=0`), `DELETE /api/sources/{id}`.

## 7. Operational limits

* Maximum source size: `RAG_MAX_SOURCE_BYTES` (default 8 MiB), enforced before
  parsing.
* Ingestion is **synchronous** — a large batch blocks the caller. There is no
  background queue in this increment.
* Token counts are estimates (~4 chars/token). Provider-reported token usage is
  stored separately when available; the UI and payloads label estimates.
* Original bytes are not retained; only extracted text and metadata live in the
  database. Re-ingest from the originals to change chunking.
