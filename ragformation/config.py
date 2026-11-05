"""Configuration, from environment variables or explicit overrides.

Design rules:

* **No secrets in code, logs or docs.** Only variable *names* are documented.
* **Sane defaults that actually run.** With no configuration at all the system
  starts, ingests and answers using the deterministic local fallbacks. It says
  so out loud (``doctor`` prints the active providers and whether embeddings are
  semantic).
* **Everything that changes retrieval semantics is fingerprintable**, so the
  index can refuse to mix incompatible vectors.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import ConfigError

DEFAULT_DATA_DIR = "data"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_EMBED_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_LLM_MODEL = "llama3.2:3b"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_EMBED_MODEL = "text-embedding-3-small"
DEFAULT_OPENAI_LLM_MODEL = "gpt-4o-mini"


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer", hint=f"got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number", hint=f"got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Immutable, explicitly validated runtime configuration."""

    # ---------------------------------------------------------------- storage
    data_dir: Path = Path(DEFAULT_DATA_DIR)
    db_filename: str = "ragformation.db"

    # -------------------------------------------------------------- embedding
    #: ``auto`` picks ollama when it is reachable, otherwise the local hashing
    #: fallback. ``hashing`` is explicitly *not* a semantic model.
    embed_provider: str = "auto"
    embed_model: str = DEFAULT_OLLAMA_EMBED_MODEL
    embed_dimension: int = 512  # only used by the hashing fallback
    embed_batch_size: int = 32

    # ------------------------------------------------------------- generation
    llm_provider: str = "auto"
    llm_model: str = DEFAULT_OLLAMA_LLM_MODEL
    temperature: float = 0.3  # whiteboard default: factual but not brittle
    top_p: float = 0.9
    max_output_tokens: int = 512

    # -------------------------------------------------------------- retrieval
    top_k: int = 6
    candidate_multiplier: int = 4
    retrieval_mode: str = "hybrid"  # hybrid | semantic | lexical
    rrf_k: int = 60
    rerank: bool = True
    mmr_lambda: float = 0.72
    max_context_tokens: int = 1400
    abstain_threshold: float = 0.22

    # --------------------------------------------------------------- chunking
    chunk_target_tokens: int = 180
    chunk_max_tokens: int = 300
    chunk_overlap_tokens: int = 40

    # --------------------------------------------------------------- network
    ollama_host: str = DEFAULT_OLLAMA_HOST
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL
    request_timeout_s: float = 60.0
    max_retries: int = 2

    # -------------------------------------------------------------- security
    max_source_bytes: int = 8 * 1024 * 1024
    api_key: Optional[str] = None  # when set, /api requires x-api-key
    allow_path_ingest: bool = True  # CLI is local-first; API gates this too
    cors_origin: str = "*"

    # ------------------------------------------------------------------ http
    host: str = "0.0.0.0"
    port: int = 8787

    # ------------------------------------------------------------- telemetry
    persist_traces: bool = True
    log_level: str = "INFO"

    extra: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ paths
    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_path / self.db_filename

    # ------------------------------------------------------------- factories
    @classmethod
    def from_env(cls, **overrides: Any) -> "Settings":
        data_dir = Path(_env("RAG_DATA_DIR", DEFAULT_DATA_DIR) or DEFAULT_DATA_DIR)
        settings = cls(
            data_dir=data_dir,
            embed_provider=(_env("RAG_EMBED_PROVIDER", "auto") or "auto").lower(),
            embed_model=_env("RAG_EMBED_MODEL", DEFAULT_OLLAMA_EMBED_MODEL) or DEFAULT_OLLAMA_EMBED_MODEL,
            embed_dimension=_env_int("RAG_EMBED_DIMENSION", 512),
            embed_batch_size=_env_int("RAG_EMBED_BATCH_SIZE", 32),
            llm_provider=(_env("RAG_LLM_PROVIDER", "auto") or "auto").lower(),
            llm_model=_env("RAG_LLM_MODEL", DEFAULT_OLLAMA_LLM_MODEL) or DEFAULT_OLLAMA_LLM_MODEL,
            temperature=_env_float("RAG_TEMPERATURE", 0.3),
            top_p=_env_float("RAG_TOP_P", 0.9),
            max_output_tokens=_env_int("RAG_MAX_OUTPUT_TOKENS", 512),
            top_k=_env_int("RAG_TOP_K", 6),
            candidate_multiplier=_env_int("RAG_CANDIDATE_MULTIPLIER", 4),
            retrieval_mode=(_env("RAG_RETRIEVAL_MODE", "hybrid") or "hybrid").lower(),
            rrf_k=_env_int("RAG_RRF_K", 60),
            rerank=(_env("RAG_RERANK", "1") or "1") not in {"0", "false", "False"},
            mmr_lambda=_env_float("RAG_MMR_LAMBDA", 0.72),
            max_context_tokens=_env_int("RAG_MAX_CONTEXT_TOKENS", 1400),
            abstain_threshold=_env_float("RAG_ABSTAIN_THRESHOLD", 0.22),
            chunk_target_tokens=_env_int("RAG_CHUNK_TARGET_TOKENS", 180),
            chunk_max_tokens=_env_int("RAG_CHUNK_MAX_TOKENS", 300),
            chunk_overlap_tokens=_env_int("RAG_CHUNK_OVERLAP_TOKENS", 40),
            ollama_host=_env("OLLAMA_HOST", DEFAULT_OLLAMA_HOST) or DEFAULT_OLLAMA_HOST,
            openai_base_url=_env("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL) or DEFAULT_OPENAI_BASE_URL,
            request_timeout_s=_env_float("RAG_REQUEST_TIMEOUT_S", 60.0),
            max_retries=_env_int("RAG_MAX_RETRIES", 2),
            max_source_bytes=_env_int("RAG_MAX_SOURCE_BYTES", 8 * 1024 * 1024),
            api_key=_env("RAG_API_KEY"),
            allow_path_ingest=(_env("RAG_ALLOW_PATH_INGEST", "1") or "1") not in {"0", "false", "False"},
            cors_origin=_env("RAG_CORS_ORIGIN", "*") or "*",
            host=_env("RAG_HOST", "0.0.0.0") or "0.0.0.0",
            port=_env_int("RAG_PORT", 8787),
            persist_traces=(_env("RAG_PERSIST_TRACES", "1") or "1") not in {"0", "false", "False"},
            log_level=(_env("RAG_LOG_LEVEL", "INFO") or "INFO").upper(),
        )
        if overrides:
            settings = replace(settings, **overrides)
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.embed_provider not in {"auto", "ollama", "openai", "hashing"}:
            raise ConfigError(
                "RAG_EMBED_PROVIDER must be auto|ollama|openai|hashing",
                hint=f"got {self.embed_provider!r}",
            )
        if self.llm_provider not in {"auto", "ollama", "openai", "extractive"}:
            raise ConfigError(
                "RAG_LLM_PROVIDER must be auto|ollama|openai|extractive",
                hint=f"got {self.llm_provider!r}",
            )
        if self.retrieval_mode not in {"hybrid", "semantic", "lexical"}:
            raise ConfigError("RAG_RETRIEVAL_MODE must be hybrid|semantic|lexical")
        if not 1 <= self.top_k <= 50:
            raise ConfigError("RAG_TOP_K must be between 1 and 50")
        if not 0.0 <= self.temperature <= 2.0:
            raise ConfigError("RAG_TEMPERATURE must be between 0 and 2")
        if not 0.0 < self.top_p <= 1.0:
            raise ConfigError("RAG_TOP_P must be in (0, 1]")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ConfigError("RAG_CHUNK_OVERLAP_TOKENS must be < RAG_CHUNK_TARGET_TOKENS")
        if self.chunk_max_tokens < self.chunk_target_tokens:
            raise ConfigError("RAG_CHUNK_MAX_TOKENS must be >= RAG_CHUNK_TARGET_TOKENS")
        if not 0.0 <= self.abstain_threshold <= 1.0:
            raise ConfigError("RAG_ABSTAIN_THRESHOLD must be in [0, 1]")
        if self.embed_dimension < 32:
            raise ConfigError("RAG_EMBED_DIMENSION must be >= 32")

    # ---------------------------------------------------------- fingerprints
    def chunking_fingerprint(self) -> str:
        payload = {
            "target": self.chunk_target_tokens,
            "max": self.chunk_max_tokens,
            "overlap": self.chunk_overlap_tokens,
            "version": 1,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def to_public_dict(self) -> Dict[str, Any]:
        """Config safe to show over HTTP: never includes keys or file paths."""
        data = asdict(self)
        data.pop("api_key", None)
        data.pop("data_dir", None)
        data["data_dir_name"] = Path(self.data_dir).name
        return data
