"""ragformation — a portable, evidence-grounded RAG engine.

Two integration surfaces, one engine:

* **Embedded library** — import and call it directly, with your own data
  directory, providers and auth::

      from ragformation import RAGConfig, RAGEngine

      config = RAGConfig.from_env_or_mapping({"data_dir": "./.rag-data"})
      with RAGEngine(config=config) as rag:
          report = rag.ingest_path("./knowledge")
          answer = rag.ask("How long are audit logs retained?")
          print(answer.answer, [c.chunk_id for c in answer.citations])

* **HTTP service** — ``python -m ragformation serve`` exposes the same engine at
  ``/api/v1/...`` (with the original ``/api/...`` routes kept as aliases) for any
  other language.

Importing this package has no filesystem, database or network side effects.
Nothing below is a singleton: construct as many engines as you need, each with
its own ``data_dir``.
"""

from __future__ import annotations

from .auth import Principal
from .config import API_VERSION, Settings
from .contracts import (
    Citation,
    HealthReport,
    IngestReport,
    IngestResult,
    QueryTrace,
    RAGConfig,
    SourceInfo,
)
from .engine import AnswerStatus, QueryResult, RAGEngine
from .errors import (
    AuthError,
    ConfigError,
    EmbeddingMismatch,
    ExtractionError,
    Forbidden,
    IndexBusy,
    NotFound,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RagError,
    RateLimited,
    SourceTooLarge,
    UnsupportedFormat,
    ValidationError,
)
from .events import EVENT_SCHEMA_VERSION, EventBus, RAGEvent
from .store import Store

__version__ = "0.2.0"
__api_version__ = API_VERSION

__all__ = [
    # engine + config
    "RAGEngine",
    "RAGConfig",
    "Settings",
    "Store",
    # results
    "QueryResult",
    "AnswerStatus",
    "Citation",
    "SourceInfo",
    "IngestResult",
    "IngestReport",
    "HealthReport",
    "QueryTrace",
    # events / auth
    "RAGEvent",
    "EventBus",
    "EVENT_SCHEMA_VERSION",
    "Principal",
    # errors
    "RagError",
    "ConfigError",
    "ValidationError",
    "UnsupportedFormat",
    "ExtractionError",
    "SourceTooLarge",
    "NotFound",
    "ProviderError",
    "ProviderUnavailable",
    "ProviderTimeout",
    "EmbeddingMismatch",
    "AuthError",
    "Forbidden",
    "RateLimited",
    "IndexBusy",
    # versions
    "__version__",
    "__api_version__",
    "API_VERSION",
]
