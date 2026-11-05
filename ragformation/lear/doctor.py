"""``lear doctor``: real readiness probes, never assumptions.

Each check performs the actual request (version, tags, show, a test embedding,
a streamed generation, a JSON-mode judge call) and reports PASS / FAIL /
UNVERIFIED / SKIP with the observed values. A model's parameter count must be
read from Ollama (``/api/show``); if Ollama does not report it, the model is
UNVERIFIED and the strict gate fails rather than trusting a model's name.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..errors import ProviderError, ProviderUnavailable, RagError
from .ollama import OllamaClient, installed_names, parameter_count, tag_present
from .settings import LearSettings, is_loopback_url

PASS, FAIL, UNVERIFIED, SKIP, WARN = "PASS", "FAIL", "UNVERIFIED", "SKIP", "WARN"


def _check(name: str, status: str, detail: Dict[str, Any], hint: Optional[str] = None, required: bool = True) -> Dict[str, Any]:
    return {"check": name, "status": status, "required": required, "detail": detail, "hint": hint}


def _timed(fn: Callable[[], Any]):
    started = time.perf_counter()
    value = fn()
    return value, round((time.perf_counter() - started) * 1000.0, 2)


def run_doctor(settings: LearSettings, *, client: Optional[OllamaClient] = None, probe_generation: bool = True) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    checks.append(
        _check(
            "config.loopback_only",
            PASS if is_loopback_url(settings.ollama_url) else FAIL,
            {"ollamaUrl": settings.ollama_url},
        )
    )
    checks.append(
        _check(
            "config.backend",
            PASS if settings.qualifying else WARN,
            {"backend": settings.backend, "qualifying": settings.qualifying},
            None if settings.qualifying else "baseline backend (hashing + extractive) is for offline testing only",
        )
    )
    cloud_keys = sorted(k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY") if os.environ.get(k))
    checks.append(
        _check(
            "config.no_cloud_providers",
            PASS,
            {
                "embedProvider": settings.engine_settings().embed_provider,
                "llmProvider": settings.engine_settings().llm_provider,
                "cloudKeysPresentButIgnored": cloud_keys,
            },
            "the Lear engine pins providers; cloud keys in the environment are never read by it" if cloud_keys else None,
            required=False,
        )
    )

    if not settings.qualifying:
        checks.append(_check("ollama.version", SKIP, {}, "baseline backend: Ollama not used", required=False))
        return _summarise(settings, checks)

    client = client or OllamaClient(settings.ollama_url, timeout=settings.request_timeout_s)
    try:
        version, ms = _timed(client.version)
        checks.append(_check("ollama.version", PASS, {"version": version.get("version"), "ms": ms}))
    except (ProviderError, ProviderUnavailable) as exc:
        checks.append(_check("ollama.version", FAIL, {"error": exc.message}, exc.hint))
        for name in ("models.installed", "models.embedder", "models.llm", "models.judge", "probe.embedding", "probe.generation_stream", "probe.judge_json"):
            checks.append(_check(name, SKIP, {"reason": "ollama unreachable"}))
        return _summarise(settings, checks)

    try:
        names = installed_names(client.tags())
    except (ProviderError, ProviderUnavailable) as exc:
        names = []
        checks.append(_check("models.installed", FAIL, {"error": exc.message}, exc.hint))
    else:
        missing = [m for m in (settings.embed_model, settings.llm_model, settings.judge_model) if not tag_present(m, names)]
        checks.append(
            _check(
                "models.installed",
                PASS if not missing else FAIL,
                {"installed": names[:30], "missing": missing},
                ("run: " + " && ".join(f"ollama pull {m}" for m in missing)) if missing else None,
            )
        )

    roles = (
        ("models.embedder", settings.embed_model, settings.embed_max_params, "embedding"),
        ("models.llm", settings.llm_model, settings.model_max_params, "generation"),
        ("models.judge", settings.judge_model, settings.model_max_params, "judge"),
    )
    families: Dict[str, Any] = {}
    for name, model, cap, role in roles:
        if names and not tag_present(model, names):
            checks.append(_check(name, SKIP, {"model": model, "reason": "not installed"}))
            continue
        try:
            show = client.show(model)
        except (ProviderError, ProviderUnavailable) as exc:
            checks.append(_check(name, FAIL, {"model": model, "error": exc.message}, exc.hint))
            continue
        count, source = parameter_count(show)
        details = show.get("details") or {}
        families[role] = details.get("family")
        detail = {
            "model": model,
            "role": role,
            "parameterCount": count,
            "parameterSource": source,
            "capParams": cap,
            "family": details.get("family"),
            "quantization": details.get("quantization_level"),
            "format": details.get("format"),
        }
        if count is None:
            checks.append(_check(name, UNVERIFIED, detail, "Ollama did not report a parameter count for this model"))
        elif count > cap:
            checks.append(_check(name, FAIL, detail, f"{model} exceeds the {cap:,} parameter cap"))
        else:
            checks.append(_check(name, PASS, detail))

    if settings.judge_model == settings.llm_model:
        checks.append(_check("models.judge_distinct", FAIL, {"llm": settings.llm_model, "judge": settings.judge_model}))
    else:
        checks.append(
            _check(
                "models.judge_distinct",
                PASS,
                {"llm": settings.llm_model, "judge": settings.judge_model, "families": families},
            )
        )

    # -- live probes
    try:
        vectors, ms = _timed(lambda: client.embed(settings.embed_model, ["Lear doctor embedding probe"]))
        dimension = len(vectors[0]) if vectors and vectors[0] else 0
        checks.append(_check("probe.embedding", PASS if dimension > 0 else FAIL, {"dimension": dimension, "ms": ms}))
    except (ProviderError, ProviderUnavailable) as exc:
        checks.append(_check("probe.embedding", FAIL, {"error": exc.message}, exc.hint))

    if probe_generation:
        checks.append(_probe_stream(client, settings))
        checks.append(_probe_judge(client, settings))
    else:
        checks.append(_check("probe.generation_stream", SKIP, {"reason": "--no-generation"}, required=False))
        checks.append(_check("probe.judge_json", SKIP, {"reason": "--no-generation"}, required=False))
    return _summarise(settings, checks)


def _probe_stream(client: OllamaClient, settings: LearSettings) -> Dict[str, Any]:
    started = time.perf_counter()
    first: Optional[float] = None
    tokens = 0
    try:
        for stamp, event in client.chat_stream(
            settings.llm_model,
            [{"role": "user", "content": "Reply with the single word: ready"}],
            options={"temperature": 0, "num_predict": 8},
            timeout=settings.request_timeout_s,
        ):
            if (event.get("message") or {}).get("content"):
                tokens += 1
                if first is None:
                    first = stamp
            if event.get("done"):
                break
    except (ProviderError, ProviderUnavailable) as exc:
        return _check("probe.generation_stream", FAIL, {"error": exc.message}, exc.hint)
    total = round((time.perf_counter() - started) * 1000.0, 2)
    if not tokens or first is None:
        return _check("probe.generation_stream", FAIL, {"tokens": tokens, "totalMs": total}, "no streamed token received")
    return _check(
        "probe.generation_stream",
        PASS,
        {"tokens": tokens, "ttftMs": round((first - started) * 1000.0, 2), "totalMs": total},
    )


def _probe_judge(client: OllamaClient, settings: LearSettings) -> Dict[str, Any]:
    try:
        reply, ms = _timed(
            lambda: client.chat(
                settings.judge_model,
                [{"role": "user", "content": 'Return the JSON object {"ok": true} and nothing else.'}],
                options={"temperature": 0, "num_predict": 32},
                format="json",
                timeout=settings.request_timeout_s,
            )
        )
    except (ProviderError, ProviderUnavailable) as exc:
        return _check("probe.judge_json", FAIL, {"error": exc.message}, exc.hint)
    content = (reply.get("message") or {}).get("content", "")
    try:
        parsed = json.loads(content)
        ok = isinstance(parsed, dict)
    except json.JSONDecodeError:
        ok = False
    return _check("probe.judge_json", PASS if ok else FAIL, {"ms": ms, "raw": content[:120]})


def _summarise(settings: LearSettings, checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    required_failures = [c["check"] for c in checks if c["required"] and c["status"] in {FAIL, UNVERIFIED, SKIP}]
    pin_path = Path(settings.docs_dir) / "SOURCE_PIN.json"
    pin: Dict[str, Any] = {}
    if pin_path.exists():
        try:
            pin = json.loads(pin_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pin = {"error": "unreadable SOURCE_PIN.json"}
    ready = settings.qualifying and not required_failures
    return {
        "ready": ready,
        "qualifying": settings.qualifying,
        "backend": settings.backend,
        "models": {"embedder": settings.embed_model, "llm": settings.llm_model, "judge": settings.judge_model},
        "caps": {"embedder": settings.embed_max_params, "llm": settings.model_max_params, "judge": settings.model_max_params},
        "indexPresent": settings.engine_settings().db_path.exists(),
        "sourcePin": {k: pin.get(k) for k in ("repository", "commit", "branch") if k in pin} if pin else None,
        "requiredFailures": required_failures,
        "checks": checks,
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def doctor_exit_code(report: Dict[str, Any]) -> int:
    """0 ready; 6 a required local dependency is missing/unverified."""

    return 0 if report.get("ready") else 6


def render_doctor(report: Dict[str, Any]) -> str:
    lines = [
        f"Lear doctor — backend={report['backend']} qualifying={report['qualifying']} ready={report['ready']}",
        f"models: embedder={report['models']['embedder']} llm={report['models']['llm']} judge={report['models']['judge']}",
    ]
    for check in report["checks"]:
        detail = ", ".join(f"{k}={v}" for k, v in check["detail"].items() if v not in (None, [], {}))
        lines.append(f"  [{check['status']:<10}] {check['check']}: {detail[:160]}")
        if check.get("hint") and check["status"] != PASS:
            lines.append(f"               hint: {check['hint']}")
    if report["requiredFailures"]:
        lines.append("NOT READY: " + ", ".join(report["requiredFailures"]))
    return "\n".join(lines)


__all__ = ["run_doctor", "render_doctor", "doctor_exit_code", "RagError"]
