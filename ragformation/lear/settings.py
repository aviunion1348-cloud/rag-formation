"""Strict-local configuration for the Lear agent.

The generic engine supports several providers (Ollama, OpenAI-compatible,
spaCy, hashing) and an ``auto`` mode that falls back silently. The Lear agent
does not use that resolution at all: it builds its own engine ``Settings`` with
``embed_provider="ollama"`` and ``llm_provider="ollama"`` pinned, so a missing
model fails with a diagnostic instead of degrading to a lexical embedder or a
cloud endpoint. The only non-Ollama mode is ``backend="baseline"`` (hashing
embeddings + extractive answers), which exists to exercise the pipeline offline
and is labelled as non-qualifying everywhere it is reported.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from ..config import Settings
from ..errors import ConfigError

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
#: Defaults sized for a small CPU-only machine; all three are under their caps.
DEFAULT_EMBED_MODEL = "nomic-embed-text"  # ~137M parameters
DEFAULT_LLM_MODEL = "qwen2.5:1.5b"  # ~1.5B parameters
DEFAULT_JUDGE_MODEL = "llama3.2:1b"  # ~1.2B parameters, a different model family
EMBED_MAX_PARAMS = 200_000_000
MODEL_MAX_PARAMS = 4_000_000_000

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
BACKENDS = {"ollama", "baseline"}


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


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


def is_loopback_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host in LOOPBACK_HOSTS


@dataclass
class LearSettings:
    data_dir: Path = Path("data/lear")
    docs_dir: Path = Path("docs/lear_backend")
    source_dir: Optional[Path] = None
    backend: str = "ollama"
    ollama_url: str = DEFAULT_OLLAMA_URL
    embed_model: str = DEFAULT_EMBED_MODEL
    llm_model: str = DEFAULT_LLM_MODEL
    judge_model: str = DEFAULT_JUDGE_MODEL
    embed_max_params: int = EMBED_MAX_PARAMS
    model_max_params: int = MODEL_MAX_PARAMS
    top_k: int = 6
    max_context_tokens: int = 1800
    max_output_tokens: int = 512
    temperature: float = 0.2
    top_p: float = 0.9
    abstain_threshold: float = 0.24
    request_timeout_s: float = 180.0
    # chunking (heading-aware, token windows); part of the chunking fingerprint
    chunk_target_tokens: int = 180
    chunk_max_tokens: int = 300
    chunk_overlap_tokens: int = 20
    # sessions / context
    session_ttl_s: int = 7 * 24 * 3600
    history_token_budget: int = 900
    keep_recent_turns: int = 4
    memory_items: int = 3
    # service-layer limits
    rate_user_per_min: int = 30
    rate_session_per_min: int = 20
    max_concurrent: int = 2
    queue_timeout_s: float = 30.0
    max_input_chars: int = 2000
    # Optional override so evaluation/benchmark runs keep their sessions out of
    # the user's agent.db while reading the same index.
    agent_db_path: Optional[Path] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, **overrides: Any) -> "LearSettings":
        source = _env("RAG_LEAR_SOURCE")
        values: Dict[str, Any] = dict(
            data_dir=Path(_env("RAG_LEAR_DATA_DIR", "data/lear") or "data/lear"),
            docs_dir=Path(_env("RAG_LEAR_DOCS_DIR", "docs/lear_backend") or "docs/lear_backend"),
            source_dir=Path(source) if source else None,
            backend=(_env("RAG_LEAR_BACKEND", "ollama") or "ollama").lower(),
            ollama_url=_env("RAG_LEAR_OLLAMA_URL", DEFAULT_OLLAMA_URL) or DEFAULT_OLLAMA_URL,
            embed_model=_env("RAG_LEAR_EMBED_MODEL", DEFAULT_EMBED_MODEL) or DEFAULT_EMBED_MODEL,
            llm_model=_env("RAG_LEAR_LLM_MODEL", DEFAULT_LLM_MODEL) or DEFAULT_LLM_MODEL,
            judge_model=_env("RAG_LEAR_JUDGE_MODEL", DEFAULT_JUDGE_MODEL) or DEFAULT_JUDGE_MODEL,
            embed_max_params=_env_int("RAG_LEAR_EMBED_MAX_PARAMS", EMBED_MAX_PARAMS),
            model_max_params=_env_int("RAG_LEAR_MODEL_MAX_PARAMS", MODEL_MAX_PARAMS),
            top_k=_env_int("RAG_LEAR_TOP_K", 6),
            max_context_tokens=_env_int("RAG_LEAR_MAX_CONTEXT_TOKENS", 1800),
            max_output_tokens=_env_int("RAG_LEAR_MAX_OUTPUT_TOKENS", 512),
            temperature=_env_float("RAG_LEAR_TEMPERATURE", 0.2),
            top_p=_env_float("RAG_LEAR_TOP_P", 0.9),
            abstain_threshold=_env_float("RAG_LEAR_ABSTAIN_THRESHOLD", 0.24),
            request_timeout_s=_env_float("RAG_LEAR_REQUEST_TIMEOUT_S", 180.0),
            chunk_target_tokens=_env_int("RAG_LEAR_CHUNK_TARGET_TOKENS", 180),
            chunk_max_tokens=_env_int("RAG_LEAR_CHUNK_MAX_TOKENS", 300),
            chunk_overlap_tokens=_env_int("RAG_LEAR_CHUNK_OVERLAP_TOKENS", 20),
            session_ttl_s=_env_int("RAG_LEAR_SESSION_TTL_S", 7 * 24 * 3600),
            history_token_budget=_env_int("RAG_LEAR_HISTORY_TOKENS", 900),
            keep_recent_turns=_env_int("RAG_LEAR_KEEP_RECENT_TURNS", 4),
            memory_items=_env_int("RAG_LEAR_MEMORY_ITEMS", 3),
            rate_user_per_min=_env_int("RAG_LEAR_RATE_USER_PER_MIN", 30),
            rate_session_per_min=_env_int("RAG_LEAR_RATE_SESSION_PER_MIN", 20),
            max_concurrent=_env_int("RAG_LEAR_MAX_CONCURRENT", 2),
            queue_timeout_s=_env_float("RAG_LEAR_QUEUE_TIMEOUT_S", 30.0),
            max_input_chars=_env_int("RAG_LEAR_MAX_INPUT_CHARS", 2000),
        )
        values.update({k: v for k, v in overrides.items() if v is not None})
        settings = cls(**values)
        settings.validate()
        return settings

    # ------------------------------------------------------------ validation
    def validate(self) -> None:
        if self.backend not in BACKENDS:
            raise ConfigError(
                f"unknown Lear backend {self.backend!r}",
                hint="RAG_LEAR_BACKEND=ollama (qualifying) or baseline (offline, labelled non-qualifying)",
            )
        if not is_loopback_url(self.ollama_url):
            raise ConfigError(
                "the Lear agent only talks to a loopback Ollama endpoint",
                hint=f"RAG_LEAR_OLLAMA_URL={self.ollama_url!r} is not http(s)://127.0.0.1|localhost|[::1]",
            )
        if self.llm_model.strip().lower() == self.judge_model.strip().lower():
            raise ConfigError(
                "the judge model must be different from the answer model",
                hint="set RAG_LEAR_JUDGE_MODEL to a separate local model",
            )
        if not 1 <= self.top_k <= 20:
            raise ConfigError("RAG_LEAR_TOP_K must be between 1 and 20")
        if self.max_concurrent < 1:
            raise ConfigError("RAG_LEAR_MAX_CONCURRENT must be at least 1")
        if self.rate_user_per_min < 1 or self.rate_session_per_min < 1:
            raise ConfigError("Lear rate limits must be at least 1 request per minute")
        if not 64 <= self.max_input_chars <= 2000:
            # The engine itself rejects questions over 2000 characters.
            raise ConfigError("RAG_LEAR_MAX_INPUT_CHARS must be between 64 and 2000")
        if self.max_context_tokens < 200:
            raise ConfigError("RAG_LEAR_MAX_CONTEXT_TOKENS must be at least 200")

    # ------------------------------------------------------------- derived
    @property
    def qualifying(self) -> bool:
        """Only the Ollama backend can satisfy the local-model requirements."""

        return self.backend == "ollama"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / ("index" if self.backend == "ollama" else "index-baseline")

    @property
    def agent_db(self) -> Path:
        return Path(self.agent_db_path) if self.agent_db_path else self.data_dir / "agent.db"

    def engine_settings(self) -> Settings:
        """Engine settings with providers pinned; never ``auto``, never cloud."""

        strict = self.backend == "ollama"
        settings = Settings(
            data_dir=self.index_dir,
            embed_provider="ollama" if strict else "hashing",
            embed_model=self.embed_model,
            require_semantic=strict,
            llm_provider="ollama" if strict else "extractive",
            llm_model=self.llm_model,
            ollama_host=self.ollama_url,
            temperature=self.temperature,
            top_p=self.top_p,
            max_output_tokens=self.max_output_tokens,
            top_k=self.top_k,
            max_context_tokens=self.max_context_tokens,
            abstain_threshold=self.abstain_threshold,
            request_timeout_s=self.request_timeout_s,
            chunk_target_tokens=self.chunk_target_tokens,
            chunk_max_tokens=self.chunk_max_tokens,
            chunk_overlap_tokens=self.chunk_overlap_tokens,
            allow_path_ingest=True,
            persist_traces=True,
            host="127.0.0.1",
            cors_origin="",
        )
        settings.validate()
        return settings

    def public_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, Path):
                data[key] = str(value)
        data["qualifying"] = self.qualifying
        data.pop("extra", None)
        return data
