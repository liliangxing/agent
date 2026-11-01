"""Authentication: resolve an API key to a subject with roles.

The server can run in two modes:

* ``MES_MCP_AUTH`` unset / ``false`` — development mode. No API key required;
  every request runs as the built-in ``demo`` subject (``admin`` role).
* ``MES_MCP_AUTH=true`` — every call must present a known API key.

Keys are configured in ``MES_MCP_API_KEYS`` as JSON::

    MES_MCP_API_KEYS='{"sk-...":"admin","sk-...":"scheduler","sk-...":"operator"}'

For the HTTP transport the key comes from the ``Authorization: Bearer``
or ``X-MES-API-Key`` header; for stdio the single ``MES_MCP_DEFAULT_KEY``
environment variable names the subject (stdio is a trusted local channel).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from .errors import AuthenticationError

ROLE_NAMES = {"admin", "scheduler", "operator", "viewer"}

# Default subject used when authentication is disabled (dev/demo mode).
DEMO_SUBJECT = "demo"


@dataclass(frozen=True)
class Subject:
    """An authenticated caller."""

    name: str
    roles: frozenset[str] = field(default_factory=lambda: frozenset({"viewer"}))

    def has_role(self, role: str) -> bool:
        return role in self.roles


def _load_key_roles() -> dict[str, list[str]]:
    raw = os.environ.get("MES_MCP_API_KEYS", "").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    out: dict[str, list[str]] = {}
    for key, role in data.items():
        roles = [role] if isinstance(role, str) else list(role)
        out[key] = roles
    return out


_KEY_ROLES = _load_key_roles()
_KNOWN_ROLES_BY_KEY: dict[str, frozenset[str]] = {
    k: frozenset(r) for k, r in _KEY_ROLES.items()
}


def required() -> bool:
    return os.environ.get("MES_MCP_AUTH", "false").strip().lower() in {"1", "true", "yes"}


def authenticate(api_key: Optional[str]) -> Subject:
    """Return the subject for ``api_key`` or raise AuthenticationError."""
    if not required():
        return Subject(name=DEMO_SUBJECT, roles=frozenset(ROLE_NAMES))

    if not api_key:
        raise AuthenticationError("缺少 API key（MES_MCP_AUTH=true）")

    if api_key == DEMO_SUBJECT:
        raise AuthenticationError("无效的 API key")

    # stdio trust channel: a configured default key names a built-in subject.
    default_key = os.environ.get("MES_MCP_DEFAULT_KEY")
    if default_key and api_key == default_key:
        role = os.environ.get("MES_MCP_DEFAULT_ROLE", "admin")
        return Subject(name=f"stdio/{default_key[:4]}", roles=frozenset({role}))

    roles = _KNOWN_ROLES_BY_KEY.get(api_key)
    if roles is None:
        raise AuthenticationError("API key 无效")

    return Subject(name=f"key/{api_key[:4]}", roles=roles)