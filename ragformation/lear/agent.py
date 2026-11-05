"""The Lear agent service layer.

``LearAgent.ask`` is the single entry point used by ``lear ask``, ``lear chat``,
the evaluation runner and the benchmark harness, so limits and statuses are
identical everywhere:

1. validate the input size and admit the request through the rate limiter;
2. load (or create) the caller's session, compact old turns if the history
   is over budget, and keep the most recent turns verbatim;
3. rewrite an anaphoric follow-up into a self-contained *retrieval* query
   (the model still sees the user's original words; both go in the trace);
4. pick relevant memory items (context only, never evidence);
5. call the engine with the versioned Lear system prompt, timestamping every
   streamed token;
6. map the engine outcome to GROUNDED / PARTIAL / ABSTAINED / CONFLICTED /
   ERROR, attach document + source-line citations from the chunk manifest,
   and persist both turns.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..engine import RAGEngine
from ..errors import ProviderError, ProviderUnavailable, RagError, RateLimited, ValidationError
from ..text import approx_tokens, content_terms, term_matches, tokenize
from .compaction import COMPACTION_VERSION, compact, extract_identifiers
from .indexer import check_index_fingerprint, load_manifest, load_pin
from .memory import MemoryStore
from .prompts import LEAR_PROMPT_VERSION, render_system_prompt
from .ratelimit import LearRateLimiter
from .sessions import AgentDB, SessionStore
from .settings import LearSettings
from .stats import describe

STATUSES = ("GROUNDED", "PARTIAL", "ABSTAINED", "CONFLICTED", "ERROR")
EXIT_CODES = {"GROUNDED": 0, "PARTIAL": 4, "CONFLICTED": 4, "ABSTAINED": 3, "ERROR": 1}

_ANAPHORA = re.compile(
    r"\b(it|its|it's|that|this|those|these|they|them|their|there|same|above|previous|former|latter)\b",
    re.IGNORECASE,
)
_EVIDENCE_TAIL = re.compile(r"Evidence:\s*(?:`[^`]*`[\s,;]*)+\.?", re.IGNORECASE)
_SOURCE_REF = re.compile(r"`?[A-Za-z0-9_./-]+\.[A-Za-z0-9]+:L\d+(?:-L?\d+)?`?")
_CITATION_MARKER = re.compile(r"\[\d+\]")
_FOLLOWUP_OPENERS = ("and ", "what about", "how about", "also", "then ", "so ", "why", "where is it", "which one")
_PARTIAL_MARKERS = re.compile(
    r"(does not (cover|say|specify|mention|state|describe)|doesn't (cover|say|specify|mention)|"
    r"not (covered|specified|mentioned|documented|stated) (in|by) the evidence|"
    r"no information (about|on)|evidence (does not|doesn't) (include|cover|say))",
    re.IGNORECASE,
)


_BACKTICKS = re.compile(r"`[^`]*`")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_'\u2019-]*[A-Za-z0-9]")


def salient_terms(question: str) -> List[str]:
    """Name-like tokens of a question: capitalised (not sentence-initial),
    ALL-CAPS, or letters mixed with digits. Text inside backticks is the
    user's own literal (a command, a pod name) and is ignored."""

    text = _BACKTICKS.sub(" ", question or "")
    out: List[str] = []
    for match in _WORD.finditer(text):
        token = re.sub(r"['\u2019]s$", "", match.group(0))
        if len(token) < 3:
            continue
        before = text[: match.start()].rstrip()
        initial = not before or before[-1] in ".?!:;\"("
        named = (token[0].isupper() and not initial) or (token.isupper() and len(token) >= 3)
        if named or (any(ch.isdigit() for ch in token) and any(ch.isalpha() for ch in token)):
            if token not in out:
                out.append(token)
    return out


def unknown_terms(question: str, vocabulary: "set[str]") -> List[str]:
    """Salient question terms that never occur in the indexed documentation."""

    missing = []
    for term in salient_terms(question):
        low = term.lower()
        parts = [p for p in re.split(r"[-_./']", low) if p]
        if low in vocabulary or (parts and all(p in vocabulary for p in parts)):
            continue
        if len(low) >= 4 and any(term_matches(low, v) for v in vocabulary if v[:2] == low[:2]):
            continue
        missing.append(term)
    return missing


def rewrite_followup(question: str, history: List[Dict[str, Any]], *, with_identifiers: bool = True) -> Optional[str]:
    """Deterministic follow-up expansion for retrieval; ``None`` if not needed."""

    previous_user = next((m for m in reversed(history) if m.get("role") == "user"), None)
    if previous_user is None:
        return None
    lowered = question.strip().lower()
    terms = content_terms(question)
    anaphoric = bool(_ANAPHORA.search(question)) or lowered.startswith(_FOLLOWUP_OPENERS)
    if not anaphoric and len(terms) > 4:
        return None
    previous_answer = next((m for m in reversed(history) if m.get("role") == "assistant"), None)
    identifiers: List[str] = []
    if previous_answer and str(previous_answer.get("status") or "").upper() not in {"ABSTAINED", "ERROR"}:
        # Identifiers the previous answer talked about, not the evidence
        # references it quoted: `path:Lx-Ly` refs and "Evidence:" tails name
        # files, not the thing a pronoun like "it" points back to.
        prose = _EVIDENCE_TAIL.sub(" ", previous_answer.get("content", ""))
        prose = _SOURCE_REF.sub(" ", _CITATION_MARKER.sub(" ", prose))
        identifiers = [i for i in extract_identifiers(prose) if ":L" not in i][:6]
    context = " ".join(previous_user.get("content", "").split())[:300]
    rewritten = f"{question.strip()} (follow-up to: {context})"
    if identifiers and with_identifiers:
        rewritten += " " + " ".join(identifiers)
    return rewritten[:2000]


class LearAgent:
    def __init__(
        self,
        settings: LearSettings,
        *,
        engine: Optional[RAGEngine] = None,
        limiter: Optional[LearRateLimiter] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._engine = engine
        self._engine_error: Optional[RagError] = None
        self._engine_lock = threading.Lock()
        self.db = AgentDB(settings.agent_db)
        self.sessions = SessionStore(self.db, ttl_s=settings.session_ttl_s, clock=clock)
        self.memory = MemoryStore(self.db, clock=clock)
        self.limiter = limiter or LearRateLimiter(
            per_user_per_min=settings.rate_user_per_min,
            per_session_per_min=settings.rate_session_per_min,
            max_concurrent=settings.max_concurrent,
            queue_timeout_s=settings.queue_timeout_s,
        )
        self.pin = load_pin(Path(settings.docs_dir))
        self._manifest: Optional[Dict[str, Dict[str, Any]]] = None

    # -------------------------------------------------------------- engine
    @property
    def engine(self) -> RAGEngine:
        """Built lazily: session/memory commands must work without Ollama."""

        with self._engine_lock:
            if self._engine is None:
                self._engine = RAGEngine(self.settings.engine_settings())
            return self._engine

    def close(self) -> None:
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception:  # pragma: no cover - best effort
                pass
        self.db.close()

    def manifest(self) -> Dict[str, Dict[str, Any]]:
        if self._manifest is None:
            self._manifest = {e["chunk_id"]: e for e in load_manifest(self.settings).get("chunks", [])}
        return self._manifest

    def vocabulary(self) -> "set[str]":
        """Every token of the indexed Lear docs (and its -/_ parts); cached per index state."""

        engine = self.engine
        key = (engine.store.counts()["chunks"], engine.store.get_meta("last_ingest_at"))
        if getattr(self, "_vocab_key", None) != key:
            vocab: set = set()
            for chunk in engine.store.all_chunks():
                for token in tokenize(chunk.text):
                    vocab.add(token)
                    vocab.update(p for p in re.split(r"[-_./']", token) if p)
            self._vocab, self._vocab_key = vocab, key
        return self._vocab

    # ----------------------------------------------------------------- ask
    def ask(
        self,
        question: str,
        *,
        user_id: str = "local",
        session_id: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
        top_k: Optional[int] = None,
        save: bool = True,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        question = (question or "").strip()
        if not question:
            raise ValidationError("question must not be empty")
        if len(question) > self.settings.max_input_chars:
            raise ValidationError(
                "question is too long", hint=f"limit is {self.settings.max_input_chars} characters"
            )
        with self.limiter.acquire(user_id, session_id):
            return self._ask_admitted(question, user_id, session_id, on_token, top_k, save, started)

    def _ask_admitted(self, question, user_id, session_id, on_token, top_k, save, started) -> Dict[str, Any]:
        session_info: Optional[Dict[str, Any]] = None
        history: List[Dict[str, Any]] = []
        summary = ""
        summary_version = ""
        compaction_report = None
        if session_id:
            session = self.sessions.open_or_create(session_id, user_id)
            messages = self.sessions.messages(session_id, user_id)
            result = compact(
                messages,
                previous_summary=session.get("summary") or "",
                previous_through=int(session.get("summary_through") or 0),
                keep_recent=self.settings.keep_recent_turns,
                budget_tokens=self.settings.history_token_budget,
            )
            if result.changed:
                self.sessions.save_summary(
                    session_id,
                    user_id,
                    summary=result.summary,
                    version=result.version,
                    through_message_id=result.through_message_id,
                    tokens_before=result.tokens_before,
                    tokens_after=result.tokens_after,
                    latency_ms=result.latency_ms,
                    summary_sha256=result.sha256,
                )
                compaction_report = result.to_dict()
            summary = result.summary
            summary_version = result.version if summary else ""
            history = result.kept
            session_info = {
                "id": session_id,
                "turn": sum(1 for m in messages if m.get("role") == "user") + 1,
                "summaryVersion": summary_version or None,
                "compaction": compaction_report,
                "historyMessages": len(history),
            }

        retrieval_query = rewrite_followup(question, history) if history else None
        memories = self.memory.relevant(user_id, question, session_id=session_id, limit=self.settings.memory_items)
        system_prompt = render_system_prompt(
            commit=str(self.pin.get("commit_short") or str(self.pin.get("commit", ""))[:12] or "unknown"),
            summary=summary,
            summary_version=summary_version or COMPACTION_VERSION,
            memories=memories,
        )

        stamps: List[float] = []

        def token_hook(text: str) -> None:
            stamps.append(time.perf_counter())
            if on_token:
                on_token(text)

        try:
            engine = self.engine
            check_index_fingerprint(engine)
            missing = unknown_terms(question, self.vocabulary()) if self.settings.scope_guard else []
            if missing:
                # Named things the documentation never mentions (a product, a
                # metric, a vendor): answering would mean guessing, so decline
                # before retrieval/generation and say which term is unknown.
                answer = self._scope_answer(question, missing, started, session_info, retrieval_query)
                if session_id and save:
                    self.sessions.add_message(session_id, user_id, "user", question, tokens=approx_tokens(question))
                    self.sessions.add_message(session_id, user_id, "assistant", answer["answer"], status="ABSTAINED")
                return answer
            # The extractive baseline has no conversation model: let it pick
            # sentences with the expanded follow-up query. An LLM gets the
            # user's own words plus the history instead.
            engine_question = question
            if retrieval_query and not self.settings.qualifying:
                # Without the identifiers: they help retrieval find the right
                # chunks but pull unrelated sentences into an extractive answer.
                engine_question = rewrite_followup(question, history, with_identifiers=False) or question
            result = engine.ask(
                engine_question,
                top_k=top_k or self.settings.top_k,
                history=[{"role": m["role"], "content": m["content"]} for m in history],
                on_token=token_hook,
                save=save,
                system_prompt=system_prompt,
                retrieval_query=retrieval_query,
            )
        except RateLimited:
            raise
        except (ProviderUnavailable, ProviderError, RagError) as exc:
            answer = self._error_answer(question, exc, started, session_info, retrieval_query)
            if session_id and save:
                self.sessions.add_message(session_id, user_id, "user", question, tokens=approx_tokens(question))
                self.sessions.add_message(session_id, user_id, "assistant", answer["answer"], status="ERROR")
            return answer

        answer = self._build_answer(result, question, retrieval_query, stamps, started, session_info, memories, system_prompt)
        if (
            answer["models"]["generation"].get("fallbackUsed")
            and self.settings.backend == "ollama"
            and not self.settings.allow_extractive_fallback
        ):
            # The engine answers with its extractive generator when the LLM
            # fails. On the Ollama backend that would pass a quote-stitcher off
            # as the configured model, so the result becomes an ERROR unless
            # the operator explicitly accepts a labelled fallback.
            failure = (getattr(result, "provider", None) or {}).get("failedProvider") or {}
            answer.update(
                {
                    "status": "ERROR",
                    "answer": "",
                    "citations": [],
                    "qualifying": False,
                    "fallbackDiscarded": True,
                    "error": {
                        "code": "generation_unavailable",
                        "message": (
                            f"local LLM ollama:{self.settings.llm_model} failed "
                            f"({failure.get('code', 'provider_error')}: {failure.get('message', 'no detail')}); "
                            "the extractive fallback answer was discarded. Set RAG_LEAR_ALLOW_EXTRACTIVE_FALLBACK=1 "
                            "to receive it, labelled as a fallback."
                        ),
                        "dependencyUnavailable": True,
                    },
                }
            )
            answer["warnings"] = list(answer["warnings"]) + ["extractive_fallback_discarded"]
        if session_id and save:
            self.sessions.add_message(session_id, user_id, "user", question, tokens=approx_tokens(question))
            self.sessions.add_message(
                session_id,
                user_id,
                "assistant",
                answer["answer"],
                status=answer["status"],
                citations=[{"document": c["document"], "heading": c["heading"]} for c in answer["citations"]],
                query_id=answer["correlationId"],
                tokens=approx_tokens(answer["answer"]),
            )
        return answer

    # -------------------------------------------------------------- mapping
    def _status(self, result: Any) -> str:
        if result.status == "failed":
            return "ERROR"
        if result.status == "abstained":
            return "ABSTAINED"
        if result.conflict:
            return "CONFLICTED"
        if result.status == "needs_review":
            return "PARTIAL"
        unsupported = [c for c in result.claims if not c.citations]
        if unsupported or "removed_invalid_citation_markers" in result.warnings or _PARTIAL_MARKERS.search(result.answer or ""):
            return "PARTIAL"
        return "GROUNDED"

    def _citations(self, result: Any) -> List[Dict[str, Any]]:
        manifest = self.manifest()
        out = []
        for citation in result.citations:
            entry = manifest.get(citation.chunk_id, {})
            out.append(
                {
                    "id": citation.evidence_id,
                    "chunkId": citation.chunk_id,
                    "document": entry.get("source_path") or citation.source_title,
                    "heading": " > ".join(entry.get("heading_path") or [citation.section]),
                    "lines": f"{entry['start_line']}-{entry['end_line']}" if entry.get("start_line") else None,
                    "linePrecision": entry.get("line_precision"),
                    "sourceRefs": entry.get("source_refs", [])[:6],
                    "commit": (entry.get("source_commit") or "")[:12] or None,
                    "support": round(float(citation.support or 0.0), 4),
                    "excerpt": citation.excerpt[:240],
                }
            )
        return out

    def _timings(self, result: Any, stamps: List[float], started: float) -> Dict[str, Any]:
        total_ms = round((time.perf_counter() - started) * 1000.0, 2)
        metrics = result.metrics or {}
        itl = [round((b - a) * 1000.0, 3) for a, b in zip(stamps, stamps[1:])]
        ttft = round((stamps[0] - started) * 1000.0, 2) if stamps else None
        completion = metrics.get("completionTokens")
        generation_ms = metrics.get("generationMs")
        decode_tps = None
        if len(stamps) >= 2 and stamps[-1] > stamps[0]:
            decode_tps = round((len(stamps) - 1) / (stamps[-1] - stamps[0]), 2)
        overall_tps = None
        if completion and generation_ms:
            overall_tps = round(float(completion) / (float(generation_ms) / 1000.0), 2)
        return {
            "ttftMs": ttft,
            "totalMs": total_ms,
            "streamEvents": len(stamps),
            "completionTokens": completion,
            "tokensEstimated": bool(metrics.get("tokensEstimated")),
            "tokensPerSec": overall_tps,
            "decodeEventsPerSec": decode_tps,
            "itlMs": describe(itl),
            "stages": {
                k: metrics.get(k)
                for k in ("retrievalMs", "generationMs", "ttftMs", "contextTokens", "promptTokens", "evidenceTokens")
                if metrics.get(k) is not None
            },
            "_itlRaw": itl,
        }

    def _models(self, result: Any) -> Dict[str, Any]:
        provider = result.provider or {}
        used = provider.get("used") or provider.get("provider")
        fallback = any(str(w).startswith("fallback_provider_used") for w in result.warnings)
        return {
            "generation": {
                "configured": f"ollama:{self.settings.llm_model}" if self.settings.qualifying else "extractive (baseline)",
                "used": f"{used}:{provider.get('model')}" if used else None,
                "fallbackUsed": fallback,
            },
            "embedding": {
                "provider": self.engine.embedder.name,
                "model": self.engine.embedder.model,
                "dimension": self.engine.embedder.dimension,
                "fingerprint": self.engine.embedder.fingerprint,
            },
        }

    def _build_answer(self, result, question, retrieval_query, stamps, started, session_info, memories, system_prompt) -> Dict[str, Any]:
        status = self._status(result)
        models = self._models(result)
        qualifying = self.settings.qualifying and not models["generation"]["fallbackUsed"] and models["embedding"]["provider"] == "ollama"
        retrieval_detail = self._retrieval_detail(result)
        return {
            "status": status,
            "answer": result.answer,
            "citations": self._citations(result),
            "qualifying": qualifying,
            "backend": self.settings.backend,
            "models": models,
            "promptVersion": LEAR_PROMPT_VERSION,
            "promptTokens": approx_tokens(system_prompt),
            "session": session_info,
            "memoryUsed": [m["memory_id"] for m in memories],
            "retrieval": {"query": question, "retrievalQuery": retrieval_query, **retrieval_detail},
            "timings": self._timings(result, stamps, started),
            "correlationId": result.query_id,
            "engineStatus": result.status,
            "abstainReason": result.abstain_reason,
            "conflict": result.conflict,
            "warnings": list(result.warnings),
            "sourceCommit": self.pin.get("commit"),
            "error": result.error,
        }

    def _retrieval_detail(self, result: Any) -> Dict[str, Any]:
        manifest = self.manifest()

        def view(item: Dict[str, Any]) -> Dict[str, Any]:
            entry = manifest.get(item.get("chunkId"), {})
            return {
                "chunkId": item.get("chunkId"),
                "document": entry.get("source_path") or item.get("sourceTitle"),
                "heading": " > ".join(entry.get("heading_path") or ([item.get("section")] if item.get("section") else [])),
                "score": item.get("score"),
                "support": item.get("support"),
                "channel": item.get("channel"),
                "reason": item.get("reason"),
            }

        excluded_ids = {item.get("chunkId") for item in result.excluded}
        quarantined_ids = {item.get("chunkId") for item in result.quarantined}
        candidates = [view(item) for item in result.evidence]
        selected = [c["chunkId"] for c in candidates if c["chunkId"] not in excluded_ids and c["chunkId"] not in quarantined_ids]
        stages = {}
        for event in result.trace:
            stage = event.get("stage", "")
            if stage.startswith("retrieval.") and event.get("status") in {"completed", "skipped"}:
                stages[stage] = {"status": event.get("status"), "ms": (event.get("detail") or {}).get("ms")}
        return {
            "fingerprint": self.engine.embedder.fingerprint,
            "candidates": candidates,
            "selected": selected,
            "excluded": [view(item) for item in result.excluded],
            "quarantined": [
                {"chunkId": q.get("chunkId"), "rules": q.get("rules"), "document": manifest.get(q.get("chunkId"), {}).get("source_path")}
                for q in result.quarantined
            ],
            "contextTokens": (result.metrics or {}).get("contextTokens"),
            "bestSupport": (result.metrics or {}).get("bestSupport"),
            "stages": stages,
            "mode": (result.metrics or {}).get("mode"),
        }

    def _configured_models(self) -> Dict[str, Any]:
        """Model labels for answers that never reached the engine (scope guard, errors)."""

        strict = self.settings.qualifying
        return {
            "generation": {
                "configured": f"ollama:{self.settings.llm_model}" if strict else "extractive (baseline)",
                "used": None,
                "fallbackUsed": False,
            },
            "embedding": {
                "provider": "ollama" if strict else "hashing",
                "model": self.settings.embed_model if strict else "hashing-lexical-v1",
            },
        }

    def _scope_answer(self, question, missing, started, session_info, retrieval_query) -> Dict[str, Any]:
        names = ", ".join(f"'{t}'" for t in missing)
        return {
            "status": "ABSTAINED",
            "answer": (
                f"The indexed Lear documentation never mentions {names}, so this cannot be answered from it. "
                "Ask about the Lear backend code (CLI, actions, permissions, connectors, desktop server) instead."
            ),
            "citations": [],
            "qualifying": self.settings.qualifying,
            "backend": self.settings.backend,
            "models": self._configured_models(),
            "promptVersion": LEAR_PROMPT_VERSION,
            "session": session_info,
            "memoryUsed": [],
            "retrieval": {"query": question, "retrievalQuery": retrieval_query, "candidates": [], "selected": [],
                          "excluded": [], "quarantined": [], "bestSupport": None, "stages": {}, "unknownTerms": list(missing)},
            "timings": {"ttftMs": None, "totalMs": round((time.perf_counter() - started) * 1000.0, 2), "streamEvents": 0,
                        "completionTokens": None, "tokensEstimated": False, "tokensPerSec": None, "decodeEventsPerSec": None,
                        "itlMs": describe([]), "stages": {}, "_itlRaw": []},
            "correlationId": None,
            "engineStatus": "not_called",
            "abstainReason": "unknown_term",
            "conflict": None,
            "warnings": ["unknown_term"],
            "sourceCommit": self.pin.get("commit"),
            "error": None,
        }

    def _error_answer(self, question, exc: RagError, started, session_info, retrieval_query) -> Dict[str, Any]:
        unavailable = isinstance(exc, ProviderUnavailable)
        return {
            "status": "ERROR",
            "answer": "",
            "citations": [],
            "qualifying": False,
            "backend": self.settings.backend,
            "models": self._configured_models(),
            "promptVersion": LEAR_PROMPT_VERSION,
            "session": session_info,
            "memoryUsed": [],
            "retrieval": {"query": question, "retrievalQuery": retrieval_query},
            "timings": {"totalMs": round((time.perf_counter() - started) * 1000.0, 2)},
            "correlationId": None,
            "engineStatus": "failed",
            "warnings": ["dependency_unavailable"] if unavailable else [],
            "sourceCommit": self.pin.get("commit"),
            "error": {**exc.to_dict(), "dependencyUnavailable": unavailable},
        }


def exit_code_for(answer: Dict[str, Any]) -> int:
    if answer.get("status") == "ERROR" and (answer.get("error") or {}).get("dependencyUnavailable"):
        return 6
    return EXIT_CODES.get(answer.get("status", "ERROR"), 1)
