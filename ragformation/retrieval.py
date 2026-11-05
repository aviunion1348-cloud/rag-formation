"""Hybrid retrieval: cosine semantic search, SQLite BM25, RRF fusion, MMR.

Ranking notes that matter for honesty:

* Cosine similarity is **not** a probability and is never presented as one.
* Fusion uses Reciprocal Rank Fusion (k=60), which needs no score calibration —
  the right call when semantic scores and BM25 scores live on different scales.
* The reranker is a transparent feature blend over the *same* signals
  (``heuristic-coverage-v1``); it is not a cross-encoder and is labelled as such.
* The abstention score is a retrieval support score, calibrated against the
  labelled evaluation set in ``evals/golden.json`` — not a confidence claim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from operator import mul
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .config import Settings
from .store import Store, StoredChunk
from .text import STOPWORDS, TextSignals, content_terms, matched_terms_signals, tokenize

RERANKER_ID = "heuristic-coverage-v1"


@dataclass
class Candidate:
    chunk: StoredChunk
    score: float
    channel: str
    rank: int = 0
    semantic_score: Optional[float] = None
    lexical_score: Optional[float] = None
    fusion_score: Optional[float] = None
    support: Optional[float] = None

    def to_dict(self, *, include_text: bool = True, text_limit: int = 400) -> Dict[str, Any]:
        data = self.chunk.to_dict()
        if not include_text:
            data["text"] = data["text"][:text_limit]
        data.update(
            {
                "score": round(self.score, 6),
                "channel": self.channel,
                "rank": self.rank,
                "semanticScore": (
                    round(self.semantic_score, 6) if self.semantic_score is not None else None
                ),
                "lexicalScore": (
                    round(self.lexical_score, 6) if self.lexical_score is not None else None
                ),
                "support": round(self.support, 6) if self.support is not None else None,
            }
        )
        return data


@dataclass
class RetrievalResult:
    candidates: List[Candidate] = field(default_factory=list)
    semantic_candidates: int = 0
    lexical_candidates: int = 0
    fused_candidates: int = 0
    mode: str = "hybrid"
    reranked: bool = False
    timings_ms: Dict[str, float] = field(default_factory=dict)
    filters: Dict[str, Any] = field(default_factory=dict)

    @property
    def best_support(self) -> float:
        return max((c.support or 0.0 for c in self.candidates), default=0.0)


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    """Raw dot product (fast path for vectors the embedder already normalised)."""
    if len(a) != len(b):
        return 0.0
    return sum(map(mul, a, b))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Correct for unnormalised input too, just slower."""
    if len(a) != len(b):
        return 0.0
    numerator = sum(map(mul, a, b))
    na = math.sqrt(sum(map(mul, a, a)))
    nb = math.sqrt(sum(map(mul, b, b)))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return numerator / (na * nb)


class Retriever:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        #: chunk_id -> cached token lookups (passages are immutable per revision).
        self._signals: Dict[str, TextSignals] = {}

    # ------------------------------------------------------------------ public
    def search(
        self,
        query: str,
        query_vector: Sequence[float],
        *,
        top_k: Optional[int] = None,
        mode: Optional[str] = None,
        rerank: Optional[bool] = None,
        allowed_sources: Optional[Set[str]] = None,
        mmr: bool = True,
    ) -> RetrievalResult:
        import time

        top_k = top_k or self.settings.top_k
        mode = mode or self.settings.retrieval_mode
        rerank = self.settings.rerank if rerank is None else rerank
        pool = max(top_k * self.settings.candidate_multiplier, top_k + 4)
        timings: Dict[str, float] = {}

        chunks = {c.chunk_id: c for c in self.store.all_chunks(allowed_sources)}
        result = RetrievalResult(mode=mode, filters={"allowedSources": sorted(allowed_sources) if allowed_sources else None})

        semantic_hits: List[Tuple[str, float]] = []
        vectors: Dict[str, List[float]] = {}
        if mode in {"semantic", "hybrid"} and chunks:
            started = time.perf_counter()
            vectors = self.store.vectors_for(list(chunks))
            # Stored vectors are produced by an embedder that L2-normalises them,
            # so dot product *is* cosine here. Embedding is verified at ingest.
            scored: List[Tuple[str, float]] = []
            for chunk_id, vector in vectors.items():
                if chunk_id not in chunks:
                    continue
                scored.append((chunk_id, dot(query_vector, vector)))
            scored.sort(key=lambda pair: (-pair[1], pair[0]))
            semantic_hits = scored[:pool]
            timings["semanticMs"] = round((time.perf_counter() - started) * 1000.0, 3)

        lexical_hits: List[Tuple[str, float]] = []
        if mode in {"lexical", "hybrid"}:
            started = time.perf_counter()
            lexical_hits = self.store.lexical_search(query, pool, allowed_sources)
            timings["lexicalMs"] = round((time.perf_counter() - started) * 1000.0, 3)

        result.semantic_candidates = len(semantic_hits)
        result.lexical_candidates = len(lexical_hits)

        started = time.perf_counter()
        if mode == "hybrid":
            fused = self._rrf(semantic_hits, lexical_hits, chunks, top_k=pool)
        elif mode == "semantic":
            fused = [
                Candidate(chunk=chunks[cid], score=score, channel="semantic", semantic_score=score)
                for cid, score in semantic_hits
                if cid in chunks
            ]
        else:
            fused = [
                Candidate(chunk=chunks[cid], score=score, channel="lexical", lexical_score=score)
                for cid, score in lexical_hits
                if cid in chunks
            ]
        timings["fusionMs"] = round((time.perf_counter() - started) * 1000.0, 3)
        result.fused_candidates = len(fused)

        # Support = how well the chunk actually covers the question, computed
        # from the same signals, blended with cosine. Ranking aid, not a truth
        # claim; documented as such in docs/retrieval.md.
        for candidate in fused:
            candidate.support = self.support_score(candidate.chunk, query, query_vector, candidate.semantic_score)

        if rerank and fused:
            started = time.perf_counter()
            fused.sort(key=lambda c: (-(c.support or 0.0), c.chunk.chunk_id))
            for index, candidate in enumerate(fused, start=1):
                candidate.rank = index
                candidate.channel = "reranked"
            timings["rerankMs"] = round((time.perf_counter() - started) * 1000.0, 3)
            result.reranked = True

        if mmr and len(fused) > 1:
            if not vectors:
                vectors = self.store.vectors_for([c.chunk.chunk_id for c in fused])
            # Only the prefix that can still reach the context window needs MMR
            # ordering; ranking the whole candidate pool is wasted work.
            fused = self._mmr(fused, limit=min(len(fused), max(top_k * 2, top_k + 4)), vectors=vectors)

        selected = fused[: max(top_k * 2, top_k)]
        for index, candidate in enumerate(selected, start=1):
            candidate.rank = index
        result.candidates = selected
        result.timings_ms = timings
        return result

    # ----------------------------------------------------------------- helpers
    def _rrf(
        self,
        semantic: Sequence[Tuple[str, float]],
        lexical: Sequence[Tuple[str, float]],
        chunks: Dict[str, StoredChunk],
        *,
        top_k: int,
    ) -> List[Candidate]:
        k = self.settings.rrf_k
        scores: Dict[str, float] = {}
        sem = {cid: score for cid, score in semantic}
        lex = {cid: score for cid, score in lexical}
        for rank, (chunk_id, _) in enumerate(semantic, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
        for rank, (chunk_id, _) in enumerate(lexical, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
        ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:top_k]
        return [
            Candidate(
                chunk=chunks[chunk_id],
                score=score,
                channel="hybrid",
                semantic_score=sem.get(chunk_id),
                lexical_score=lex.get(chunk_id),
                fusion_score=score,
            )
            for chunk_id, score in ordered
            if chunk_id in chunks
        ]

    def _mmr(
        self,
        candidates: Sequence[Candidate],
        *,
        limit: int,
        vectors: Dict[str, List[float]],
    ) -> List[Candidate]:
        """Maximal Marginal Relevance over a bounded candidate pool.

        Pairwise similarities are precomputed once (n <= ~2*top_k, so this is a
        few hundred dot products) instead of being recomputed inside the greedy
        loop — the difference between a ~10 ms and a ~1 ms refinement step.
        """
        pool = list(candidates)
        size = len(pool)
        if size <= 1 or limit <= 0:
            return pool[:limit]

        matrix = [[0.0] * size for _ in range(size)]
        for i in range(size):
            left = vectors.get(pool[i].chunk.chunk_id)
            if left is None:
                continue
            for j in range(i + 1, size):
                right = vectors.get(pool[j].chunk.chunk_id)
                if right is None:
                    continue
                similarity = dot(left, right)
                matrix[i][j] = similarity
                matrix[j][i] = similarity

        lam = self.settings.mmr_lambda
        remaining = set(range(size))
        selected: List[int] = []
        while remaining and len(selected) < limit:
            best_index = -1
            best_value = -1e9
            for index in remaining:
                candidate = pool[index]
                relevance = candidate.support or candidate.score
                redundancy = max((matrix[index][chosen] for chosen in selected), default=0.0)
                value = lam * relevance - (1.0 - lam) * redundancy
                if value > best_value:
                    best_value, best_index = value, index
            if best_index < 0:
                break
            selected.append(best_index)
            remaining.discard(best_index)
        return [pool[index] for index in selected]

    def support_score(
        self,
        chunk: StoredChunk,
        query: str,
        query_vector: Sequence[float],
        semantic_score: Optional[float] = None,
    ) -> float:
        """Retrieval support in [0, 1]: does this chunk address the question?

        Blends query-term coverage (lexical, robust for identifiers) with
        embedding cosine (semantic, when the provider is neural). The cosine
        term is weighted down when the embedder is lexical hashing so the score
        does not double-count words.
        """
        signals = self._signals.get(chunk.chunk_id)
        if signals is None:
            signals = TextSignals(tokenize(chunk.text))
            if len(self._signals) > 20000:  # bound memory on large corpora
                self._signals.clear()
            self._signals[chunk.chunk_id] = signals
        coverage = coverage_score(chunk.text, query, signals=signals)
        phrase = phrase_score(chunk.text, query)
        lexical = 0.75 * coverage + 0.25 * phrase
        if semantic_score is None:
            semantic = 0.0
        else:
            # Cosine for normalised non-negative hashing vectors sits near 0..1;
            # map the useful band to [0, 1] without pretending it is calibrated.
            semantic = max(0.0, min(1.0, (semantic_score + 0.15) / 1.15))
        support = 0.65 * lexical + 0.35 * semantic

        # An identifier the question names exactly (svc-003, MACRO-BILLING-01,
        # p95) is not one quarter of the question: it is the thing being asked
        # about. Without this boost a corpus of near-identical documents ranks
        # by accident — every chunk matches the surrounding words, so the id is
        # the only discriminating signal there is.
        identifiers = {term for term in content_terms(query) if len(term) >= 3 and any(c.isdigit() for c in term)}
        if identifiers:
            exact = len(identifiers & signals.raw) / len(identifiers)
            if exact:
                support = support * (1.0 - 0.45 * exact) + 0.45 * exact
        return round(min(1.0, support), 6)


def coverage_score(
    text: str,
    query: str,
    *,
    signals: Optional[TextSignals] = None,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Weighted fraction of question terms the passage covers (prefix tolerant).

    Without ``weights`` every term counts the same; with them, a rare term
    carries more than a term that appears in every candidate.
    """
    query_terms = set(content_terms(query))
    if not query_terms:
        return 0.0
    signals = signals or TextSignals(tokenize(text))
    matched = matched_terms_signals(query_terms, signals)
    if not weights:
        return len(matched) / len(query_terms)
    total = sum(weights.get(term, 1.0) for term in query_terms)
    if total <= 0:
        return 0.0
    return sum(weights.get(term, 1.0) for term in matched) / total


def phrase_score(text: str, query: str) -> float:
    tokens = [t for t in content_terms(query) if t not in STOPWORDS]
    if len(tokens) < 2:
        return 0.0
    phrases = [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
    joined = " ".join(tokenize(text))
    return sum(1 for phrase in phrases if phrase in joined) / len(phrases)


def dedupe_overlaps(candidates: Iterable[Candidate], threshold: float = 0.92) -> List[Candidate]:
    """Remove passages whose token sets almost fully overlap an earlier one."""
    kept: List[Candidate] = []
    kept_tokens: List[set] = []
    for candidate in candidates:
        tokens = set(tokenize(candidate.chunk.text))
        if not tokens:
            continue
        duplicate = False
        for existing in kept_tokens:
            union = len(tokens | existing)
            if union and len(tokens & existing) / union >= threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
            kept_tokens.append(tokens)
    return kept


def assemble_context(
    candidates: Sequence[Candidate], token_budget: int
) -> Tuple[List[Candidate], int, List[Dict[str, Any]]]:
    """Fill a hard token budget, preferring support order.

    Returns (selected, used_tokens, skipped) where ``skipped`` records *why* a
    candidate was dropped, so the debug view never has to guess.
    """
    selected: List[Candidate] = []
    used = 0
    skipped: List[Dict[str, Any]] = []
    for candidate in candidates:
        cost = candidate.chunk.token_count
        if used + cost > token_budget:
            skipped.append(
                {
                    "chunkId": candidate.chunk.chunk_id,
                    "reason": "context_token_budget",
                    "tokenCount": cost,
                }
            )
            continue
        selected.append(candidate)
        used += cost
    return selected, used, skipped
