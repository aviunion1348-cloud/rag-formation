"""A hand-maintained OpenAPI 3.1 document for the versioned HTTP contract.

No framework generates this: the server is a stdlib ``ThreadingHTTPServer`` and
adding FastAPI purely to emit Swagger would triple the dependency surface. The
document is instead part of the package and is **tested against the live route
table** (``tests/test_api_v1.py``), so it cannot drift silently.
"""

from __future__ import annotations

from typing import Any, Dict

from .config import API_VERSION, Settings

_ERROR_SCHEMA = {
    "type": "object",
    "required": ["error", "message", "requestId"],
    "properties": {
        "error": {"type": "string", "description": "Stable machine-readable code"},
        "message": {"type": "string"},
        "hint": {"type": "string"},
        "requestId": {"type": "string"},
        "details": {"type": "object"},
    },
}

_CITATION = {
    "type": "object",
    "properties": {
        "evidenceId": {"type": "integer"},
        "chunkId": {"type": "string"},
        "sourceId": {"type": "string"},
        "sourceTitle": {"type": "string"},
        "section": {"type": "string"},
        "page": {"type": "integer"},
        "location": {"type": "string"},
        "excerpt": {"type": "string", "description": "Short excerpt, never a whole document"},
        "support": {"type": "number", "description": "Uncalibrated retrieval support score"},
        "valid": {"type": "boolean"},
    },
}

_ANSWER = {
    "type": "object",
    "properties": {
        "queryId": {"type": "string"},
        "question": {"type": "string"},
        "status": {"enum": ["answered", "needs_review", "abstained", "failed"]},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": _CITATION},
        "claims": {"type": "array", "items": {"type": "object"}},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "excluded": {"type": "array", "items": {"type": "object"}},
        "quarantined": {"type": "array", "items": {"type": "object"}},
        "trace": {"type": "array", "items": {"type": "object"}},
        "metrics": {"type": "object"},
        "provider": {"type": "object"},
        "generationMode": {"enum": ["llm", "extractive"]},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "abstainReason": {"type": ["string", "null"]},
        "conflict": {"type": ["object", "null"]},
    },
}

_SOURCE = {
    "type": "object",
    "properties": {
        "sourceId": {"type": "string"},
        "title": {"type": "string"},
        "kind": {"type": "string"},
        "origin": {"type": "string"},
        "chunks": {"type": "integer"},
        "revision": {"type": "integer"},
        "status": {"type": "string"},
        "createdAt": {"type": "string", "format": "date-time"},
        "updatedAt": {"type": "string", "format": "date-time"},
    },
}

_EVENT = {
    "type": "object",
    "properties": {
        "schemaVersion": {"type": "string"},
        "eventId": {"type": "string"},
        "traceId": {"type": "string"},
        "sequence": {"type": "integer"},
        "timestamp": {"type": "string", "format": "date-time"},
        "stage": {"type": "string"},
        "status": {"enum": ["started", "progress", "completed", "failed", "skipped"]},
        "durationMs": {"type": ["number", "null"]},
        "counts": {"type": "object"},
        "metrics": {"type": "object"},
        "references": {"type": "array", "items": {"type": "object"}},
        "safeSummary": {"type": "string"},
        "mode": {"enum": ["live", "demo", "extractive", "fallback"]},
        "warningCodes": {"type": "array", "items": {"type": "string"}},
    },
}


def _response(description: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return {"description": description, "content": {"application/json": {"schema": schema}}}


def document(settings: Settings) -> Dict[str, Any]:
    """Return the OpenAPI document for ``/api/v1``."""

    bearer = {
        "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-api-key"},
        "BearerAuth": {"type": "http", "scheme": "bearer"},
    }
    security = [{"ApiKeyAuth": []}, {"BearerAuth": []}] if settings.api_key else []

    paths: Dict[str, Any] = {
        "/api/v1/health": {
            "get": {
                "summary": "Service and index health",
                "security": [],
                "responses": {"200": _response("Health report", {"type": "object"})},
            }
        },
        "/api/v1/ready": {
            "get": {
                "summary": "Readiness probe",
                "security": [],
                "responses": {
                    "200": _response("Ready", {"type": "object"}),
                    "503": _response("Not ready", {"type": "object"}),
                },
            }
        },
        "/api/v1/config": {
            "get": {
                "summary": "Effective public configuration",
                "responses": {"200": _response("Config", {"type": "object"})},
            }
        },
        "/api/v1/sources": {
            "get": {
                "summary": "List sources",
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": 500}},
                    {"name": "cursor", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": {"200": _response("Sources", {"type": "object"})},
            },
            "post": {
                "summary": "Register a source (upload, text or server path)",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "filename": {"type": "string"},
                                    "contentBase64": {"type": "string"},
                                    "title": {"type": "string"},
                                    "text": {"type": "string"},
                                    "path": {"type": "string"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": _response("Ingest report", {"type": "object"}),
                    "413": _response("Too large", _ERROR_SCHEMA),
                    "415": _response("Unsupported format", _ERROR_SCHEMA),
                },
            },
        },
        "/api/v1/sources/{source_id}": {
            "get": {
                "summary": "Source detail",
                "parameters": [{"name": "source_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {
                    "200": _response("Source", _SOURCE),
                    "404": _response("Not found", _ERROR_SCHEMA),
                },
            },
            "delete": {
                "summary": "Delete a source and its evidence",
                "parameters": [{"name": "source_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {
                    "200": _response("Deletion result", {"type": "object"}),
                    "404": _response("Not found", _ERROR_SCHEMA),
                },
            },
        },
        "/api/v1/sources/{source_id}/chunks": {
            "get": {
                "summary": "Chunks for a source (authorised)",
                "parameters": [{"name": "source_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": _response("Chunks", {"type": "object"})},
            }
        },
        "/api/v1/ingestions/{job_id}": {
            "get": {
                "summary": "Ingestion job status",
                "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": _response("Job", {"type": "object"}), "404": _response("Not found", _ERROR_SCHEMA)},
            }
        },
        "/api/v1/query": {
            "post": {
                "summary": "Ask a question",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["question"],
                                "properties": {
                                    "question": {"type": "string", "maxLength": 2000},
                                    "topK": {"type": "integer", "minimum": 1, "maximum": 50},
                                    "mode": {"enum": ["hybrid", "semantic", "lexical"]},
                                    "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                                    "topP": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": _response("Grounded answer", _ANSWER),
                    "400": _response("Invalid request", _ERROR_SCHEMA),
                    "503": _response("Provider unavailable", _ERROR_SCHEMA),
                },
            }
        },
        "/api/v1/traces/{query_id}": {
            "get": {
                "summary": "Trace for a query (operator role)",
                "parameters": [{"name": "query_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {
                    "200": _response("Trace", {"type": "object"}),
                    "404": _response("Not found", _ERROR_SCHEMA),
                },
            }
        },
        "/api/v1/traces": {
            "get": {
                "summary": "Recent query traces (operator role)",
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": 500}}],
                "responses": {"200": _response("Traces", {"type": "object"})},
            }
        },
        "/api/v1/index/stats": {
            "get": {
                "summary": "Latency and quality statistics (operator role)",
                "responses": {"200": _response("Stats", {"type": "object"})},
            }
        },
        "/api/v1/index/rebuild": {
            "post": {
                "summary": "Rebuild the vector index (admin role)",
                "responses": {"200": _response("Rebuild report", {"type": "object"})},
            }
        },
        "/api/v1/index/rollback": {
            "post": {
                "summary": "Roll back to the previous vector index (admin role)",
                "responses": {"200": _response("Rollback report", {"type": "object"})},
            }
        },
        "/api/v1/events": {
            "get": {
                "summary": "Server-Sent Events stream of pipeline events",
                "parameters": [
                    {"name": "trace_id", "in": "query", "schema": {"type": "string"}},
                    {"name": "since", "in": "query", "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "description": "text/event-stream of RAGEvent objects",
                        "content": {"text/event-stream": {"schema": _EVENT}},
                    }
                },
            }
        },
        "/api/v1/openapi.json": {
            "get": {
                "summary": "This document",
                "security": [],
                "responses": {"200": _response("OpenAPI 3.1 document", {"type": "object"})},
            }
        },
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "ragformation HTTP API",
            "version": API_VERSION,
            "description": (
                "Versioned contract for the ragformation RAG service. Legacy /api/... routes remain "
                "available as compatibility aliases; new integrations should target /api/v1."
            ),
            "license": {"name": "MIT"},
        },
        "servers": [{"url": "/", "description": "Same origin"}],
        "components": {
            "securitySchemes": bearer,
            "schemas": {
                "Error": _ERROR_SCHEMA,
                "Citation": _CITATION,
                "Answer": _ANSWER,
                "Source": _SOURCE,
                "RAGEvent": _EVENT,
            },
        },
        "security": security,
        "paths": paths,
        "x-ragformation": {
            "eventSchemaVersion": "1.0",
            "deprecationPolicy": (
                "Legacy /api/... routes stay available for the whole 1.x line; breaking changes to "
                "/api/v1 require /api/v2."
            ),
        },
    }
