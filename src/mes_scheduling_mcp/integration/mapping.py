"""ERP <-> MES dual-key mapping registry.

The two systems use independent identifiers for the same physical entity
(an ERP work order ``WO-...`` becomes a MES production order ``PO-...``).
This registry records the correspondence and sync status so that feedback
(MES -> ERP) and reconciliation can always find the counterpart.

In a real deployment this table is the join point between the two
systems' message payloads and is the first place to look when integration
drifts.
"""
from __future__ import annotations

from typing import Any

from ..models import ErpMesMapping, SyncStatus
from ..store import Store


class MappingRegistry:
    def __init__(self, store: Store) -> None:
        self._store = store

    def link(self, erp_id: str, mes_id: str, target_type: str,
             status: SyncStatus = SyncStatus.SYNCED) -> ErpMesMapping:
        mapping = ErpMesMapping(erp_id=erp_id, mes_id=mes_id, target_type=target_type, status=status)
        self._store.upsert_mapping(mapping)
        return mapping

    def mes_for(self, erp_id: str) -> str | None:
        m = self._store.get_mapping_by_erp(erp_id)
        return m.mes_id if m else None

    def erp_for(self, mes_id: str) -> str | None:
        m = self._store.get_mapping_by_mes(mes_id)
        return m.erp_id if m else None

    def all(self) -> list[ErpMesMapping]:
        return self._store.list_mappings()

    def update_status(self, erp_id: str, mes_id: str, status: SyncStatus) -> None:
        from datetime import datetime

        mapping = ErpMesMapping(
            erp_id=erp_id, mes_id=mes_id, target_type="work_order",
            status=status, last_synced_at=datetime.now().isoformat(timespec="seconds"),
        )
        self._store.upsert_mapping(mapping)

    def snapshot(self) -> list[dict[str, Any]]:
        return [m.__dict__ for m in self.all()]