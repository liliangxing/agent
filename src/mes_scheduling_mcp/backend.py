"""Backend facade: the single entry point the MCP layer talks to.

Owns the store, the integration fabric, and the three domain services
(ERP / MES / APS), and exposes the cross-system operations that glue them
together:

- ``synchronize_orders``   ERP -> MES (consume ORDER_RELEASE, create production orders)
- ``synchronize_progress`` MES -> ERP (consume COMPLETION, complete work orders)
- ``generate_schedule`` / ``dispatch_schedule``  APS finite-capacity scheduling
- ``reconcile``            detect ERP <-> MES drift

In a production deployment the ERP/MES domain services would be replaced by
adapters to real 金蝶 / Odoo / SAP / a custom MES WebAPI — but the integration
seam (idempotency, outbox, mapping, audit) and the MCP tool signatures do not
change.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .erp import ERPService
from .errors import NotFoundError, ValidationError
from .integration import Integration, Outbox
from .mes import MESService
from .models import EventType, OrderStatus, ProductionOrderStatus
from .scheduling import APSService
from .store import Store


class ManufacturingBackend:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store if store is not None else Store()
        self.integration = Integration(self.store)
        self.erp = ERPService(self.store, self.integration)
        self.mes = MESService(self.store, self.integration)
        self.aps = APSService(self.store, erp=self.erp, integration=self.integration)

    # ---------------------------------------------------------------- health
    def health(self) -> dict[str, Any]:
        return {
            "erp": self.erp.health(),
            "mes": self.mes.health(),
            "outbox_pending": len(self.integration.outbox.pending()),
            "mapping_count": len(self.integration.mapping.all()),
        }

    def reset(self) -> None:
        self.store.reset()

    # ------------------------------------------------------- cross-system ops
    def synchronize_orders(self) -> dict[str, Any]:
        """ERP -> MES: create MES production orders for released ERP work orders.

        Processes the ORDER_RELEASE outbox events (transactional outbox
        semantics) so the integration behaves like a real message-driven handoff
        even though both systems share one store in the demo.
        """
        events = [e for e in self.integration.outbox.list()
                  if e.event_type == EventType.ORDER_RELEASE and str(e.status) != "ACKED"]
        created = []
        for e in events:
            wo = self.store.get_work_order(e.aggregate_id)
            if wo and wo.status == OrderStatus.RELEASED:
                po = self.mes.create_production_order(wo)
                created.append({"erp": wo.order_no, "mes": po.production_order_no})
                self.integration.outbox.receive(e)
                self.integration.outbox.mark_acked(e.event_id)
        return {"created": created, "count": len(created)}

    def synchronize_progress(self) -> dict[str, Any]:
        """MES -> ERP: complete ERP work orders from COMPLETION feedback."""
        events = [e for e in self.integration.outbox.list()
                  if e.event_type == EventType.COMPLETION and str(e.status) != "ACKED"]
        updated = []
        for e in events:
            erp_no = e.payload.get("erp_work_order_no")
            qty = e.payload.get("completed_qty")
            wo = self.store.get_work_order(erp_no)
            if wo and wo.status != OrderStatus.COMPLETED:
                self.erp.complete_work_order(erp_no, completed_qty=qty)
                updated.append({"erp": erp_no, "status": OrderStatus.COMPLETED})
            self.integration.outbox.receive(e)
            self.integration.outbox.mark_acked(e.event_id)
        return {"updated": updated, "count": len(updated)}

    def reconcile(self) -> dict[str, Any]:
        return self.integration.reconciler.run()

    # ---------------------------------------------------------- observability
    def outbox_status(self, status: str | None = None) -> dict[str, Any]:
        events = self.integration.outbox.list(status=status)
        return {"events": [
            {"event_id": e.event_id, "event_type": e.event_type, "aggregate_type": e.aggregate_type,
             "aggregate_id": e.aggregate_id, "status": e.status, "created_at": e.created_at}
            for e in events
        ], "count": len(events)}

    def inbox_status(self) -> dict[str, Any]:
        return {"events": self.integration.outbox.inbox(), "count": len(self.integration.outbox.inbox())}

    def mappings(self) -> dict[str, Any]:
        return {"mappings": self.integration.mapping.snapshot()}

    def audit_log(self, resource: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"entries": self.integration.audit.query(resource=resource, limit=limit)}

    # ------------------------------------------------------- resource lookup
    def resource_get(self, resource: str, record_id: str) -> dict[str, Any]:
        store = self.store
        if resource == "work_order":
            return asdict(store.get_work_order(record_id)) if store.get_work_order(record_id) else _missing(resource, record_id)
        if resource == "item":
            return asdict(store.get_item(record_id)) if store.get_item(record_id) else _missing(resource, record_id)
        if resource == "sales_order":
            so = store.get_sales_order(record_id)
            return asdict(so) if so else _missing(resource, record_id)
        if resource == "purchase_order":
            po = store.get_purchase_order(record_id)
            return asdict(po) if po else _missing(resource, record_id)
        if resource == "work_center":
            wc = store.get_work_center(record_id)
            return asdict(wc) if wc else _missing(resource, record_id)
        if resource == "production_order":
            poo = store.get_production_order(record_id)
            return asdict(poo) if poo else _missing(resource, record_id)
        if resource == "schedule":
            plan = store.get_schedule_plan(record_id)
            if not plan:
                return _missing(resource, record_id)
            return {"plan": asdict(plan), "operations": [asdict(o) for o in store.list_schedule_operations(record_id)]}
        if resource == "routing":
            ops = store.list_routing_operations(routing_code=record_id)
            if not ops:
                return _missing(resource, record_id)
            return {"routing_code": record_id, "product_sku": ops[0].product_sku,
                    "operations": [asdict(o) for o in ops]}
        raise ValidationError(f"未知资源: {resource}")


def _missing(resource: str, record_id: str) -> dict[str, Any]:
    raise NotFoundError(f"{resource}({record_id}) 不存在")