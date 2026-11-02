"""Integration fabric: the transactional glue between ERP, MES and APS.

Provides a single facade that domain services and the MCP tool layer use to
write safely: idempotent, audited, and with outbox events committed atomically
alongside the domain mutation.
"""
from __future__ import annotations

from ..store import Store
from .audit import AuditTrail
from .idempotency import IdempotencyGuard
from .mapping import MappingRegistry
from .outbox import Outbox
from .reconcile import Reconciler

__all__ = [
    "Integration", "AuditTrail", "IdempotencyGuard", "MappingRegistry",
    "Outbox", "Reconciler",
]


class Integration:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.audit = AuditTrail(store)
        self.outbox = Outbox(store)
        self.mapping = MappingRegistry(store)
        self.idempotency = IdempotencyGuard(store)
        self.reconciler = Reconciler(store)