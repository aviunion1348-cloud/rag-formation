"""Chat sessions for the Lear agent (SQLite, one file per data directory).

Guarantees, each covered by tests:

* **Isolation** – every read and write is keyed by ``(session_id, user_id)``.
  A session id that belongs to another user behaves exactly like an unknown
  id (``not_found``), so ids cannot be probed across users.
* **Persistence** – sessions and messages survive process restarts.
* **Expiry** – a session expires ``ttl_s`` after its last activity; expired
  sessions are refused and removed by :meth:`SessionStore.purge_expired`.
* **Delete** – :meth:`SessionStore.delete` removes the session, its messages,
  its compaction records and its session-scoped memory in one transaction.

The clock is injectable so expiry is tested without sleeping.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..errors import NotFound, ValidationError

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    summary_version TEXT NOT NULL DEFAULT '',
    summary_through INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    status TEXT,
    citations_json TEXT NOT NULL DEFAULT '[]',
    query_id TEXT,
    tokens INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, message_id);
CREATE TABLE IF NOT EXISTS compactions (
    compaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    version TEXT NOT NULL,
    through_message_id INTEGER NOT NULL,
    tokens_before INTEGER NOT NULL,
    tokens_after INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    summary_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('user', 'session', 'global')),
    session_id TEXT,
    type TEXT NOT NULL,
    text TEXT NOT NULL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    verification TEXT NOT NULL DEFAULT 'unverified',
    confidence REAL NOT NULL DEFAULT 0.5,
    content_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, scope, deleted);
"""


class AgentDB:
    """One SQLite connection guarded by a lock (same pattern as ``store.Store``)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
            self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def _validate_user(user_id: str) -> str:
    user_id = (user_id or "").strip()
    if not user_id or len(user_id) > 128 or any(ch.isspace() for ch in user_id):
        raise ValidationError("user id must be 1-128 characters without whitespace")
    return user_id


def _validate_session_id(session_id: str) -> str:
    session_id = (session_id or "").strip()
    if not session_id or len(session_id) > 64 or not all(ch.isalnum() or ch in "-_." for ch in session_id):
        raise ValidationError("session id must be 1-64 characters of [A-Za-z0-9._-]")
    return session_id


class SessionStore:
    def __init__(self, db: AgentDB, *, ttl_s: int, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.ttl_s = int(ttl_s)
        self.clock = clock

    # ------------------------------------------------------------- sessions
    def create(self, user_id: str, *, session_id: Optional[str] = None, title: str = "") -> Dict[str, Any]:
        user_id = _validate_user(user_id)
        session_id = _validate_session_id(session_id) if session_id else f"s-{uuid.uuid4().hex[:12]}"
        now = self.clock()
        with self.db.lock:
            exists = self.db.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if exists:
                raise ValidationError("session id already exists", hint="choose another --session id")
            self.db.execute(
                "INSERT INTO sessions(session_id, user_id, title, created_at, updated_at, expires_at) VALUES (?,?,?,?,?,?)",
                (session_id, user_id, title[:200], now, now, now + self.ttl_s),
            )
            self.db.conn.commit()
        return self.get(session_id, user_id)

    def get(self, session_id: str, user_id: str, *, allow_expired: bool = False) -> Dict[str, Any]:
        user_id = _validate_user(user_id)
        session_id = _validate_session_id(session_id)
        row = self.db.execute(
            "SELECT * FROM sessions WHERE session_id=? AND user_id=?", (session_id, user_id)
        ).fetchone()
        if row is None:
            # Same answer for "unknown" and "someone else's": ids are not probeable.
            raise NotFound("session not found", hint=f"no session {session_id!r} for this user")
        data = dict(row)
        data["expired"] = data["expires_at"] <= self.clock()
        if data["expired"] and not allow_expired:
            raise NotFound("session expired", hint="start a new session; expired sessions are purged")
        return data

    def open_or_create(self, session_id: str, user_id: str) -> Dict[str, Any]:
        try:
            return self.get(session_id, user_id)
        except NotFound as exc:
            if "expired" in exc.message:
                raise
            owner = self.db.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if owner is not None:
                # Exists for a different user: refuse without revealing that.
                raise NotFound("session not found", hint=f"no session {session_id!r} for this user") from None
            return self.create(user_id, session_id=session_id)

    def touch(self, session_id: str, user_id: str) -> None:
        now = self.clock()
        with self.db.lock:
            self.db.execute(
                "UPDATE sessions SET updated_at=?, expires_at=? WHERE session_id=? AND user_id=?",
                (now, now + self.ttl_s, session_id, user_id),
            )
            self.db.conn.commit()

    def list(self, user_id: str, *, include_expired: bool = False) -> List[Dict[str, Any]]:
        user_id = _validate_user(user_id)
        rows = self.db.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.session_id) AS messages "
            "FROM sessions s WHERE s.user_id=? ORDER BY s.updated_at DESC",
            (user_id,),
        ).fetchall()
        now = self.clock()
        out = []
        for row in rows:
            data = dict(row)
            data["expired"] = data["expires_at"] <= now
            if data["expired"] and not include_expired:
                continue
            out.append(data)
        return out

    def delete(self, session_id: str, user_id: str) -> Dict[str, Any]:
        session = self.get(session_id, user_id, allow_expired=True)
        with self.db.lock:
            counts = {
                "messages": self.db.execute("DELETE FROM messages WHERE session_id=?", (session_id,)).rowcount,
                "compactions": self.db.execute("DELETE FROM compactions WHERE session_id=?", (session_id,)).rowcount,
                "memories": self.db.execute(
                    "DELETE FROM memories WHERE session_id=? AND scope='session' AND user_id=?", (session_id, user_id)
                ).rowcount,
            }
            self.db.execute("DELETE FROM sessions WHERE session_id=? AND user_id=?", (session_id, user_id))
            self.db.conn.commit()
        return {"deleted": session["session_id"], **counts}

    def purge_expired(self) -> int:
        now = self.clock()
        with self.db.lock:
            ids = [r["session_id"] for r in self.db.execute("SELECT session_id FROM sessions WHERE expires_at<=?", (now,))]
            for session_id in ids:
                self.db.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
                self.db.execute("DELETE FROM compactions WHERE session_id=?", (session_id,))
                self.db.execute("DELETE FROM memories WHERE session_id=? AND scope='session'", (session_id,))
                self.db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            self.db.conn.commit()
        return len(ids)

    # ------------------------------------------------------------- messages
    def add_message(
        self,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        *,
        status: Optional[str] = None,
        citations: Optional[List[Dict[str, Any]]] = None,
        query_id: Optional[str] = None,
        tokens: int = 0,
    ) -> int:
        self.get(session_id, user_id)
        with self.db.lock:
            cursor = self.db.execute(
                "INSERT INTO messages(session_id, user_id, role, content, status, citations_json, query_id, tokens, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    user_id,
                    role,
                    content,
                    status,
                    json.dumps(citations or [], ensure_ascii=False),
                    query_id,
                    int(tokens),
                    self.clock(),
                ),
            )
            self.db.conn.commit()
            message_id = int(cursor.lastrowid)
        self.touch(session_id, user_id)
        return message_id

    def messages(self, session_id: str, user_id: str, *, allow_expired: bool = False) -> List[Dict[str, Any]]:
        self.get(session_id, user_id, allow_expired=allow_expired)
        rows = self.db.execute(
            "SELECT * FROM messages WHERE session_id=? AND user_id=? ORDER BY message_id", (session_id, user_id)
        ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["citations"] = json.loads(data.pop("citations_json") or "[]")
            out.append(data)
        return out

    # ----------------------------------------------------------- compaction
    def save_summary(
        self,
        session_id: str,
        user_id: str,
        *,
        summary: str,
        version: str,
        through_message_id: int,
        tokens_before: int,
        tokens_after: int,
        latency_ms: float,
        summary_sha256: str,
    ) -> None:
        with self.db.lock:
            self.db.execute(
                "UPDATE sessions SET summary=?, summary_version=?, summary_through=? WHERE session_id=? AND user_id=?",
                (summary, version, int(through_message_id), session_id, user_id),
            )
            self.db.execute(
                "INSERT INTO compactions(session_id, version, through_message_id, tokens_before, tokens_after, latency_ms, summary_sha256, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (session_id, version, int(through_message_id), int(tokens_before), int(tokens_after), float(latency_ms), summary_sha256, self.clock()),
            )
            self.db.conn.commit()

    def compactions(self, session_id: str, user_id: str) -> List[Dict[str, Any]]:
        self.get(session_id, user_id, allow_expired=True)
        return [
            dict(r)
            for r in self.db.execute("SELECT * FROM compactions WHERE session_id=? ORDER BY compaction_id", (session_id,))
        ]
