# Source register

What this build was derived from, how much authority each input has, and what was
actually inspected. Read this before assuming a requirement exists.

| ID | Source | Type | What it specifies | Authority | Status |
|---|---|---|---|---|---|
| S1 | Whiteboard photograph supplied with the task (`WhatsApp Image 2026-11-05 at 1.34.33 PM.jpeg`) | Hand-drawn architecture sketch | Local-first stack: Ollama generation with a small model budget (~4B) and a ~200M embedding model; vector DB; "semantic" retrieval; generate → guardrails; CLI with reasoning; system prompts; context rerank; citations; chunking by heading/topic; metrics (`top_k`, `top_p`, `temperature 0.3`, mean/p50/p95, TTFT, ITL); "agents sdk" and "langchain" both struck through | **Primary product intent** for this increment | Read, transcribed below |
| S2 | `MEGA_RAG_COMPLETE_TWO_PROMPT_MASTER.md` (supplied attachment) — part 1, "The RAG system — autonomous build master prompt" | Engineering specification | Complete RAG data path, provenance, idempotency, transactions, citation validation, abstention, guardrails, security, evaluation, documentation, Definition of Done | **Primary engineering spec** | Read in full (sections 0–21) |
| S3 | Same file — part 2, "The RAG Experience — cinematic VFX walkthrough" | Product/experience specification | A cinematic, explorable RAG visualiser with three modes, storyboard scenes, reusable integration contract | Secondary; **explicitly out of scope for this increment** (see D6) | Read to section 4; deferred |
| S4 | `engine/` (merged in PR #1, `feat/01-rag-engine`) | Existing code | A deterministic fixture-corpus demo engine emitting `RagExperienceEvent` trace contracts for a future frontend | **Existing repository contract** — preserved, not modified | Inspected; 29 tests still pass |
| S5 | `ragformation/` (this increment) | New code | The real system described by S1 + S2 | Implementation under review | 107 tests, golden evaluation |
| S6 | `corpus/` (6 documents) | Synthetic fixtures | Demonstration knowledge base: incident runbook, retention policy, deployment standard, vendor onboarding (HTML), support macros (JSON), injection fixture (Markdown) | Fixture data — **invented, labelled as such** | Generated for this project |
| S7 | `evals/golden.json` | Evaluation labels | 14 questions, expected sources, required and forbidden facts | Test data | Authored for this project |

## Conflicts and how they were resolved

| Conflict | Resolution |
|---|---|
| S1 draws "langchain" and "agents sdk" struck through, while S2 warns against replacing a mechanism with a fashionable framework | No agent framework and no orchestration library. The pipeline is explicit code (~2,000 lines of Python), which is also what makes the trace and tests readable. |
| S1 shows "Ollama" as the generator; the build environment has no Ollama binary, no model weights and no network access to a model registry | Ollama is implemented as the **preferred** provider, verified against a stub that speaks its real HTTP contract (`tests/test_ollama_provider.py`). Deterministic local fallbacks keep the pipeline runnable and are labelled `semantic: false` / `extractive` everywhere. Never silently substituted. |
| S1 shows a "vector db" as a distinct service | SQLite stores vectors next to their metadata and an FTS5 index. For the tested scale this is exact, transactional and dependency-free; `docs/decisions/0001-vector-store.md` records the trade-off and the migration path. |
| S2 asks for OCR/PDF/DOCX breadth "only when the corpus requires it" | DOCX, HTML, JSON, CSV, Markdown and text are supported with stdlib parsers and tests. PDF is supported when the optional `pypdf` package is installed; OCR is explicitly not claimed. |
| S2 asks for a UI "only if appropriate"; S3 asks for a cinematic 3D walkthrough | A functional, accessible **operator console** (sources, ask, evidence, trace, metrics, safety) ships with the API. The cinematic walkthrough belongs to S3 and is deferred rather than half-built. |
| S2 asks for multi-tenant authorisation; S1 and the repo describe a local single-user tool | Single-user local deployment, documented. Sources carry `tenant_id` and retrieval enforces a server-side `allowed_sources` filter, but there is no user directory or session auth. `SECURITY.md` §3 states this plainly. |

## What was **not** available

* No tutorial PDF, slide deck or assignment rubric exists in the repository or the
  attachments. The only design inputs are S1 (whiteboard) and S2 (spec), so
  "tutorial compliance" is reported against those, not against a missing document.
* No live LLM or embedding server, no API key, no GPU. Every neural-path claim in
  these docs is marked as verified-against-stub or unverified.
* No product/design system to inherit: the console defines its own small visual
  language (`ragformation/web/style.css`).
