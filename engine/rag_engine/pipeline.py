"""The end-to-end pipeline: ingest -> chunk -> embed -> index -> query -> answer.

Every public method emits real trace events with real durations. Generation is
**extractive and deterministic** in demo mode: sentences are selected from the
assembled evidence and each one is bound to the chunk it came from, so every
citation is verifiable by construction. No language model is called, and the UI
labels the answer as ``extractive-synthesis-v1``.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Sequence, Tuple

from .chunking import Chunk, ChunkingConfig, chunk_document, tokenize, approx_tokens
from .contracts import TraceRecorder
from .embedding import HashEmbedder, cosine
from .index import SearchHit, VectorIndex
from .ingest import Document, SourceRegistry, load_corpus
from .rerank import RERANKER_ID, coverage_score, relevance_score, rerank
from .security import InjectionFinding, scan_for_injection

ANSWER_SYNTHESIS_ID = "extractive-synthesis-v1"
ABSTAIN_THRESHOLD = 0.30  # grounding score below this => insufficient evidence
CONTEXT_TOKEN_BUDGET = 520


@dataclass
class Citation:
    marker: str
    chunk_id: str
    source_id: str
    source_title: str
    revision: str
    section: str
    page: int
    excerpt: str
    valid: bool
    validation_note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class Claim:
    text: str
    citations: List[str] = field(default_factory=list)
    supported: bool = True
    support_score: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class Answer:
    trace_id: str
    question: str
    status: str  # "answered" | "abstained" | "needs_review"
    answer_text: str
    claims: List[Claim] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)
    abstain_reason: str = ""
    conflict: Optional[Dict[str, object]] = None
    context_chunks: List[Dict[str, object]] = field(default_factory=list)
    excluded: List[Dict[str, object]] = field(default_factory=list)
    retrieved: List[Dict[str, object]] = field(default_factory=list)
    quarantined: List[Dict[str, object]] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        return data


class RagEngine:
    """Owns the corpus, the index and the answer policy."""

    def __init__(
        self,
        registry: Optional[SourceRegistry] = None,
        embedder: Optional[HashEmbedder] = None,
        chunking: Optional[ChunkingConfig] = None,
    ) -> None:
        self.registry = registry or load_corpus()
        self.embedder = embedder or HashEmbedder()
        self.chunking = chunking or ChunkingConfig()
        self.index = VectorIndex(self.embedder)
        self.chunks: Dict[str, Chunk] = {}
        self.ingest_trace: List[Dict[str, object]] = []
        self.ingest_report: Dict[str, object] = {}

    # ---------------------------------------------------------------- ingest
    def ingest(self, trace_id: Optional[str] = None) -> List[Dict[str, object]]:
        trace_id = trace_id or f"ing-{uuid.uuid4().hex[:10]}"
        rec = TraceRecorder(trace_id)
        timings: List[Tuple[str, float]] = []
        warnings: List[str] = []

        for doc in self.registry.all():
            t0 = time.perf_counter()
            rec.emit(
                "source_received",
                "completed",
                sourceId=doc.source_id,
                sourceRevision=doc.revision,
                safeMetadata={"title": doc.title, "bytes": doc.byte_size, "mediaType": doc.media_type},
            )
            rec.emit(
                "extracting",
                "completed",
                sourceId=doc.source_id,
                safeMetadata={"blocks": len(doc.blocks), "parser": "structure-blocks-v1"},
            )
            rec.emit(
                "normalising",
                "completed",
                sourceId=doc.source_id,
                safeMetadata={"checksum": doc.checksum},
            )
            chunks = chunk_document(doc, self.chunking)
            for chunk in chunks:
                self.chunks[chunk.chunk_id] = chunk
                self.index.add(chunk)
            rec.emit(
                "chunking",
                "completed",
                sourceId=doc.source_id,
                chunkIds=[c.chunk_id for c in chunks],
                safeMetadata={
                    "chunkCount": len(chunks),
                    "targetTokens": self.chunking.target_tokens,
                    "overlapTokens": self.chunking.overlap_tokens,
                },
            )
            rec.emit(
                "embedding",
                "completed",
                sourceId=doc.source_id,
                safeMetadata={"model": self.embedder.model_id, "dimension": self.embedder.dimension},
            )
            rec.emit(
                "indexing",
                "completed",
                sourceId=doc.source_id,
                chunkIds=[c.chunk_id for c in chunks],
                safeMetadata={"indexSize": self.index.size},
            )
            timings.append((doc.source_id, (time.perf_counter() - t0) * 1000.0))

        rec.emit(
            "completed",
            "completed",
            safeMetadata={
                "sources": len(self.registry),
                "chunks": self.index.size,
                "embeddingModel": self.embedder.model_id,
                "dimension": self.embedder.dimension,
            },
        )
        self.ingest_trace = rec.to_dicts()
        self.ingest_report = {
            "traceId": trace_id,
            "sources": len(self.registry),
            "chunks": self.index.size,
            "totalMs": round(sum(t for _, t in timings), 3),
            "perSourceMs": {sid: round(t, 3) for sid, t in timings},
            "warnings": warnings,
        }
        return self.ingest_trace

    # ----------------------------------------------------------------- query
    def query(
        self,
        question: str,
        top_k: int = 6,
        mode: str = "hybrid",
        rerank_enabled: bool = True,
        temperature: float = 0.0,
        trace_id: Optional[str] = None,
        allowed_sources: Optional[set] = None,
    ) -> Dict[str, object]:
        """Run one full query and return a JSON-safe payload including the trace."""
        question = (question or "").strip()
        if not question:
            raise ValueError("question must not be empty")
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        if mode not in {"semantic", "lexical", "hybrid"}:
            raise ValueError("mode must be semantic|lexical|hybrid")

        trace_id = trace_id or f"qry-{uuid.uuid4().hex[:10]}"
        rec = TraceRecorder(trace_id)
        active_ids = {d.source_id for d in self.registry.active()}
        if allowed_sources is not None:
            active_ids &= set(allowed_sources)

        t0 = time.perf_counter()
        rec.emit(
            "query_received",
            "completed",
            safeMetadata={"questionChars": len(question), "topK": top_k, "mode": mode},
        )

        q_vec = self.embedder.embed(question)
        rec.emit(
            "query_embedding",
            "completed",
            safeMetadata={"model": self.embedder.model_id, "dimension": self.embedder.dimension},
        )

        semantic: List[SearchHit] = []
        lexical: List[SearchHit] = []
        if mode in {"semantic", "hybrid"}:
            semantic = self.index.semantic_search(q_vec, top_k=max(top_k * 3, 12), allowed_sources=active_ids)
            rec.emit(
                "semantic_retrieval",
                "completed",
                resultIds=[h.chunk_id for h in semantic],
                safeMetadata={"candidates": len(semantic), "metric": "cosine"},
            )
        else:
            rec.emit("semantic_retrieval", "skipped", safeMetadata={"reason": "mode=lexical"})

        if mode in {"lexical", "hybrid"}:
            lexical = self.index.lexical_search(question, top_k=max(top_k * 3, 12), allowed_sources=active_ids)
            rec.emit(
                "lexical_retrieval",
                "completed",
                resultIds=[h.chunk_id for h in lexical],
                safeMetadata={"candidates": len(lexical), "metric": "bm25"},
            )
        else:
            rec.emit("lexical_retrieval", "skipped", safeMetadata={"reason": "mode=semantic"})

        if mode == "hybrid":
            fused = self.index.fuse_rrf(semantic, lexical, top_k=max(top_k * 2, 10))
            rec.emit(
                "hybrid_fusion",
                "completed",
                resultIds=[h.chunk_id for h in fused],
                safeMetadata={"algorithm": "rrf", "k": 60},
            )
        else:
            fused = semantic or lexical
            rec.emit("hybrid_fusion", "skipped", safeMetadata={"reason": f"mode={mode}"})

        if rerank_enabled and fused:
            tr = time.perf_counter()
            ranked = rerank(fused, question, query_vector=q_vec, embedder=self.embedder)
            ms = (time.perf_counter() - tr) * 1000.0
            rec.emit(
                "reranking",
                "completed",
                durationMs=round(ms, 3),
                resultIds=[h.chunk_id for h in ranked],
                safeMetadata={"reranker": RERANKER_ID, "inCandidates": len(fused)},
            )
        else:
            ranked = list(fused)
            rec.emit("reranking", "skipped", safeMetadata={"reason": "disabled"})

        shortlist = ranked[: max(top_k, 1)]
        best_score = shortlist[0].score if shortlist else 0.0

        # --- security boundary: retrieved text is data, not instructions
        shortlist_chunks = [h.chunk for h in shortlist if h.chunk]
        findings: List[InjectionFinding] = scan_for_injection(shortlist_chunks)
        flagged = {f.chunk_id for f in findings}
        if findings:
            rec.emit(
                "context_assembly",
                "skipped",
                warningCode="untrusted_instruction_detected",
                resultIds=sorted(flagged),
                safeMetadata={
                    "policy": "retrieved_text_is_data",
                    "rules": sorted({f.rule_id for f in findings}),
                    "quarantined": len(flagged),
                },
            )

        # --- context assembly with a real token budget
        selected: List[SearchHit] = []
        excluded: List[Dict[str, object]] = []
        used_tokens = 0
        seen_sources: Dict[str, int] = {}
        for hit in shortlist:
            chunk = hit.chunk
            if chunk is None:
                continue
            if chunk.chunk_id in flagged:
                excluded.append(
                    {
                        "chunkId": chunk.chunk_id,
                        "reason": "quarantined_untrusted_instruction",
                        "sourceId": chunk.source_id,
                    }
                )
                continue
            # Diversity guard: never let one source occupy more than half the packet.
            if seen_sources.get(chunk.source_id, 0) >= max(1, math.ceil(top_k / 2)):
                excluded.append(
                    {"chunkId": chunk.chunk_id, "reason": "source_diversity_cap", "sourceId": chunk.source_id}
                )
                continue
            if used_tokens + chunk.token_count > CONTEXT_TOKEN_BUDGET:
                excluded.append(
                    {"chunkId": chunk.chunk_id, "reason": "token_budget", "sourceId": chunk.source_id}
                )
                continue
            selected.append(hit)
            used_tokens += chunk.token_count
            seen_sources[chunk.source_id] = seen_sources.get(chunk.source_id, 0) + 1

        rec.emit(
            "context_assembly",
            "completed",
            chunkIds=[h.chunk_id for h in selected],
            safeMetadata={
                "selected": len(selected),
                "excluded": len(excluded),
                "tokenBudget": CONTEXT_TOKEN_BUDGET,
                "tokensUsed": used_tokens,
            },
        )

        # --- generation (extractive) or honest abstention
        conflict = self._detect_conflict(selected[:3])
        if not selected or best_score < ABSTAIN_THRESHOLD:
            reason = "empty_evidence" if not selected else "evidence_below_threshold"
            rec.emit(
                "abstention",
                "completed",
                safeMetadata={
                    "reason": reason,
                    "bestCoverage": round(best_score, 4),
                    "threshold": ABSTAIN_THRESHOLD,
                },
            )
            text = (
                "I don't have enough evidence in the indexed sources to answer this. "
                f"The best retrieved passage scored {best_score:.2f} on query coverage, "
                f"below the {ABSTAIN_THRESHOLD:.2f} grounding threshold. "
                "Rather than guess, this response abstains."
            )
            answer = Answer(
                trace_id=trace_id,
                question=question,
                status="abstained",
                answer_text=text,
                claims=[Claim(text=text, citations=[], supported=False, support_score=best_score)],
                abstain_reason=reason,
                context_chunks=[self._chunk_view(h) for h in selected],
                excluded=excluded,
                retrieved=[h.to_dict() for h in ranked],
                quarantined=[f.to_dict() for f in findings],
            )
        else:
            tg = time.perf_counter()
            claims, citations = self._synthesise(selected, question)
            gen_ms = (time.perf_counter() - tg) * 1000.0
            rec.emit(
                "generation",
                "completed",
                durationMs=round(gen_ms, 3),
                chunkIds=[h.chunk_id for h in selected],
                safeMetadata={
                    "synthesis": ANSWER_SYNTHESIS_ID,
                    "claims": len(claims),
                    "temperature": temperature,
                    "note": "extractive; no language model invoked",
                },
            )
            valid = [c for c in citations if c.valid]
            rec.emit(
                "citation_validation",
                "completed",
                safeMetadata={
                    "citations": len(citations),
                    "valid": len(valid),
                    "invalid": len(citations) - len(valid),
                },
            )
            text = " ".join(c.text for c in claims)
            status = "needs_review" if conflict else "answered"
            answer = Answer(
                trace_id=trace_id,
                question=question,
                status=status,
                answer_text=text,
                claims=claims,
                citations=citations,
                conflict=conflict,
                context_chunks=[self._chunk_view(h) for h in selected],
                excluded=excluded,
                retrieved=[h.to_dict() for h in ranked],
                quarantined=[f.to_dict() for f in findings],
            )

        total_ms = (time.perf_counter() - t0) * 1000.0
        rec.emit(
            "completed",
            "completed",
            durationMs=round(total_ms, 3),
            safeMetadata={"status": answer.status},
        )

        answer.metrics = {
            "totalMs": round(total_ms, 3),
            "candidatesSemantic": len(semantic),
            "candidatesLexical": len(lexical),
            "shortlist": len(shortlist),
            "selected": len(selected),
            "contextTokens": used_tokens,
            "bestCoverage": round(best_score, 4),
        }
        payload = answer.to_dict()
        payload["trace"] = rec.to_dicts()
        payload["config"] = {
            "embeddingModel": self.embedder.model_id,
            "dimension": self.embedder.dimension,
            "reranker": RERANKER_ID if rerank_enabled else None,
            "synthesis": ANSWER_SYNTHESIS_ID,
            "chunking": asdict(self.chunking),
            "contextTokenBudget": CONTEXT_TOKEN_BUDGET,
            "abstainThreshold": ABSTAIN_THRESHOLD,
            "indexSize": self.index.size,
            "sources": self.registry.to_meta(),
            "temperatureNote": "No LLM is called; temperature is recorded but has no effect in demo mode.",
        }
        return payload

    # -------------------------------------------------------------- internals
    @staticmethod
    def _chunk_view(hit: SearchHit) -> Dict[str, object]:
        chunk = hit.chunk
        assert chunk is not None
        data = chunk.to_dict()
        data["score"] = round(hit.score, 6)
        data["channel"] = hit.channel
        return data

    def _synthesise(
        self, selected: Sequence[SearchHit], question: str
    ) -> Tuple[List[Claim], List[Citation]]:
        """Pick the best-supporting sentence from each selected chunk."""
        q_terms = set(tokenize(question))
        claims: List[Claim] = []
        citations: List[Citation] = []
        counters = {"c": 0}

        for hit in selected[:3]:
            chunk = hit.chunk
            if chunk is None:
                continue
            sentences = [
                s.strip()
                for s in re.split(r"(?<=[.!?])\s+", chunk.text)
                # Require a complete, capitalised sentence: chunks may start mid-sentence
                # because of overlap carry, and a fragment is a bad citation.
                if len(s.split()) >= 6 and s[:1].isupper() and s[-1:] in ".!?"
            ]
            if not sentences:
                continue
            best = max(
                sentences,
                key=lambda s: len(q_terms & set(tokenize(s))) / max(1, len(q_terms)),
            )
            support = relevance_score(chunk, question)
            counters["c"] += 1
            marker = f"[{counters['c']}]"
            claims.append(
                Claim(text=f"{best} {marker}", citations=[marker], supported=support >= 0.3, support_score=round(support, 4))
            )
            citations.append(
                Citation(
                    marker=marker,
                    chunk_id=chunk.chunk_id,
                    source_id=chunk.source_id,
                    source_title=chunk.source_title,
                    revision=chunk.revision,
                    section=chunk.section,
                    page=chunk.page_start,
                    excerpt=best,
                    valid=self.chunks.get(chunk.chunk_id) is not None,
                    validation_note="bound to indexed chunk at synthesis time",
                )
            )
        return claims, citations

    def _detect_conflict(self, selected: Sequence[SearchHit]) -> Optional[Dict[str, object]]:
        """Flag numeric disagreement about the same unit across two sources.

        Deliberately narrow: only fires when the *most relevant sentence* of two
        different sources states a different number for the same unit. Returns
        None when there is nothing to report — it never invents a conflict.
        """
        num_rx = re.compile(r"(\d+(?:\.\d+)?)\s*(days|hours|minutes|years|months)\b", re.I)
        by_unit: Dict[str, Dict[str, str]] = {}
        for hit in selected:
            chunk = hit.chunk
            if chunk is None:
                continue
            claims, _ = self._synthesise([hit], chunk.text)
            sentence = claims[0].text if claims else chunk.text
            for value, unit in num_rx.findall(sentence):
                unit_key = unit.lower()
                bucket = by_unit.setdefault(unit_key, {})
                bucket.setdefault(chunk.source_id, value)
        for unit, sources in by_unit.items():
            values = set(sources.values())
            if len(sources) > 1 and len(values) > 1:
                return {
                    "unit": unit,
                    "statements": [{"sourceId": sid, "value": val} for sid, val in sources.items()],
                    "policy": "both statements surfaced with provenance; no silent choice",
                }
        return None

    # ------------------------------------------------------------ lifecycle
    def update_source(self, doc: Document) -> Dict[str, object]:
        """Swap in a new revision and reindex only that source."""
        previous = self.registry.upsert_revision(doc)
        removed = self.index.remove_source(doc.source_id)
        for chunk in list(self.chunks.values()):
            if chunk.source_id == doc.source_id:
                del self.chunks[chunk.chunk_id]
        chunks = chunk_document(doc, self.chunking)
        for chunk in chunks:
            self.chunks[chunk.chunk_id] = chunk
            self.index.add(chunk)
        rec = TraceRecorder(f"life-{uuid.uuid4().hex[:8]}")
        rec.emit(
            "source_updated",
            "completed",
            sourceId=doc.source_id,
            sourceRevision=doc.revision,
            chunkIds=[c.chunk_id for c in chunks],
            safeMetadata={"previousRevision": previous, "retiredChunks": removed},
        )
        return {"trace": rec.to_dicts(), "retired": removed, "indexSize": self.index.size}

    def delete_source(self, source_id: str) -> Dict[str, object]:
        removed = self.index.remove_source(source_id)
        for chunk in list(self.chunks.values()):
            if chunk.source_id == source_id:
                del self.chunks[chunk.chunk_id]
        ok = self.registry.delete(source_id)
        rec = TraceRecorder(f"life-{uuid.uuid4().hex[:8]}")
        rec.emit(
            "source_deleted",
            "completed",
            sourceId=source_id,
            safeMetadata={"existed": ok, "retiredChunks": removed, "indexSize": self.index.size},
        )
        return {"trace": rec.to_dicts(), "retired": removed, "indexSize": self.index.size}


def answer_to_dict(answer: Answer) -> Dict[str, object]:  # convenience for tests
    return answer.to_dict()
