"""Principals, authentication and server-side authorisation.

Two deployment modes are supported and they are deliberately different:

``local`` (default)
    One operator on one machine. No login. Loopback binding only. If the API key
    is unset, every request is the same local principal and ``doctor`` says so.
    Binding to a non-loopback address without a key is refused unless the
    operator sets ``RAG_ALLOW_INSECURE_PUBLIC=1`` — an explicit, logged risk
    acceptance, not a default.

``host``
    The RAG service runs behind a host application (FastAPI/Flask/nginx/ingress)
    that has already authenticated the user. The host passes a signed-in
    principal to the engine server-side. We never read a tenant or subject from a
    client-controlled body field.

The principal contract is intentionally small: identity, tenant, roles and an
optional source allow-list. Anything richer belongs to the host.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Sequence

from .errors import AuthError, Forbidden

#: Actions the engine authorises. Every route maps onto exactly one.
ACTIONS = {
    "read": {"viewer", "writer", "operator", "admin"},
    "query": {"viewer", "writer", "operator", "admin"},
    "ingest": {"writer", "operator", "admin"},
    "delete": {"writer", "operator", "admin"},
    "stats": {"operator", "admin"},
    "traces": {"operator", "admin"},
    "rebuild": {"admin"},
    "manage": {"admin"},
}

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def is_loopback(host: str) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


@dataclass(frozen=True)
class Principal:
    """An authenticated caller, established server-side."""

    subject_id: str = "local-operator"
    tenant_id: str = "local"
    roles: FrozenSet[str] = field(default_factory=lambda: frozenset({"admin", "operator", "writer", "viewer"}))
    allowed_sources: Optional[FrozenSet[str]] = None
    issuer: str = "local"

    def __post_init__(self) -> None:
        object.__setattr__(self, "roles", frozenset(self.roles or ()))
        if self.allowed_sources is not None:
            object.__setattr__(self, "allowed_sources", frozenset(self.allowed_sources))

    # -------------------------------------------------------------- checks
    def can(self, action: str) -> bool:
        allowed = ACTIONS.get(action)
        if allowed is None:
            return False
        return bool(self.roles & allowed)

    def require(self, action: str) -> None:
        if not self.can(action):
            raise Forbidden(
                f"principal is not allowed to {action}",
                hint=f"required role: {sorted(ACTIONS.get(action, set()))}",
            )

    @property
    def is_local(self) -> bool:
        return self.issuer == "local"

    # --------------------------------------------------------------- scope
    def scope(self, requested: Optional[Iterable[str]] = None) -> Optional[FrozenSet[str]]:
        """Intersect the principal's allow-list with a *requested* filter.

        A request can only ever narrow what the principal may see. If the
        principal has no allow-list, ``None`` means "everything in the tenant".
        """

        requested_set = frozenset(requested or ()) or None
        if self.allowed_sources is None:
            return requested_set
        if requested_set is None:
            return self.allowed_sources
        return self.allowed_sources & requested_set

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subjectId": self.subject_id,
            "tenantId": self.tenant_id,
            "roles": sorted(self.roles),
            "allowedSources": None if self.allowed_sources is None else sorted(self.allowed_sources),
            "issuer": self.issuer,
        }


LOCAL_PRINCIPAL = Principal()


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _principal_from_payload(payload: Mapping[str, Any], issuer: str) -> Principal:
    roles: Sequence[str]
    raw_roles = payload.get("roles") or payload.get("scopes") or payload.get("role") or ()
    if isinstance(raw_roles, str):
        roles = [r.strip() for r in raw_roles.replace(",", " ").split() if r.strip()]
    else:
        roles = [str(r) for r in raw_roles]
    if not roles:
        roles = ["viewer"]
    unknown = [r for r in roles if r not in {"viewer", "writer", "operator", "admin"}]
    if unknown:
        raise AuthError("principal carries unknown roles", hint=f"unknown: {sorted(set(unknown))}")
    sources = payload.get("allowedSources", payload.get("allowed_sources"))
    if isinstance(sources, str):
        sources = [s.strip() for s in sources.replace(",", " ").split() if s.strip()]
    return Principal(
        subject_id=str(payload.get("subjectId") or payload.get("sub") or payload.get("subject") or "unknown"),
        tenant_id=str(payload.get("tenantId") or payload.get("tenant") or "local"),
        roles=frozenset(roles),
        allowed_sources=None if sources is None else frozenset(str(s) for s in sources),
        issuer=str(payload.get("issuer") or issuer),
    )


def resolve_principal(
    headers: Mapping[str, str],
    settings: Any,
    *,
    require_auth: bool = True,
) -> Principal:
    """Resolve the caller from trusted headers only.

    ``headers`` is a case-insensitive mapping built by the HTTP layer. Client
    supplied ``x-tenant-id`` / ``x-allowed-sources`` / ``x-subject-id`` headers
    are **ignored by design**: identity comes from the API key (local mode) or
    from the host's principal header (host mode).
    """

    api_key = getattr(settings, "api_key", None)
    provided = headers.get("x-api-key") or ""
    auth_header = headers.get("authorization") or ""
    if not provided and auth_header.lower().startswith("bearer "):
        provided = auth_header[7:].strip()

    trusted_header = getattr(settings, "trusted_principal_header", None)
    trust_proxy = _parse_bool(getattr(settings, "trust_proxy", False))

    if api_key:
        if not provided or not hmac.compare_digest(str(provided), str(api_key)):
            raise AuthError(
                "missing or invalid API key",
                hint="send the key in the x-api-key header",
            )
    elif require_auth and not is_loopback(getattr(settings, "host", "")) and not trust_proxy:
        raise AuthError("this deployment requires authentication")

    if trusted_header and trust_proxy:
        raw = headers.get(str(trusted_header).lower())
        if not raw:
            if api_key:
                # Authenticated service-to-service call without a user context:
                # the local/service principal, never a client-supplied tenant.
                return Principal(
                    subject_id="service", tenant_id="local",
                    roles=frozenset({"operator", "writer", "viewer"}), issuer="service",
                )
            raise AuthError("host principal header is missing", hint=f"header: {trusted_header}")
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise AuthError("host principal header is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise AuthError("host principal header must be a JSON object")
        return _principal_from_payload(payload, issuer="host")

    if api_key:
        return Principal(
            subject_id="api-client",
            tenant_id=getattr(settings, "default_tenant", "local") or "local",
            roles=frozenset({"admin", "operator", "writer", "viewer"}),
            issuer="api-key",
        )
    return LOCAL_PRINCIPAL


def check_deployment(settings: Any) -> Dict[str, Any]:
    """Describe the security posture; used by ``doctor`` and ``/api/v1/health``."""

    host = getattr(settings, "host", "")
    api_key = bool(getattr(settings, "api_key", None))
    trust_proxy = _parse_bool(getattr(settings, "trust_proxy", False))
    insecure_public = _parse_bool(getattr(settings, "allow_insecure_public", False))
    loopback = is_loopback(host)

    if loopback and not api_key:
        mode = "local-single-user"
        ok = True
        detail = "loopback bind, no authentication: single-user local mode"
    elif api_key:
        mode = "api-key" if not trust_proxy else "host-authenticated"
        ok = True
        detail = "API key required; tenant scope comes from the key principal" if not trust_proxy else "host principal header trusted behind proxy; API key required from the host"
    elif trust_proxy:
        mode = "host-authenticated"
        ok = True
        detail = "trusted host principal header; ensure the proxy strips client-supplied headers"
    elif insecure_public:
        mode = "insecure-public"
        ok = False
        detail = "non-loopback bind without authentication (explicit RAG_ALLOW_INSECURE_PUBLIC=1)"
    else:
        mode = "misconfigured"
        ok = False
        detail = "non-loopback bind without authentication is refused"
    return {
        "securityMode": mode,
        "ok": ok,
        "loopback": loopback,
        "authenticated": api_key or trust_proxy,
        "detail": detail,
        "tenantIsolation": "enforced" if (api_key or trust_proxy) else "single-tenant",
    }
