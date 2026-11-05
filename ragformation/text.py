"""Text primitives shared by loaders, chunking, retrieval and evaluation.

Kept in one module so tokenisation is *identical* everywhere: a mismatch between
the chunker's idea of a word and BM25's idea of a word silently degrades recall.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable, List

#: Word tokens. Deliberately keeps identifiers, versions, paths and percentages
#: intact ("p95", "OLLAMA_HOST", "12.5%", "v1.2.3") because exact-token queries
#: are exactly where lexical retrieval earns its keep.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-_./:%]*")

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")

STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those of for to in on at by with from
    is are was were be been being do does did done have has had having it its their there
    we you they he she i me my our your us them as not no nor so such very can could should
    would may might must will shall about into over under between during after before while
    what when where which who whom whose how why please tell explain describe give show
    """.split()
)


def normalize_text(raw: str) -> str:
    """Unicode NFC, unified newlines, no trailing spaces, at most one blank line.

    Deliberately conservative: punctuation, casing, code, formulas and table
    pipes are preserved. Destructive normalisation makes retrieval worse and
    makes citations unfaithful to the source.
    """
    text = unicodedata.normalize("NFC", raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens. The single source of truth for tokenisation."""
    return _TOKEN_RE.findall(text.lower())


def content_terms(text: str) -> List[str]:
    """Tokens minus stopwords and single characters (used by coverage scoring)."""
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


_STEM_SUFFIXES = (
    "ations", "ation", "ments", "ment", "ions", "ion", "ings", "ing",
    "edly", "ies", "ied", "es", "ed", "ly", "s",
)


def stem(token: str) -> str:
    """Very light suffix stemming, applied identically to queries and passages.

    Not a linguistics engine: it exists so "deleted"/"deletion" or
    "backups"/"backup" line up during coverage scoring. Both sides are stemmed,
    so consistency matters more than grammatical correctness.
    """
    if len(token) <= 5:
        return token
    for suffix in _STEM_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def term_matches(left: str, right: str) -> bool:
    """Prefix-tolerant term equality ("rollback" ~ "roll", "deleted" ~ "deletion").

    Keys are kept short on purpose (>= 4 shared characters) and the comparison is
    one-directional: it must not equate "log" with "login" or "data" with "date".
    """
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return len(short) >= 4 and long.startswith(short)


class TextSignals:
    """Pre-computed lookup sets for one passage.

    Matching a query term against a passage naively is O(terms x tokens); with a
    few dozen candidates per query that shows up in latency. This builds the
    lookup sets once (per chunk, cached by the retriever) so each term check is a
    handful of set lookups.
    """

    __slots__ = ("raw", "stems", "prefixes")

    def __init__(self, tokens: Iterable[str]) -> None:
        self.raw = set(tokens)
        self.stems = {stem(token) for token in self.raw}
        self.prefixes = {
            token[:length] for token in self.raw for length in range(4, len(token) + 1)
        }

    def has_exact(self, word: str) -> bool:
        """Exact token or stem match only — used for compound-word joins."""
        return word in self.raw or stem(word) in self.stems

    def matches(self, term: str) -> bool:
        if term in self.raw or stem(term) in self.stems:
            return True
        if len(term) < 4:
            return False
        if term in self.prefixes:  # term is a prefix of a passage token
            return True
        # Some passage token is a prefix of term ("delet" vs "deletion").
        for length in range(4, len(term) + 1):
            if term[:length] in self.raw:
                return True
        return False


def matched_terms_signals(query_terms: Iterable[str], signals: TextSignals) -> set:
    terms = list(dict.fromkeys(query_terms))
    found = {term for term in terms if signals.matches(term)}
    if len(terms) > 1:
        for first in terms:
            for second in terms:
                if first == second:
                    continue
                joined = first + second
                # Exact only: a prefix match here would join unrelated words
                # ("audit" + "logs" would "match" any token starting with audit).
                if signals.has_exact(joined):
                    found.add(first)
                    found.add(second)
    return found


def matched_terms(query_terms: Iterable[str], text_terms: Iterable[str]) -> set:
    """Query terms that the passage covers, tolerating stems and word splitting.

    Handles "deleted" vs "deletion" (stem), "deployment" vs "deploy" (stem +
    prefix) and "roll back" vs "rollback" (adjacent-term concatenation, checked
    in both orders so it does not depend on set iteration order).
    """
    return matched_terms_signals(query_terms, TextSignals(text_terms))


def approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token).

    It is an *estimate* and is labelled as such everywhere it is shown. When a
    provider reports real token usage, that number wins and is stored separately.
    """
    return max(1, (len(text) + 3) // 4)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_hash(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def split_sentences(text: str) -> List[str]:
    """Sentence split that survives abbreviations reasonably well.

    Used for citation granularity and for the extractive fallback generator, so
    it only has to be *useful*, not perfect.
    """
    parts = _SENTENCE_RE.split(text.strip())
    out: List[str] = []
    for part in parts:
        cleaned = part.strip()
        if cleaned:
            out.append(cleaned)
    return out


def truncate(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def dedupe_preserving_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def safe_display_name(name: str, limit: int = 120) -> str:
    """Strip directory components and control characters from a user filename."""
    base = name.replace("\\", "/").split("/")[-1]
    base = "".join(ch for ch in base if ch.isprintable())
    base = base.strip().strip(".") or "untitled"
    return base[:limit]
