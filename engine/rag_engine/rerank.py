"""Second-stage reranking and the grounding score.

Honesty notes, stated plainly because they matter:

* This is **not** a cross-encoder reranker. It is a transparent, inspectable
  feature blend labelled ``heuristic-coverage-v1`` in the UI and the trace.
* The grounding/relevance score combines a real cosine similarity from the
  embedding backend with query-term coverage over *content* terms (stopwords
  removed). It is an internal ranking score, **not** a calibrated probability.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .chunking import Chunk, tokenize
from .index import SearchHit

RERANKER_ID = "heuristic-coverage-v1"

#: Function words carry no retrieval signal and inflate coverage for short questions.
STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those of for to in on at by with from
    is are was were be been being do does did done have has had having it its their there
    we you they he she i me my our your us them as not no nor so such very can could should
    would may might must will shall about into over under between during after before while
    what when where which who whom whose how why long many much more most other some any
    please tell explain describe give show summarise summarize summarization requirements
    """.split()
)


def content_terms(text: str) -> List[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


def _phrases(query: str) -> List[str]:
    toks = content_terms(query)
    return [f"{a} {b}" for a, b in zip(toks, toks[1:])]


def coverage_score(chunk: Chunk, query: str) -> float:
    """Content-term coverage in [0, 1] — the lexical half of the relevance score."""
    q_terms = set(content_terms(query))
    if not q_terms:
        return 0.0
    c_terms = set(content_terms(chunk.text))
    return len(q_terms & c_terms) / len(q_terms)


def phrase_score(chunk: Chunk, query: str) -> float:
    phrases = _phrases(query)
    if not phrases:
        return 0.0
    text = " ".join(content_terms(chunk.text))
    return sum(1 for p in phrases if p in text) / len(phrases)


def relevance_score(
    chunk: Chunk, query: str, query_vector: Optional[Sequence[float]] = None, embedder=None
) -> float:
    """Blended grounding/relevance score in [0, 1]. Ranking signal, not probability."""
    cov = coverage_score(chunk, query)
    phr = phrase_score(chunk, query)
    lex = 0.72 * cov + 0.28 * phr
    if query_vector is not None and embedder is not None:
        from .embedding import cosine

        sem = max(0.0, cosine(query_vector, embedder.embed(chunk.text)))
        return round(0.45 * sem + 0.55 * lex, 6)
    return round(lex, 6)


def rerank(
    hits: Sequence[SearchHit],
    query: str,
    query_vector: Optional[Sequence[float]] = None,
    embedder=None,
) -> List[SearchHit]:
    scored = []
    for hit in hits:
        if hit.chunk is None:
            continue
        scored.append((relevance_score(hit.chunk, query, query_vector, embedder), hit))
    scored.sort(key=lambda p: (-p[0], p[1].chunk_id))
    return [
        SearchHit(chunk_id=hit.chunk_id, score=score, rank=i + 1, channel="reranked", chunk=hit.chunk)
        for i, (score, hit) in enumerate(scored)
    ]
