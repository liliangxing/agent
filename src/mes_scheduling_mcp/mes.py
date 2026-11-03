"""MES domain: shop-floor execution, OEE, quality and material issues.

Simulates the MES side: work centers with real-time status, process routings,
production orders (the MES counterpart of ERP work orders), per-operation
progress, downtime events feeding OEE, quality inspection and material
backflushing.  Execution feedback is published back to ERP via the outbox.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict
from datetime import datetime, timedelta
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
    ProductionRun,
    QualityDisposition,
    QualityRecord,
    RunStatus,
    TestRecord,
    TestResult,
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

    def list_tests(self, production_order_no: str | None = None,
                   result: str | None = None) -> list[TestRecord]:
        return self._s.list_tests(production_order_no=production_order_no, result=result)

    def list_runs(self, production_order_no: str | None = None,
                  work_center: str | None = None,
                  status: str | None = None) -> list[ProductionRun]:
        return self._s.list_runs(production_order_no=production_order_no,
                                 work_center=work_center, status=status)

    def get_run(self, run_no: str) -> ProductionRun:
        run = self._s.get_run(run_no)
        if not run:
            raise KeyError(f"unknown run {run_no}")
        return run

    def get_work_instructions(self, production_order_no: str,
                              operation_no: int | None = None) -> dict[str, Any]:
        po = self.get_production_order(production_order_no)
        item = self._s.get_item(po.product_sku)
        routing_code = item.default_routing_code if item else None
        ops = self._s.list_routing_operations(routing_code=routing_code)
        progress = {o.operation_no: o for o in self.list_operations(production_order_no)}
        selected = [op for op in ops if operation_no is None or op.operation_no == operation_no]
        if not selected:
            raise KeyError(f"routing operation {operation_no} not found for {production_order_no}")
        return {
            "production_order_no": production_order_no,
            "product_sku": po.product_sku,
            "routing_code": routing_code,
            "operations": [
                {
                    "operation_no": op.operation_no,
                    "operation_name": op.operation_name,
                    "work_center": op.work_center,
                    "tooling": op.tooling,
                    "setup_time_min": op.setup_time_min,
                    "run_time_min_per_unit": op.run_time_min_per_unit,
                    "inspection_required": op.inspection_required,
                    "instructions": op.instructions,
                    "status": str(progress[op.operation_no].status)
                    if op.operation_no in progress else "PENDING",
                }
                for op in selected
            ],
        }

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

    # --------------------------------------------------------------- runs / OEE
    def start_run(self, production_order_no: str, operation_no: int,
                  planned_minutes: float | None = None,
                  ideal_cycle_sec: float | None = None) -> dict[str, Any]:
        po = self.get_production_order(production_order_no)
        item = self._s.get_item(po.product_sku)
        routing_code = item.default_routing_code if item else None
        op = self._s.get_routing_operation(routing_code, operation_no) if routing_code else None
        if not op:
            raise KeyError(f"routing operation {operation_no} not found for {production_order_no}")
        self.get_work_center(op.work_center)
        active = self._s.list_runs(work_center=op.work_center, status=str(RunStatus.RUNNING))
        if active:
            raise ValidationError(
                f"{op.work_center} 已有运行中的报工 {active[0].run_no}，请先结束或转移")
        if planned_minutes is None:
            planned_minutes = round(op.setup_time_min + op.run_time_min_per_unit * po.quantity, 1)
        if ideal_cycle_sec is None:
            ideal_cycle_sec = round(op.run_time_min_per_unit * 60, 1)
        if planned_minutes <= 0:
            raise ValidationError("planned_minutes must be positive")
        run = ProductionRun(
            run_no=new_id("RUN-", 10), production_order_no=production_order_no,
            operation_no=operation_no, work_center=op.work_center,
            planned_minutes=planned_minutes, ideal_cycle_sec=ideal_cycle_sec)
        with self._s.transaction():
            self._s.insert_run(run)
            self._s.update_work_center(op.work_center, status=WorkCenterStatus.RUNNING,
                                       current_production_order=production_order_no)
            if po.status in (ProductionOrderStatus.DRAFT, ProductionOrderStatus.RELEASED):
                self._s.update_production_order(production_order_no,
                                                status=ProductionOrderStatus.IN_PROCESS)
            current = self._s.list_operation_progress(production_order_no=production_order_no)
            existing = next((o for o in current if o.operation_no == operation_no), None)
            if existing and existing.status in (OperationStatus.PENDING, OperationStatus.SETUP):
                self._s.update_operation_progress(
                    production_order_no, operation_no, status=OperationStatus.RUNNING,
                    started_at=existing.started_at or _now())
            self._audit("start_run", "production_run", run.run_no, None, _snapshot(run))
        return asdict(run)

    def log_run_output(self, run_no: str, qty_good: int, qty_scrap: int = 0) -> dict[str, Any]:
        run = self.get_run(run_no)
        if run.status == RunStatus.ENDED:
            raise ValidationError(f"run {run_no} already ended")
        if qty_good < 0 or qty_scrap < 0:
            raise ValidationError("qty_good/qty_scrap must be non-negative")
        with self._s.transaction():
            self._s.update_run(run_no, qty_good=run.qty_good + qty_good,
                               qty_scrap=run.qty_scrap + qty_scrap)
            ops = self._s.list_operation_progress(production_order_no=run.production_order_no)
            op = next((o for o in ops if o.operation_no == run.operation_no), None)
            if op:
                self._s.update_operation_progress(
                    run.production_order_no, run.operation_no,
                    qty_good=op.qty_good + qty_good, qty_bad=op.qty_bad + qty_scrap)
        updated = self.get_run(run_no)
        return asdict(updated)

    def log_run_downtime(self, run_no: str, reason: str, minutes: float,
                         planned: bool = False, start_time: str | None = None) -> dict[str, Any]:
        run = self.get_run(run_no)
        if minutes <= 0:
            raise ValidationError("minutes must be positive")
        start = datetime.fromisoformat(start_time) if start_time else datetime.now()
        event = DowntimeEvent(
            event_id=new_id("DT-", 10), work_center=run.work_center,
            reason=DowntimeReason(reason), start_time=start.isoformat(timespec="seconds"),
            end_time=(start + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
            duration_min=round(minutes, 1), run_no=run_no, planned=planned)
        with self._s.transaction():
            self._s.insert_downtime(event)
            if planned:
                status = WorkCenterStatus.MAINTENANCE if reason in ("MAINTENANCE", "SETUP") else WorkCenterStatus.IDLE
            else:
                status = WorkCenterStatus.BREAKDOWN if reason == "BREAKDOWN" else WorkCenterStatus.MAINTENANCE
            self._s.update_work_center(run.work_center, status=status)
        return asdict(event)

    def end_run(self, run_no: str) -> dict[str, Any]:
        run = self.get_run(run_no)
        if run.status == RunStatus.ENDED:
            return asdict(run)
        with self._s.transaction():
            self._s.update_run(run_no, status=RunStatus.ENDED, ended_at=_now())
            self._s.update_work_center(run.work_center, status=WorkCenterStatus.IDLE,
                                       current_production_order=None)
            self._audit("end_run", "production_run", run_no, _snapshot(run), None)
        return asdict(self.get_run(run_no))

    def oee_report(self, work_center: str | None = None,
                   run_no: str | None = None,
                   production_order_no: str | None = None) -> dict[str, Any]:
        if work_center:
            self.get_work_center(work_center)
        if run_no:
            runs = [self.get_run(run_no)]
        else:
            runs = self._s.list_runs(work_center=work_center,
                                     production_order_no=production_order_no)
        planned_prod = 0.0
        run_time = 0.0
        planned_dt = 0.0
        unplanned_dt = 0.0
        ideal_minutes = 0.0
        good = 0
        scrap = 0
        for run in runs:
            events = self._s.list_downtime(run_no=run.run_no)
            run_planned_dt = sum(e.duration_min for e in events if e.planned)
            run_unplanned_dt = sum(e.duration_min for e in events if not e.planned)
            window = max(0.0, run.planned_minutes - run_planned_dt)
            planned_prod += window
            run_time += max(0.0, window - run_unplanned_dt)
            planned_dt += run_planned_dt
            unplanned_dt += run_unplanned_dt
            total = run.qty_good + run.qty_scrap
            ideal_minutes += run.ideal_cycle_sec * total / 60.0
            good += run.qty_good
            scrap += run.qty_scrap
        availability = run_time / planned_prod if planned_prod > 0 else 0.0
        performance = min(1.0, ideal_minutes / run_time) if run_time > 0 else 0.0
        quality = good / (good + scrap) if (good + scrap) > 0 else 0.0
        oee = availability * performance * quality
        return {
            "work_center": work_center,
            "run_no": run_no,
            "production_order_no": production_order_no,
            "runs": len(runs),
            "availability": round(availability, 3),
            "performance": round(performance, 3),
            "quality": round(quality, 3),
            "oee": round(oee, 3),
            "planned_minutes": round(planned_prod, 1),
            "run_minutes": round(run_time, 1),
            "planned_downtime_min": round(planned_dt, 1),
            "unplanned_downtime_min": round(unplanned_dt, 1),
            "downtime_min": round(planned_dt + unplanned_dt, 1),
            "good": good,
            "scrap": scrap,
        }

    # ------------------------------------------------------------- quality
    def quality_inspect(self, production_order_no: str, operation_no: int, sample_qty: int,
                        defect_qty: int, disposition: str, inspector: str = "",
                        defect_code: str = "", defect_desc: str = "",
                        unit_ref: str = "", station: str = "") -> dict[str, Any]:
        record = QualityRecord(
            record_no=new_id("QC-", 10), production_order_no=production_order_no,
            operation_no=operation_no, sample_qty=sample_qty, defect_qty=defect_qty,
            disposition=QualityDisposition(disposition), defect_code=defect_code,
            defect_desc=defect_desc, unit_ref=unit_ref, station=station,
            inspector=inspector)
        with self._s.transaction():
            self._s.insert_quality_record(record)
            if self._itg:
                self._itg.audit.record(
                    operator=inspector or "qc", operation="quality_inspect",
                    resource="quality", resource_id=record.record_no, correlation_id="",
                    before={"op": operation_no}, after=_snapshot(record))
        return asdict(record)

    def record_test(self, production_order_no: str, operation_no: int, unit_ref: str,
                    result: str, station: str = "", defect_code: str = "",
                    defect_desc: str = "", characteristic: str = "",
                    nominal: float | None = None, tolerance: float | None = None,
                    measured: float | None = None, inspector: str = "") -> dict[str, Any]:
        self.get_production_order(production_order_no)
        if result not in ("PASS", "FAIL"):
            raise ValidationError("result must be PASS or FAIL")
        if result == "FAIL" and not defect_code:
            defect_code = "UNSPECIFIED"
        record = TestRecord(
            test_no=new_id("TST-", 10), production_order_no=production_order_no,
            operation_no=operation_no, unit_ref=unit_ref, station=station,
            result=TestResult(result), defect_code=defect_code, defect_desc=defect_desc,
            characteristic=characteristic, nominal=nominal, tolerance=tolerance,
            measured=measured, inspector=inspector)
        with self._s.transaction():
            self._s.insert_test(record)
            if result == "FAIL":
                self._audit("test_fail", "test_record", record.test_no, None, _snapshot(record))
        return asdict(record)

    def yield_report(self, production_order_no: str | None = None) -> dict[str, Any]:
        tests = self._s.list_tests(production_order_no=production_order_no)
        total = len(tests)
        passed = sum(1 for t in tests if t.result == TestResult.PASS)
        failed = total - passed
        pareto: dict[str, dict[str, Any]] = {}
        for t in tests:
            if t.result != TestResult.FAIL:
                continue
            key = t.defect_code or "UNSPECIFIED"
            item = pareto.setdefault(key, {"defect_code": key, "count": 0,
                                           "description": t.defect_desc})
            item["count"] += 1
        pareto_rows = sorted(pareto.values(), key=lambda x: (-x["count"], x["defect_code"]))
        for row in pareto_rows:
            row["share"] = round(row["count"] / failed, 3) if failed else 0.0
        quality_records = self._s.list_quality_records(production_order_no=production_order_no)
        inspected = sum(q.sample_qty for q in quality_records)
        defects = sum(q.defect_qty for q in quality_records)
        rework = sum(q.defect_qty for q in quality_records if q.disposition == QualityDisposition.REWORK)
        scrap = sum(q.defect_qty for q in quality_records if q.disposition == QualityDisposition.SCRAP)
        return {
            "production_order_no": production_order_no,
            "tested": total,
            "passed": passed,
            "failed": failed,
            "first_pass_yield": round(passed / total, 3) if total else 0.0,
            "defect_rate": round(failed / total, 3) if total else 0.0,
            "defect_pareto": pareto_rows,
            "inspection_samples": inspected,
            "inspection_defects": defects,
            "rework_samples": rework,
            "scrap_samples": scrap,
        }

    def spc_report(self, characteristic: str | None = None,
                   production_order_no: str | None = None) -> dict[str, Any]:
        tests = [t for t in self._s.list_tests(production_order_no=production_order_no)
                 if t.measured is not None and t.characteristic]
        groups: dict[str, list[TestRecord]] = {}
        for t in tests:
            if characteristic and t.characteristic != characteristic:
                continue
            groups.setdefault(t.characteristic, []).append(t)
        rows: list[dict[str, Any]] = []
        for name, records in groups.items():
            values = [float(t.measured) for t in records if t.measured is not None]
            if not values:
                continue
            nominal = next((t.nominal for t in records if t.nominal is not None), None)
            tolerance = next((t.tolerance for t in records if t.tolerance is not None), None)
            mean = statistics.fmean(values)
            sigma = statistics.stdev(values) if len(values) > 1 else 0.0
            lsl = nominal - tolerance if nominal is not None and tolerance is not None else None
            usl = nominal + tolerance if nominal is not None and tolerance is not None else None
            cp = ((usl - lsl) / (6 * sigma)) if lsl is not None and usl is not None and sigma > 0 else None
            cpk = None
            if lsl is not None and usl is not None and sigma > 0:
                cpk = min(usl - mean, mean - lsl) / (3 * sigma)
            out_of_spec = sum(1 for v in values
                              if (lsl is not None and v < lsl) or (usl is not None and v > usl))
            rows.append({
                "characteristic": name,
                "n": len(values),
                "nominal": nominal,
                "tolerance": tolerance,
                "lsl": lsl,
                "usl": usl,
                "mean": round(mean, 4),
                "stdev": round(sigma, 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "cp": round(cp, 3) if cp is not None else None,
                "cpk": round(cpk, 3) if cpk is not None else None,
                "out_of_spec": out_of_spec,
            })
        rows.sort(key=lambda x: x["characteristic"])
        return {"characteristic": characteristic,
                "production_order_no": production_order_no,
                "characteristics": rows}

    def recall_report(self, lot_no: str, max_depth: int = 10) -> dict[str, Any]:
        trace = self.trace_lot(lot_no, direction="down", max_depth=max_depth)
        affected: dict[str, dict[str, Any]] = {}
        for edge in trace["children"]:
            po_no = edge["production_order_no"]
            if not po_no:
                continue
            po = self._s.get_production_order(po_no)
            if not po:
                continue
            wo = self._s.get_work_order(po.erp_work_order_no)
            so = self._s.get_sales_order(wo.sales_order_no) if wo and wo.sales_order_no else None
            customer = self._s.get_customer(so.customer_id) if so else None
            item = {
                "production_order_no": po_no,
                "erp_work_order_no": po.erp_work_order_no,
                "product_sku": po.product_sku,
                "product_name": po.product_name,
                "quantity": po.quantity,
                "status": str(po.status),
                "output_lot": f"LOT-{po_no}",
                "sales_order_no": wo.sales_order_no if wo else None,
                "customer": customer.name if customer else (wo.customer if wo else ""),
                "customer_id": customer.customer_id if customer else None,
            }
            affected[po_no] = item
        for consumption in self._s.list_consumptions():
            if consumption.lot_no != lot_no:
                continue
            po = self._s.get_production_order(consumption.production_order_no)
            if not po or po.production_order_no in affected:
                continue
            wo = self._s.get_work_order(po.erp_work_order_no)
            so = self._s.get_sales_order(wo.sales_order_no) if wo and wo.sales_order_no else None
            customer = self._s.get_customer(so.customer_id) if so else None
            affected[po.production_order_no] = {
                "production_order_no": po.production_order_no,
                "erp_work_order_no": po.erp_work_order_no,
                "product_sku": po.product_sku,
                "product_name": po.product_name,
                "quantity": po.quantity,
                "status": str(po.status),
                "output_lot": f"LOT-{po.production_order_no}",
                "sales_order_no": wo.sales_order_no if wo else None,
                "customer": customer.name if customer else (wo.customer if wo else ""),
                "customer_id": customer.customer_id if customer else None,
            }
        orders = sorted(affected.values(), key=lambda x: x["production_order_no"])
        customers = sorted({o["customer"] for o in orders if o["customer"]})
        return {
            "lot_no": lot_no,
            "direction": "down",
            "affected_orders": orders,
            "affected_order_count": len(orders),
            "affected_customers": customers,
            "affected_quantity": sum(o["quantity"] for o in orders),
            "downstream_lots": trace["downstream_lots"],
            "links": len(trace["children"]),
        }

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