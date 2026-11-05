"""Embedding providers.

Three honest options, selected by ``RAG_EMBED_PROVIDER``:

``ollama``   Local neural embeddings (default model ``nomic-embed-text``). This
             is the whiteboard path: nothing leaves the machine.
``openai``   Any OpenAI-compatible ``/embeddings`` endpoint (cloud or a local
             proxy such as vLLM / LM Studio / Ollama's OpenAI shim).
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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence

from .config import Settings
from .errors import ProviderError, ProviderUnavailable
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
    def fingerprint(self) -> str:
        return f"{self.name}:{self.model}:d{self.dimension}"

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        keyed: List[tuple[str, str]] = [
            (text_hash(text), text if text.strip() else " ") for text in texts
        ]
        results: List[Optional[List[float]]] = [None] * len(keyed)
        pending: List[int] = []
        for index, (key, _) in enumerate(keyed):
            cached = self.store.cache_get(key, self.model)
            if cached is not None:
                results[index] = cached
                self.cache_hits += 1
            else:
                pending.append(index)
        if pending:
            fresh = self.provider.embed([keyed[i][1] for i in pending])
            for slot, vector in zip(pending, fresh):
                results[slot] = vector
                self.store.cache_put(keyed[slot][0], self.model, len(vector), vector)
                self.cache_misses += 1
        return [vector or [] for vector in results]

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]

    def health(self) -> Dict[str, Any]:
        return self.provider.health()

    def cache_stats(self) -> Dict[str, int]:
        return {"hits": self.cache_hits, "misses": self.cache_misses}


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

    if provider is None:
        provider = HashingEmbedder(dimension=settings.embed_dimension)

    return CachedEmbedder(provider, store)


def describe(embedder: CachedEmbedder) -> Dict[str, Any]:
    return {
        "provider": embedder.name,
        "model": embedder.model,
        "dimension": embedder.dimension,
        "semantic": embedder.semantic,
        "fingerprint": embedder.fingerprint,
        "note": (
            "Neural embeddings: cosine similarity reflects semantic relatedness."
            if embedder.semantic
            else "Deterministic lexical hashing (NOT a neural model): similarity reflects shared "
            "words and bigrams. Set up Ollama or an OpenAI-compatible endpoint for semantic retrieval."
        ),
    }
