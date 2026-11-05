"""ragformation — a small, complete, operable Retrieval-Augmented Generation system.

The public surface is deliberately narrow:

    from ragformation import RAGEngine, Settings

    settings = Settings.from_env()
    engine = RAGEngine(settings)
    engine.ingest_path("corpus/")
    result = engine.ask("How long are audit logs retained?")
    print(result.answer, [c.citation_id for c in result.citations])

Everything below that is composable and independently testable: loaders,
chunking, embedding providers, a SQLite-backed index, hybrid retrieval,
guardrails, grounded generation with citation validation, and telemetry.
"""

from .config import Settings
from .engine import RAGEngine, QueryResult, AnswerStatus
from .store import Store
from .errors import RagError

__all__ = [
    "Settings",
    "RAGEngine",
    "QueryResult",
    "AnswerStatus",
    "Store",
    "RagError",
]

__version__ = "0.1.0"
