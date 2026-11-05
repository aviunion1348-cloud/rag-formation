# Security model

`ragformation` ingests documents you give it and answers questions about them.
That makes it a data-processing application with two untrusted inputs: uploaded
files and model output. This document states what is enforced in code, what is
only partially mitigated, and what is out of scope. It does not claim any
certification.

## 1. Threat model

| Threat | Where it comes from | Control |
|---|---|---|
| Prompt injection | Text inside a document tells the assistant to ignore its rules, reveal secrets or call tools | Injection patterns quarantine the passage before it reaches the prompt; the prompt delimits evidence as data; regression tests cover the known phrasings |
| Credential disclosure | A document (or a model) emits secret-shaped strings | Credential patterns are redacted in generated output; retrieved text never becomes instructions; secrets are configuration-only |
| Fabricated citations | A model invents `[7]` or a document cannot support its claim | Citations are re-derived server-side from retrieved chunk ids; unknown markers are stripped and counted |
| Fabricated links | A model invents a URL that looks verifiable | URLs absent from the evidence are replaced with `[unverified-link-removed]` |
| Path traversal | A filename like `../../etc/passwd` | Filenames are never used as filesystem paths; uploads are parsed in memory; static serving is confined to `ragformation/web/` and resolved paths must stay inside it |
| Oversized / binary uploads | Large or non-text files | Size limit (`RAG_MAX_SOURCE_BYTES`, default 8 MiB), content sniffing, binary detection, structured rejection |
| FTS syntax injection | Query strings containing `"`, `*`, `NEAR(`, `;` | Every term is quoted before it reaches `fts5 MATCH`; hostile queries are covered by tests |
| Access leakage across sources | A query retrieving documents outside the caller's scope | `allowed_sources` is applied inside the retriever and in citation resolution; vectors are filtered before scoring |
| Unauthorised API use | Anything reaching the HTTP port | Optional `RAG_API_KEY` (`x-api-key`) for every `/api` route except health; permissive CORS is only safe because the default deployment is local |
| Secret leakage into logs | Debugging output | No request bodies, prompts or document text are logged; errors return stable codes with safe messages; unexpected exceptions log a traceback server-side only |
| Index corruption / stale vectors | Crash during ingest, model change | Revision swaps are transactional; vectors carry an embedding fingerprint; ingest refuses to mix models; `doctor` and `health` report integrity problems; `rebuild` re-embeds from stored chunks |

## 2. What is enforced in code (with tests)

* Retrieved passages are scanned; matches are quarantined and excluded from the
  generation context (`tests/test_engine_e2e.py::test_injection_content_is_quarantined_and_never_answered`).
* The system prompt states that evidence is untrusted data and forbids following
  instructions inside it (`tests/test_guardrails.py::PromptTests`).
* Invalid citation markers are stripped; the count is recorded in metrics
  (`tests/test_guardrails.py::RedactionTests::test_invalid_citation_markers_are_removed`).
* Unverified URLs are neutralised (`…test_unverified_urls_are_neutralised`).
* Credential-shaped strings are redacted from answers (`…test_credentials_are_replaced`).
* Uploads are size-limited, content-sniffed and rejected when binary
  (`tests/test_loaders.py`).
* API authentication, request-size limits, JSON validation and path-traversal
  rejection (`tests/test_api.py`).
* Deletion removes chunks, vectors **and** lexical rows
  (`tests/test_store.py::test_delete_removes_chunks_vectors_and_lexical_rows`).

## 3. Known limits (stated plainly)

1. **Injection detection is heuristic.** It catches the common instruction
   patterns. A novel, well-written injection inside a document can still reach
   the model. The prompt-level instruction and citation validation reduce the
   blast radius; they do not eliminate it. If you need stronger guarantees, keep
   the extractive generator (no instruction following at all) or add an
   allow-listed corpus.
2. **No authentication by default.** With `RAG_API_KEY` unset, anyone who can
   reach the port can read and write the corpus. Run it on localhost or behind
   an authenticating proxy.
3. **Single-user model.** Sources carry `tenant_id`, but there is no user
   directory, session handling or per-user authorisation. `allowed_sources` is a
   server-side filter for embedders of this library, not an authentication
   boundary.
4. **Uploaded files are not stored.** Only extracted text is kept in SQLite. The
   original bytes are gone after ingestion, which limits forensic review but
   removes a whole class of stored-file exposure.
5. **No malware scanning.** Uploads are never executed, macros are never run, and
   archives other than `.docx` are rejected — but a hostile `.docx` is still
   parsed by the standard library.
6. **Prompt and answer content can still reach a model provider** if you
   configure a cloud provider. That is a deliberate configuration choice; the
   default (`ollama` or `hashing`/`extractive`) keeps everything local.
7. **Audit logging is local and best-effort.** `jobs` and `queries` tables record
   what happened, but an operator with write access to `data/` can edit them.

## 4. Deployment checklist

```bash
# 1. Keep it local (default) or put it behind an authenticating proxy.
export RAG_HOST=127.0.0.1

# 2. Require an API key before exposing the port at all.
export RAG_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

# 3. Restrict CORS to your own origin, not "*".
export RAG_CORS_ORIGIN="https://your-console.example"

# 4. Keep the model local unless data leaving the machine is acceptable.
export RAG_EMBED_PROVIDER=ollama RAG_LLM_PROVIDER=ollama

# 5. Verify.
python3 -m ragformation doctor
```

## 5. Reporting

This is a demonstration/research codebase, not a maintained product. If you
deploy it, treat the limitations above as your starting risk register.
