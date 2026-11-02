"""ERP domain: order management, materials, BOM and MRP.

Simulates the ERP side of the integration.  It is deliberately richer than the
original toy: multi-level BOM, inventory lots with reservations, purchase
orders, and a material-requirements engine.  Lifecycle mutations are audited
and publish outbox events so the MES side can react (transactional outbox).
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

from .integration import Integration
from .models import (
    AbcClass,
    Customer,
    EventType,
    Item,
    ItemType,
    MrpProposal,
    MrpProposalType,
    OrderStatus,
    Priority,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    SalesOrder,
    SalesOrderLine,
    SalesOrderStatus,
    SyncStatus,
    WorkOrder,
)
from .store import Store


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ERPService:
    def __init__(self, store: Store, integration: Integration | None = None) -> None:
        self._s = store
        self._itg = integration

    # ------------------------------------------------------------------ meta
    def health(self) -> dict[str, Any]:
        wo = self._s.list_work_orders()
        return {
            "status": "ok",
            "work_orders": len(wo),
            "released_orders": sum(1 for w in wo if w.status == OrderStatus.RELEASED),
            "items": len(self._s.list_items()),
            "sales_orders": len(self._s.list_sales_orders()),
        }

    # ----------------------------------------------------------------- items
    def list_items(self, item_type: str | None = None) -> list[Item]:
        return self._s.list_items(item_type=item_type)

    def get_item(self, sku: str) -> Item:
        item = self._s.get_item(sku)
        if not item:
            raise KeyError(f"unknown item sku {sku}")
        return item

    def create_item(self, sku: str, name: str, item_type: str, unit: str,
                    lead_time_days: int = 1, safety_stock: float = 0.0,
                    abc_class: str = "C", **extra: Any) -> Item:
        item = Item(sku=sku, name=name, item_type=ItemType(item_type), unit=unit,
                    lead_time_days=lead_time_days, safety_stock=safety_stock,
                    abc_class=AbcClass(abc_class), **extra)
        with self._s.transaction():
            self._s.upsert_item(item)
            self._audit("create", "item", sku, None, asdict(item))
        return item

    # ------------------------------------------------------------------- BOM
    def get_bom(self, bom_code: str) -> dict[str, Any]:
        header = self._s.get_bom_header(bom_code)
        if not header:
            raise KeyError(f"unknown bom {bom_code}")
        lines = self._s.get_bom_lines(bom_code)
        return {"header": asdict(header), "lines": [asdict(ln) for ln in lines]}

    def get_bom_by_product(self, product_sku: str) -> dict[str, Any]:
        item = self.get_item(product_sku)
        if not item.default_bom_code:
            raise KeyError(f"item {product_sku} has no default BOM")
        return self.get_bom(item.default_bom_code)

    def gross_requirements(self, product_sku: str, quantity: int) -> dict[str, float]:
        """Full multi-level BOM explosion (ignoring stock) -> raw requirements.

        Returns a mapping of ``raw/consumable sku -> gross quantity`` including
        scrap factors, recursing through semi-finished goods.
        """
        want: dict[str, float] = {}
        self._gross(product_sku, float(quantity), want, 0)
        return want

    def _gross(self, sku: str, qty: float, want: dict[str, float], depth: int) -> None:
        if depth > 10:
            return
        item = self._s.get_item(sku)
        if item is None:
            return
        if item.item_type in (ItemType.RAW, ItemType.CONSUMABLE):
            want[sku] = want.get(sku, 0.0) + qty
            return
        bom_code = item.default_bom_code
        if not bom_code:
            return
        for line in self._s.get_bom_lines(bom_code):
            need = line.qty_per * (1.0 + line.scrap_rate)
            self._gross(line.component_sku, qty * need, want, depth + 1)

    def bom_explode(self, product_sku: str, quantity: int) -> list[dict[str, Any]]:
        """Gross requirement vs available stock, per raw/consumable material."""
        want = self.gross_requirements(product_sku, quantity)
        out = []
        for sku, gross in sorted(want.items()):
            item = self._s.get_item(sku)
            available = self._s.get_inventory_available(sku)
            out.append({
                "sku": sku, "name": item.name if item else sku, "unit": item.unit if item else "",
                "required": round(gross, 2), "available": available,
                "shortage": round(max(0.0, gross - available), 2),
            })
        return out

    def check_material_availability(self, order_no: str) -> dict[str, Any]:
        """齐套检查: explode the order's BOM and flag shortages."""
        wo = self._s.get_work_order(order_no)
        if not wo:
            raise KeyError(f"unknown work order {order_no}")
        gaps = [g for g in self.bom_explode(wo.product_sku, wo.quantity) if g["shortage"] > 0]
        return {
            "order_no": order_no,
            "product_sku": wo.product_sku,
            "quantity": wo.quantity,
            "fulfilled": len(gaps) == 0,
            "requirements": self.bom_explode(wo.product_sku, wo.quantity),
            "shortages": gaps,
        }

    # ------------------------------------------------------------- inventory
    def inventory_summary(self, sku: str | None = None) -> list[dict[str, Any]]:
        lots = self._s.list_inventory(sku=sku)
        by_sku: dict[str, dict[str, Any]] = {}
        for lot in lots:
            agg = by_sku.setdefault(lot.sku, {
                "sku": lot.sku, "on_hand": 0.0, "reserved": 0.0, "in_transit": 0.0, "available": 0.0})
            agg["on_hand"] += lot.on_hand
            agg["reserved"] += lot.reserved
            agg["in_transit"] += lot.in_transit
        for a in by_sku.values():
            a["available"] = a["on_hand"] - a["reserved"]
        return sorted(by_sku.values(), key=lambda x: x["sku"])

    def inventory_adjust(self, sku: str, lot_no: str, delta_on_hand: float,
                         delta_reserved: float = 0.0) -> dict[str, Any]:
        with self._s.transaction():
            self._s.adjust_inventory(sku, lot_no, delta_on_hand, delta_reserved)
            self._audit("adjust", "inventory", sku, {"delta_on_hand": delta_on_hand})
        return {"sku": sku, "lot_no": lot_no, "available": self._s.get_inventory_available(sku)}

    # ---------------------------------------------------------- sales orders
    def list_sales_orders(self, status: str | None = None) -> list[SalesOrder]:
        return self._s.list_sales_orders(status=status)

    def get_sales_order(self, order_no: str) -> SalesOrder:
        so = self._s.get_sales_order(order_no)
        if not so:
            raise KeyError(f"unknown sales order {order_no}")
        return so

    def create_sales_order(self, order_no: str, customer_id: str, due_date: str,
                           priority: str, lines: list[dict[str, Any]],
                           order_date: str | None = None) -> SalesOrder:
        customer = self._s.get_customer(customer_id)
        if not customer:
            raise KeyError(f"unknown customer {customer_id}")
        so = SalesOrder(
            order_no=order_no, customer_id=customer_id, order_date=order_date or _now()[:10],
            due_date=due_date, priority=Priority(priority), status=SalesOrderStatus.CONFIRMED,
            lines=[SalesOrderLine(seq=ln.get("seq", i + 1), item_sku=ln["item_sku"],
                                  quantity=ln["quantity"], unit_price=ln.get("unit_price", 0.0))
                   for i, ln in enumerate(lines)],
        )
        with self._s.transaction():
            self._s.insert_sales_order(so)
            self._audit("create", "sales_order", order_no, None, asdict(so))
        return so

    # -------------------------------------------------------- purchase orders
    def list_purchase_orders(self, status: str | None = None) -> list[PurchaseOrder]:
        return self._s.list_purchase_orders(status=status)

    def create_purchase_order(self, po_no: str, supplier: str, due_date: str,
                              lines: list[dict[str, Any]]) -> PurchaseOrder:
        po = PurchaseOrder(
            po_no=po_no, supplier=supplier, order_date=_now()[:10], due_date=due_date,
            status=PurchaseOrderStatus.ISSUED,
            lines=[PurchaseOrderLine(seq=ln.get("seq", i + 1), item_sku=ln["item_sku"],
                                     quantity=ln["quantity"]) for i, ln in enumerate(lines)],
        )
        with self._s.transaction():
            self._s.insert_purchase_order(po)
            self._audit("create", "purchase_order", po_no, None, asdict(po))
        return po

    def receive_purchase_order(self, po_no: str) -> dict[str, Any]:
        po = self._s.get_purchase_order(po_no)
        if not po:
            raise KeyError(f"unknown purchase order {po_no}")
        with self._s.transaction():
            for line in po.lines:
                self._s.upsert_inventory(line.item_sku, "PO-RECEIPT", "WH-RAW", line.quantity, 0.0)
            # simple lot assignment for raw materials
            self._s.update_purchase_order(po_no, status=PurchaseOrderStatus.RECEIVED)
        return {"po_no": po_no, "status": PurchaseOrderStatus.RECEIVED}

    # ------------------------------------------------------------ work orders
    def list_work_orders(self, status: str | None = None,
                         due_date_from: str | None = None,
                         due_date_to: str | None = None) -> list[WorkOrder]:
        return self._s.list_work_orders(status=status, due_date_from=due_date_from, due_date_to=due_date_to)

    def get_work_order(self, order_no: str) -> WorkOrder:
        wo = self._s.get_work_order(order_no)
        if not wo:
            raise KeyError(f"unknown work order {order_no}")
        return wo

    def create_work_order(self, order_no: str, product_sku: str, quantity: int,
                          due_date: str, priority: str = "NORMAL", customer: str = "",
                          sales_order_no: str | None = None) -> WorkOrder:
        item = self.get_item(product_sku)
        wo = WorkOrder(
            order_no=order_no, product_sku=product_sku, product_name=item.name,
            quantity=quantity, status=OrderStatus.DRAFT, priority=Priority(priority),
            due_date=due_date, customer=customer, sales_order_no=sales_order_no,
            bom_code=item.default_bom_code, routing_code=item.default_routing_code,
        )
        with self._s.transaction():
            self._s.insert_work_order(wo)
            self._audit("create", "work_order", order_no, None, _snapshot(wo))
        return wo

    def release_work_order(self, order_no: str) -> dict[str, Any]:
        """Release a work order, announcing it to MES via the outbox."""
        wo = self.get_work_order(order_no)
        if wo.status != OrderStatus.DRAFT:
            raise ValueError(f"work order {order_no} is {wo.status}, not DRAFT")
        before = _snapshot(wo)
        with self._s.transaction():
            self._s.update_work_order(order_no, status=OrderStatus.RELEASED, released_at=_now())
            self._audit("release", "work_order", order_no, before,
                        _snapshot(self._s.get_work_order(order_no)))
            if self._itg:
                self._itg.outbox.publish(
                    EventType.ORDER_RELEASE, "work_order", order_no,
                    {"order_no": order_no, "product_sku": wo.product_sku,
                     "quantity": wo.quantity, "due_date": wo.due_date})
        return {"order_no": order_no, "status": OrderStatus.RELEASED}

    def complete_work_order(self, order_no: str, completed_qty: int | None = None) -> dict[str, Any]:
        wo = self.get_work_order(order_no)
        before = _snapshot(wo)
        with self._s.transaction():
            self._s.update_work_order(
                order_no, status=OrderStatus.COMPLETED,
                completed_qty=completed_qty if completed_qty is not None else wo.quantity,
                completed_at=_now())
            self._audit("complete", "work_order", order_no, before,
                        _snapshot(self._s.get_work_order(order_no)))
        return {"order_no": order_no, "status": OrderStatus.COMPLETED}

    def close_work_order(self, order_no: str) -> dict[str, Any]:
        wo = self.get_work_order(order_no)
        with self._s.transaction():
            self._s.update_work_order(order_no, status=OrderStatus.CLOSED)
            self._audit("close", "work_order", order_no, _snapshot(wo),
                        _snapshot(self._s.get_work_order(order_no)))
        return {"order_no": order_no, "status": OrderStatus.CLOSED}

    # ------------------------------------------------------------------- MRP
    def run_mrp(self) -> dict[str, Any]:
        """Demand explosion focused on raw material shortfalls -> proposals."""
        with self._s.transaction():
            self._s.clear_proposals()
            proposals: list[MrpProposal] = []
            for wo in self._s.list_work_orders():
                if wo.status not in (OrderStatus.RELEASED,):
                    continue
                for gap in self.bom_explode(wo.product_sku, wo.quantity):
                    if gap["shortage"] > 0:
                        item = self._s.get_item(gap["sku"])
                        p = MrpProposal(
                            proposal_no=f"MP-{wo.order_no}-{gap['sku']}",
                            item_sku=gap["sku"], proposal_type=MrpProposalType.PURCHASE,
                            quantity=gap["shortage"], due_date=wo.due_date,
                            source_work_order=wo.order_no)
                        self._s.insert_proposal(p)
                        proposals.append(p)
        return {"proposals": [_snapshot(p) for p in proposals],
                "count": len(proposals)}

    def list_proposals(self, status: str | None = None) -> list[MrpProposal]:
        return self._s.list_proposals(status=status)

    # --------------------------------------------------------------- helpers
    def _audit(self, operation: str, resource: str, resource_id: str,
               before: Any = None, after: Any = None) -> None:
        if self._itg:
            self._itg.audit.record(
                operator="system", operation=operation, resource=resource,
                resource_id=resource_id, correlation_id="", before=before, after=after)


def _snapshot(obj: Any) -> dict[str, Any]:
    return asdict(obj)