"""Minimal Ollama HTTP client for the Lear agent's probes, judge and benchmarks.

Answer generation goes through the engine's own ``OllamaGenerator``; this
client is used where the agent needs the raw protocol: ``/api/version``,
``/api/tags``, ``/api/show`` (parameter counts), ``/api/embed``, streaming
``/api/chat`` with per-token timestamps, and JSON-mode chat for the judge.

Every request goes to the configured loopback URL; the constructor refuses
anything else so no code path can reach a remote host by accident.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from ..errors import ConfigError, ProviderError, ProviderUnavailable
from .settings import is_loopback_url


class OllamaClient:
    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        if not is_loopback_url(base_url):
            raise ConfigError("refusing a non-loopback Ollama URL", hint=base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------- transport
    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, *, timeout: Optional[float] = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout or self.timeout)  # noqa: S310 - loopback only
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # pragma: no cover - best effort
                pass
            raise ProviderError(f"ollama returned HTTP {exc.code} for {path}", hint=body or None) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ProviderUnavailable(
                f"ollama is not reachable at {self.base_url}",
                hint="start it with `ollama serve` (and keep it bound to 127.0.0.1)",
            ) from exc

    def _json(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        with self._request(method, path, payload, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"ollama returned invalid JSON for {path}", hint=raw[:200]) from exc
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"ollama error for {path}", hint=str(data["error"])[:300])
        return data

    # ------------------------------------------------------------ endpoints
    def version(self) -> Dict[str, Any]:
        return self._json("GET", "/api/version", timeout=5.0)

    def tags(self) -> List[Dict[str, Any]]:
        return list(self._json("GET", "/api/tags", timeout=10.0).get("models") or [])

    def show(self, model: str) -> Dict[str, Any]:
        return self._json("POST", "/api/show", {"model": model}, timeout=30.0)

    def embed(self, model: str, texts: Sequence[str]) -> List[List[float]]:
        data = self._json("POST", "/api/embed", {"model": model, "input": list(texts)})
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ProviderError("ollama /api/embed returned an unexpected payload")
        return vectors

    def chat(
        self,
        model: str,
        messages: Sequence[Dict[str, str]],
        *,
        options: Optional[Dict[str, Any]] = None,
        format: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": model, "messages": list(messages), "stream": False}
        if options:
            payload["options"] = options
        if format is not None:
            payload["format"] = format
        return self._json("POST", "/api/chat", payload, timeout=timeout)

    def chat_stream(
        self,
        model: str,
        messages: Sequence[Dict[str, str]],
        *,
        options: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Iterator[Tuple[float, Dict[str, Any]]]:
        """Yield ``(perf_counter_timestamp, event)`` for every streamed line."""

        payload: Dict[str, Any] = {"model": model, "messages": list(messages), "stream": True}
        if options:
            payload["options"] = options
        with self._request("POST", "/api/chat", payload, timeout=timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                stamp = time.perf_counter()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    raise ProviderError("ollama reported a streaming error", hint=str(event["error"])[:300])
                yield stamp, event


# ----------------------------------------------------------- parameter counts
_SIZE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMBT])?\s*$", re.IGNORECASE)
_SCALE = {None: 1, "K": 10**3, "M": 10**6, "B": 10**9, "T": 10**12}


def parse_parameter_size(text: Any) -> Optional[int]:
    """Parse Ollama's ``details.parameter_size`` (e.g. ``"1.5B"``, ``"137M"``)."""

    if not isinstance(text, str):
        return None
    match = _SIZE.match(text)
    if not match:
        return None
    number, unit = match.groups()
    return int(round(float(number) * _SCALE[unit.upper() if unit else None]))


def parameter_count(show_payload: Dict[str, Any]) -> Tuple[Optional[int], str]:
    """Return ``(count, source)`` from an ``/api/show`` payload.

    ``model_info["general.parameter_count"]`` is the exact count read from the
    model file and is preferred. ``details.parameter_size`` is a rounded label
    and is reported as such. ``(None, "unverified")`` means neither was present.
    """

    info = show_payload.get("model_info") or {}
    exact = info.get("general.parameter_count")
    if isinstance(exact, (int, float)) and exact > 0:
        return int(exact), "model_info.general.parameter_count"
    label = parse_parameter_size((show_payload.get("details") or {}).get("parameter_size"))
    if label:
        return label, "details.parameter_size"
    return None, "unverified"


def installed_names(tags: Sequence[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for item in tags:
        for key in ("name", "model"):
            value = item.get(key)
            if isinstance(value, str) and value and value not in names:
                names.append(value)
    return names


def tag_present(model: str, names: Sequence[str]) -> bool:
    """Exact tag match; a bare name matches ``name:latest`` only."""

    wanted = model if ":" in model else f"{model}:latest"
    return any(name == wanted or name == model for name in names)
