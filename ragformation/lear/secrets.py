"""Secret detection shared by the memory guard and ``lear docs verify``.

Deliberately conservative: a false positive blocks a memory write or flags a
doc line for review; a false negative could persist a credential. Patterns
match credential *shapes* (prefixes, key/value assignments, PEM headers),
never specific values. Placeholders such as ``DEEPSEEK_API_KEY=`` (empty) or
``<your-key>`` do not match.
"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

SECRET_PATTERNS: Sequence[Tuple[str, re.Pattern]] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE)),
    (
        "assigned_secret",
        # KEY=value / "password": "value" with a non-placeholder value of 8+ chars
        re.compile(
            r"(?i)\b[A-Z0-9_]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|token)[A-Z0-9_]*"
            r"\s*[:=]\s*[\"']?(?!<|\$\{|\*{3}|x{4}|your|changeme|example|placeholder|redacted)"
            r"[A-Za-z0-9/+_.:@-]{8,}"
        ),
    ),
)


def find_secrets(text: str) -> List[Tuple[str, str]]:
    """Return ``(rule, masked_excerpt)`` for each credential-shaped match."""

    hits: List[Tuple[str, str]] = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text or ""):
            raw = match.group(0)
            masked = raw[:6] + "…" + f"[{len(raw)} chars]"
            hits.append((name, masked))
    return hits


def contains_secret(text: str) -> bool:
    return bool(find_secrets(text))
