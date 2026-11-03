"""MES domain: shop-floor execution, OEE, quality and material issues.

Simulates the MES side: work centers with real-time status, process routings,
production orders (the MES counterpart of ERP work orders), per-operation
progress, downtime events feeding OEE, quality inspection and material
backflushing.  Execution feedback is published back to ERP via the outbox.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

from .errors import ValidationError
from .integration import Integration
from .models import (
    DowntimeEvent,
    DowntimeReason,
    EventType,
    ItemType,
    LotGenealogyLink,
    MaterialConsumption,
    MaterialIssue,
    OperationProgress,
    OperationStatus,
    ProductionOrder,
    ProductionOrderStatus,
    QualityDisposition,
    QualityRecord,
    WorkCenter,
    WorkCenterStatus,
    WorkOrder,
)
from .store import Store, new_id


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class MESService:
    def __init__(self, store: Store, integration: Integration | None = None) -> None:
        self._s = store
        self._itg = integration

    # ------------------------------------------------------------------ meta
    def health(self) -> dict[str, Any]:
        po = self._s.list_production_orders()
        wc = self._s.list_work_centers()
        return {
            "status": "ok",
            "production_orders": len(po),
            "in_process": sum(1 for p in po if p.status == ProductionOrderStatus.IN_PROCESS),
            "work_centers": len(wc),
            "busy": sum(1 for c in wc if c.status == WorkCenterStatus.RUNNING),
        }

    # --------------------------------------------------------- work centers
    def list_work_centers(self, status: str | None = None) -> list[WorkCenter]:
        return self._s.list_work_centers(status=status)

    def get_work_center(self, code: str) -> WorkCenter:
        wc = self._s.get_work_center(code)
        if not wc:
            raise KeyError(f"unknown work center {code}")
        return wc

    def set_work_center_status(self, code: str, status: str) -> dict[str, Any]:
        self.get_work_center(code)  # validate
        with self._s.transaction():
            self._s.update_work_center(code, status=status)
            self._audit("set_status", "work_center", code, {"status": status})
        return {"code": code, "status": status}

    def list_shifts(self, work_center: str | None = None, shift_date: str | None = None) -> list[Any]:
        return [asdict(s) for s in self._s.list_shifts(work_center=work_center, shift_date=shift_date)]

    # -------------------------------------------------------------- routings
    def list_routings(self, product_sku: str | None = None, routing_code: str | None = None) -> list[Any]:
        return [asdict(r) for r in self._s.list_routing_operations(routing_code=routing_code, product_sku=product_sku)]

    def get_routing(self, routing_code: str) -> list[Any]:
        return [asdict(r) for r in self._s.list_routing_operations(routing_code=routing_code)]

    # ----------------------------------------------------- production orders
    def list_production_orders(self, status: str | None = None) -> list[ProductionOrder]:
        return self._s.list_production_orders(status=status)

    def get_production_order(self, production_order_no: str) -> ProductionOrder:
        po = self._s.get_production_order(production_order_no)
        if not po:
            raise KeyError(f"unknown production order {production_order_no}")
        return po

    def list_operations(self, production_order_no: str) -> list[OperationProgress]:
        return self._s.list_operation_progress(production_order_no=production_order_no)

    def list_quality_records(self, production_order_no: str | None = None) -> list[QualityRecord]:
        return self._s.list_quality_records(production_order_no=production_order_no)

    def create_production_order(self, work_order: WorkOrder) -> ProductionOrder:
        """Create the MES counterpart of a released ERP work order."""
        existing = self._s.get_production_order_by_erp(work_order.order_no)
        if existing:
            return existing
        po = ProductionOrder(
            production_order_no=new_id("PO-", 10),
            erp_work_order_no=work_order.order_no,
            product_sku=work_order.product_sku,
            product_name=work_order.product_name,
            quantity=work_order.quantity,
            status=ProductionOrderStatus.DRAFT,
            priority=work_order.priority,
            due_date=work_order.due_date,
        )
        with self._s.transaction():
            self._s.insert_production_order(po)
            # pre-create operation progress from routing
            for op in self._s.list_routing_operations(routing_code=work_order.routing_code):
                self._s.insert_operation_progress(OperationProgress(
                    production_order_no=po.production_order_no,
                    operation_no=op.operation_no, work_center=op.work_center,
                    status=OperationStatus.PENDING))
            if self._itg:
                self._itg.mapping.link(work_order.order_no, po.production_order_no, "work_order")
                self._audit("create", "production_order", po.production_order_no,
                            None, _snapshot(po))
        return po

    def start_production_order(self, production_order_no: str) -> dict[str, Any]:
        po = self.get_production_order(production_order_no)
        ops = self.list_operations(production_order_no)
        with self._s.transaction():
            self._s.update_production_order(production_order_no, status=ProductionOrderStatus.IN_PROCESS)
            if ops:
                first = ops[0]
                self._s.update_operation_progress(
                    production_order_no, first.operation_no,
                    status=OperationStatus.SETUP, started_at=_now())
                self._s.update_work_center(first.work_center, status=WorkCenterStatus.SETUP,
                                           current_production_order=production_order_no)
        return {"production_order_no": production_order_no, "status": ProductionOrderStatus.IN_PROCESS}

    def report_progress(self, production_order_no: str, operation_no: int,
                        qty_good: int, qty_bad: int = 0, complete: bool = False) -> dict[str, Any]:
        """Report good/bad quantities on an operation.

        When ``complete`` is set, the operation is marked COMPLETED and the
        next operation is armed; completing the last operation completes the
        production order and publishes a COMPLETION event back to ERP.
        """
        po = self.get_production_order(production_order_no)
        ops = self.list_operations(production_order_no)
        ordered = sorted(ops, key=lambda o: o.operation_no)
        was_completed = po.status == ProductionOrderStatus.COMPLETED
        with self._s.transaction():
            self._s.update_operation_progress(
                production_order_no, operation_no, qty_good=qty_good, qty_bad=qty_bad)
            final_status: str | None = None
            if complete:
                self._s.update_operation_progress(
                    production_order_no, operation_no, status=OperationStatus.COMPLETED,
                    completed_at=_now())
                last_op = ordered[-1].operation_no if ordered else operation_no
                if operation_no == last_op:
                    self._s.update_production_order(
                        production_order_no, status=ProductionOrderStatus.COMPLETED,
                        completed_qty=qty_good)
                    final_status = ProductionOrderStatus.COMPLETED
                    self._s.update_work_center(
                        self._work_center_of(ordered, operation_no), status=WorkCenterStatus.IDLE,
                        current_production_order=None)
                    if not was_completed:
                        self._record_output_lot(production_order_no, qty_good, po)
                else:
                    next_op = next(o for o in ordered if o.operation_no > operation_no)
                    self._s.update_operation_progress(
                        production_order_no, next_op.operation_no, status=OperationStatus.SETUP)
        if final_status == ProductionOrderStatus.COMPLETED and self._itg:
            self._itg.outbox.publish(
                EventType.COMPLETION, "production_order", production_order_no,
                {"production_order_no": production_order_no, "erp_work_order_no": po.erp_work_order_no,
                 "completed_qty": qty_good})
        return {"production_order_no": production_order_no, "operation_no": operation_no,
                "qty_good": qty_good, "qty_bad": qty_bad,
                "production_order_status": final_status or po.status}

    @staticmethod
    def _work_center_of(ops: list[OperationProgress], operation_no: int) -> str:
        for o in ops:
            if o.operation_no == operation_no:
                return o.work_center
        return ""

    # ------------------------------------------------------------ downtime
    def report_downtime(self, work_center: str, reason: str, start_time: str,
                        end_time: str | None = None) -> dict[str, Any]:
        self.get_work_center(work_center)
        duration = 0.0
        if end_time:
            duration = (_parse(end_time) - _parse(start_time)).total_seconds() / 60.0
        event = DowntimeEvent(
            event_id=new_id("DT-", 10), work_center=work_center, reason=DowntimeReason(reason),
            start_time=start_time, end_time=end_time, duration_min=round(duration, 1))
        with self._s.transaction():
            self._s.insert_downtime(event)
            self._s.update_work_center(work_center,
                                       status=WorkCenterStatus.BREAKDOWN if reason == "BREAKDOWN" else WorkCenterStatus.MAINTENANCE)
        return asdict(event)

    def oee_report(self, work_center: str) -> dict[str, Any]:
        self.get_work_center(work_center)
        events = self._s.list_downtime(work_center=work_center)
        downtime_min = sum(e.duration_min for e in events)
        # availability against an 8h shift window
        availability = round(max(0.0, 1 - downtime_min / (8 * 60)), 3)
        # performance / quality are placeholders computed from recorded data where available
        quality_records = [q for q in self._s.list_quality_records()
                           if q.production_order_no]  # plant-level simplification
        quality = 1.0
        if quality_records:
            total = sum(q.sample_qty for q in quality_records)
            defects = sum(q.defect_qty for q in quality_records)
            quality = round(1 - defects / total, 3) if total else 1.0
        # performance from completed quantities vs capacity
        performance = 0.9
        oee = round(availability * performance * quality, 3)
        return {"work_center": work_center, "availability": availability,
                "performance": performance, "quality": quality, "oee": oee,
                "downtime_min": round(downtime_min, 1)}

    # ------------------------------------------------------------- quality
    def quality_inspect(self, production_order_no: str, operation_no: int, sample_qty: int,
                        defect_qty: int, disposition: str, inspector: str = "") -> dict[str, Any]:
        record = QualityRecord(
            record_no=new_id("QC-", 10), production_order_no=production_order_no,
            operation_no=operation_no, sample_qty=sample_qty, defect_qty=defect_qty,
            disposition=QualityDisposition(disposition), inspector=inspector)
        with self._s.transaction():
            self._s.insert_quality_record(record)
            if self._itg:
                self._itg.audit.record(
                    operator=inspector or "qc", operation="quality_inspect",
                    resource="quality", resource_id=record.record_no, correlation_id="",
                    before={"op": operation_no}, after=_snapshot(record))
        return asdict(record)

    # ------------------------------------------------------ material issues
    def issue_material(self, production_order_no: str, item_sku: str, quantity: float) -> dict[str, Any]:
        po = self.get_production_order(production_order_no)
        with self._s.transaction():
            allocations = self._s.reserve_inventory_by_sku(item_sku, quantity)
            issue = MaterialIssue(issue_no=new_id("MI-", 10), production_order_no=production_order_no,
                                  item_sku=item_sku, quantity=quantity)
            self._s.insert_material_issue(issue)
            for alloc in allocations:
                self._s.insert_consumption(MaterialConsumption(
                    production_order_no=production_order_no, item_sku=item_sku,
                    lot_no=alloc["lot_no"], quantity=alloc["quantity"]))
        result = asdict(issue)
        result["lots"] = allocations
        return result

    def list_consumptions(self, production_order_no: str | None = None) -> list[dict[str, Any]]:
        return [asdict(c) for c in self._s.list_consumptions(production_order_no)]

    def trace_lot(self, lot_no: str, direction: str = "both", max_depth: int = 10) -> dict[str, Any]:
        if direction not in ("up", "down", "both"):
            raise ValidationError("direction must be one of up/down/both")
        parents: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []
        if direction in ("up", "both"):
            parents = self._walk(lot_no, up=True, max_depth=max_depth)
        if direction in ("down", "both"):
            children = self._walk(lot_no, up=False, max_depth=max_depth)
        return {"lot_no": lot_no, "direction": direction,
                "parents": parents, "children": children,
                "upstream_lots": sorted({p["parent_lot"] for p in parents} - {lot_no}),
                "downstream_lots": sorted({c["child_lot"] for c in children} - {lot_no})}

    def _walk(self, lot_no: str, *, up: bool, max_depth: int) -> list[dict[str, Any]]:
        seen = {lot_no}
        frontier = [lot_no]
        edges: list[dict[str, Any]] = []
        for _ in range(max(1, max_depth)):
            next_frontier: list[str] = []
            for lot in frontier:
                links = self._s.genealogy_parents(lot) if up else self._s.genealogy_children(lot)
                for link in links:
                    nxt = link.parent_lot if up else link.child_lot
                    edges.append({
                        "parent_sku": link.parent_sku, "parent_lot": link.parent_lot,
                        "child_sku": link.child_sku, "child_lot": link.child_lot,
                        "production_order_no": link.production_order_no,
                        "quantity": link.quantity, "depth": _,
                    })
                    if nxt not in seen:
                        seen.add(nxt)
                        next_frontier.append(nxt)
            if not next_frontier:
                break
            frontier = next_frontier
        return edges

    def _record_output_lot(self, production_order_no: str, qty_good: float, po: ProductionOrder) -> None:
        if qty_good <= 0:
            return
        item = self._s.get_item(po.product_sku)
        warehouse = "WH-WIP" if item and item.item_type == ItemType.SEMI else "WH-FG"
        lot_no = f"LOT-{production_order_no}"
        self._s.upsert_inventory(po.product_sku, lot_no, warehouse, qty_good)
        for c in self._s.list_consumptions(production_order_no):
            self._s.insert_genealogy(LotGenealogyLink(
                genealogy_id=new_id("LG-", 12), parent_sku=c.item_sku, parent_lot=c.lot_no,
                child_sku=po.product_sku, child_lot=lot_no,
                production_order_no=production_order_no, quantity=c.quantity))

    # --------------------------------------------------------------- helpers
    def _audit(self, operation: str, resource: str, resource_id: str,
               before: Any = None, after: Any = None) -> None:
        if self._itg:
            self._itg.audit.record(
                operator="system", operation=operation, resource=resource,
                resource_id=resource_id, correlation_id="", before=before, after=after)


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _snapshot(obj: Any) -> dict[str, Any]:
    return asdict(obj)