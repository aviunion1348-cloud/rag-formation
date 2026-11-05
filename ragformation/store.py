"""SQLite persistence: sources, chunks, vectors, lexical index, jobs, telemetry.

Why SQLite: it is in the standard library, transactional, crash-safe, and keeps
the vector payload next to the metadata that gives it meaning. At the corpus
sizes this project targets (thousands to low tens of thousands of chunks) a
brute-force cosine scan in Python is exact, predictable and fast enough; the
in-process store is the constraint we document, not a hidden one.

Durability rules implemented here:

* A source revision is swapped **inside one transaction**. A crash mid-ingest
  leaves the previous revision intact rather than a half-indexed source.
* Vectors carry their embedding fingerprint. Ingest refuses to write vectors
  from an incompatible model into an index built by another one.
* FTS and vector tables are keyed by ``chunk_id`` and deleted with the source,
  so "delete" really removes evidence (tested).
"""

from __future__ import annotations

import array
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .errors import RagError

SCHEMA_VERSION = 1


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def pack_vector(vector: Sequence[float]) -> bytes:
    return array.array("f", vector).tobytes()


def unpack_vector(blob: bytes) -> List[float]:
    arr = array.array("f")
    arr.frombytes(blob)
    return arr.tolist()


@dataclass
class SourceRecord:
    source_id: str
    title: str
    origin: str
    media_type: str
    byte_size: int
    content_hash: str
    revision: int
    status: str
    tenant_id: str
    visibility: str
    tags: List[str]
    warnings: List[str]
    meta: Dict[str, Any]
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "title": self.title,
            "origin": self.origin,
            "mediaType": self.media_type,
            "byteSize": self.byte_size,
            "contentHash": self.content_hash,
            "revision": self.revision,
            "status": self.status,
            "tenantId": self.tenant_id,
            "visibility": self.visibility,
            "tags": self.tags,
            "warnings": self.warnings,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass
class StoredChunk:
    chunk_id: str
    source_id: str
    revision: int
    ordinal: int
    text: str
    token_count: int
    section: str
    section_path: List[str]
    page_start: int
    page_end: int
    content_hash: str
    source_title: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "sourceId": self.source_id,
            "sourceTitle": self.source_title,
            "revision": self.revision,
            "ordinal": self.ordinal,
            "text": self.text,
            "tokenCount": self.token_count,
            "section": self.section,
            "sectionPath": self.section_path,
            "pageStart": self.page_start,
            "pageEnd": self.page_end,
            "contentHash": self.content_hash,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id    TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    origin       TEXT NOT NULL,
    media_type   TEXT NOT NULL,
    byte_size    INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    revision     INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL,
    tenant_id    TEXT NOT NULL DEFAULT 'local',
    visibility   TEXT NOT NULL DEFAULT 'private',
    tags         TEXT NOT NULL DEFAULT '[]',
    warnings     TEXT NOT NULL DEFAULT '[]',
    meta         TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    revision     INTEGER NOT NULL,
    ordinal      INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    section      TEXT NOT NULL DEFAULT '',
    section_path TEXT NOT NULL DEFAULT '[]',
    page_start   INTEGER NOT NULL DEFAULT 1,
    page_end     INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_by_source ON chunks(source_id);

CREATE TABLE IF NOT EXISTS vectors (
    chunk_id  TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    model     TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    vector    BLOB NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    source_id UNINDEXED,
    title,
    text,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    source_id   TEXT,
    status      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    error_code  TEXT,
    error       TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_by_source ON jobs(source_id);

CREATE TABLE IF NOT EXISTS queries (
    query_id          TEXT PRIMARY KEY,
    question          TEXT NOT NULL,
    status            TEXT NOT NULL,
    mode              TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    top_k             INTEGER NOT NULL,
    temperature       REAL NOT NULL,
    latency_ms        REAL,
    retrieval_ms      REAL,
    generation_ms     REAL,
    ttft_ms           REAL,
    itl_ms            REAL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    tokens_estimated  INTEGER NOT NULL DEFAULT 0,
    citations_total   INTEGER,
    citations_valid   INTEGER,
    abstain_reason    TEXT,
    trace_json        TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS queries_by_time ON queries(created_at);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    query_id    TEXT NOT NULL,
    rating      INTEGER NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    text_hash TEXT NOT NULL,
    model     TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    vector    BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (text_hash, model)
);
"""


class Store:
    """Thin, explicit data-access layer. No business logic lives here."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self.set_meta("schema_version", str(SCHEMA_VERSION))

    # ------------------------------------------------------------------ basics
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _exec(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    # -------------------------------------------------------------------- meta
    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._exec(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._exec("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ----------------------------------------------------------------- sources
    def get_source(self, source_id: str) -> Optional[SourceRecord]:
        with self._lock:
            row = self._exec("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        return self._source_from_row(row) if row else None

    def find_source_by_path(self, origin: str) -> Optional[SourceRecord]:
        with self._lock:
            row = self._exec(
                "SELECT * FROM sources WHERE origin=? AND status!='deleted'", (origin,)
            ).fetchone()
        return self._source_from_row(row) if row else None

    def list_sources(self, include_deleted: bool = False) -> List[SourceRecord]:
        sql = "SELECT * FROM sources"
        if not include_deleted:
            sql += " WHERE status!='deleted'"
        sql += " ORDER BY created_at, source_id"
        with self._lock:
            rows = self._exec(sql).fetchall()
        return [self._source_from_row(r) for r in rows]

    def active_source_ids(self, tenant_id: Optional[str] = None) -> Set[str]:
        sql = "SELECT source_id FROM sources WHERE status='active'"
        params: List[Any] = []
        if tenant_id is not None:
            sql += " AND tenant_id=?"
            params.append(tenant_id)
        with self._lock:
            rows = self._exec(sql, params).fetchall()
        return {r["source_id"] for r in rows}

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(
            source_id=row["source_id"],
            title=row["title"],
            origin=row["origin"],
            media_type=row["media_type"],
            byte_size=row["byte_size"],
            content_hash=row["content_hash"],
            revision=row["revision"],
            status=row["status"],
            tenant_id=row["tenant_id"],
            visibility=row["visibility"],
            tags=json.loads(row["tags"] or "[]"),
            warnings=json.loads(row["warnings"] or "[]"),
            meta=json.loads(row["meta"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -------------------------------------------------------------------- jobs
    def start_job(self, kind: str, source_id: Optional[str] = None) -> str:
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._exec(
                "INSERT INTO jobs(job_id, kind, source_id, status, stage, started_at) "
                "VALUES(?,?,?,?,?,?)",
                (job_id, kind, source_id, "running", "queued", now_iso()),
            )
            self._conn.commit()
        return job_id

    def update_job(self, job_id: str, stage: str, detail: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._exec(
                "UPDATE jobs SET stage=?, detail=COALESCE(?, detail) WHERE job_id=?",
                (stage, json.dumps(detail) if detail is not None else None, job_id),
            )
            self._conn.commit()

    def finish_job(
        self,
        job_id: str,
        status: str,
        *,
        error_code: Optional[str] = None,
        error: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self._exec(
                "UPDATE jobs SET status=?, stage=?, error_code=?, error=?, "
                "detail=COALESCE(?, detail), finished_at=? WHERE job_id=?",
                (
                    status,
                    "complete" if status == "succeeded" else "failed",
                    error_code,
                    error,
                    json.dumps(detail) if detail is not None else None,
                    now_iso(),
                    job_id,
                ),
            )
            self._conn.commit()

    def recent_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._exec(
                "SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- write paths
    def begin(self) -> None:
        self._lock.acquire()
        self._exec("BEGIN IMMEDIATE")

    def commit(self) -> None:
        try:
            self._conn.commit()
        finally:
            self._lock.release()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        finally:
            self._lock.release()

    def replace_source(
        self,
        *,
        source_id: str,
        title: str,
        origin: str,
        media_type: str,
        byte_size: int,
        content_hash: str,
        tenant_id: str,
        visibility: str,
        tags: Sequence[str],
        warnings: Sequence[str],
        meta: Dict[str, Any],
        chunks: Sequence[StoredChunk],
        vectors: Sequence[Sequence[float]],
        embedding_fingerprint: str,
        dimension: int,
    ) -> Dict[str, Any]:
        """Atomically install one source revision (chunks + vectors + FTS rows).

        Caller must have obtained embeddings first: this method only writes, so a
        provider failure can never leave a half-written revision.
        """
        if len(chunks) != len(vectors):
            raise RagError("chunk/vector count mismatch", chunks=len(chunks), vectors=len(vectors))
        for vector in vectors:
            if len(vector) != dimension:
                raise RagError(
                    "vector dimension mismatch",
                    expected=dimension,
                    actual=len(vector),
                )

        with self._lock:
            existing = self._exec(
                "SELECT revision, content_hash FROM sources WHERE source_id=?", (source_id,)
            ).fetchone()
            previous_revision = existing["revision"] if existing else 0
            new_revision = previous_revision + 1

            self._exec("BEGIN IMMEDIATE")
            try:
                # Remove the previous revision's derived data first: no stale
                # evidence may survive an update.
                self._delete_chunks_for_source(source_id)
                ts = now_iso()
                if existing:
                    self._exec(
                        "UPDATE sources SET title=?, origin=?, media_type=?, byte_size=?, "
                        "content_hash=?, revision=?, status='active', tenant_id=?, visibility=?, "
                        "tags=?, warnings=?, meta=?, updated_at=? WHERE source_id=?",
                        (
                            title,
                            origin,
                            media_type,
                            byte_size,
                            content_hash,
                            new_revision,
                            tenant_id,
                            visibility,
                            json.dumps(list(tags)),
                            json.dumps(list(warnings)),
                            json.dumps(meta, default=str),
                            ts,
                            source_id,
                        ),
                    )
                else:
                    self._exec(
                        "INSERT INTO sources(source_id, title, origin, media_type, byte_size, "
                        "content_hash, revision, status, tenant_id, visibility, tags, warnings, "
                        "meta, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            source_id,
                            title,
                            origin,
                            media_type,
                            byte_size,
                            content_hash,
                            new_revision,
                            "active",
                            tenant_id,
                            visibility,
                            json.dumps(list(tags)),
                            json.dumps(list(warnings)),
                            json.dumps(meta, default=str),
                            ts,
                            ts,
                        ),
                    )

                for chunk, vector in zip(chunks, vectors):
                    self._exec(
                        "INSERT INTO chunks(chunk_id, source_id, revision, ordinal, text, "
                        "token_count, section, section_path, page_start, page_end, content_hash, "
                        "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            chunk.chunk_id,
                            source_id,
                            new_revision,
                            chunk.ordinal,
                            chunk.text,
                            chunk.token_count,
                            chunk.section,
                            json.dumps(chunk.section_path),
                            chunk.page_start,
                            chunk.page_end,
                            chunk.content_hash,
                            ts,
                        ),
                    )
                    self._exec(
                        "INSERT INTO vectors(chunk_id, model, dimension, vector) VALUES(?,?,?,?)",
                        (chunk.chunk_id, embedding_fingerprint, dimension, pack_vector(vector)),
                    )
                    self._exec(
                        "INSERT INTO chunks_fts(chunk_id, source_id, title, text) VALUES(?,?,?,?)",
                        (chunk.chunk_id, source_id, title, chunk.text),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        return {
            "sourceId": source_id,
            "revision": new_revision,
            "previousRevision": previous_revision or None,
            "chunks": len(chunks),
        }

    def _delete_chunks_for_source(self, source_id: str) -> int:
        rows = self._exec("SELECT chunk_id FROM chunks WHERE source_id=?", (source_id,)).fetchall()
        ids = [r["chunk_id"] for r in rows]
        for chunk_id in ids:
            self._exec("DELETE FROM vectors WHERE chunk_id=?", (chunk_id,))
        self._exec("DELETE FROM chunks WHERE source_id=?", (source_id,))
        self._exec("DELETE FROM chunks_fts WHERE source_id=?", (source_id,))
        return len(ids)

    def delete_source(self, source_id: str) -> Dict[str, Any]:
        with self._lock:
            self._exec("BEGIN IMMEDIATE")
            try:
                removed = self._delete_chunks_for_source(source_id)
                row = self._exec(
                    "SELECT revision FROM sources WHERE source_id=?", (source_id,)
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                    return {"sourceId": source_id, "existed": False, "removedChunks": 0}
                self._exec(
                    "UPDATE sources SET status='deleted', updated_at=? WHERE source_id=?",
                    (now_iso(), source_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {"sourceId": source_id, "existed": True, "removedChunks": removed}

    def mark_source_failed(self, source_id: str, reason: str) -> None:
        with self._lock:
            self._exec(
                "UPDATE sources SET status='failed', updated_at=?, warnings=? WHERE source_id=?",
                (now_iso(), json.dumps([reason]), source_id),
            )
            self._conn.commit()

    # -------------------------------------------------------------- read paths
    def chunks_for_source(self, source_id: str) -> List[StoredChunk]:
        with self._lock:
            rows = self._exec(
                "SELECT c.*, s.title AS source_title FROM chunks c JOIN sources s "
                "ON s.source_id=c.source_id WHERE c.source_id=? ORDER BY c.ordinal",
                (source_id,),
            ).fetchall()
        return [self._chunk_from_row(r) for r in rows]

    def all_chunks(self, allowed_sources: Optional[Set[str]] = None) -> List[StoredChunk]:
        sql = (
            "SELECT c.*, s.title AS source_title FROM chunks c "
            "JOIN sources s ON s.source_id=c.source_id WHERE s.status='active'"
        )
        params: List[Any] = []
        if allowed_sources is not None:
            if not allowed_sources:
                return []
            placeholders = ",".join("?" for _ in allowed_sources)
            sql += f" AND c.source_id IN ({placeholders})"
            params.extend(sorted(allowed_sources))
        sql += " ORDER BY c.source_id, c.ordinal"
        with self._lock:
            rows = self._exec(sql, params).fetchall()
        return [self._chunk_from_row(r) for r in rows]

    @staticmethod
    def _chunk_from_row(row: sqlite3.Row) -> StoredChunk:
        return StoredChunk(
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            revision=row["revision"],
            ordinal=row["ordinal"],
            text=row["text"],
            token_count=row["token_count"],
            section=row["section"],
            section_path=json.loads(row["section_path"] or "[]"),
            page_start=row["page_start"],
            page_end=row["page_end"],
            content_hash=row["content_hash"],
            source_title=row["source_title"] if "source_title" in row.keys() else "",
        )

    def vectors_for(
        self, chunk_ids: Optional[Sequence[str]] = None
    ) -> Dict[str, List[float]]:
        with self._lock:
            if chunk_ids is None:
                rows = self._exec("SELECT chunk_id, vector FROM vectors").fetchall()
            else:
                if not chunk_ids:
                    return {}
                placeholders = ",".join("?" for _ in chunk_ids)
                rows = self._exec(
                    f"SELECT chunk_id, vector FROM vectors WHERE chunk_id IN ({placeholders})",
                    list(chunk_ids),
                ).fetchall()
        return {r["chunk_id"]: unpack_vector(r["vector"]) for r in rows}

    def lexical_search(
        self, query: str, limit: int, allowed_sources: Optional[Set[str]] = None
    ) -> List[Tuple[str, float]]:
        """BM25 via SQLite FTS5. Returns (chunk_id, bm25_score) best-first.

        FTS5's ``bm25()`` returns lower-is-better, so scores are negated.
        """
        match = _fts_match_expression(query)
        if not match:
            return []
        sql = (
            "SELECT chunk_id, bm25(chunks_fts) AS score FROM chunks_fts "
            "WHERE chunks_fts MATCH ?"
        )
        params: List[Any] = [match]
        if allowed_sources is not None:
            if not allowed_sources:
                return []
            placeholders = ",".join("?" for _ in allowed_sources)
            sql += f" AND source_id IN ({placeholders})"
            params.extend(sorted(allowed_sources))
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        try:
            with self._lock:
                rows = self._exec(sql, params).fetchall()
        except sqlite3.OperationalError:
            # A query made only of quote/operator characters can still be invalid.
            return []
        return [(r["chunk_id"], -float(r["score"])) for r in rows]

    def counts(self) -> Dict[str, int]:
        with self._lock:
            sources = self._exec(
                "SELECT COUNT(*) c FROM sources WHERE status!='deleted'"
            ).fetchone()["c"]
            chunks = self._exec("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
            vectors = self._exec("SELECT COUNT(*) c FROM vectors").fetchone()["c"]
            fts = self._exec("SELECT COUNT(*) c FROM chunks_fts").fetchone()["c"]
        return {"sources": sources, "chunks": chunks, "vectors": vectors, "lexicalRows": fts}

    def integrity_report(self, expected_dimension: Optional[int]) -> Dict[str, Any]:
        """Cheap, honest health check: counts plus real consistency problems."""
        problems: List[Dict[str, Any]] = []
        with self._lock:
            missing_vectors = self._exec(
                "SELECT COUNT(*) c FROM chunks c LEFT JOIN vectors v ON v.chunk_id=c.chunk_id "
                "WHERE v.chunk_id IS NULL"
            ).fetchone()["c"]
            orphan_vectors = self._exec(
                "SELECT COUNT(*) c FROM vectors v LEFT JOIN chunks c ON c.chunk_id=v.chunk_id "
                "WHERE c.chunk_id IS NULL"
            ).fetchone()["c"]
            missing_fts = self._exec(
                "SELECT COUNT(*) c FROM chunks c LEFT JOIN chunks_fts f ON f.chunk_id=c.chunk_id "
                "WHERE f.chunk_id IS NULL"
            ).fetchone()["c"]
            dims = [
                r["dimension"]
                for r in self._exec("SELECT DISTINCT dimension FROM vectors").fetchall()
            ]
            models = [
                r["model"] for r in self._exec("SELECT DISTINCT model FROM vectors").fetchall()
            ]
        if missing_vectors:
            problems.append({"code": "chunks_without_vectors", "count": missing_vectors})
        if orphan_vectors:
            problems.append({"code": "orphan_vectors", "count": orphan_vectors})
        if missing_fts:
            problems.append({"code": "chunks_missing_from_lexical_index", "count": missing_fts})
        if expected_dimension is not None and dims and set(dims) != {expected_dimension}:
            problems.append(
                {
                    "code": "dimension_mismatch",
                    "expected": expected_dimension,
                    "found": sorted(dims),
                }
            )
        if len(models) > 1:
            problems.append({"code": "mixed_embedding_models", "models": models})
        return {"problems": problems, "dimensions": dims, "models": models}

    # ------------------------------------------------------- embedding cache
    def cache_get(self, text_hash: str, model: str) -> Optional[List[float]]:
        with self._lock:
            row = self._exec(
                "SELECT vector FROM embedding_cache WHERE text_hash=? AND model=?",
                (text_hash, model),
            ).fetchone()
        return unpack_vector(row["vector"]) if row else None

    def cache_put(self, text_hash: str, model: str, dimension: int, vector: Sequence[float]) -> None:
        with self._lock:
            self._exec(
                "INSERT OR REPLACE INTO embedding_cache(text_hash, model, dimension, vector, created_at) "
                "VALUES(?,?,?,?,?)",
                (text_hash, model, dimension, pack_vector(vector), now_iso()),
            )
            self._conn.commit()

    def cache_clear(self) -> None:
        with self._lock:
            self._exec("DELETE FROM embedding_cache")
            self._conn.commit()

    # --------------------------------------------------------------- telemetry
    def record_query(self, row: Dict[str, Any]) -> None:
        columns = (
            "query_id, question, status, mode, provider, model, top_k, temperature, "
            "latency_ms, retrieval_ms, generation_ms, ttft_ms, itl_ms, prompt_tokens, "
            "completion_tokens, tokens_estimated, citations_total, citations_valid, "
            "abstain_reason, trace_json, created_at"
        )
        values = tuple(row.get(key) for key in columns.split(", "))
        with self._lock:
            self._exec(
                f"INSERT OR REPLACE INTO queries({columns}) VALUES({','.join('?' * 21)})",
                values,
            )
            self._conn.commit()

    def query_rows(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._exec(
                "SELECT * FROM queries ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def count_queries(self) -> int:
        with self._lock:
            return int(self._exec("SELECT COUNT(*) c FROM queries").fetchone()["c"])

    def record_feedback(self, query_id: str, rating: int, note: str = "") -> str:
        feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._exec(
                "INSERT INTO feedback(feedback_id, query_id, rating, note, created_at) VALUES(?,?,?,?,?)",
                (feedback_id, query_id, rating, note[:500], now_iso()),
            )
            self._conn.commit()
        return feedback_id

    # ------------------------------------------------------------------ misc
    def reset_index(self) -> None:
        """Drop every derived row. Source registry is intentionally preserved
        only when explicitly asked for; this is a full local reset."""
        with self._lock:
            self._exec("BEGIN IMMEDIATE")
            try:
                for table in ("vectors", "chunks_fts", "chunks", "sources", "jobs"):
                    self._exec(f"DELETE FROM {table}")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise


def _fts_match_expression(query: str) -> str:
    """Build a safe FTS5 MATCH expression: OR of quoted terms, prefix on last.

    Quoting every term removes FTS5 operator injection ("*", "NEAR(", quotes,
    column filters) from user input while keeping BM25 ranking meaningful.
    """
    tokens = [
        token
        for token in (
            "".join(ch for ch in raw if ch.isalnum() or ch in "-_.")
            for raw in query.split()
        )
        if token
    ]
    if not tokens:
        return ""
    quoted = [f'"{token}"' for token in tokens[:-1]]
    quoted.append(f'"{tokens[-1]}"*')
    return " OR ".join(quoted)
