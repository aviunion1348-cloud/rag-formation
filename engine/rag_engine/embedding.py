"""Embedding backend.

``HashEmbedder`` is a *real* embedding function: it maps text to a fixed
dimensional, L2-normalised vector using signed feature hashing over unigrams and
bigrams. It is deterministic, needs no network or credentials, and is good
enough for genuine semantic-ish retrieval on a small corpus.

It is explicitly **not** a transformer embedding model. The UI labels it as
``hash-ngram-v1`` everywhere so nobody mistakes the demo for a neural encoder.
Swap in any provider by implementing the ``Embedder`` protocol.
"""

from __future__ import annotations

import hashlib
import math
from typing import Dict, List, Protocol, Sequence

from .chunking import tokenize


class Embedder(Protocol):
    model_id: str
    dimension: int

    def embed(self, text: str) -> List[float]: ...

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]: ...


def _bucket(feature: str, dimension: int) -> int:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest[:4], "big") % dimension


def _sign(feature: str) -> float:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return 1.0 if digest[4] & 1 else -1.0


class HashEmbedder:
    """Signed feature hashing over unigrams + bigrams, sublinear TF, L2 norm."""

    model_id = "hash-ngram-v1"

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def _features(self, text: str) -> Dict[str, float]:
        toks = tokenize(text)
        counts: Dict[str, float] = {}
        for tok in toks:
            counts[tok] = counts.get(tok, 0.0) + 1.0
        for a, b in zip(toks, toks[1:]):
            bigram = f"{a}_{b}"
            counts[bigram] = counts.get(bigram, 0.0) + 1.0
        return counts

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dimension
        for feature, count in self._features(text).items():
            idx = _bucket(feature, self.dimension)
            vec[idx] += _sign(feature) * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    # Vectors are already L2-normalised, so this is a dot product.
    return sum(x * y for x, y in zip(a, b))
