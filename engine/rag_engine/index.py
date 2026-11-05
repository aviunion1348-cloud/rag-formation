"""Hybrid retrieval index: cosine semantic search + BM25 lexical search + RRF.

All three are genuinely computed. ``hybrid`` fuses the two ranked lists with
Reciprocal Rank Fusion (k=60), which needs no score calibration — important
because cosine similarity here is *not* a probability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .chunking import Chunk, tokenize
from .embedding import Embedder, cosine


@dataclass
class SearchHit:
    chunk_id: str
    score: float
    rank: int
    channel: str  # "semantic" | "lexical" | "hybrid"
    chunk: Optional[Chunk] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "chunkId": self.chunk_id,
            "score": round(self.score, 6),
            "rank": self.rank,
            "channel": self.channel,
        }


class VectorIndex:
    """Flat index. Brute-force cosine is exact and fast for a demo corpus."""

    def __init__(self, embedder: Embedder, k1: float = 1.5, b: float = 0.75) -> None:
        self.embedder = embedder
        self.k1 = k1
        self.b = b
        self._chunks: Dict[str, Chunk] = {}
        self._vectors: Dict[str, List[float]] = {}
        self._df: Dict[str, int] = {}
        self._postings: Dict[str, Dict[str, int]] = {}
        self._lengths: Dict[str, int] = {}
        self._total_len = 0

    # ------------------------------------------------------------------ build
    def add(self, chunk: Chunk) -> None:
        self._chunks[chunk.chunk_id] = chunk
        self._vectors[chunk.chunk_id] = self.embedder.embed(chunk.text)
        toks = tokenize(chunk.text)
        self._lengths[chunk.chunk_id] = len(toks)
        self._total_len += len(toks)
        seen = set()
        for tok in toks:
            post = self._postings.setdefault(tok, {})
            post[chunk.chunk_id] = post.get(chunk.chunk_id, 0) + 1
            if tok not in seen:
                self._df[tok] = self._df.get(tok, 0) + 1
                seen.add(tok)

    def remove_source(self, source_id: str) -> int:
        ids = [cid for cid, c in self._chunks.items() if c.source_id == source_id]
        for cid in ids:
            self._drop(cid)
        return len(ids)

    def _drop(self, chunk_id: str) -> None:
        chunk = self._chunks.pop(chunk_id, None)
        if chunk is None:
            return
        self._vectors.pop(chunk_id, None)
        length = self._lengths.pop(chunk_id, 0)
        self._total_len -= length
        for tok, post in list(self._postings.items()):
            if chunk_id in post:
                del post[chunk_id]
                self._df[tok] = max(0, self._df.get(tok, 1) - 1)
                if not post:
                    del self._postings[tok]

    # ----------------------------------------------------------------- search
    @property
    def size(self) -> int:
        return len(self._chunks)

    def chunk(self, chunk_id: str) -> Optional[Chunk]:
        return self._chunks.get(chunk_id)

    def chunks(self) -> List[Chunk]:
        return sorted(self._chunks.values(), key=lambda c: c.chunk_id)

    def _allowed(self, chunk: Chunk, allowed_sources: Optional[set]) -> bool:
        return allowed_sources is None or chunk.source_id in allowed_sources

    def semantic_search(
        self,
        query_vector: Sequence[float],
        top_k: int = 8,
        allowed_sources: Optional[set] = None,
    ) -> List[SearchHit]:
        scored: List[Tuple[float, str]] = []
        for cid, vec in self._vectors.items():
            chunk = self._chunks[cid]
            if not self._allowed(chunk, allowed_sources):
                continue
            scored.append((cosine(query_vector, vec), cid))
        scored.sort(key=lambda p: (-p[0], p[1]))
        return [
            SearchHit(chunk_id=cid, score=s, rank=i + 1, channel="semantic", chunk=self._chunks[cid])
            for i, (s, cid) in enumerate(scored[:top_k])
        ]

    def _idf(self, term: str) -> float:
        n = max(1, len(self._chunks))
        df = self._df.get(term, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def lexical_search(
        self,
        query: str,
        top_k: int = 8,
        allowed_sources: Optional[set] = None,
    ) -> List[SearchHit]:
        terms = tokenize(query)
        if not terms or not self._chunks:
            return []
        avgdl = self._total_len / max(1, len(self._chunks))
        scores: Dict[str, float] = {}
        for term in terms:
            for cid, tf in self._postings.get(term, {}).items():
                chunk = self._chunks[cid]
                if not self._allowed(chunk, allowed_sources):
                    continue
                dl = self._lengths.get(cid, 0) or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / max(avgdl, 1e-9))
                scores[cid] = scores.get(cid, 0.0) + self._idf(term) * (tf * (self.k1 + 1)) / denom
        ranked = sorted(scores.items(), key=lambda p: (-p[1], p[0]))[:top_k]
        return [
            SearchHit(chunk_id=cid, score=s, rank=i + 1, channel="lexical", chunk=self._chunks[cid])
            for i, (cid, s) in enumerate(ranked)
        ]

    @staticmethod
    def fuse_rrf(
        semantic: Sequence[SearchHit],
        lexical: Sequence[SearchHit],
        k: int = 60,
        top_k: int = 8,
    ) -> List[SearchHit]:
        """Reciprocal Rank Fusion over the two channels."""
        scores: Dict[str, float] = {}
        by_id: Dict[str, Chunk] = {}
        for hits, weight in ((semantic, 1.0), (lexical, 1.0)):
            for hit in hits:
                scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + weight / (k + hit.rank)
                if hit.chunk is not None:
                    by_id[hit.chunk_id] = hit.chunk
        ranked = sorted(scores.items(), key=lambda p: (-p[1], p[0]))[:top_k]
        return [
            SearchHit(chunk_id=cid, score=s, rank=i + 1, channel="hybrid", chunk=by_id.get(cid))
            for i, (cid, s) in enumerate(ranked)
        ]

    def mmr(
        self,
        hits: Sequence[SearchHit],
        query_vector: Sequence[float],
        top_k: int,
        lambda_: float = 0.72,
    ) -> List[SearchHit]:
        """Maximal Marginal Relevance — reduces near-duplicate context."""
        selected: List[SearchHit] = []
        pool = list(hits)
        while pool and len(selected) < top_k:
            best = None
            best_score = -1e9
            for cand in pool:
                vec = self._vectors.get(cand.chunk_id)
                if vec is None:
                    continue
                relevance = cosine(query_vector, vec)
                redundancy = max(
                    (cosine(vec, self._vectors[s.chunk_id]) for s in selected), default=0.0
                )
                score = lambda_ * relevance - (1 - lambda_) * redundancy
                if score > best_score:
                    best_score, best = score, cand
            if best is None:
                break
            selected.append(best)
            pool.remove(best)
        return [
            SearchHit(
                chunk_id=h.chunk_id, score=h.score, rank=i + 1, channel="mmr", chunk=h.chunk
            )
            for i, h in enumerate(selected)
        ]
