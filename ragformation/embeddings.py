"""Embedding providers.

Three honest options, selected by ``RAG_EMBED_PROVIDER``:

``ollama``   Local neural embeddings (default model ``nomic-embed-text``). This
             is the whiteboard path: nothing leaves the machine.
``openai``   Any OpenAI-compatible ``/embeddings`` endpoint (cloud or a local
             proxy such as vLLM / LM Studio / Ollama's OpenAI shim).
``spacy``    A real, pretrained static embedding model (``en_core_web_md`` or
             any spaCy model with vectors). Requires the optional ``semantic``
             extra. Genuinely distributional, but static (no context): it is not
             a transformer encoder and the docs say so.

``distributional``
             Corpus-trained count-based semantics (PPMI co-occurrence +
             truncated SVD). Needs numpy. It learns meaning *from your corpus*,
             so it says nothing about words your corpus never uses.

``hashing``  A deterministic signed feature-hash projection over words and
             bigrams. **It is not a neural encoder and it is not semantic
             search.** It exists so the whole pipeline runs with zero network,
             zero credentials and zero downloads, and the system labels it
             ``semantic: false`` everywhere (health, CLI, UI, docs).

``auto`` probes Ollama with a short timeout and falls back to ``hashing``,
announcing which one it chose and why.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence

from .config import Settings
from .errors import ConfigError, ProviderError, ProviderUnavailable
from .store import Store
from .text import text_hash, tokenize


class EmbeddingProvider(Protocol):
    name: str
    model: str
    semantic: bool

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...

    def health(self) -> Dict[str, Any]: ...


# --------------------------------------------------------------------- helpers
def _post_json(url: str, payload: Dict[str, Any], timeout: float, api_key: Optional[str] = None) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:  # server answered with an error
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # pragma: no cover
            pass
        raise ProviderError(
            f"provider returned HTTP {exc.code}", hint=detail or exc.reason
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderUnavailable(
            "provider is not reachable",
            hint=f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}",
        ) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("provider returned a non-JSON body") from exc


def _get_json(url: str, timeout: float, api_key: Optional[str] = None) -> Dict[str, Any]:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"provider returned HTTP {exc.code}", hint=str(exc.reason)) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ProviderUnavailable("provider is not reachable", hint=str(exc)) from exc


def _normalize(vector: Sequence[float]) -> List[float]:
    values = [float(v) for v in vector]
    if any(math.isnan(v) or math.isinf(v) for v in values):
        raise ProviderError("provider returned a non-finite vector")
    norm = math.sqrt(math.fsum(v * v for v in values))
    if norm == 0.0:
        return values
    return [v / norm for v in values]


def _retry(call, *, attempts: int, base_delay: float = 0.4):
    """Bounded retries with jitter. Never retries a 4xx client error."""
    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return call()
        except ProviderUnavailable as exc:
            last = exc
        except ProviderError:
            raise
        if attempt + 1 < attempts:
            time.sleep(base_delay * (2**attempt) + random.uniform(0, 0.15))
    raise last  # type: ignore[misc]


# ------------------------------------------------------------------- providers
@dataclass
class OllamaEmbedder:
    model: str = "nomic-embed-text"
    host: str = "http://127.0.0.1:11434"
    timeout: float = 30.0
    retries: int = 2
    name: str = "ollama"
    semantic: bool = True
    _dimension: Optional[int] = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            probe = self.embed(["dimension probe"])
            self._dimension = len(probe[0])
        return self._dimension

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        cleaned = [t if t.strip() else " " for t in texts]
        if not cleaned:
            return []

        def call() -> List[List[float]]:
            payload = {"model": self.model, "input": cleaned, "truncate": True}
            response = _post_json(f"{self.host.rstrip('/')}/api/embed", payload, self.timeout)
            embeddings = response.get("embeddings")
            if not embeddings and "embedding" in response:  # legacy single-input API
                embeddings = [response["embedding"]]
            if not isinstance(embeddings, list) or len(embeddings) != len(cleaned):
                raise ProviderError(
                    "ollama returned an unexpected embeddings payload",
                    hint=f"expected {len(cleaned)} vectors",
                )
            return [_normalize(vec) for vec in embeddings]

        vectors = _retry(call, attempts=self.retries + 1)
        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
        return vectors

    def health(self) -> Dict[str, Any]:
        try:
            tags = _get_json(f"{self.host.rstrip('/')}/api/tags", timeout=3.0)
        except (ProviderError, ProviderUnavailable) as exc:
            return {"ok": False, "provider": self.name, "detail": str(exc)}
        models = [m.get("name", "") for m in tags.get("models", [])]
        present = any(m.split(":")[0] == self.model.split(":")[0] for m in models)
        return {
            "ok": present,
            "provider": self.name,
            "model": self.model,
            "modelInstalled": present,
            "availableModels": models[:20],
            "hint": None if present else f"run: ollama pull {self.model}",
        }


@dataclass
class OpenAIEmbedder:
    model: str = "text-embedding-3-small"
    base_url: str = "https://api.openai.com/v1"
    api_key: Optional[str] = None
    timeout: float = 30.0
    retries: int = 2
    name: str = "openai"
    semantic: bool = True
    _dimension: Optional[int] = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = len(self.embed(["dimension probe"])[0])
        return self._dimension

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        cleaned = [t if t.strip() else " " for t in texts]
        if not cleaned:
            return []
        if not self.api_key:
            raise ProviderUnavailable(
                "OPENAI_API_KEY is not set",
                hint="export OPENAI_API_KEY=... or use RAG_EMBED_PROVIDER=hashing",
            )

        def call() -> List[List[float]]:
            payload = {"model": self.model, "input": cleaned}
            data = _post_json(
                f"{self.base_url.rstrip('/')}/embeddings", payload, self.timeout, self.api_key
            )
            items = data.get("data") or []
            if len(items) != len(cleaned):
                raise ProviderError("provider returned an unexpected number of embeddings")
            ordered = sorted(items, key=lambda item: item.get("index", 0))
            return [_normalize(item["embedding"]) for item in ordered]

        vectors = _retry(call, attempts=self.retries + 1)
        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
        return vectors

    def health(self) -> Dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "provider": self.name, "detail": "OPENAI_API_KEY not set"}
        try:
            _get_json(f"{self.base_url.rstrip('/')}/models", timeout=5.0, api_key=self.api_key)
        except (ProviderError, ProviderUnavailable) as exc:
            return {"ok": False, "provider": self.name, "detail": str(exc)[:200]}
        return {"ok": True, "provider": self.name, "model": self.model}


@dataclass
class HashingEmbedder:
    """Deterministic feature hashing. Lexical, not semantic — by construction."""

    dimension: int = 512
    name: str = "hashing"
    model: str = "hashing-lexical-v1"
    semantic: bool = False

    def _features(self, text: str) -> Dict[str, float]:
        tokens = tokenize(text)
        counts: Dict[str, float] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0.0) + 1.0
        for left, right in zip(tokens, tokens[1:]):
            bigram = f"{left}_{right}"
            counts[bigram] = counts.get(bigram, 0.0) + 1.0
        return counts

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for feature, count in self._features(text).items():
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[index] += sign * (1.0 + math.log(count))
            vectors.append(_normalize(vector))
        return vectors

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "provider": self.name,
            "model": self.model,
            "semantic": False,
            "detail": "deterministic lexical hashing; no model download, no network",
        }


# --------------------------------------------------------------------- caching
class CachedEmbedder:
    """Content-addressed embedding cache.

    Key = sha256(normalised text) + model identity, so changing the model can
    never serve a stale vector, and re-ingesting an unchanged document costs
    zero provider calls (measured by ``doctor``/``ingest`` reports).
    """

    def __init__(self, provider: EmbeddingProvider, store: Store) -> None:
        self.provider = provider
        self.store = store
        self.cache_hits = 0
        self.cache_misses = 0

    @property
    def name(self) -> str:
        return getattr(self.provider, "name", "unknown")

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def semantic(self) -> bool:
        return bool(getattr(self.provider, "semantic", False))

    @property
    def dimension(self) -> int:
        return self.provider.dimension

    @property
    def preprocessing(self) -> str:
        return getattr(self.provider, "preprocessing", "lower-token-v1")

    @property
    def fingerprint(self) -> str:
        """Complete index identity.

        Any change to provider, model, dimension, normalisation, similarity
        metric or preprocessing produces a different fingerprint, which the
        store refuses to mix with existing vectors.
        """

        component = ""
        hook = getattr(self.provider, "fingerprint_component", None)
        if hook is not None:
            try:
                piece = hook()
            except Exception:  # pragma: no cover - a provider must not break hashing
                piece = ""
            if piece:
                component = f":{piece}"
        return (
            f"{self.name}:{self.model}:d{self.dimension}"
            f":norm=l2:metric=cosine:pre={self.preprocessing}:fpv=2{component}"
        )

    # ------------------------------------------------------------ stateful providers
    def prepare(self, texts: Sequence[str] = ()) -> Dict[str, Any]:
        """Let corpus-trained providers fit or load their model.

        Called by the engine before embedding and before answering. A provider
        without a corpus stage (ollama/openai/hashing/spacy) is a no-op, which
        keeps the ingest path identical for every provider.
        """

        fitter = getattr(self.provider, "fit_from_corpus", None)
        if fitter is None:
            return {}
        key = getattr(self.provider, "state_key", lambda: f"{self.name}:{self.model}")()
        stored = self.store.get_semantic_model(key)
        if stored is not None:
            self.provider.load_state(stored)  # type: ignore[attr-defined]
            return {"state": "loaded", "key": key, "terms": len(stored.get("terms") or {})}
        payload = fitter(list(texts), self.store)
        if payload:
            self.store.put_semantic_model(key, payload)
        return {"state": "fitted", "key": key, "terms": len(payload.get("terms") or {})}

    def reset_state(self) -> None:
        """Forget a fitted corpus model so the next ``prepare`` refits it."""

        if hasattr(self.provider, "load_state"):
            self.store.delete_semantic_model(
                getattr(self.provider, "state_key", lambda: f"{self.name}:{self.model}")()
            )
            self.provider.load_state({})  # type: ignore[attr-defined]

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        keyed: List[tuple[str, str]] = [
            (text_hash(text), text if text.strip() else " ") for text in texts
        ]
        cache_model = self.fingerprint
        results: List[Optional[List[float]]] = [None] * len(keyed)
        pending: List[int] = []
        for index, (key, _) in enumerate(keyed):
            cached = self.store.cache_get(key, cache_model)
            if cached is not None:
                results[index] = cached
                self.cache_hits += 1
            else:
                pending.append(index)
        if pending:
            fresh = self.provider.embed([keyed[i][1] for i in pending])
            for slot, vector in zip(pending, fresh):
                results[slot] = vector
                self.store.cache_put(keyed[slot][0], cache_model, len(vector), vector)
                self.cache_misses += 1
        return [vector or [] for vector in results]

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]

    def health(self) -> Dict[str, Any]:
        return self.provider.health()

    def cache_stats(self) -> Dict[str, int]:
        return {"hits": self.cache_hits, "misses": self.cache_misses}




# --------------------------------------------------------------------------- spacy
@dataclass
class SpacyEmbedder:
    """Pretrained static word vectors from a spaCy model (e.g. ``en_core_web_md``).

    This is a *real* semantic embedding: the vectors come from a trained model
    (GloVe-style static vectors), so cosine similarity tracks relatedness rather
    than shared spelling. It is **static**, not contextual — "bank" has one
    vector — and the docs say so. Requires the optional ``semantic`` extra and a
    model with vectors (``python -m spacy download en_core_web_md``).
    """

    model: str = "en_core_web_md"
    name: str = "spacy"
    semantic: bool = True
    preprocessing: str = "spacy-token-mean-v1"
    _nlp: Any = None
    _dimension: Optional[int] = None
    _missing_terms: int = 0
    _vectors_seen: int = 0

    # ------------------------------------------------------------- lifecycle
    def _load(self) -> Any:
        if self._nlp is not None:
            return self._nlp
        try:
            import spacy  # noqa: F401  (optional dependency)
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ProviderUnavailable(
                "the spaCy embedding provider is not installed",
                hint='install the extra: pip install "ragformation[semantic]"',
            ) from exc
        try:
            nlp = spacy.load(self.model)
        except Exception as exc:  # OSError/ImportError from spaCy
            raise ProviderUnavailable(
                f"spaCy model {self.model!r} is not installed",
                hint=f"python -m spacy download {self.model}",
            ) from exc
        shape = getattr(getattr(nlp.vocab, "vectors", None), "shape", None)
        if not shape or len(shape) < 2 or int(shape[1]) == 0:
            raise ProviderError(
                f"spaCy model {self.model!r} has no word vectors",
                hint="use a model with vectors, e.g. en_core_web_md or en_core_web_lg",
            )
        self._nlp = nlp
        self._dimension = int(shape[1])
        return nlp

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._load()
        if self._dimension is None:
            raise ProviderError("spaCy model exposes no vector dimension")
        return self._dimension

    # --------------------------------------------------------------- embed
    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        nlp = self._load()
        out: List[List[float]] = []
        for text in texts:
            doc = nlp.make_doc(text or " ")
            token_vectors = [t.vector for t in doc if t.has_vector]
            self._vectors_seen += len(token_vectors)
            if not token_vectors:
                self._missing_terms += 1
                out.append([0.0] * self.dimension)
                continue
            total = [0.0] * len(token_vectors[0])
            for vector in token_vectors:
                for index, value in enumerate(vector):
                    total[index] += float(value)
            count = float(len(token_vectors))
            out.append(_normalize([value / count for value in total]))
        return out

    def health(self) -> Dict[str, Any]:
        try:
            import spacy  # noqa: F401
        except ImportError:
            return {
                "ok": False,
                "provider": self.name,
                "model": self.model,
                "detail": "spaCy is not installed",
                "hint": 'pip install "ragformation[semantic]"',
            }
        try:
            self._load()
        except (ProviderError, ProviderUnavailable) as exc:
            return {
                "ok": False,
                "provider": self.name,
                "model": self.model,
                "detail": exc.message,
                "hint": exc.hint,
            }
        return {
            "ok": True,
            "provider": self.name,
            "model": self.model,
            "dimension": self.dimension,
            "semantic": True,
            "vectorsSeen": self._vectors_seen,
            "zeroVectorTexts": self._missing_terms,
            "note": "static pretrained word vectors, mean-pooled; not contextual",
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "vectorsSeen": self._vectors_seen,
            "zeroVectorTexts": self._missing_terms,
            "normalisation": "l2",
        }


# ------------------------------------------------------------------ distributional
@dataclass
class DistributionalEmbedder:
    """Corpus-trained semantic embeddings: TF-IDF + truncated SVD (LSA).

    Why this exists: a hash projection is lexical by construction, and no
    pretrained model could be downloaded in the build environment. This provider
    learns a genuine distributional space *from the indexed corpus* — verbs that
    share contexts collapse together, which lexical matching cannot do.

    Honest limits (also in ``docs/semantic_embeddings.md``):

    * It only knows words that appear in the corpus; unseen vocabulary becomes a
      zero vector (lexical BM25 still covers those queries).
    * The space is fitted once per index and persisted, so incremental ingests
      reuse it. Re-fitting (``rebuild``) creates a new fingerprint — vectors and
      model are always replaced together, never mixed.
    * Quality depends on corpus size. On a 20-chunk corpus it is a demonstration,
      not a competitive retriever.
    """

    projection_dim: int = 192
    window: int = 6
    min_count: int = 1
    max_vocab: int = 4000
    model: str = "lsa-tfidf-v1"
    name: str = "distributional"
    semantic: bool = True
    preprocessing: str = "tfidf-lsa-v1"
    _terms: Dict[str, List[float]] = field(default_factory=dict)
    _fitted_on: str = ""
    _docs: int = 0
    _rank: Optional[int] = None
    _fit_id: str = ""
    _idf: Dict[str, float] = field(default_factory=dict)

    # -------------------------------------------------------------- identity
    def state_key(self) -> str:
        return f"{self.name}:{self.model}:d{self.projection_dim}:w{self.window}"

    def _require_numpy(self) -> Any:
        try:
            import numpy  # noqa: F401
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ProviderUnavailable(
                "the distributional embedding provider needs numpy",
                hint='install the extra: pip install "ragformation[semantic]"',
            ) from exc
        import numpy as np

        return np

    @property
    def dimension(self) -> int:
        """Vector length. Always ``projection_dim``: unused SVD directions are zero-padded.

        A stable dimension keeps the index fingerprint stable across incremental
        ingests; the *fit* (basis) identity is carried separately by
        :meth:`fingerprint_component`.
        """

        return int(self.projection_dim)

    def fingerprint_component(self) -> str:
        return f"fit={self._fit_id or 'unfitted'}"



    # --------------------------------------------------------------- fitting
    def fit_from_corpus(self, texts: Sequence[str], store: Any = None) -> Dict[str, Any]:
        """Fit term vectors from the corpus texts and return a serialisable state."""

        np = self._require_numpy()
        from .text import tokenize

        tokenised = [tokenize(text) for text in texts or ()]
        tokenised = [tokens for tokens in tokenised if tokens]
        self._docs = len(tokenised)
        if not tokenised:
            self._terms = {}
            self._rank = None
            return {"terms": {}, "docs": 0, "window": self.window, "dimension": self.projection_dim}

        counts: Dict[str, int] = {}
        for tokens in tokenised:
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
        vocab = [
            term
            for term, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            if count >= self.min_count and len(term) > 1
        ][: self.max_vocab]
        index = {term: i for i, term in enumerate(vocab)}
        size = len(vocab)
        if size == 0:
            self._terms = {}
            return {"terms": {}, "docs": self._docs, "window": self.window, "dimension": self.projection_dim}

        # Latent Semantic Analysis over a term x document matrix of sublinear
        # TF-IDF weights. Documents are the corpus chunks themselves, which is
        # what retrieval compares against, so a query's averaged term vector lands
        # close to documents that use those terms in the same contexts.
        import math as _math

        docs = len(tokenised)
        term_doc = np.zeros((size, docs), dtype=np.float64)
        for column, tokens in enumerate(tokenised):
            seen: Dict[int, int] = {}
            for token in tokens:
                row = index.get(token)
                if row is not None:
                    seen[row] = seen.get(row, 0) + 1
            for row, tf in seen.items():
                term_doc[row, column] = 1.0 + _math.log(float(tf))
        df = np.count_nonzero(term_doc, axis=1)
        idf = np.log((1.0 + docs) / (1.0 + df)) + 1.0
        weighted = term_doc * idf[:, None]
        norms = np.linalg.norm(weighted, axis=1, keepdims=True)
        weighted = np.divide(weighted, norms, out=np.zeros_like(weighted), where=norms > 0)

        rank = int(min(self.projection_dim, max(1, min(size, docs) - 1)))
        try:
            u, s, _vt = np.linalg.svd(weighted, full_matrices=False)
            dense = u[:, :rank] * np.sqrt(np.maximum(s[:rank], 0.0))
        except np.linalg.LinAlgError:  # pragma: no cover - pathological matrices
            dense = np.random.default_rng(0).normal(size=(size, rank))

        if rank < self.projection_dim:
            padded = np.zeros((size, self.projection_dim), dtype=np.float64)
            padded[:, :rank] = dense
            dense = padded

        # Unit-normalise each term vector so averaging over a query is not
        # dominated by high-energy directions.
        row_norms = np.linalg.norm(dense, axis=1, keepdims=True)
        dense = np.divide(dense, row_norms, out=np.zeros_like(dense), where=row_norms > 0)

        self._terms = {
            term: [round(float(value), 6) for value in dense[i]]
            for term, i in index.items()
        }
        self._idf = {term: float(idf[i]) for term, i in index.items()}
        self._rank = rank
        from .text import text_hash

        digest = hashlib.sha256()
        for text in sorted(text_hash(t) for t in (texts or ())):
            digest.update(text.encode("utf-8"))
        self._fit_id = digest.hexdigest()[:12]
        return {
            "terms": self._terms,
            "docs": self._docs,
            "window": self.window,
            "dimension": self.dimension,
            "rank": rank,
            "idf": {term: round(value, 4) for term, value in self._idf.items()},
            "fitId": self._fit_id,
            "vocab": size,
            "fittedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        }

    # ----------------------------------------------------------- state i/o
    def load_state(self, payload: Dict[str, Any]) -> None:
        terms = payload.get("terms") or {}
        self._terms = {str(k): [float(x) for x in v] for k, v in terms.items() if v}
        self._idf = {str(k): float(v) for k, v in (payload.get("idf") or {}).items()}
        self._rank = int(payload.get("rank") or 0) or None
        self._fit_id = str(payload.get("fitId") or "")
        self._docs = int(payload.get("docs") or 0)

    def state(self) -> Dict[str, Any]:
        return {
            "terms": self._terms,
            "docs": self._docs,
            "window": self.window,
            "dimension": self.dimension,
            "rank": self._rank,
            "idf": self._idf,
            "fitId": self._fit_id,
        }

    # --------------------------------------------------------------- embed
    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        from .text import tokenize

        if not self._terms:
            raise ProviderUnavailable(
                "the distributional model is not fitted yet",
                hint="run: ragformation rebuild --force",
            )
        dim = len(next(iter(self._terms.values())))
        out: List[List[float]] = []
        for text in texts:
            total = [0.0] * dim
            used = 0
            # IDF weighting: rare query terms (the ones that carry meaning) must
            # not be drowned out by "how long do we keep"-style filler.
            for token in tokenize(text or ""):
                vector = self._terms.get(token)
                if vector is None:
                    continue
                weight = self._idf.get(token, 1.0)
                for i, value in enumerate(vector):
                    total[i] += value * weight
                used += 1
            out.append(_normalize([value / used for value in total]) if used else [0.0] * dim)
        return out

    def health(self) -> Dict[str, Any]:
        try:
            import numpy  # noqa: F401
        except ImportError:
            return {
                "ok": False,
                "provider": self.name,
                "model": self.model,
                "detail": "numpy is not installed",
                "hint": 'pip install "ragformation[semantic]"',
            }
        return {
            "ok": bool(self._terms),
            "provider": self.name,
            "model": self.model,
            "dimension": self.dimension,
            "vocab": len(self._terms),
            "docs": self._docs,
            "semantic": True,
            "note": "corpus-trained PPMI+SVD; knows only words present in the corpus",
            "hint": None if self._terms else "run: ragformation rebuild --force",
        }

    def stats(self) -> Dict[str, Any]:
        return {"vocab": len(self._terms), "docs": self._docs}


# ------------------------------------------------------------------- factories
def build_embedder(settings: Settings, store: Store) -> CachedEmbedder:
    provider_name = settings.embed_provider
    provider: Optional[EmbeddingProvider] = None

    if provider_name in {"auto", "ollama"}:
        candidate = OllamaEmbedder(
            model=settings.embed_model,
            host=settings.ollama_host,
            timeout=settings.request_timeout_s,
            retries=settings.max_retries,
        )
        health = candidate.health()
        if health.get("ok"):
            provider = candidate
        elif provider_name == "ollama":
            raise ProviderUnavailable(
                "Ollama is configured as the embedding provider but is not usable",
                hint=str(health.get("hint") or health.get("detail") or "start Ollama and pull the model"),
            )

    if provider is None and provider_name in {"auto", "openai"}:
        import os

        api_key = os.environ.get("OPENAI_API_KEY")
        if provider_name == "openai" or api_key:
            candidate = OpenAIEmbedder(
                model=settings.embed_model if provider_name == "openai" else "text-embedding-3-small",
                base_url=settings.openai_base_url,
                api_key=api_key,
                timeout=settings.request_timeout_s,
                retries=settings.max_retries,
            )
            health = candidate.health()
            if health.get("ok"):
                provider = candidate
            elif provider_name == "openai":
                raise ProviderUnavailable(
                    "the OpenAI-compatible embedding endpoint is not usable",
                    hint=str(health.get("detail")),
                )

    if provider is None and provider_name in {"auto", "spacy"}:
        candidate = SpacyEmbedder(model=settings.spacy_model)
        health = candidate.health()
        if health.get("ok"):
            provider = candidate
        elif provider_name == "spacy":
            raise ProviderUnavailable(
                f"the spaCy embedding provider is unavailable ({health.get('detail')})",
                hint=str(health.get("hint")),
            )

    if provider is None and provider_name == "distributional":
        provider = DistributionalEmbedder(
            projection_dim=settings.distributional_dimension,
            window=settings.distributional_window,
            min_count=settings.distributional_min_count,
        )
        # A distributional model is fitted from the corpus during ingest/rebuild;
        # `prepare()` reports whether it was fitted or loaded.
        if getattr(provider, "_terms", None) in (None, {}) and not hasattr(provider, "fit_from_corpus"):
            raise ProviderUnavailable("distributional provider could not be initialised")

    if provider is None:
        provider = HashingEmbedder(dimension=settings.embed_dimension)

    embedder = CachedEmbedder(provider, store)
    if getattr(settings, "require_semantic", False) and not embedder.semantic:
        raise ConfigError(
            "RAG_REQUIRE_SEMANTIC is set but the selected embedder is lexical",
            hint=(
                "configure a semantic provider (RAG_EMBED_PROVIDER=ollama|openai|spacy|distributional) "
                "or unset RAG_REQUIRE_SEMANTIC"
            ),
        )
    return embedder


def describe(embedder: CachedEmbedder) -> Dict[str, Any]:
    notes = {
        "ollama": "Local neural embeddings served by Ollama; cosine reflects semantic relatedness.",
        "openai": "Hosted neural embeddings; cosine reflects semantic relatedness.",
        "spacy": "Static pretrained word vectors (mean-pooled). Semantic but not contextual.",
        "distributional": "Corpus-trained PPMI+SVD vectors. Semantic inside this corpus only.",
        "hashing": "Deterministic feature hashing (NOT a neural model): similarity reflects shared "
        "words and bigrams, not meaning.",
    }
    return {
        "provider": embedder.name,
        "model": embedder.model,
        "dimension": embedder.dimension,
        "semantic": embedder.semantic,
        "fingerprint": embedder.fingerprint,
        "preprocessing": embedder.preprocessing,
        "normalisation": "l2",
        "metric": "cosine",
        "note": notes.get(
            embedder.name,
            "Neural embeddings" if embedder.semantic else "Lexical fallback embeddings",
        ),
        "setup": (
            None
            if embedder.semantic
            else 'install a semantic provider: pip install "ragformation[semantic]" '
            "then RAG_EMBED_PROVIDER=spacy (or run Ollama with an embedding model)"
        ),
    }
