"""Structured errors with stable codes.

Every error that can reach a caller (CLI, HTTP API, UI) carries a machine-stable
``code`` so clients do not have to parse English. Messages are safe to show:
they never embed credentials, absolute internal paths or raw document text.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class RagError(Exception):
    """Base class for expected, reportable failures."""

    code = "rag_error"
    http_status = 400

    def __init__(self, message: str, *, hint: Optional[str] = None, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details: Dict[str, Any] = details

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"error": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        if self.details:
            payload["details"] = self.details
        return payload


class ConfigError(RagError):
    code = "config_error"
    http_status = 500


class ValidationError(RagError):
    code = "validation_error"
    http_status = 400


class UnsupportedFormat(RagError):
    code = "unsupported_format"
    http_status = 415


class ExtractionError(RagError):
    code = "extraction_error"
    http_status = 422


class SourceTooLarge(RagError):
    code = "source_too_large"
    http_status = 413


class NotFound(RagError):
    code = "not_found"
    http_status = 404


class ProviderError(RagError):
    """A model provider returned something unusable."""

    code = "provider_error"
    http_status = 502


class ProviderUnavailable(ProviderError):
    """A model provider could not be reached (connection refused, timeout, 5xx)."""

    code = "provider_unavailable"
    http_status = 503


class EmbeddingMismatch(RagError):
    """Stored vectors were produced by a different embedding fingerprint."""

    code = "embedding_mismatch"
    http_status = 409


class AuthError(RagError):
    code = "unauthorized"
    http_status = 401
