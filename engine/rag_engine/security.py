"""Untrusted-content boundary.

Retrieved text is **data**, never instructions. ``scan_for_injection`` detects
instruction-like patterns in retrieved passages and returns a structured
finding. The pipeline quarantines flagged chunks: they stay visible in the
technical view as evidence-of-attack but are excluded from the generation
context, and the policy line recorded in the trace explains why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Sequence

from .chunking import Chunk

# Deliberately conservative patterns; these are heuristics, not a classifier.
PATTERNS: Dict[str, str] = {
    "instruction_override": r"\b(ignore|disregard|forget)\b[^.]{0,40}\b(previous|prior|above|earlier|all)\b[^.]{0,20}\b(instructions?|prompts?|rules?)\b",
    "role_hijack": r"\byou are now\b|\bact as\b[^.]{0,30}\b(system|admin|developer)\b|\bnew system prompt\b",
    "secret_exfiltration": r"\b(reveal|print|output|leak|send)\b[^.]{0,40}\b(api[_ -]?key|token|password|secret|credentials?)\b",
    "tool_abuse": r"\b(call|invoke|execute)\b[^.]{0,30}\b(tool|function|shell|command)\b",
    "answer_forcing": r"\b(respond|answer)\b[^.]{0,30}\b(only|exactly)\b[^.]{0,20}\bwith\b",
}

_COMPILED = {name: re.compile(pat, re.IGNORECASE) for name, pat in PATTERNS.items()}


@dataclass
class InjectionFinding:
    rule_id: str
    chunk_id: str
    source_id: str
    excerpt: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


def scan_text(text: str) -> List[str]:
    return [name for name, rx in _COMPILED.items() if rx.search(text)]


def scan_for_injection(chunks: Sequence[Chunk], max_excerpt: int = 160) -> List[InjectionFinding]:
    findings: List[InjectionFinding] = []
    for chunk in chunks:
        for rule in scan_text(chunk.text):
            rx = _COMPILED[rule]
            match = rx.search(chunk.text)
            start = max(0, (match.start() if match else 0) - 30)
            excerpt = chunk.text[start : start + max_excerpt]
            findings.append(
                InjectionFinding(
                    rule_id=rule,
                    chunk_id=chunk.chunk_id,
                    source_id=chunk.source_id,
                    excerpt=excerpt,
                )
            )
    return findings


def redact_instruction_spans(text: str) -> str:
    """Neutralise instruction-like spans for safe display in the context panel."""
    safe = text
    for rx in _COMPILED.values():
        safe = rx.sub("[quarantined instruction-like span]", safe)
    return safe
