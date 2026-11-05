"""Deterministic, dependency-free RAG engine used by the RAG Experience walkthrough.

Design goals
------------
* **Real, not faked.** Chunking, embedding, indexing, hybrid retrieval,
  reranking, context assembly, citation validation and abstention are all
  genuinely computed. Nothing in the trace is a random number.
* **Deterministic.** The embedding backend is a hashed bag-of-ngrams
  projection (``HashEmbedder``) so the same corpus always yields the same
  vectors, scores and chunk ids. This makes the demo repeatable and makes the
  golden evaluation set meaningful.
* **Instrumented.** Every stage emits ``TraceEvent`` records matching the
  versioned visual contract in ``contracts.py`` so any frontend can consume a
  truthful trace.
* **Swappable.** ``Embedder`` and ``Reranker`` are protocols. A real
  provider can be substituted without touching retrieval logic.
"""

from .contracts import RagExperienceEvent, RagStage, SCHEMA_VERSION
from .chunking import Chunk, chunk_document, ChunkingConfig
from .embedding import HashEmbedder, Embedder
from .index import VectorIndex, SearchHit
from .pipeline import RagEngine, Answer, Claim, Citation, answer_to_dict
from .ingest import Document, SourceRegistry, load_corpus
from .security import scan_for_injection

__all__ = [
    "RagExperienceEvent",
    "RagStage",
    "SCHEMA_VERSION",
    "Chunk",
    "chunk_document",
    "ChunkingConfig",
    "HashEmbedder",
    "Embedder",
    "VectorIndex",
    "SearchHit",
    "RagEngine",
    "Answer",
    "Claim",
    "Citation",
    "answer_to_dict",
    "Document",
    "SourceRegistry",
    "load_corpus",
    "scan_for_injection",
]

__version__ = "1.0.0"
