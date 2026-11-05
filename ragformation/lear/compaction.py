"""Deterministic, versioned conversation compaction.

When a session's history exceeds its token budget, turns older than the most
recent ``keep_recent`` messages are folded into a summary. The summary is built
mechanically — no model call — so it is reproducible and testable:

* each folded exchange keeps the question (truncated), the answer status, the
  first answer sentence and the documents it cited;
* identifiers (``code spans``, ``snake_case``, ``CamelCase``, dotted names,
  file paths, ``UPPER_CASE`` settings, numbers with units) are collected and
  kept verbatim, because paraphrase destroys exactly those tokens;
* sentences with negations or constraints ("not", "never", "only", "must",
  "without"…) from the *user* are kept verbatim — dropping a "not" inverts a
  requirement.

Idempotence: compaction is a pure function of (previous summary, folded
messages). Re-running it on the same inputs returns the same bytes, and running
it when nothing new is beyond the boundary is a no-op. The summary is capped;
when the cap is hit the oldest exchange lines are dropped first, identifiers
and constraints last.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..text import approx_tokens, split_sentences

COMPACTION_VERSION = "lear-compaction-v1"
SUMMARY_TOKEN_CAP = 360

_IDENTIFIER_PATTERNS = (
    re.compile(r"`([^`\n]{2,80})`"),
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+\b"),  # snake_case / UPPER_CASE
    re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]+\b"),  # camelCase
    re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]+\b"),  # CamelCase
    re.compile(r"\b[\w-]+(?:/[\w.-]+)+\.\w+\b"),  # paths with an extension
    re.compile(r"\b[\w-]+\.(?:py|md|json|yaml|yml|toml|ts|tsx|sh|txt|cfg|ini|env)\b"),
    re.compile(r"(?<![\w/])/[a-z0-9_{}\-/.]+"),  # URL paths like /api/v1/chat
    re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|seconds|minutes|min|hours|h|days|MB|GB|KB|%)\b"),
)
_NEGATION = re.compile(
    r"\b(not|never|no|don't|do not|doesn't|cannot|can't|without|only|must|avoid|except|unless)\b",
    re.IGNORECASE,
)
_CITE_REF = re.compile(r"\b[\w./-]+\.md\b")


@dataclass
class CompactionResult:
    summary: str
    version: str
    through_message_id: int
    tokens_before: int
    tokens_after: int
    latency_ms: float
    folded: int
    kept: List[Dict[str, Any]] = field(default_factory=list)
    changed: bool = True

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.summary.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "throughMessageId": self.through_message_id,
            "tokensBefore": self.tokens_before,
            "tokensAfter": self.tokens_after,
            "latencyMs": self.latency_ms,
            "folded": self.folded,
            "changed": self.changed,
            "summarySha256": self.sha256,
        }


def extract_identifiers(text: str) -> List[str]:
    found: List[str] = []
    for pattern in _IDENTIFIER_PATTERNS:
        for match in pattern.finditer(text or ""):
            value = (match.group(1) if match.groups() else match.group(0)).strip()
            if 2 <= len(value) <= 80 and value not in found:
                found.append(value)
    return found


def constraint_sentences(text: str) -> List[str]:
    return [s.strip() for s in split_sentences(text or "") if _NEGATION.search(s)]


def _first_sentence(text: str, limit: int = 220) -> str:
    sentences = split_sentences(text or "")
    head = sentences[0] if sentences else (text or "")
    head = " ".join(head.split())
    return head if len(head) <= limit else head[: limit - 1] + "…"


def _parse(summary: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {"exchanges": [], "identifiers": [], "constraints": []}
    current = None
    for line in (summary or "").splitlines():
        if line.startswith("## "):
            key = line[3:].strip().lower()
            current = key if key in sections else None
            continue
        if current and line.startswith("- "):
            sections[current].append(line[2:])
    return sections


def _render(sections: Dict[str, List[str]]) -> str:
    out = [f"# Conversation summary ({COMPACTION_VERSION})"]
    for key in ("exchanges", "identifiers", "constraints"):
        if sections[key]:
            out.append(f"## {key}")
            out.extend(f"- {item}" for item in sections[key])
    return "\n".join(out)


def _cap(sections: Dict[str, List[str]], cap_tokens: int) -> Dict[str, List[str]]:
    # Oldest exchanges go first; identifiers/constraints are trimmed last.
    while approx_tokens(_render(sections)) > cap_tokens:
        if len(sections["exchanges"]) > 1:
            sections["exchanges"].pop(0)
        elif len(sections["identifiers"]) > 12:
            sections["identifiers"].pop(0)
        elif len(sections["constraints"]) > 4:
            sections["constraints"].pop(0)
        elif sections["exchanges"]:
            sections["exchanges"].pop(0)
        else:
            break
    return sections


def compact(
    messages: Sequence[Dict[str, Any]],
    *,
    previous_summary: str = "",
    previous_through: int = 0,
    keep_recent: int = 4,
    budget_tokens: int = 900,
    cap_tokens: int = SUMMARY_TOKEN_CAP,
    force: bool = False,
) -> CompactionResult:
    """Fold messages older than the last ``keep_recent`` into the summary.

    ``messages`` are the session's full message rows in order (each with
    ``message_id``, ``role``, ``content``, optional ``status``/``citations``).
    Only rows with ``message_id > previous_through`` are considered new.
    """

    started = time.perf_counter()
    pending = [m for m in messages if int(m.get("message_id", 0)) > previous_through]
    tokens_before = (approx_tokens(previous_summary) if previous_summary else 0) + sum(
        approx_tokens(m.get("content", "")) for m in pending
    )
    keep = list(pending[-keep_recent:]) if keep_recent > 0 else []
    fold = list(pending[: max(0, len(pending) - keep_recent)])

    if not fold or (tokens_before <= budget_tokens and not force):
        return CompactionResult(
            summary=previous_summary,
            version=COMPACTION_VERSION,
            through_message_id=previous_through,
            tokens_before=tokens_before,
            tokens_after=tokens_before,
            latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
            folded=0,
            kept=pending,
            changed=False,
        )

    sections = _parse(previous_summary)
    pending_question: Optional[Dict[str, Any]] = None
    for message in fold:
        content = message.get("content", "") or ""
        for ident in extract_identifiers(content):
            if ident not in sections["identifiers"]:
                sections["identifiers"].append(ident)
        if message.get("role") == "user":
            for sentence in constraint_sentences(content):
                sentence = " ".join(sentence.split())[:240]
                if sentence not in sections["constraints"]:
                    sections["constraints"].append(sentence)
            pending_question = message
            continue
        question = _first_sentence(pending_question.get("content", ""), 160) if pending_question else "(no question)"
        cited = []
        for citation in message.get("citations") or []:
            label = citation.get("document") or citation.get("sourceTitle") or ""
            if label and label not in cited:
                cited.append(label)
        cited += [ref for ref in _CITE_REF.findall(content) if ref not in cited]
        status = message.get("status") or "unknown"
        line = f"Q: {question} → [{status}] {_first_sentence(content)}"
        if cited:
            line += f" (cited: {', '.join(cited[:4])})"
        sections["exchanges"].append(line)
        pending_question = None
    if pending_question is not None:
        sections["exchanges"].append(f"Q: {_first_sentence(pending_question.get('content', ''), 160)} → (unanswered)")

    sections = _cap(sections, cap_tokens)
    summary = _render(sections)
    through = int(fold[-1]["message_id"])
    tokens_after = approx_tokens(summary) + sum(approx_tokens(m.get("content", "")) for m in keep)
    return CompactionResult(
        summary=summary,
        version=COMPACTION_VERSION,
        through_message_id=through,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
        folded=len(fold),
        kept=keep,
        changed=summary != previous_summary or through != previous_through,
    )
