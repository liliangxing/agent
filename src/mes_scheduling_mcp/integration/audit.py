"""Append-only audit trail.

Every mutation is written to ``audit_log`` with operator, operation, resource,
resource id, a correlation id tying the write to its originating request, and
before/after snapshots.  This is the compatibility surface for SOX / ISO 9001
style "who did what, when, and what changed" requirements in manufacturing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import AuditEntry
from ..store import Store


class AuditTrail:
    def __init__(self, store: Store) -> None:
        self._store = store

    def record(self, operator: str, operation: str, resource: str, resource_id: str,
               correlation_id: str, before: dict[str, Any] | None = None,
               after: dict[str, Any] | None = None, success: bool = True,
               error: str | None = None) -> None:
        entry = AuditEntry(
            audit_id=f"AUD-{datetime.now().timestamp():.0f}-{resource_id[-6:] if resource_id else 'x'}",
            ts=datetime.now().isoformat(timespec="seconds"),
            operator=operator, operation=operation, resource=resource,
            resource_id=resource_id, correlation_id=correlation_id,
            before=_diffable(before), after=_diffable(after),
            success=success, error=error,
        )
        self._store.insert_audit(entry)

    def query(self, resource: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self._store.list_audit(resource=resource, limit=limit)


def _diffable(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Make a snapshot JSON-safe (dataclasses/enums -> value)."""
    if value is None:
        return None
    return {k: (v.value if hasattr(v, "value") else v) for k, v in value.items()}