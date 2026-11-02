"""Role-based access control over resources and operations.

Resource/operation access is a simple role matrix with a fail-closed policy:
an unknown resource or an unlisted (resource, operation) pair is denied, not
allowed. This mirrors the per-model permission tables of real ERP MCP
servers (Odoo's ``ModelPermissions`` / ``AccessController``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .auth import Subject
from .errors import AccessDeniedError

# resource -> operation -> set of roles allowed to perform it
_ALL = {"admin", "scheduler", "operator", "viewer"}
ACCESS_MATRIX: dict[str, dict[str, set[str]]] = {
    "work_order": {
        "read": _ALL,
        "create": {"admin", "scheduler"},
        "update": {"admin", "scheduler"},
        "release": {"admin", "scheduler"},
    },
    "item": {
        "read": _ALL,
        "create": {"admin"},
        "update": {"admin", "scheduler"},
    },
    "sales_order": {
        "read": _ALL,
        "create": {"admin", "scheduler"},
    },
    "purchase_order": {
        "read": _ALL,
        "create": {"admin", "scheduler"},
    },
    "inventory": {
        "read": _ALL,
        "update": {"admin", "scheduler"},
    },
    "work_center": {
        "read": _ALL,
        "update": {"admin", "scheduler", "operator"},
    },
    "routing": {
        "read": _ALL,
    },
    "production_order": {
        "read": _ALL,
        "update": {"admin", "scheduler"},
        "report": {"admin", "scheduler", "operator"},
        "inspect": {"operator", "admin"},
        "issue": {"admin", "scheduler", "operator"},
    },
    "schedule": {
        "read": _ALL,
        "create": {"admin", "scheduler"},
        "dispatch": {"admin", "scheduler"},
    },
    "integration": {
        "read": {"admin", "scheduler"},
        "sync": {"admin", "scheduler"},
        "reconcile": {"admin", "scheduler"},
    },
    "system": {
        "read": _ALL,
        "reset": {"admin"},
    },
}

_OPERATION_ALIASES = {
    "list": "read",
    "get": "read",
    "query": "read",
    "count": "read",
    "import": "update",
}


@dataclass
class AccessController:
    """Checks (resource, operation) against the role matrix with caching."""

    matrix: dict[str, dict[str, set[str]]] = field(default_factory=lambda: ACCESS_MATRIX)
    _cache: dict[tuple[str, str, str], bool] = field(default_factory=dict)

    def _allowed_roles(self, resource: str, operation: str) -> Optional[set[str]]:
        op = _OPERATION_ALIASES.get(operation, operation)
        return self.matrix.get(resource, {}).get(op)

    def check(self, subject: Subject, resource: str, operation: str) -> None:
        """Raise AccessDeniedError if ``subject`` cannot ``operation`` on ``resource``."""
        cache_key = (subject.name, resource, operation)
        cached = self._cache.get(cache_key)
        if cached is True:
            return
        if cached is False:
            raise AccessDeniedError(
                f"Access denied: 当前身份无 {resource}.{operation} 权限"
            )

        allowed_roles = self._allowed_roles(resource, operation)
        if allowed_roles is None or not any(subject.has_role(r) for r in allowed_roles):
            self._cache[cache_key] = False
            raise AccessDeniedError(
                f"Access denied: 当前身份无 {resource}.{operation} 权限"
            )
        self._cache[cache_key] = True