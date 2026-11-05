"""Grounded generation providers.

* ``ollama``      Local neural generation (default ``llama3.2:3b``). Streams
                  token-by-token so TTFT and ITL are measured, not simulated.
* ``openai``      Any OpenAI-compatible ``/chat/completions`` endpoint.
* ``extractive``  Deterministic, dependency-free fallback. It quotes the
                  best-matching evidence sentences and binds each one to its
                  chunk id, so citations are correct *by construction*. It is
                  labelled ``extractive-synthesis-v1`` everywhere and never
                  pretends to be a language model.

Provider routing is ``auto`` by default: Ollama when it is reachable with the
configured model, else the extractive fallback. A provider that is configured
explicitly and then fails raises; the engine surfaces the failure and records
which fallback (if any) produced the answer.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from .config import Settings
from .embeddings import _get_json
from .errors import ProviderError, ProviderUnavailable
from .guardrails import redact_credentials
from .metrics import StreamTiming
from .prompts import INSUFFICIENT_EVIDENCE, build_messages
from .text import (
    TextSignals,
    approx_tokens,
    content_terms,
    matched_terms,
    split_sentences,
    tokenize,
)

EXTRACTIVE_ID = "extractive-synthesis-v1"
TokenCallback = Optional[Callable[[str], None]]


@dataclass
class GenerationOutput:
    text: str
    provider: str
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    tokens_estimated: bool = False
    finish_reason: str = "stop"
    timing: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "tokensEstimated": self.tokens_estimated,
            "finishReason": self.finish_reason,
            "timing": self.timing,
        }


class GenerationProvider(Protocol):
    name: str
    model: str
    kind: str

    def availability(self) -> Dict[str, Any]: ...

    def generate(
        self,
        question: str,
        evidence: Sequence[Dict[str, Any]],
        *,
        temperature: float,
        top_p: float,
        max_tokens: int,
        timeout: float,
        on_token: TokenCallback = None,
        history: Sequence[Dict[str, str]] = (),
    ) -> GenerationOutput: ...


# ------------------------------------------------------------------- streaming
def _stream_json_lines(
    url: str,
    payload: Dict[str, Any],
    timeout: float,
    api_key: Optional[str] = None,
):
    headers = {"Content-Type": "application/json", "Accept": "application/x-ndjson"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # pragma: no cover
            pass
        raise ProviderError(f"provider returned HTTP {exc.code}", hint=detail or exc.reason) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderUnavailable(
            "generation provider is not reachable",
            hint=f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}",
        ) from exc
    try:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                yield line
    finally:
        response.close()


class OllamaGenerator:
    name = "ollama"
    kind = "neural"

    def __init__(self, model: str, host: str) -> None:
        self.model = model
        self.host = host.rstrip("/")

    def availability(self) -> Dict[str, Any]:
        try:
            tags = _get_json(f"{self.host}/api/tags", timeout=3.0)
        except (ProviderError, ProviderUnavailable) as exc:
            return {"ok": False, "provider": self.name, "detail": str(exc)[:200]}
        models = [m.get("name", "") for m in tags.get("models", [])]
        present = any(m.split(":")[0] == self.model.split(":")[0] for m in models)
        return {
            "ok": present,
            "provider": self.name,
            "model": self.model,
            "availableModels": models[:20],
            "hint": None if present else f"run: ollama pull {self.model}",
        }

    def generate(
        self,
        question: str,
        evidence: Sequence[Dict[str, Any]],
        *,
        temperature: float,
        top_p: float,
        max_tokens: int,
        timeout: float,
        on_token: TokenCallback = None,
        history: Sequence[Dict[str, str]] = (),
    ) -> GenerationOutput:
        messages = build_messages(question, evidence, history=history)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
        }
        timing = StreamTiming()
        parts: List[str] = []
        prompt_tokens: Optional[int] = None
        completion_tokens: Optional[int] = None
        finish = "stop"

        for line in _stream_json_lines(f"{self.host}/api/chat", payload, timeout):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("error"):
                raise ProviderError("ollama reported an error", hint=str(event["error"])[:200])
            content = (event.get("message") or {}).get("content", "")
            if content:
                parts.append(content)
                timing.mark(content)
                if on_token:
                    on_token(content)
            if event.get("done"):
                prompt_tokens = event.get("prompt_eval_count")
                completion_tokens = event.get("eval_count")
                reason = event.get("done_reason")
                if reason:
                    finish = reason

        text = "".join(parts).strip()
        if not text:
            raise ProviderError("ollama returned an empty completion")
        return GenerationOutput(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_estimated=completion_tokens is None,
            finish_reason=finish,
            timing=timing.as_dict(),
        )


class OpenAIGenerator:
    name = "openai"
    kind = "neural"

    def __init__(self, model: str, base_url: str, api_key: Optional[str], provider_name: str = "openai") -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.name = provider_name

    def availability(self) -> Dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "provider": self.name, "detail": "OPENAI_API_KEY not set"}
        return {"ok": True, "provider": self.name, "model": self.model}

    def generate(
        self,
        question: str,
        evidence: Sequence[Dict[str, Any]],
        *,
        temperature: float,
        top_p: float,
        max_tokens: int,
        timeout: float,
        on_token: TokenCallback = None,
        history: Sequence[Dict[str, str]] = (),
    ) -> GenerationOutput:
        if not self.api_key:
            raise ProviderUnavailable("OPENAI_API_KEY is not set")
        messages = build_messages(question, evidence, history=history)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        timing = StreamTiming()
        parts: List[str] = []
        prompt_tokens: Optional[int] = None
        completion_tokens: Optional[int] = None
        finish = "stop"

        for line in _stream_json_lines(
            f"{self.base_url}/chat/completions", payload, timeout, self.api_key
        ):
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("error"):
                raise ProviderError("provider reported an error", hint=str(event["error"])[:200])
            choices = event.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                content = delta.get("content") or ""
                if content:
                    parts.append(content)
                    timing.mark(content)
                    if on_token:
                        on_token(content)
                if choices[0].get("finish_reason"):
                    finish = choices[0]["finish_reason"]
            usage = event.get("usage") or {}
            if usage:
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                completion_tokens = usage.get("completion_tokens", completion_tokens)

        text = "".join(parts).strip()
        if not text:
            raise ProviderError("provider returned an empty completion")
        return GenerationOutput(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_estimated=completion_tokens is None,
            finish_reason=finish,
            timing=timing.as_dict(),
        )


class ExtractiveGenerator:
    """Deterministic grounded answer: evidence sentences, each with its marker.

    This is the fallback when no LLM is available. It cannot paraphrase, invent
    facts or follow injected instructions; it can only quote what retrieval
    found, which is exactly the behaviour a reviewer can verify.

    Selection is scored, not first-match: question terms are weighted by how
    rare they are *inside the retrieved evidence* (the topic word cannot
    discriminate between passages), the question's answer type (a duration, an
    owner) is required of the quoted sentence, and sentences that only restate
    the question are penalised. Structured ``record — field: value`` lines are
    quoted whole so the record's identity travels with the value.
    """

    name = "extractive"
    kind = "extractive"
    model = EXTRACTIVE_ID

    def availability(self) -> Dict[str, Any]:
        return {"ok": True, "provider": self.name, "model": self.model, "detail": "built-in"}

    def generate(
        self,
        question: str,
        evidence: Sequence[Dict[str, Any]],
        *,
        temperature: float,
        top_p: float,
        max_tokens: int,
        timeout: float,
        on_token: TokenCallback = None,
        history: Sequence[Dict[str, str]] = (),
    ) -> GenerationOutput:
        timing = StreamTiming()
        question_terms = list(dict.fromkeys(content_terms(question)))
        weights = _question_term_weights(question_terms, evidence)
        total_weight = sum(weights.values()) or 1.0
        shape = _answer_shape(question)

        scored: List[tuple[float, str, int]] = []
        for item in evidence[:5]:
            for sentence in _quotable_sentences(item["text"]):
                terms = set(content_terms(sentence))
                overlap = matched_terms(question_terms, terms)
                if not overlap:
                    continue
                mass = sum(weights.get(term, 1.0) for term in overlap)
                coverage = mass / total_weight
                fits = bool(shape and shape.search(sentence))
                # One matched term is usually a coincidence ("life" in "life of
                # the contract"), unless it is a rare term *and* the sentence
                # has the shape the question asks for (a duration, an owner).
                if len(overlap) == 1 and coverage < 0.34 and not fits:
                    continue
                score = coverage + 0.08 * min(4, len(overlap))
                if shape is not None:
                    if fits:
                        score += 0.55
                    elif shape.strict:
                        score -= 0.25
                # Identifiers named in the question (MACRO-BILLING-01, SEV-1,
                # p95) are the strongest possible signal.
                if any(re.search(r"\d", term) for term in overlap):
                    score += 0.35
                # A sentence built only from the question's own words restates
                # the topic instead of answering it ("Production deploys use the
                # blue/green model." for "when is a deploy promoted?").
                if _novel_terms(terms, question_terms) <= 2:
                    score -= 0.15
                scored.append((score, sentence, item["id"]))

        # Keep the strongest pick plus only the picks that are genuinely close
        # to it: three mediocre quotations read like a document dump.
        scored.sort(key=lambda row: -row[0])
        picks: List[str] = []
        seen: List[set] = []
        covered: set = set()
        for score, sentence, item_id in scored:
            if picks and score < scored[0][0] - 0.18:
                break
            if len(picks) >= 3:
                break
            signature = set(tokenize(sentence))
            if any(len(signature & other) / max(1, len(signature | other)) > 0.8 for other in seen):
                continue
            # A quote that adds no new content ("id: MACRO-AUTH-02" after the
            # title and steps) is filler: it lengthens the answer without adding
            # evidence, so it is dropped.
            if picks and len(signature - covered) < 3:
                continue
            seen.append(signature)
            covered |= signature
            picks.append(f"{sentence.rstrip()} [{item_id}]")

        if not picks:
            output = INSUFFICIENT_EVIDENCE
        else:
            output = " ".join(picks)
        for chunk in _chunks_for_stream(output):
            timing.mark(chunk)
            if on_token:
                on_token(chunk)
            time.sleep(0)  # keep the generator cooperative without faking latency
        return GenerationOutput(
            text=output,
            provider=self.name,
            model=self.model,
            prompt_tokens=approx_tokens(question),
            completion_tokens=approx_tokens(output),
            tokens_estimated=True,
            finish_reason="stop" if picks else "insufficient_evidence",
            timing=timing.as_dict(),
        )


def _question_term_weights(question_terms: Sequence[str], evidence: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Rarity of each question term *inside the retrieved evidence*.

    A term that appears in every retrieved passage is the topic, not the answer:
    "deploy" and "blue/green" are in the question and in the passage that merely
    restates it. A term confined to one passage ("promoted", "minutes") is the
    discriminating one. Weights are computed per query from the evidence in
    front of the generator, so nothing is tuned to a fixed corpus.
    """
    if not question_terms:
        return {}
    passages = [TextSignals(tokenize(str(item.get("text") or ""))) for item in evidence[:5]]
    total = max(1, len(passages))
    weights: Dict[str, float] = {}
    for term in question_terms:
        df = sum(1 for passage in passages if passage.matches(term))
        weights[term] = round(1.0 + math.log((total + 1.0) / (df + 0.5)), 4)
    return weights


def _novel_terms(sentence_terms: set, question_terms: Sequence[str]) -> int:
    """How many of the sentence's terms the question does not already contain."""
    novel = 0
    for term in sentence_terms:
        if not matched_terms([term], question_terms):
            novel += 1
    return novel


#: A *quantity* the answer must contain: a number with a unit, or a frequency
#: word. A bare digit is not a quantity — "SEV-1" and "svc-042" contain digits but
#: say nothing about duration, and treating them as a match let a sentence that
#: merely restates the question look like a temporal answer.
_UNIT = (
    r"(?:percent|days?|hours?|minutes?|mins?|seconds?|secs?|weeks?|months?|years?|"
    r"quarters?|ms|milliseconds?)"
)
_QUANTITY_RE = re.compile(
    rf"\b\d+(?:[.,]\d+)?[\s-]*(?:%|(?:business\s+|working\s+)?{_UNIT}\b)"
    rf"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|"
    rf"fifty|sixty|ninety|hundred)\s+(?:(?:business|working)\s+)?{_UNIT}\b"
    rf"|\b(?:daily|weekly|monthly|quarterly|annual(?:ly)?|hourly|yearly|half day|immediately)\b",
    re.I,
)

_ENTITY_RE = re.compile(
    r"\b[A-Za-z0-9]+-team\b|\b(?:owner|owned|oncall|team|squad|responsible)\b\s*[:=]", re.I
)


class _AnswerShape:
    """A sentence shape the question asks for, plus how strictly to enforce it.

    ``strict`` shapes (a magnitude was requested: "how long", "what is the
    window") also *penalise* sentences without the shape, because a sentence
    with more shared words but no number is almost always the topic restated.
    ``soft`` shapes (temporal "when ...", ownership) only reward a fitting
    sentence: penalising there would punish, for example, the procedural answer
    to "what happens when a break-glass account is used?".
    """

    __slots__ = ("pattern", "kind", "strict")

    def __init__(self, pattern, kind: str, strict: bool) -> None:
        self.pattern = pattern
        self.kind = kind
        self.strict = strict

    def search(self, sentence: str):
        return self.pattern.search(sentence)


_TEMPORAL_RE = re.compile(
    _QUANTITY_RE.pattern + r"|\b(?:immediately|immediate|within|after|following|pending|until)\b",
    re.I,
)


def _answer_shape(question: str) -> Optional[_AnswerShape]:
    """The shape an answer sentence should have, inferred from the question.

    Extractive answers otherwise drift towards the sentence with the most shared
    words, which is usually the definition that restates the question.
    """
    if re.search(
        r"\b(?:how (?:long|many|much|quickly|fast|soon|often)|window|period|deadline|"
        r"ttl|retention|limit|duration|budget|quota|timeout|interval|cost|rate|sla|slo)\b",
        question,
        re.I,
    ):
        return _AnswerShape(_QUANTITY_RE, "magnitude", True)
    if re.search(r"\bwhen\b", question, re.I):
        return _AnswerShape(_TEMPORAL_RE, "temporal", False)
    if re.search(r"\b(who|whom|which team|which squad|owner|owns|responsible|oncall)\b", question, re.I):
        return _AnswerShape(_ENTITY_RE, "owner", False)
    return None


_RECORD_LINE_RE = re.compile(
    r"^(?:[-*+]\s*)?(?P<label>[^—]{1,80}?)\s+—\s+(?P<field>[A-Za-z0-9_. ()]{1,40}):\s+(?P<value>.+)$"
)


def _quotable_sentences(text: str) -> List[str]:
    """Evidence that can be quoted verbatim, in document order.

    Two shapes are quotable:

    * prose sentences (headings and label lines are dropped — a heading is not
      an answer), and
    * structured ``record — field: value`` lines, kept **whole**. Sentence
      splitting inside such a line would strip the record label from every
      sentence after the first, and an unattributed value ("5 business days")
      is exactly the kind of unfalsifiable quote this system must not produce.
    """
    kept: List[str] = []
    records: List[tuple] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        record = _RECORD_LINE_RE.match(stripped)
        if record:
            value = " ".join(record.group("value").split())
            label = " ".join(record.group("label").split()).lstrip("-*+ ").strip()
            field = record.group("field").strip()
            records.append((len(kept), f"{label} — {field}: {value}"))
            continue
        words = stripped.split()
        # Headings and labels are not answer sentences.
        if len(words) <= 7 and not stripped.endswith((".", "!", "?", ";", ":")):
            continue
        # Pipe-joined table rows read badly when quoted verbatim; they remain
        # fully available to a language-model provider.
        if stripped.count("|") >= 2:
            continue
        kept.append(stripped)

    sentences = split_sentences(" ".join(kept))
    output: List[str] = []
    for sentence in sentences:
        cleaned = sentence.strip()
        # Strip a leading list marker or table pipe artefact.
        cleaned = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", cleaned)
        if len(cleaned.split()) >= 6 and cleaned[:1].isupper():
            output.append(cleaned)

    # Re-interleave the record lines with the prose they sat between.
    for position, record_text in reversed(records):
        output.insert(min(position, len(output)), record_text)
    return output


def _chunks_for_stream(text: str, size: int = 24) -> List[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


# ------------------------------------------------------------------- factories
def build_generator(settings: Settings) -> GenerationProvider:
    """Resolve the configured provider; ``auto`` prefers a local Ollama model."""
    import os

    choice = settings.llm_provider

    if choice in {"auto", "ollama"}:
        candidate = OllamaGenerator(settings.llm_model, settings.ollama_host)
        status = candidate.availability()
        if status.get("ok"):
            return candidate
        if choice == "ollama":
            raise ProviderUnavailable(
                "Ollama is configured as the LLM provider but is not usable",
                hint=str(status.get("hint") or status.get("detail") or "start Ollama"),
            )

    if choice in {"auto", "openai"}:
        api_key = os.environ.get("OPENAI_API_KEY")
        if choice == "openai" or api_key:
            model = os.environ.get("OPENAI_LLM_MODEL", "gpt-4o-mini") if choice == "auto" else settings.llm_model
            return OpenAIGenerator(
                model=model,
                base_url=settings.openai_base_url,
                api_key=api_key,
                provider_name="openai",
            )

    return ExtractiveGenerator()


def describe(provider: GenerationProvider) -> Dict[str, Any]:
    return {
        "provider": provider.name,
        "model": provider.model,
        "kind": provider.kind,
        "neural": provider.kind == "neural",
        "note": (
            "Language model generates the answer from the retrieved evidence."
            if provider.kind == "neural"
            else "No language model is used: sentences are quoted from retrieved evidence and "
            "bound to their chunk ids. Configure Ollama for generated prose."
        ),
    }


def redact_generation_output(text: str) -> tuple[str, int]:
    return redact_credentials(text)
