"""The RAG engine: ingestion lifecycle, retrieval orchestration, grounded answers.

This module owns the *policy* decisions (what counts as sufficient evidence,
when to abstain, how citations are validated). The mechanics live in the
focused modules around it, so each stage stays independently testable.

Answer statuses
---------------
``answered``      Evidence was sufficient, the answer is cited and validated.
``needs_review``  An answer was produced but something needs a human: conflicting
                  evidence, an uncited answer, or a removed invalid citation.
``abstained``     Not enough evidence (or the model declined). No answer invented.
``failed``        A provider or storage failure prevented an answer. Never faked
                  as success.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from .chunking import Chunker
from .auth import Principal, check_deployment
from .config import API_VERSION, Settings
from .contracts import HealthReport, IngestReport, IngestResult, QueryTrace, SourceInfo
from .embeddings import CachedEmbedder, build_embedder, describe as describe_embedder
from .errors import (
    EmbeddingMismatch,
    ExtractionError,
    NotFound,
    ProviderError,
    ProviderUnavailable,
    RagError,
    ValidationError,
)
from .generation import (
    ExtractiveGenerator,
    GenerationProvider,
    build_generator,
    describe as describe_generator,
)
from .guardrails import (
    looks_like_refusal,
    output_warnings,
    redact_credentials,
    scan_text,
    strip_invalid_citations,
    strip_unverified_urls,
)
from .loaders import LoadedSource, load_bytes, load_file
from .metrics import summarize
from .retrieval import Candidate, Retriever, assemble_context, dedupe_overlaps
from .store import Store, StoredChunk, now_iso, pack_vector
from .events import EVENT_SCHEMA_VERSION, EventBus, make_event
from .text import content_terms, sha256_hex, split_sentences


from .contracts import Citation  # noqa: E402  (public type, single definition)


class AnswerStatus(str, Enum):
    ANSWERED = "answered"
    NEEDS_REVIEW = "needs_review"
    ABSTAINED = "abstained"
    FAILED = "failed"




@dataclass
class Claim:
    text: str
    citations: List[int] = field(default_factory=list)
    supported: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "citations": self.citations, "supported": self.supported}


@dataclass
class QueryResult:
    query_id: str
    question: str
    status: str
    answer: str
    citations: List[Citation] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    excluded: List[Dict[str, Any]] = field(default_factory=list)
    quarantined: List[Dict[str, Any]] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    provider: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    abstain_reason: Optional[str] = None
    conflict: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=now_iso)

    @property
    def mode(self) -> str:
        """``llm`` when a real model generated the prose, else ``extractive``."""

        name = str((self.provider or {}).get("provider") or (self.provider or {}).get("name") or "")
        return "llm" if name.lower() in {"ollama", "openai"} else "extractive"

    @property
    def trace_summary(self) -> QueryTrace:
        return QueryTrace.from_result(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "queryId": self.query_id,
            "question": self.question,
            "status": self.status,
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "claims": [c.to_dict() for c in self.claims],
            "evidence": self.evidence,
            "excluded": self.excluded,
            "quarantined": self.quarantined,
            "trace": self.trace,
            "metrics": self.metrics,
            "provider": self.provider,
            "generationMode": self.mode,
            "warnings": self.warnings,
            "abstainReason": self.abstain_reason,
            "conflict": self.conflict,
            "error": self.error,
            "createdAt": self.created_at,
        }


class _Trace:
    """Stage timer that also publishes sanitised :class:`RAGEvent` objects.

    The dict trace and the event stream describe the same run: the trace is
    persisted with the query row, the events feed the console and the S3
    Experience. Publishing is optional so a headless ingest stays cheap.
    """

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        *,
        trace_id: str = "",
        mode: str = "live",
        operation_id: Optional[str] = None,
    ) -> None:
        self.events: List[Dict[str, Any]] = []
        self._starts: Dict[str, float] = {}
        self.bus = bus
        self.trace_id = trace_id
        self.mode = mode
        self.operation_id = operation_id

    def start(self, stage: str, detail: Optional[Dict[str, Any]] = None) -> None:
        self._starts[stage] = time.perf_counter()
        self._emit(stage, "started", detail)

    def end(self, stage: str, detail: Optional[Dict[str, Any]] = None) -> float:
        started = self._starts.pop(stage, None)
        duration = round(((time.perf_counter() - started) * 1000.0), 3) if started else None
        self._emit(stage, "completed", detail, duration)
        return duration or 0.0

    def skip(self, stage: str, detail: Optional[Dict[str, Any]] = None) -> None:
        self._emit(stage, "skipped", detail)

    def note(self, stage: str, status: str, detail: Optional[Dict[str, Any]] = None) -> None:
        """Record a stage that is reported but not timed (e.g. a sub-channel)."""

        self._emit(stage, status, detail)

    def fail(self, stage: str, detail: Optional[Dict[str, Any]] = None) -> None:
        self._emit(stage, "failed", detail)

    def _emit(
        self,
        stage: str,
        status: str,
        detail: Optional[Dict[str, Any]],
        duration: Optional[float] = None,
    ) -> None:
        event: Dict[str, Any] = {
            "stage": stage,
            "status": status,
            "at": now_iso(),
        }
        if duration is not None:
            event["durationMs"] = duration
        if detail:
            event["detail"] = _safe_detail(detail)
        self.events.append(event)
        if self.bus is not None:
            try:
                self.bus.publish(
                    make_event(
                        stage,
                        status,
                        trace_id=self.trace_id,
                        mode=self.mode,
                        detail=detail,
                        duration_ms=duration,
                        operation_id=self.operation_id,
                    )
                )
            except Exception:  # a broken visual client must never break a query
                pass


def _safe_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    """Trace details must never carry document text or secrets."""
    safe: Dict[str, Any] = {}
    for key, value in detail.items():
        if isinstance(value, str) and len(value) > 120:
            safe[key] = value[:120] + "…"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = [_safe_scalar(item) for item in list(value)[:24]]
        elif isinstance(value, dict):
            safe[key] = _safe_detail(value)
        else:
            safe[key] = str(value)[:120]
    return safe


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value[:120] if isinstance(value, str) else value
    return str(value)[:120]


class RAGEngine:
    """High-level facade used by the CLI, the HTTP API and the tests."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        config: Optional[Settings] = None,
        store: Optional[Store] = None,
        embedder: Optional[CachedEmbedder] = None,
        generator: Optional[GenerationProvider] = None,
        event_bus: Optional[EventBus] = None,
        principal: Optional[Principal] = None,
    ) -> None:
        self.settings = settings or config or Settings.from_env()
        self._owns_store = store is None
        self.store = store or Store(self.settings.db_path)
        self.embedder = embedder or build_embedder(self.settings, self.store)
        self.generator = generator or build_generator(self.settings)
        self.retriever = Retriever(self.store, self.settings)
        self.chunker = Chunker(self.settings)
        self.events = event_bus if event_bus is not None else EventBus()
        self.default_principal = principal or Principal()
        self._recover_interrupted_jobs()

    # ------------------------------------------------------- public identity
    @property
    def config(self) -> Settings:
        return self.settings

    def _event_mode(self) -> str:
        name = str(getattr(self.generator, "name", "") or "").lower()
        return "live" if name in {"ollama", "openai"} else "extractive"

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> "RAGEngine":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _recover_interrupted_jobs(self) -> None:
        """A job left 'running' by a crash is marked failed, never 'complete'."""
        for job in self.store.recent_jobs(limit=50):
            if job.get("status") == "running":
                self.store.finish_job(
                    job["job_id"],
                    "failed",
                    error_code="interrupted",
                    error="job was interrupted by a restart",
                )

    # ------------------------------------------------------------------ ingestion
    def ingest_path(
        self,
        paths: Sequence[str],
        *,
        tenant_id: str = "local",
        tags: Sequence[str] = (),
        force: bool = False,
    ) -> Dict[str, Any]:
        from .loaders import iter_input_files

        files = iter_input_files(paths)
        if not files:
            raise ValidationError("no input files found", hint="pass a file or a directory")

        # Corpus-trained embedders must fit once over the whole batch, not once per
        # file: a per-file fit would give each document a different vector space.
        loaded: List[Tuple[Path, Any]] = []
        failures: List[Dict[str, Any]] = []
        for path in files:
            try:
                loaded.append((path, load_file(path, max_bytes=self.settings.max_source_bytes)))
            except RagError as exc:
                failures.append(
                    {"filename": path.name, "status": "failed", "error": exc.to_dict(), "chunks": 0}
                )
        if loaded:
            self._prepare_embeddings([source.text for _, source in loaded])
        results: List[Dict[str, Any]] = []
        for path, source in loaded:
            results.append(
                self._ingest_loaded(
                    source,
                    origin=str(path),
                    tenant_id=tenant_id,
                    tags=tags,
                    force=force,
                )
            )
        return self._ingest_report(results + failures)

    def ingest_bytes(
        self,
        filename: str,
        data: bytes,
        *,
        title: Optional[str] = None,
        tenant_id: str = "local",
        tags: Sequence[str] = (),
        force: bool = False,
        origin: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            source = load_bytes(
                filename, data, max_bytes=self.settings.max_source_bytes, title=title
            )
        except RagError as exc:
            return self._ingest_report(
                [
                    {
                        "filename": filename,
                        "status": "failed",
                        "error": exc.to_dict(),
                        "chunks": 0,
                    }
                ]
            )
        self._prepare_embeddings([source.text])
        result = self._ingest_loaded(
            source,
            origin=origin or f"upload:{source.filename}",
            tenant_id=tenant_id,
            tags=tags,
            force=force,
        )
        return self._ingest_report([result])

    def _ingest_file(self, path: Path, *, tenant_id: str, tags: Sequence[str], force: bool) -> Dict[str, Any]:
        try:
            source = load_file(path, max_bytes=self.settings.max_source_bytes)
        except RagError as exc:
            return {"filename": path.name, "status": "failed", "error": exc.to_dict(), "chunks": 0}
        try:
            origin = str(path.resolve().relative_to(Path.cwd().resolve()))
        except ValueError:
            origin = path.name
        return self._ingest_loaded(source, origin=origin, tenant_id=tenant_id, tags=tags, force=force)

    def _ingest_loaded(
        self,
        source: LoadedSource,
        *,
        origin: str,
        tenant_id: str,
        tags: Sequence[str],
        force: bool,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        source_id = self._source_id_for(origin)
        job_id = self.store.start_job("ingest", source_id)
        trace = _Trace(self.events, trace_id=job_id, mode="live", operation_id=job_id)
        trace.start(
            "ingestion.source_accepted",
            {
                "sourceId": source_id,
                "mediaType": getattr(source, "media_type", ""),
                "bytes": int(getattr(source, "byte_size", 0) or 0),
                "tenantId": tenant_id,
            },
        )
        try:
            existing = self.store.get_source(source_id)
            fingerprint = self.embedder.fingerprint
            chunk_fingerprint = self.settings.chunking_fingerprint()

            if existing and existing.status == "active" and not force:
                same_content = existing.content_hash == source.content_hash
                same_embedding = self.store.get_meta("embedding_fingerprint") == fingerprint
                same_chunking = self.store.get_meta("chunking_fingerprint") == chunk_fingerprint
                if same_content and same_embedding and same_chunking:
                    trace.end("ingestion.source_accepted", {"sourceId": source_id})
                    trace.end("ingestion.duplicate", {"sourceId": source_id, "status": "unchanged"})
                    self.store.finish_job(job_id, "succeeded", detail={"status": "unchanged"})
                    return {
                        "filename": source.filename,
                        "sourceId": source_id,
                        "status": "unchanged",
                        "revision": existing.revision,
                        "chunks": 0,
                        "warnings": existing.warnings,
                        "ms": round((time.perf_counter() - started) * 1000.0, 3),
                    }

            stored_fingerprint = self.store.get_meta("embedding_fingerprint")
            if (
                stored_fingerprint
                and stored_fingerprint != fingerprint
                and self.store.counts()["vectors"] > 0
                and not force
            ):
                raise EmbeddingMismatch(
                    "the index was built with a different embedding model",
                    hint=(
                        f"index={stored_fingerprint}, configured={fingerprint}. "
                        "Run `ragformation rebuild --reembed` (or delete data/ to start over)."
                    ),
                )

            trace.end("ingestion.source_accepted", {"sourceId": source_id, "mediaType": source.media_type})
            trace.note(
                "ingestion.extracted",
                "completed",
                {
                    "sourceId": source_id,
                    "blocks": len(source.blocks),
                    "pages": source.page_count,
                    "characters": len(source.text),
                    "parser": source.parser,
                    "warnings": len(source.warnings),
                },
            )
            trace.start("ingestion.chunking", {"sourceId": source_id})
            self.store.update_job(job_id, "chunking")
            chunks = self.chunker.chunk(source, source_id)
            if not chunks:
                raise ExtractionError("no chunks produced", hint="document may be empty")
            trace.end(
                "ingestion.chunking",
                {
                    "sourceId": source_id,
                    "chunks": len(chunks),
                    "tokens": sum(c.token_count for c in chunks),
                },
            )
            self.store.update_job(job_id, "embedding", {"chunks": len(chunks)})
            self._prepare_embeddings([c.text for c in chunks])
            trace.start("ingestion.embedding", {"sourceId": source_id, "chunks": len(chunks)})

            vectors: List[List[float]] = []
            batch = max(1, self.settings.embed_batch_size)
            for index in range(0, len(chunks), batch):
                window = chunks[index : index + batch]
                vectors.extend(self.embedder.embed([c.text for c in window]))

            dimension = len(vectors[0])
            if any(len(v) != dimension for v in vectors):
                raise ProviderError("embedding provider returned inconsistent dimensions")
            trace.end(
                "ingestion.embedding",
                {
                    "sourceId": source_id,
                    "chunks": len(vectors),
                    "dimension": dimension,
                    "provider": self.embedder.name,
                    "semantic": self.embedder.semantic,
                },
            )

            self.store.update_job(job_id, "indexing", {"chunks": len(chunks), "dimension": dimension})
            stored_chunks = [
                StoredChunk(
                    chunk_id=chunk.chunk_id,
                    source_id=source_id,
                    revision=0,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    section=chunk.section,
                    section_path=chunk.section_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    content_hash=chunk.content_hash,
                    source_title=source.title,
                )
                for chunk in chunks
            ]
            installed = self.store.replace_source(
                source_id=source_id,
                title=source.title,
                origin=origin,
                media_type=source.media_type,
                byte_size=source.byte_size,
                content_hash=source.content_hash,
                tenant_id=tenant_id,
                visibility="private",
                tags=tags,
                warnings=source.warnings,
                meta={
                    "parser": source.parser,
                    "pageCount": source.page_count,
                    "blockCount": len(source.blocks),
                    "filename": source.filename,
                },
                chunks=stored_chunks,
                vectors=vectors,
                embedding_fingerprint=fingerprint,
                dimension=dimension,
            )
            self.store.set_meta("embedding_fingerprint", fingerprint)
            self.store.set_meta("chunking_fingerprint", chunk_fingerprint)
            self.store.set_meta("last_ingest_at", now_iso())
            trace.end(
                "ingestion.index_committed",
                {
                    "sourceId": source_id,
                    "chunks": len(chunks),
                    "revision": installed["revision"],
                    "vectors": len(vectors),
                    "dimension": dimension,
                },
            )
            self.store.finish_job(
                job_id,
                "succeeded",
                detail={
                    "chunks": len(chunks),
                    "revision": installed["revision"],
                    "dimension": dimension,
                },
            )
            return {
                "filename": source.filename,
                "sourceId": source_id,
                "status": "indexed",
                "revision": installed["revision"],
                "previousRevision": installed["previousRevision"],
                "chunks": len(chunks),
                "tokens": sum(c.token_count for c in chunks),
                "warnings": source.warnings,
                "ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        except RagError as exc:
            trace.fail("ingestion.source_accepted", {"sourceId": source_id, "error": exc.code})
            self.store.finish_job(job_id, "failed", error_code=exc.code, error=exc.message)
            existing_now = self.store.get_source(source_id)
            if existing_now is None:
                # Nothing indexed before: record the failure so it is visible.
                self.store.mark_source_failed(source_id, exc.message)
            elif existing_now.status == "active" and isinstance(exc, EmbeddingMismatch):
                # The previous revision is still valid; leave it in place and
                # report the failure instead of hiding a good source.
                pass
            return {
                "filename": source.filename,
                "sourceId": source_id,
                "status": "failed",
                "error": exc.to_dict(),
                "chunks": 0,
                "ms": round((time.perf_counter() - started) * 1000.0, 3),
            }

    @staticmethod
    def _source_id_for(origin: str) -> str:
        return "src-" + sha256_hex(origin.encode("utf-8"))[:10]

    # ------------------------------------------------------------- embeddings
    def _prepare_embeddings(self, texts: Sequence[str] = ()) -> Dict[str, Any]:
        """Fit or load corpus-trained embedding state (no-op for other providers).

        The fitted model is persisted per index fingerprint, so restarting the
        process, restarting a query or ingesting one more file never silently
        replaces the vector space underneath existing vectors.
        """

        try:
            if texts:
                existing = [c.text for c in self.store.all_chunks()]
                combined = list(existing) + [t for t in texts if t]
            else:
                combined = [c.text for c in self.store.all_chunks()]
            return self.embedder.prepare(combined)
        except (ProviderUnavailable, ProviderError):
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ProviderError("embedding preparation failed", hint=str(exc)[:200])

    def _ingest_report(self, results: Sequence[Dict[str, Any]]) -> IngestReport:
        counts = self.store.counts()
        typed = [
            IngestResult(
                filename=str(r.get("filename", "")),
                status=str(r.get("status", "failed")),
                chunks=int(r.get("chunks") or 0),
                source_id=r.get("sourceId"),
                duration_ms=float(r.get("ms") or 0.0),
                warnings=list(r.get("warnings") or []),
                error=r.get("error"),
            )
            for r in results
        ]
        return IngestReport(
            {
                "results": list(results),
                "indexed": sum(1 for r in results if r["status"] == "indexed"),
                "unchanged": sum(1 for r in results if r["status"] == "unchanged"),
                "failed": sum(1 for r in results if r["status"] == "failed"),
                "chunks": counts["chunks"],
                "sources": counts["sources"],
                "embedding": describe_embedder(self.embedder),
                "totalMs": round(sum(r.get("ms", 0.0) for r in results), 3),
            },
            typed,
        )

    # -------------------------------------------------------------------- query
    def ask(
        self,
        question: str,
        *,
        top_k: Optional[int] = None,
        mode: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        allowed_sources: Optional[Set[str]] = None,
        history: Sequence[Dict[str, str]] = (),
        on_token: Optional[Any] = None,
        save: bool = True,
        principal: Optional[Principal] = None,
    ) -> QueryResult:
        question = (question or "").strip()
        if not question:
            raise ValidationError("question must not be empty")
        if len(question) > 2000:
            raise ValidationError("question is too long", hint="limit is 2000 characters")
        top_k = top_k or self.settings.top_k
        if not 1 <= top_k <= 50:
            raise ValidationError("topK must be between 1 and 50")
        mode = mode or self.settings.retrieval_mode
        if mode not in {"hybrid", "semantic", "lexical"}:
            raise ValidationError("mode must be hybrid, semantic or lexical")
        temperature = self.settings.temperature if temperature is None else temperature
        top_p = self.settings.top_p if top_p is None else top_p

        # Authorisation happens before retrieval: the caller's scope can only
        # narrow what is searched, never widen it.
        principal = principal or self.default_principal
        principal.require("query")
        allowed_sources = principal.scope(allowed_sources)

        query_id = f"q-{uuid.uuid4().hex[:12]}"
        trace = _Trace(self.events, trace_id=query_id, mode=self._event_mode())
        started = time.perf_counter()
        warnings: List[str] = []
        timings: Dict[str, float] = {}

        result = QueryResult(query_id=query_id, question=question, status=AnswerStatus.FAILED.value, answer="")
        result.provider = describe_generator(self.generator)

        trace.start("query_received", {"topK": top_k, "mode": mode, "questionChars": len(question)})

        # ------------------------------------------------------------ embedding
        self._prepare_embeddings()
        query_vector: List[float] = []
        trace.start("query_embedding", {"provider": self.embedder.name, "model": self.embedder.model})
        try:
            query_vector = self.embedder.embed_one(question)
            trace.end("query_embedding", {"dimension": len(query_vector)})
        except (ProviderError, ProviderUnavailable) as exc:
            trace.fail("query_embedding", {"error": exc.code})
            warnings.append("query_embedding_failed")
            if mode == "semantic":
                mode = "hybrid"  # degrade, and say so
            mode = "lexical" if mode == "hybrid" else mode
            warnings.append("degraded_to_lexical_retrieval")
            result.provider["degraded"] = True
        timings["embeddingMs"] = round((time.perf_counter() - started) * 1000.0, 3)

        # ------------------------------------------------------------ retrieval
        trace.start("retrieval", {"mode": mode})
        try:
            retrieval = self.retriever.search(
                question,
                query_vector,
                top_k=top_k,
                mode=mode,
                allowed_sources=allowed_sources,
            )
        except Exception as exc:  # storage failure must be visible, not silent
            trace.fail("retrieval", {"error": type(exc).__name__})
            result.status = AnswerStatus.FAILED.value
            result.error = {"code": "retrieval_failed", "message": "retrieval failed"}
            result.trace = trace.events
            result.metrics = {"totalMs": round((time.perf_counter() - started) * 1000.0, 3)}
            result.warnings = warnings + ["retrieval_failed"]
            if save and self.settings.persist_traces:
                self._persist_query(result, principal)
            return result
        trace.end(
            "retrieval",
            {
                "semanticCandidates": retrieval.semantic_candidates,
                "lexicalCandidates": retrieval.lexical_candidates,
                "fused": retrieval.fused_candidates,
                "reranked": retrieval.reranked,
                "bestSupport": round(retrieval.best_support, 4),
            },
        )
        timings["retrievalMs"] = round(sum(retrieval.timings_ms.values()), 3)

        # Report the sub-stages that actually ran. A channel that was not used is
        # marked "skipped" with a reason rather than invented as completed.
        channel_detail = {
            "retrieval.semantic": (
                "semanticMs",
                {"candidates": retrieval.semantic_candidates, "provider": self.embedder.name},
                "mode excludes the semantic channel" if mode == "lexical" else "no vector candidates",
            ),
            "retrieval.lexical": (
                "lexicalMs",
                {"candidates": retrieval.lexical_candidates},
                "mode excludes the lexical channel" if mode == "semantic" else "no lexical matches",
            ),
            "retrieval.fusion": (
                "fusionMs",
                {"fused": retrieval.fused_candidates, "rrfK": self.settings.rrf_k},
                "fusion not run",
            ),
            "retrieval.rerank": (
                "rerankMs",
                {"reranked": retrieval.reranked, "strategy": "heuristic"},
                "reranking disabled (RAG_RERANK=0)",
            ),
            "retrieval.mmr": (
                "mmrMs",
                {"candidates": len(retrieval.candidates), "lambda": self.settings.mmr_lambda},
                "diversification not run",
            ),
        }
        for stage, (timing_key, detail, reason) in channel_detail.items():
            if timing_key in retrieval.timings_ms:
                trace.note(stage, "completed", {**detail, "ms": retrieval.timings_ms[timing_key]})
            else:
                trace.note(stage, "skipped", {"reason": reason})

        candidates = dedupe_overlaps(retrieval.candidates)
        result.evidence = [c.to_dict(include_text=False) for c in candidates[: max(top_k * 2, 8)]]

        # ------------------------------------------------------- security filter
        trace.start("guardrail_scan", {"candidates": len(candidates)})
        safe: List[Candidate] = []
        for candidate in candidates:
            findings = scan_text(candidate.chunk.text)
            if findings:
                result.quarantined.append(
                    {
                        "chunkId": candidate.chunk.chunk_id,
                        "sourceId": candidate.chunk.source_id,
                        "sourceTitle": candidate.chunk.source_title,
                        "rules": [f.rule_id for f in findings],
                        "excerpt": " ".join(findings[0].excerpt.split())[:200],
                        "policy": "excluded from generation context: retrieved text is data, not instructions",
                    }
                )
                warnings.append("quarantined_instruction_like_content")
                continue
            safe.append(candidate)
        trace.end(
            "guardrail_scan",
            {"quarantined": len(result.quarantined), "usable": len(safe)},
        )

        if not safe:
            trace.skip("context_assembly", {"reason": "no usable candidates"})
            trace.skip("generation", {"reason": "no usable candidates"})
            result.status = AnswerStatus.ABSTAINED.value
            result.abstain_reason = (
                "no_usable_evidence" if candidates else "no_retrieved_evidence"
            )
            result.answer = self._abstention_text(result.abstain_reason, 0.0)
            result.metrics = self._final_metrics(started, timings, retrieval, 0, 0)
            result.warnings = warnings
            trace.end("response_ready", {"status": result.status})
            result.trace = trace.events
            if save and self.settings.persist_traces:
                self._persist_query(result, principal)
            return result

        # ----------------------------------------------------- context assembly
        trace.start("context_assembly", {"budgetTokens": self.settings.max_context_tokens})
        selected, used_tokens, skipped = assemble_context(safe, self.settings.max_context_tokens)
        if not selected:
            selected = safe[:1]
            used_tokens = selected[0].chunk.token_count
        selected_ids = {c.chunk.chunk_id for c in selected}
        by_id = {c.chunk.chunk_id: c for c in safe}
        skipped_views = []
        for item in skipped:
            candidate = by_id.get(item["chunkId"])
            view = candidate.to_dict(include_text=False) if candidate else {"chunkId": item["chunkId"]}
            view["reason"] = item["reason"]
            skipped_views.append(view)
        result.excluded = [
            {**c.to_dict(include_text=False), "reason": "ranked_outside_top_k"}
            for c in safe
            if c.chunk.chunk_id not in selected_ids
        ] + skipped_views
        trace.end(
            "context_assembly",
            {"selected": len(selected), "tokens": used_tokens, "excluded": len(result.excluded)},
        )

        best_support = max((c.support or 0.0) for c in selected)
        if best_support < self.settings.abstain_threshold:
            trace.end(
                "abstention",
                {
                    "reason": "insufficient_support",
                    "bestSupport": round(best_support, 4),
                    "threshold": self.settings.abstain_threshold,
                },
            )
            trace.skip("generation", {"reason": "abstained before generation"})
            result.status = AnswerStatus.ABSTAINED.value
            result.abstain_reason = "insufficient_support"
            result.answer = self._abstention_text("insufficient_support", best_support)
            result.evidence = [c.to_dict(include_text=True) for c in selected]
            result.metrics = self._final_metrics(started, timings, retrieval, used_tokens, 0)
            result.metrics["bestSupport"] = round(best_support, 4)
            result.warnings = warnings
            trace.end("response_ready", {"status": result.status})
            result.trace = trace.events
            if save and self.settings.persist_traces:
                self._persist_query(result, principal)
            return result

        # ------------------------------------------------------------ generation
        evidence_payload = [
            {
                "id": index,
                "text": candidate.chunk.text,
                "sourceId": candidate.chunk.source_id,
                "sourceTitle": candidate.chunk.source_title,
                "section": candidate.chunk.section,
                "page": candidate.chunk.page_start,
                "chunkId": candidate.chunk.chunk_id,
            }
            for index, candidate in enumerate(selected, start=1)
        ]
        result.evidence = [
            {**candidate.to_dict(include_text=True), "evidenceId": index}
            for index, candidate in enumerate(selected, start=1)
        ]

        provider_used = self.generator
        provider_failure: Optional[Dict[str, Any]] = None
        trace.start(
            "generation",
            {
                "provider": provider_used.name,
                "model": provider_used.model,
                "temperature": temperature,
                "topP": top_p,
                "evidenceTokens": used_tokens,
            },
        )
        generation_started = time.perf_counter()
        try:
            output = provider_used.generate(
                question,
                evidence_payload,
                temperature=temperature,
                top_p=top_p,
                max_tokens=self.settings.max_output_tokens,
                timeout=self.settings.request_timeout_s,
                on_token=on_token,
                history=history,
            )
        except (ProviderError, ProviderUnavailable) as exc:
            trace.fail("generation", {"error": exc.code, "provider": provider_used.name})
            warnings.append("generation_provider_failed")
            fallback = ExtractiveGenerator()
            output = fallback.generate(
                question,
                evidence_payload,
                temperature=0.0,
                top_p=0.0,
                max_tokens=self.settings.max_output_tokens,
                timeout=self.settings.request_timeout_s,
                on_token=on_token,
            )
            provider_used = fallback
            warnings.append(f"fallback_provider_used:{fallback.name}")
            provider_failure = {
                "provider": getattr(self.generator, "name", "unknown"),
                "model": getattr(self.generator, "model", "unknown"),
                "code": exc.code,
                "message": exc.message,
                "hint": exc.hint,
            }
        generation_ms = round((time.perf_counter() - generation_started) * 1000.0, 3)
        timings["generationMs"] = generation_ms
        trace.end(
            "generation",
            {
                "provider": provider_used.name,
                "chars": len(output.text),
                "ttftMs": output.timing.get("ttftMs"),
                "itlMs": output.timing.get("itlMs"),
                "finishReason": output.finish_reason,
            },
        )
        result.provider = {**describe_generator(provider_used), "used": provider_used.name}
        if provider_failure:
            result.provider["failedProvider"] = provider_failure

        raw_text, secret_hits = redact_credentials(output.text)
        if secret_hits:
            warnings.append("credential_shaped_text_redacted")

        if looks_like_refusal(raw_text):
            result.status = AnswerStatus.ABSTAINED.value
            result.abstain_reason = "model_declined_insufficient_evidence"
            result.answer = self._abstention_text("model_declined_insufficient_evidence", best_support)
            result.metrics = {**self._final_metrics(started, timings, retrieval, used_tokens, 0), "bestSupport": round(best_support, 4)}
            result.warnings = sorted(set(warnings))
            trace.end("abstention", {"reason": result.abstain_reason})
            trace.end("response_ready", {"status": result.status})
            result.trace = trace.events
            if save and self.settings.persist_traces:
                self._persist_query(result, principal)
            return result

        # ------------------------------------------------- citation validation
        trace.start("citation_validation")
        valid_ids = [item["id"] for item in evidence_payload]
        text, invalid_markers = strip_invalid_citations(raw_text, valid_ids)
        if invalid_markers:
            warnings.append("removed_invalid_citation_markers")
        text, removed_urls = strip_unverified_urls(text, " ".join(item["text"] for item in evidence_payload))
        if removed_urls:
            warnings.append("removed_unverified_urls")

        claims = self._claims_from_answer(text, evidence_payload)
        cited_ids = sorted({cid for claim in claims for cid in claim.citations})
        citations = [
            self._citation_for(
                item,
                next(c for c in selected if c.chunk.chunk_id == item["chunkId"]),
                question,
            )
            for item in evidence_payload
            if item["id"] in cited_ids
        ]
        uncited = not citations
        if uncited:
            warnings.append("answer_has_no_citation")
        trace.end(
            "citation_validation",
            {
                "cited": len(citations),
                "invalid": len(invalid_markers),
                "uncited": uncited,
            },
        )

        # Conflict detection runs over the *assembled evidence*, not only over the
        # sentences the generator happened to cite: a contradiction that exists in
        # the authorised context must surface even if the answer quoted one side.
        conflict = self._detect_conflict(citations, []) or self._conflict_from_evidence(selected)
        status = AnswerStatus.ANSWERED.value
        if conflict:
            status = AnswerStatus.NEEDS_REVIEW.value
            warnings.append("conflicting_evidence")
            citations = self._merge_conflict_citations(citations, conflict, selected, question)
            text = text.rstrip() + "\n\n" + self._conflict_note(conflict)
        elif uncited:
            status = AnswerStatus.NEEDS_REVIEW.value

        result.status = status
        result.answer = text.strip()
        result.claims = claims
        result.citations = citations
        result.conflict = conflict
        result.metrics = {
            **self._final_metrics(started, timings, retrieval, used_tokens, output.completion_tokens or 0),
            "bestSupport": round(best_support, 4),
            "ttftMs": output.timing.get("ttftMs"),
            "itlMs": output.timing.get("itlMs"),
            "generationMs": generation_ms,
            "claimCitationRate": self._citation_precision(claims),
            "citationsValid": len(citations),
            "citationsInvalid": len(invalid_markers),
            "promptTokens": output.prompt_tokens,
            "completionTokens": output.completion_tokens,
            "tokensEstimated": output.tokens_estimated,
            "evidenceTokens": used_tokens,
        }
        result.warnings = sorted(set(warnings + output_warnings(text)))
        trace.end("response_ready", {"status": status})
        result.trace = trace.events
        if save and self.settings.persist_traces:
            self._persist_query(result, principal)
        return result

    # -------------------------------------------------------------- internals
    def _abstention_text(self, reason: str, best_support: float) -> str:
        if reason == "no_retrieved_evidence":
            return (
                "I could not find this in the indexed sources: retrieval returned no passages at all. "
                "This is a statement about the indexed corpus, not about the world."
            )
        if reason == "no_usable_evidence":
            return (
                "I could not answer this from the indexed sources. Every retrieved passage was "
                "quarantined by the prompt-injection guard, and none of the remaining text could "
                "support an answer."
            )
        if reason == "model_declined_insufficient_evidence":
            return (
                "The retrieved evidence does not contain enough information to answer this "
                "question, so the system abstained instead of guessing."
            )
        return (
            "I could not find enough evidence in the indexed sources to answer this. The best "
            f"passage reached only {best_support:.2f} retrieval support, below the "
            f"{self.settings.abstain_threshold:.2f} threshold, so no answer was generated."
        )

    def _final_metrics(
        self,
        started: float,
        timings: Dict[str, float],
        retrieval: Any,
        context_tokens: int,
        completion_tokens: int,
    ) -> Dict[str, Any]:
        return {
            "totalMs": round((time.perf_counter() - started) * 1000.0, 3),
            "retrievalMs": round(retrieval.timings_ms.get("semanticMs", 0.0) + retrieval.timings_ms.get("lexicalMs", 0.0) + retrieval.timings_ms.get("fusionMs", 0.0) + retrieval.timings_ms.get("rerankMs", 0.0), 3),
            "contextTokens": context_tokens,
            "completionTokens": completion_tokens,
            "semanticCandidates": retrieval.semantic_candidates,
            "lexicalCandidates": retrieval.lexical_candidates,
            "reranked": retrieval.reranked,
        }

    def _claims_from_answer(self, answer: str, evidence: Sequence[Dict[str, Any]]) -> List[Claim]:
        """Split the answer into claims, keeping citation markers with their sentence.

        Markers usually trail the sentence ("... 90 days. [1]"), so a naive
        sentence split strands them in a claim of their own and every citation
        looks unsupported. Marker-only segments are therefore re-attached to the
        preceding claim before markers are extracted.
        """
        # Split after sentence punctuation, but only where a real sentence
        # starts: a trailing "[1]" stays with the sentence it supports.
        segments = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", answer.strip())
        merged: List[str] = []
        for segment in segments:
            stripped = segment.strip()
            if not stripped:
                continue
            if re.fullmatch(r"(?:\s*\[\d{1,2}\])+", stripped) and merged:
                merged[-1] = f"{merged[-1]} {stripped}"
            else:
                merged.append(stripped)

        claims: List[Claim] = []
        for segment in merged:
            markers = [int(marker) for marker in re.findall(r"\[(\d{1,2})\]", segment)]
            markers = sorted({m for m in markers if 1 <= m <= len(evidence)})
            text = re.sub(r"\s*\[\d{1,2}\]", "", segment).strip()
            if not text:
                continue
            claims.append(Claim(text=text, citations=markers, supported=bool(markers)))
        return claims

    def _citation_for(self, item: Dict[str, Any], candidate: Candidate, question: str) -> Citation:
        return Citation(
            evidence_id=item["id"],
            chunk_id=item["chunkId"],
            source_id=item["sourceId"],
            source_title=item["sourceTitle"],
            section=item["section"],
            page=item["page"],
            excerpt=self._best_excerpt(question, candidate.chunk),
            support=float(candidate.support or 0.0),
            valid=True,
            note="citation bound to a retrieved chunk at validation time",
        )

    @staticmethod
    def _best_excerpt(question_text: str, chunk: StoredChunk, *, limit: int = 320) -> str:
        """The sentence of the passage that best matches the question."""
        sentences = split_sentences(chunk.text)
        if not sentences:
            return chunk.text[:limit]
        terms = set(content_terms(question_text))
        best = max(
            sentences,
            key=lambda sentence: len(terms & set(content_terms(sentence))),
            default=sentences[0],
        )
        return best[:limit]

    @staticmethod
    def _citation_precision(claims: Sequence[Claim]) -> float:
        if not claims:
            return 0.0
        return round(sum(1 for claim in claims if claim.citations) / len(claims), 3)

    def _conflict_from_evidence(self, selected: Sequence[Candidate]) -> Optional[Dict[str, Any]]:
        """Numeric contradiction across two selected chunks (authorised context only)."""

        pseudo: List[Citation] = []
        for index, candidate in enumerate(selected[:8]):
            pseudo.append(
                Citation(
                    evidence_id=index + 1,
                    chunk_id=candidate.chunk.chunk_id,
                    source_id=candidate.chunk.source_id,
                    source_title=candidate.chunk.source_title,
                    section=candidate.chunk.section,
                    page=candidate.chunk.page_start,
                    excerpt=candidate.chunk.text,
                    support=float(candidate.support or 0.0),
                )
            )
        return self._detect_conflict(pseudo, [])

    def _conflict_note(self, conflict: Dict[str, Any]) -> str:
        parts = []
        for statement in conflict.get("statements", []):
            unit = conflict["unit"]
            if unit != "percent" and not unit.endswith("s"):
                unit = unit + "s"
            value = statement["value"] + ("%" if conflict["unit"] == "percent" else " " + unit)
            source = statement.get("sourceTitle") or statement.get("sourceId") or "an indexed source"
            parts.append(f"**{value}** according to {source}")
        return (
            "Sources disagree: " + "; ".join(parts) + ". Both statements are cited with provenance; "
            "this answer is marked for review instead of choosing one."
        )

    def _merge_conflict_citations(
        self,
        citations: Sequence[Citation],
        conflict: Dict[str, Any],
        selected: Sequence[Candidate],
        question: str,
    ) -> List[Citation]:
        """Ensure both sides of a contradiction are cited, with provenance."""

        existing = {citation.source_id for citation in citations}
        merged = list(citations)
        by_source = {candidate.chunk.source_id: candidate for candidate in selected}
        for statement in conflict.get("statements", []):
            source_id = statement.get("sourceId")
            candidate_for_title = by_source.get(source_id)
            if candidate_for_title is not None:
                statement.setdefault("sourceTitle", candidate_for_title.chunk.source_title)
            if source_id in existing:
                continue
            candidate = by_source.get(source_id)
            if candidate is None:
                continue
            citation = self._citation_for(
                {
                    "chunkId": candidate.chunk.chunk_id,
                    "sourceId": candidate.chunk.source_id,
                    "sourceTitle": candidate.chunk.source_title,
                    "section": candidate.chunk.section,
                    "page": candidate.chunk.page_start,
                    "id": len(merged) + 1,
                },
                candidate,
                question,
            )
            citation.note = f"conflicting value: {statement['value']} {conflict['unit']}"
            citation.excerpt = statement.get("sentence") or citation.excerpt
            merged.append(citation)
            existing.add(source_id)
        return merged

    def _detect_conflict(
        self, citations: Sequence[Citation], evidence: Sequence[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Flag contradictory statements about the *same subject and unit*.

        Scope is deliberately narrow and stated in the docs:

        * only numbers with a unit (days, hours, weeks, months, years, %) or
          explicit version-like strings are compared;
        * two statements only compete when their subject terms overlap, so
          "backups rotate every 14 days" never conflicts with "the review is due
          in five business days";
        * two different *sources* must be involved (a single document disagreeing
          with itself is a document bug, reported separately);
        * the system never averages or picks a winner: it returns both values
          with provenance and a ``needs_review`` status.
        """

        pattern = re.compile(
            r"(\d+(?:\.\d+)?)\s*(days?|hours?|minutes?|weeks?|months?|years?)s?\b", re.I
        )
        percent = re.compile(r"(\d+(?:\.\d+)?)\s*%")
        stop = {
            "the", "a", "an", "is", "are", "be", "of", "for", "in", "on", "to", "and", "or",
            "with", "by", "at", "after", "before", "this", "that", "it", "must", "should",
            "may", "can", "will", "has", "have", "every", "within", "than", "more", "less",
        }

        def subject_terms(sentence: str) -> set:
            words = {w for w in re.findall(r"[a-z0-9]+", sentence.lower()) if w not in stop}
            return {w for w in words if not w.isdigit()}

        def facts(citation: Citation) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for sentence in re.split(r"(?<=[.!?])\s+", citation.excerpt or ""):
                terms = subject_terms(sentence)
                if not terms:
                    continue
                for value, unit in pattern.findall(sentence):
                    out.append(
                        {
                            "unit": unit.lower().rstrip("s"),
                            "value": value,
                            "terms": terms,
                            "sentence": " ".join(sentence.split())[:200],
                            "sourceId": citation.source_id,
                        }
                    )
                for value in percent.findall(sentence):
                    out.append(
                        {
                            "unit": "percent",
                            "value": value,
                            "terms": terms,
                            "sentence": " ".join(sentence.split())[:200],
                            "sourceId": citation.source_id,
                        }
                    )
            return out

        all_facts: List[Dict[str, Any]] = []
        for citation in citations:
            all_facts.extend(facts(citation))

        for index, left in enumerate(all_facts):
            for right in all_facts[index + 1 :]:
                if left["sourceId"] == right["sourceId"]:
                    continue
                if left["unit"] != right["unit"]:
                    continue
                if left["value"] == right["value"]:
                    continue
                overlap = left["terms"] & right["terms"]
                smaller = max(1, min(len(left["terms"]), len(right["terms"])))
                if len(overlap) / smaller < 0.6:
                    continue  # different subjects: not a contradiction
                return {
                    "unit": left["unit"],
                    "statements": [
                        {
                            "sourceId": left["sourceId"],
                            "value": left["value"],
                            "sentence": left["sentence"],
                        },
                        {
                            "sourceId": right["sourceId"],
                            "value": right["value"],
                            "sentence": right["sentence"],
                        },
                    ],
                    "sharedTerms": sorted(overlap)[:8],
                    "policy": (
                        "both values are surfaced with provenance; the system does not pick one. "
                        "Scope: same-unit numeric disagreement across two sources."
                    ),
                }
        return None

    def _persist_query(self, result: QueryResult, principal: Optional[Principal] = None) -> None:
        metrics = result.metrics or {}
        principal = principal or self.default_principal
        metrics.setdefault("mode", self.settings.retrieval_mode)
        self.store.record_query(
            {
                "query_id": result.query_id,
                "question": result.question[:1000],
                "status": result.status,
                "mode": result.metrics.get("mode") or self.settings.retrieval_mode,
                "provider": str(result.provider.get("used") or result.provider.get("provider") or "unknown"),
                "model": str(result.provider.get("model") or "unknown"),
                "top_k": self.settings.top_k,
                "temperature": self.settings.temperature,
                "latency_ms": metrics.get("totalMs"),
                "retrieval_ms": metrics.get("retrievalMs"),
                "generation_ms": metrics.get("generationMs"),
                "ttft_ms": metrics.get("ttftMs"),
                "itl_ms": metrics.get("itlMs"),
                "prompt_tokens": metrics.get("promptTokens"),
                "completion_tokens": metrics.get("completionTokens"),
                "tokens_estimated": 1 if metrics.get("tokensEstimated") else 0,
                "citations_total": len(result.citations) + int(metrics.get("citationsInvalid") or 0),
                "citations_valid": len(result.citations),
                "abstain_reason": result.abstain_reason,
                "trace_json": _json_dumps(result.trace),
                "created_at": result.created_at,
                "tenant_id": principal.tenant_id,
                "principal": principal.subject_id,
            }
        )

    # --------------------------------------------------------------- lifecycle
    def list_sources(
        self, *, principal: Optional[Principal] = None, include_deleted: bool = False
    ) -> List[Dict[str, Any]]:
        principal = principal or self.default_principal
        principal.require("read")
        chunks = self.store.all_chunks(allowed_sources=principal.scope())
        per_source: Dict[str, Dict[str, int]] = {}
        for chunk in chunks:
            bucket = per_source.setdefault(chunk.source_id, {"chunks": 0, "tokens": 0})
            bucket["chunks"] += 1
            bucket["tokens"] += chunk.token_count
        output = []
        for source in self.store.list_sources(include_deleted=include_deleted):
            if not principal.is_local and source.tenant_id != principal.tenant_id:
                continue
            scope = principal.allowed_sources
            if scope is not None and source.source_id not in scope:
                continue
            data = source.to_dict()
            data.update(per_source.get(source.source_id, {"chunks": 0, "tokens": 0}))
            output.append(data)
        return output

    def get_source(self, source_id: str, *, principal: Optional[Principal] = None) -> Dict[str, Any]:
        principal = principal or self.default_principal
        principal.require("read")
        record = self.store.get_source(source_id)
        if record is None:
            raise NotFound("source not found", sourceId=source_id)
        if not self._may_read_source(record, principal):
            # Same error as a missing source: never confirm another tenant's id.
            raise NotFound("source not found", sourceId=source_id)
        data = record.to_dict()
        data["chunks"] = len(self.store.chunks_for_source(source_id))
        return data

    def _may_read_source(self, record: Any, principal: Principal) -> bool:
        if principal.allowed_sources is not None and record.source_id not in principal.allowed_sources:
            return False
        if principal.is_local:
            return True
        return record.tenant_id == principal.tenant_id

    def source_chunks(self, source_id: str, *, principal: Optional[Principal] = None) -> List[Dict[str, Any]]:
        principal = principal or self.default_principal
        principal.require("read")
        record = self.store.get_source(source_id)
        if record is None or not self._may_read_source(record, principal):
            raise NotFound("source not found", sourceId=source_id)
        return [chunk.to_dict() for chunk in self.store.chunks_for_source(source_id)]

    def delete_source(self, source_id: str, *, principal: Optional[Principal] = None) -> Dict[str, Any]:
        principal = principal or self.default_principal
        principal.require("delete")
        record = self.store.get_source(source_id)
        if record is None or not self._may_read_source(record, principal):
            raise NotFound("source not found", sourceId=source_id)
        job_id = self.store.start_job("delete", source_id)
        trace = _Trace(self.events, trace_id=job_id, mode="live", operation_id=job_id)
        trace.start("source.delete", {"sourceId": source_id})
        result = self.store.delete_source(source_id)
        trace.end("source.delete", {"sourceId": source_id, **result})
        self.store.finish_job(job_id, "succeeded", detail=result)
        return {**result, "indexSize": self.store.counts()["chunks"]}

    def rebuild(
        self,
        *,
        reembed: bool = True,
        force: bool = False,
        principal: Optional[Principal] = None,
        refit: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Rebuild the vector index without ever leaving a half-written index live.

        Stages every vector in ``vectors_staged``, validates counts/dimensions,
        then swaps in one transaction while the previous vectors are kept in
        ``vectors_backup`` for :meth:`rollback_rebuild`. A crash before the swap
        leaves the previous index exactly as it was.
        """

        principal = principal or self.default_principal
        principal.require("rebuild")
        stored_chunking = self.store.get_meta("chunking_fingerprint")
        current_chunking = self.settings.chunking_fingerprint()
        notes: List[str] = []
        if stored_chunking and stored_chunking != current_chunking and not force:
            notes.append(
                "chunking configuration changed; re-ingest the original files to apply it "
                "(rebuild only re-embeds existing chunks)"
            )
        previous_fingerprint = self.store.get_meta("embedding_fingerprint")
        job_id = self.store.start_job("rebuild")
        trace = _Trace(self.events, trace_id=job_id, mode="live", operation_id=job_id)
        chunks = self.store.all_chunks()
        started = time.perf_counter()
        trace.start("index.rebuild", {"chunks": len(chunks)})
        try:
            if refit is None:
                refit = force
            if refit and hasattr(self.embedder.provider, "fit_from_corpus"):
                self.embedder.reset_state()
            self._prepare_embeddings([c.text for c in chunks])

            staged: List[tuple] = []
            fingerprint = self.embedder.fingerprint
            for chunk in chunks:
                vector = self.embedder.embed_one(chunk.text)
                staged.append((chunk.chunk_id, len(vector), pack_vector(vector)))
            if staged and len({row[1] for row in staged}) > 1:
                raise ProviderError("rebuild produced inconsistent vector dimensions")
            self.store.stage_vectors(staged, fingerprint)
            staged_count = self.store.staged_counts()
            if staged_count != len(chunks):
                raise ProviderError(
                    "staged vector count does not match chunk count",
                    hint=f"staged={staged_count} chunks={len(chunks)}",
                )
            installed = self.store.commit_staged_vectors(
                fingerprint, previous_fingerprint=previous_fingerprint
            )
            self.store.set_meta("embedding_fingerprint", fingerprint)
            self.store.set_meta("chunking_fingerprint", current_chunking)
            self.store.set_meta("last_rebuild_at", now_iso())
            trace.end(
                "index.rebuild",
                {
                    "chunks": len(staged),
                    "installed": installed["installed"],
                    "backedUp": installed["backedUp"],
                    "fingerprint": fingerprint,
                },
            )
            self.store.finish_job(job_id, "succeeded", detail={"chunks": len(staged)})
        except Exception as exc:
            self.store.clear_staged()
            trace.fail("index.rebuild", {"error": str(exc)[:120]})
            self.store.finish_job(job_id, "failed", error_code="rebuild_failed", error=str(exc)[:300])
            raise
        return {
            "chunks": len(staged),
            "sources": len({chunk.source_id for chunk in chunks}),
            "ms": round((time.perf_counter() - started) * 1000.0, 3),
            "embedding": describe_embedder(self.embedder),
            "fingerprint": fingerprint,
            "previousFingerprint": previous_fingerprint,
            "rollbackAvailable": bool(installed["backedUp"]),
            "notes": notes,
        }

    def rollback_rebuild(self, *, principal: Optional[Principal] = None) -> Dict[str, Any]:
        """Restore the vectors that were live before the last rebuild."""

        principal = principal or self.default_principal
        principal.require("rebuild")
        restored = self.store.restore_backup()
        if restored.get("fingerprint"):
            self.store.set_meta("embedding_fingerprint", restored["fingerprint"])
        return restored

    def backup_info(self) -> Dict[str, Any]:
        return self.store.backup_info()

    # ------------------------------------------------------------------- traces
    def get_trace(self, query_id: str, *, principal: Optional[Principal] = None) -> Dict[str, Any]:
        principal = principal or self.default_principal
        principal.require("traces")
        row = self.store.query_row(query_id)
        if row is None or (not principal.is_local and row.get("tenant_id") not in (None, principal.tenant_id)):
            raise NotFound("trace not found", queryId=query_id)
        return {
            "queryId": row["query_id"],
            "question": row["question"][:500],
            "status": row["status"],
            "mode": row["mode"],
            "provider": row["provider"],
            "model": row["model"],
            "stages": json.loads(row["trace_json"] or "[]"),
            "latencyMs": row["latency_ms"],
            "retrievalMs": row["retrieval_ms"],
            "generationMs": row["generation_ms"],
            "createdAt": row["created_at"],
        }

    def recent_events(self, *, trace_id: Optional[str] = None, since: int = 0, limit: int = 128) -> List[Dict[str, Any]]:
        return self.events.history(trace_id=trace_id, since=since, limit=limit)

    # ---------------------------------------------------------------- diagnostics
    def health(self) -> HealthReport:
        counts = self.store.counts()
        integrity = self.store.integrity_report(self.embedder.dimension)
        embedder_health = self.embedder.health()
        generator_health = self.generator.availability()
        deployment = check_deployment(self.settings)
        return HealthReport(
            {
                "ok": not integrity["problems"],
                "service": "ragformation",
                "version": __import__("ragformation").__version__,
                "apiVersion": API_VERSION,
                "eventSchemaVersion": EVENT_SCHEMA_VERSION,
                "sources": counts["sources"],
                "chunks": counts["chunks"],
                "vectors": counts["vectors"],
                "lexicalRows": counts["lexicalRows"],
                "queries": self.store.count_queries(),
                "embedding": {**describe_embedder(self.embedder), "health": embedder_health},
                "generation": {
                    **describe_generator(self.generator),
                    "health": generator_health,
                    "mode": self._event_mode(),
                },
                "indexFingerprint": self.store.get_meta("embedding_fingerprint"),
                "lastIngestAt": self.store.get_meta("last_ingest_at"),
                "lastRebuildAt": self.store.get_meta("last_rebuild_at"),
                "rollback": self.store.backup_info(),
                "integrity": integrity,
                "schemaVersion": self.store.get_meta("schema_version"),
                "security": deployment,
                "events": {"schemaVersion": EVENT_SCHEMA_VERSION, "published": self.events.published},
                "config": {
                    "topK": self.settings.top_k,
                    "mode": self.settings.retrieval_mode,
                    "temperature": self.settings.temperature,
                    "topP": self.settings.top_p,
                    "abstainThreshold": self.settings.abstain_threshold,
                    "contextTokenBudget": self.settings.max_context_tokens,
                    "requireSemantic": self.settings.require_semantic,
                },
            }
        )

    def stats(self, limit: int = 500, *, principal: Optional[Principal] = None) -> Dict[str, Any]:
        principal = principal or self.default_principal
        principal.require("stats")
        rows = self.store.query_rows(
            limit=limit, tenant_id=None if principal.is_local else principal.tenant_id
        )
        latencies = [row["latency_ms"] for row in rows]
        ttft = [row["ttft_ms"] for row in rows]
        itl = [row["itl_ms"] for row in rows]
        generation = [row["generation_ms"] for row in rows]
        retrieval = [row["retrieval_ms"] for row in rows]
        statuses: Dict[str, int] = {}
        for row in rows:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        citation_rows = [
            (row["citations_valid"] or 0, row["citations_total"] or 0)
            for row in rows
            if row["citations_total"]
        ]
        citation_precision = (
            round(sum(valid for valid, _ in citation_rows) / max(1, sum(total for _, total in citation_rows)), 3)
            if citation_rows
            else None
        )
        citations = [row["citations_valid"] or 0 for row in rows]
        return {
            "queries": len(rows),
            "statuses": statuses,
            "latencyMs": summarize(latencies),
            "retrievalMs": summarize(retrieval),
            "generationMs": summarize(generation),
            "ttftMs": summarize([value for value in ttft if value is not None]),
            "itlMs": summarize([value for value in itl if value is not None]),
            "citationsPerAnswer": summarize(citations),
            "citationPrecision": citation_precision,
            "abstentionRate": round(statuses.get("abstained", 0) / len(rows), 3) if rows else 0.0,
            "window": min(limit, len(rows)),
            "embedding": {"provider": self.embedder.name, "semantic": self.embedder.semantic, "fingerprint": self.embedder.fingerprint},
            "generationMode": self._event_mode(),
            "recent": [
                {
                    "queryId": row["query_id"],
                    "question": row["question"][:160],
                    "status": row["status"],
                    "latencyMs": row["latency_ms"],
                    "ttftMs": row["ttft_ms"],
                    "provider": row["provider"],
                    "model": row["model"],
                    "citationsValid": row["citations_valid"],
                    "createdAt": row["created_at"],
                }
                for row in rows[:20]
            ],
        }

    def doctor(self) -> Dict[str, Any]:
        health = self.health()
        checks: List[Dict[str, Any]] = []
        embedding = health["embedding"]
        embedder_health = embedding["health"]
        checks.append(
            {
                "name": "embedding_provider",
                "ok": bool(embedder_health.get("ok")) and bool(embedding["semantic"]),
                "detail": f"{embedding['provider']} / {embedding['model']} (d={embedding['dimension']}): {embedding['note']}",
                "fix": None if embedding["semantic"] else embedding.get("setup") or "configure a semantic provider",
            }
        )
        generation = health["generation"]
        generation_health = generation["health"]
        checks.append(
            {
                "name": "generation_provider",
                "ok": bool(generation_health.get("ok")) and bool(generation["neural"]),
                "detail": generation["note"],
                "fix": None if generation["neural"] else "install Ollama and pull a chat model, e.g. ollama pull llama3.2:3b",
            }
        )
        checks.append(
            {
                "name": "index_integrity",
                "ok": not health["integrity"]["problems"],
                "detail": health["integrity"]["problems"] or "no problems found",
                "fix": "run: ragformation rebuild" if health["integrity"]["problems"] else None,
            }
        )
        deployment = health["security"]
        checks.append(
            {
                "name": "deployment_security",
                "ok": bool(deployment["ok"]),
                "detail": f"{deployment['securityMode']}: {deployment['detail']}",
                "fix": None
                if deployment["ok"]
                else "set RAG_API_KEY, or RAG_TRUST_PROXY=1 behind an authenticating host",
            }
        )
        checks.append(
            {
                "name": "corpus_present",
                "ok": health["chunks"] > 0,
                "detail": f"{health['sources']} sources / {health['chunks']} chunks",
                "fix": "run: ragformation ingest corpus/",
            }
        )
        checks.append(
            {
                "name": "lexical_index",
                "ok": health["lexicalRows"] == health["chunks"],
                "detail": f"{health['lexicalRows']} FTS rows for {health['chunks']} chunks",
                "fix": "run: ragformation rebuild",
            }
        )
        return {
            "ok": all(check["ok"] for check in checks),
            "checks": checks,
            "health": health,
            "readiness": self.readiness(),
        }

    def readiness(self) -> str:
        """A precise readiness label; never "production-ready" without evidence."""

        health = self.health()
        if health["integrity"]["problems"]:
            return "index-degraded"
        semantic = bool(health["embedding"]["semantic"])
        neural = bool(health["generation"]["neural"])
        secured = bool(health["security"]["ok"])
        if semantic and neural and secured:
            return "provider-verified"
        if semantic and secured:
            return "integration-ready"
        if secured:
            return "local-ready"
        return "insecure-configuration"

    # ------------------------------------------------------------------ export
    def export_bundle(self, questions: Sequence[str] = ()) -> Dict[str, Any]:
        return {
            "generatedAt": now_iso(),
            "health": self.health(),
            "sources": self.list_sources(),
            "chunks": [chunk.to_dict() for chunk in self.store.all_chunks()],
            "answers": {question: self.ask(question).to_dict() for question in questions},
        }


def _json_dumps(value: Any) -> str:
    import json

    try:
        return json.dumps(value, default=str)[:200_000]
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return "[]"

