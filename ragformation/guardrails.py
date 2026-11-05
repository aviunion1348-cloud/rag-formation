"""Guardrails for untrusted content and for model output.

Two boundaries, both enforced in code rather than by asking the model nicely:

1. **Retrieved text is data.** Passages matching instruction-injection patterns
   are quarantined: kept in the trace as evidence-of-attack, excluded from the
   generation context. The prompt also wraps every passage in an ``<evidence>``
   delimiter and states that instructions inside it have no authority.
2. **Model output is untrusted too.** Citations are re-derived server-side,
   unknown citation markers are stripped, URLs that do not appear in the
   evidence are neutralised, and credential-shaped strings are redacted before
   anything is shown or logged.

These are heuristics with honest limits: pattern matching will not catch a
novel, well-written injection. They raise the cost of the obvious attacks and
they are covered by regression tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

INJECTION_RULES: Dict[str, str] = {
    # "ignore all previous instructions", "disregard the above rules", ...
    "instruction_override": (
        r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,60}"
        r"\b(previous|prior|above|earlier|all|any|system|developer)\b[^.\n]{0,30}"
        r"\b(instruction|instructions|prompt|prompts|rule|rules|guideline|guidelines)\b"
    ),
    # "you are now DAN", "act as the system administrator", "new system prompt"
    "role_hijack": (
        r"\byou are now\b|\bfrom now on you\b|\bact as\b[^.\n]{0,40}"
        r"\b(system|developer|admin|administrator|root)\b|\bnew system prompt\b"
    ),
    # "reveal your api key", "print the token", ...
    "secret_exfiltration": (
        r"\b(reveal|print|output|show|leak|send|exfiltrate|share)\b[^.\n]{0,50}"
        r"\b(api[ _-]?key|token|password|passphrase|secret|credential|credentials|"
        r"system prompt|hidden prompt)\b"
    ),
    # "call the shell tool", "execute this command", ...
    "tool_abuse": (
        r"\b(call|invoke|execute|run)\b[^.\n]{0,30}\b(tool|function|shell|command|script|"
        r"terminal)\b"
    ),
    # "answer only with YES", ...
    "answer_forcing": (
        r"\b(respond|answer|reply)\b[^.\n]{0,40}\b(only|exactly|solely)\b[^.\n]{0,25}\bwith\b"
    ),
    # data exfiltration via markdown images / links
    "markdown_exfiltration": r"!\[[^\]]{0,40}\]\(\s*https?://",
}

_COMPILED = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in INJECTION_RULES.items()}

_CREDENTIAL_PATTERNS: Sequence[Tuple[str, re.Pattern]] = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{12,}\b")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE)),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("long_hex_token", re.compile(r"\b[a-f0-9]{32,}\b", re.IGNORECASE)),
)

_URL_RE = re.compile(r"https?://[^\s<>)\"'\]]+", re.IGNORECASE)


@dataclass
class Finding:
    rule_id: str
    excerpt: str
    start: int
    end: int

    def to_dict(self) -> Dict[str, Any]:
        return {"ruleId": self.rule_id, "excerpt": self.excerpt}


def scan_text(text: str, *, window: int = 90) -> List[Finding]:
    findings: List[Finding] = []
    for rule_id, pattern in _COMPILED.items():
        for match in pattern.finditer(text):
            start = max(0, match.start() - 20)
            excerpt = " ".join(text[start : match.end() + window].split())[:220]
            findings.append(Finding(rule_id, excerpt, match.start(), match.end()))
    return findings


def is_suspicious(text: str) -> bool:
    return bool(scan_text(text))


def redact_credentials(text: str) -> Tuple[str, int]:
    """Replace credential-shaped strings. Returns (redacted, count)."""
    redacted = text
    count = 0
    for _name, pattern in _CREDENTIAL_PATTERNS:
        redacted, hits = pattern.subn("[redacted-secret]", redacted)
        count += hits
    return redacted, count


def find_urls(text: str) -> List[str]:
    return [match.group(0).rstrip(".,;:)") for match in _URL_RE.finditer(text)]


def strip_unverified_urls(answer: str, evidence_text: str) -> Tuple[str, List[str]]:
    """Neutralise URLs the model produced that are absent from the evidence.

    A fabricated link in a cited answer is worse than no link: it looks
    verifiable. Legitimate evidence URLs are preserved.
    """
    evidence_urls = {url.lower() for url in find_urls(evidence_text)}
    removed: List[str] = []

    def replace(match: re.Match) -> str:
        url = match.group(0).rstrip(".,;:)")
        if url.lower() in evidence_urls:
            return match.group(0)
        removed.append(url)
        return "[unverified-link-removed]"

    return _URL_RE.sub(replace, answer), removed


def strip_invalid_citations(answer: str, valid_ids: Iterable[int]) -> Tuple[str, List[int]]:
    """Remove citation markers that do not map to a retrieved passage."""
    allowed = set(valid_ids)
    invalid: List[int] = []

    def replace(match: re.Match) -> str:
        raw = match.group(1)
        try:
            number = int(raw)
        except ValueError:
            number = -1
        if number in allowed:
            return match.group(0)
        if number >= 0:
            invalid.append(number)
        return ""

    cleaned = re.sub(r"\[(\d{1,2})\]", replace, answer)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned).strip()
    return cleaned, invalid


def looks_like_refusal(answer: str) -> bool:
    normalized = answer.strip().lower()
    return normalized in {"insufficient_evidence", "insufficient evidence"} or normalized.startswith(
        "insufficient_evidence"
    )


_META_LEAK_MARKERS = (
    "system prompt",
    "here is my prompt",
    "my instructions are",
    "as an ai language model, i cannot access",
)


def output_warnings(answer: str, max_chars: int = 6000) -> List[str]:
    """Non-blocking quality flags attached to every answer payload."""
    warnings: List[str] = []
    if not answer.strip():
        warnings.append("empty_answer")
    if len(answer) > max_chars:
        warnings.append("answer_truncated_by_policy" if len(answer) > max_chars * 2 else "answer_long")
    lowered = answer.lower()
    for marker in _META_LEAK_MARKERS:
        if marker in lowered:
            warnings.append("possible_meta_disclosure")
            break
    if any(rule in {"instruction_override", "role_hijack"} for rule in [f.rule_id for f in scan_text(answer)]):
        warnings.append("answer_contains_instruction_like_text")
    return sorted(set(warnings))
