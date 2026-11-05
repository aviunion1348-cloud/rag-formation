"""Long-term memory for the Lear agent.

Memory is *context*, never *evidence*:

* items are rendered into the system prompt inside a block that says they may
  be stale and must not override the cited documentation;
* they are never given an evidence id, so they cannot be cited;
* nothing is promoted into memory automatically — items come from an explicit
  ``lear memory add`` or an approved, read-only shared-memory import.

Every item carries a type, a scope (``user`` / ``session`` / ``global``),
provenance, a verification state, a confidence and an optional expiry. Writes
that look like credentials or like prompt-injection instructions are refused.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from ..errors import NotFound, ValidationError
from ..guardrails import scan_text
from ..text import content_terms, term_matches
from .secrets import find_secrets
from .sessions import AgentDB, _validate_user

MEMORY_TYPES = ("preference", "project_fact", "task_state", "shared")
SCOPES = ("user", "session", "global")
VERIFICATION = ("unverified", "user_asserted", "verified")
SHARED_OWNER = "__shared__"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class MemoryStore:
    def __init__(self, db: AgentDB, *, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock

    def guard(self, text: str) -> None:
        secrets = find_secrets(text)
        if secrets:
            raise ValidationError(
                "memory text looks like it contains a credential; it was not stored",
                hint=", ".join(sorted({rule for rule, _ in secrets})),
            )
        findings = scan_text(text)
        if findings:
            raise ValidationError(
                "memory text contains instruction-like content; it was not stored",
                hint=", ".join(sorted({f.rule_id for f in findings})),
            )

    def add(
        self,
        user_id: str,
        text: str,
        *,
        type: str = "preference",
        scope: str = "user",
        session_id: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
        verification: str = "user_asserted",
        confidence: float = 0.5,
        ttl_s: Optional[int] = None,
    ) -> Dict[str, Any]:
        user_id = user_id if user_id == SHARED_OWNER else _validate_user(user_id)
        text = " ".join((text or "").split())
        if not 3 <= len(text) <= 1000:
            raise ValidationError("memory text must be 3-1000 characters")
        if type not in MEMORY_TYPES:
            raise ValidationError(f"memory type must be one of {', '.join(MEMORY_TYPES)}")
        if scope not in SCOPES:
            raise ValidationError(f"memory scope must be one of {', '.join(SCOPES)}")
        if scope == "session" and not session_id:
            raise ValidationError("session-scoped memory needs a session id")
        if scope == "global" and user_id != SHARED_OWNER:
            raise ValidationError("global memory can only come from an approved shared-memory import")
        if verification not in VERIFICATION:
            raise ValidationError(f"verification must be one of {', '.join(VERIFICATION)}")
        if not 0.0 <= confidence <= 1.0:
            raise ValidationError("confidence must be between 0 and 1")
        self.guard(text)
        now = self.clock()
        memory_id = f"m-{uuid.uuid4().hex[:12]}"
        record = {
            "memory_id": memory_id,
            "user_id": user_id,
            "scope": scope,
            "session_id": session_id if scope == "session" else None,
            "type": type,
            "text": text,
            "provenance_json": json.dumps(provenance or {"origin": "cli"}, sort_keys=True),
            "verification": verification,
            "confidence": float(confidence),
            "content_sha256": _sha(text),
            "created_at": now,
            "updated_at": now,
            "expires_at": now + ttl_s if ttl_s else None,
        }
        with self.db.lock:
            self.db.execute(
                "INSERT INTO memories(memory_id, user_id, scope, session_id, type, text, provenance_json, verification, "
                "confidence, content_sha256, created_at, updated_at, expires_at) VALUES "
                "(:memory_id,:user_id,:scope,:session_id,:type,:text,:provenance_json,:verification,:confidence,"
                ":content_sha256,:created_at,:updated_at,:expires_at)",
                record,
            )
            self.db.conn.commit()
        return self.get(memory_id, user_id)

    def _row(self, row) -> Dict[str, Any]:
        data = dict(row)
        data["provenance"] = json.loads(data.pop("provenance_json") or "{}")
        data["expired"] = bool(data["expires_at"] and data["expires_at"] <= self.clock())
        return data

    def get(self, memory_id: str, user_id: str) -> Dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM memories WHERE memory_id=? AND (user_id=? OR scope='global') AND deleted=0",
            (memory_id, user_id),
        ).fetchone()
        if row is None:
            raise NotFound("memory item not found")
        return self._row(row)

    def list(self, user_id: str, *, session_id: Optional[str] = None, include_global: bool = True) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM memories WHERE deleted=0 AND (user_id=? OR (scope='global' AND ?)) ORDER BY created_at",
            (user_id, 1 if include_global else 0),
        ).fetchall()
        out = []
        for row in rows:
            item = self._row(row)
            if item["expired"]:
                continue
            if item["scope"] == "session" and item["session_id"] != session_id and session_id is not None:
                continue
            out.append(item)
        return out

    def delete(self, memory_id: str, user_id: str) -> bool:
        item = self.get(memory_id, user_id)
        if item["scope"] == "global":
            raise ValidationError("shared memory is read-only here; remove it at its source and re-import")
        with self.db.lock:
            self.db.execute("UPDATE memories SET deleted=1, updated_at=? WHERE memory_id=?", (self.clock(), memory_id))
            self.db.conn.commit()
        return True

    def relevant(self, user_id: str, query: str, *, session_id: Optional[str] = None, limit: int = 3) -> List[Dict[str, Any]]:
        """Lexical relevance over the caller's visible, unexpired items."""

        terms = content_terms(query)
        if not terms or limit <= 0:
            return []
        scored = []
        for item in self.list(user_id, session_id=session_id):
            if item["scope"] == "session" and item["session_id"] != session_id:
                continue
            item_terms = content_terms(item["text"])
            if not item_terms:
                continue
            hits = sum(1 for t in terms if any(term_matches(t, other) for other in item_terms))
            if hits:
                score = hits / len(set(terms))
                scored.append((round(score * (0.5 + 0.5 * item["confidence"]), 4), item["created_at"], item))
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        return [dict(item, relevance=score) for score, _, item in scored[:limit]]

    # ---------------------------------------------------------- shared import
    def upsert_shared(self, *, key: str, text: str, provenance: Dict[str, Any]) -> str:
        """Idempotent import of one shared record; returns imported|unchanged|updated."""

        self.guard(text)
        digest = _sha(" ".join(text.split()))
        existing = [
            row
            for row in self.db.execute(
                "SELECT * FROM memories WHERE user_id=? AND scope='global' AND deleted=0", (SHARED_OWNER,)
            ).fetchall()
            if json.loads(row["provenance_json"] or "{}").get("key") == key
        ]
        if existing and existing[0]["content_sha256"] == digest:
            return "unchanged"
        if existing:
            with self.db.lock:
                for row in existing:
                    self.db.execute("UPDATE memories SET deleted=1, updated_at=? WHERE memory_id=?", (self.clock(), row["memory_id"]))
                self.db.conn.commit()
        self.add(
            SHARED_OWNER,
            text[:1000],
            type="shared",
            scope="global",
            provenance={**provenance, "key": key},
            verification="unverified",
            confidence=0.5,
        )
        return "updated" if existing else "imported"
