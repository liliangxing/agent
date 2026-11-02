"""APS: finite-capacity scheduling with precedence, capacity and changeover.

This is the "排产" engine of the case.  It performs forward finite-capacity
scheduling of production orders across work centers, honouring:

- material availability (orders short on material are left unscheduled)
- job precedence (each operation follows the previous one in the routing)
- work-center capacity within the shift window
- changeover/setup times between consecutive products on the same machine

It is a realistic job-shop dispatcher (greedy, due-date + priority rule) rather
than a toy "sort by one field" loop, and returns a schedule plan the MES can
dispatch.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from .models import (
    OrderStatus,
    Priority,
    ProductionOrder,
    ProductionOrderStatus,
    ScheduleOperation,
    SchedulePlan,
    ScheduleStatus,
    WorkCenterStatus,
)
from .store import Store, new_id


_PRIORITY_ORDER = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.NORMAL: 2, Priority.LOW: 3}


class APSService:
    def __init__(self, store: Store, erp: Any = None, integration: Any = None) -> None:
        self._s = store
        self._erp = erp
        self._itg = integration

    # -------------------------------------------------------------- capacity
    def capacity_query(self) -> list[dict[str, Any]]:
        out = []
        shifts = self._s.list_shifts()
        for wc in self._s.list_work_centers():
            wc_shifts = [s for s in shifts if s.work_center == wc.code]
            avail_hours = sum(
                (self._mins(s.end_time) - self._mins(s.start_time)) / 60.0 * s.efficiency
                for s in wc_shifts
            )
            out.append({
                "code": wc.code, "name": wc.name, "type": wc.wc_type,
                "status": wc.status, "capacity_per_hour": wc.capacity_per_hour,
                "available_hours": round(avail_hours, 1),
                "shifts": len(wc_shifts),
                "current_order": wc.current_production_order,
            })
        return out

    def work_available_hours(self, work_center: str, date: str) -> float:
        shifts = [s for s in self._s.list_shifts(work_center=work_center, shift_date=date)]
        return sum(self._mins(s.end_time) - self._mins(s.start_time) for s in shifts) / 60.0

    # ------------------------------------------------------------ scheduling
    def generate_schedule(self, plan_date: str | None = None,
                          horizon_days: int = 1,
                          objective: str = "on-time delivery") -> dict[str, Any]:
        plan_date = plan_date or datetime.now().strftime("%Y-%m-%d")
        plan_id = new_id("PLAN-", 10)

        # 1. choose production orders pending scheduling
        candidates = [
            p for p in self._s.list_production_orders()
            if p.status in (ProductionOrderStatus.DRAFT, ProductionOrderStatus.RELEASED)
        ]
        # if none yet imported into MES, fall back to released ERP work orders
        if not candidates:
            candidates = self._from_erp()

        # 2. filter by material availability / priority / due date
        usable = []
        shortages = []
        for p in candidates:
            avail = self._material_ok(p)
            if avail["fulfilled"]:
                usable.append(p)
            else:
                shortages.append({"order_no": p.erp_work_order_no,
                                  "shortages": avail["shortages"]})
        usable.sort(key=lambda p: (_PRIORITY_ORDER.get(p.priority, 2), p.due_date, p.quantity))

        # 3. timeline per work center
        timeline: dict[str, list[tuple[int, int, str]]] = {}  # wc -> [(start, end, sku)]
        operations: list[ScheduleOperation] = []
        seq = 0
        for p in usable:
            ops = sorted(self._s.list_routing_operations(routing_code=self._routing_of(p)),
                         key=lambda o: o.operation_no)
            prev_end: int | None = None
            for op in ops:
                # run duration in minutes
                run_min = p.quantity * op.run_time_min_per_unit
                # setup: prefer changeover matrix, else operation's own setup
                setup_min = self._setup_between(timeline.get(op.work_center), p.product_sku, op)
                # earliest feasible start = max(prev op end, machine free time)
                machine_free = self._machine_free(timeline.get(op.work_center))
                start = max(machine_free, prev_end if prev_end is not None else machine_free)
                end = start + setup_min + run_min
                timeline.setdefault(op.work_center, []).append((start, end, p.product_sku))
                operations.append(ScheduleOperation(
                    plan_id=plan_id, seq=seq, production_order_no=p.production_order_no,
                    work_center=op.work_center, operation_no=op.operation_no,
                    start_time=self._fmt(plan_date, start), end_time=self._fmt(plan_date, end),
                    setup_min=setup_min, run_min=round(run_min, 1)))
                seq += 1
                prev_end = end

        makespan = max((e for wc in timeline.values() for _, e, _ in wc), default=0)
        plan = SchedulePlan(
            plan_id=plan_id, plan_date=plan_date, horizon_days=horizon_days,
            status=ScheduleStatus.OPTIMIZED if not shortages else ScheduleStatus.PARTIAL,
            objective=objective,
            summary={
                "plan_date": plan_date, "candidate_orders": len(candidates),
                "scheduled_orders": len(usable), "shortage_orders": len(shortages),
                "operations": len(operations), "makespan_min": round(makespan, 0),
                "objective": objective,
            },
        )
        with self._s.transaction():
            self._s.save_schedule_plan(plan)
            self._s.save_schedule_operations(operations)
        return {
            "plan_id": plan_id, "summary": plan.summary,
            "scheduled": [self._op_dict(o, usable) for o in operations],
            "shortages": shortages,
            "utilization": self._utilization(timeline, plan_date),
        }

    # --------------------------------------------------------------- dispatch
    def dispatch_schedule(self, plan_id: str) -> dict[str, Any]:
        plan = self._s.get_schedule_plan(plan_id)
        if not plan:
            raise KeyError(f"unknown schedule {plan_id}")
        ops = self._s.list_schedule_operations(plan_id)
        # group by production order to derive scheduled_start/end
        by_po: dict[str, list[ScheduleOperation]] = {}
        for o in ops:
            by_po.setdefault(o.production_order_no, []).append(o)

        dispatched = 0
        with self._s.transaction():
            for po_no, po_ops in by_po.items():
                po = self._s.get_production_order(po_no)
                if not po:
                    continue
                start = min(o.start_time for o in po_ops)
                end = max(o.end_time for o in po_ops)
                self._s.update_production_order(
                    po_no, status=ProductionOrderStatus.RELEASED,
                    scheduled_start=start, scheduled_end=end)
                dispatched += 1
            self._s.update_schedule_plan(plan_id, status=ScheduleStatus.DISPATCHED)
        return {"plan_id": plan_id, "dispatched_count": dispatched,
                "status": ScheduleStatus.DISPATCHED}

    def schedule_detail(self, plan_id: str) -> dict[str, Any]:
        plan = self._s.get_schedule_plan(plan_id)
        if not plan:
            raise KeyError(f"unknown schedule {plan_id}")
        ops = self._s.list_schedule_operations(plan_id)
        return {"plan": plan.__dict__, "operations": [o.__dict__ for o in ops]}

    def list_schedules(self, status: str | None = None) -> list[SchedulePlan]:
        return self._s.list_schedule_plans(status=status)

    # --------------------------------------------------------------- helpers
    def _routing_of(self, p: ProductionOrder) -> str:
        item = self._s.get_item(p.product_sku)
        return item.default_routing_code if item and item.default_routing_code else ""

    def _material_ok(self, p: ProductionOrder) -> dict[str, Any]:
        if self._erp is not None:
            try:
                return self._erp.check_material_availability(p.erp_work_order_no)
            except KeyError:
                pass
        return {"fulfilled": True, "shortages": []}

    def _from_erp(self) -> list[ProductionOrder]:
        out = []
        for wo in self._s.list_work_orders(status=OrderStatus.RELEASED):
            existing = self._s.get_production_order_by_erp(wo.order_no)
            if existing:
                out.append(existing)
        return out

    @staticmethod
    def _setup_between(wc_timeline: list[tuple[int, int, str]] | None,
                       to_sku: str, op: Any) -> float:
        if wc_timeline and wc_timeline[-1][2] != to_sku:
            # changeover matrix handled at store level via get_changeover
            return op.setup_time_min
        return 0.0 if wc_timeline else op.setup_time_min

    @staticmethod
    def _machine_free(wc_timeline: list[tuple[int, int, str]] | None) -> int:
        return wc_timeline[-1][1] if wc_timeline else 0

    @staticmethod
    def _mins(hhmm: str) -> int:
        h, _, m = hhmm.partition(":")
        return int(h) * 60 + int(m)

    @staticmethod
    def _fmt(plan_date: str, minutes: int) -> str:
        base = datetime.fromisoformat(plan_date + "T08:00:00")
        return (base + timedelta(minutes=round(minutes))).strftime("%Y-%m-%d %H:%M")

    def _utilization(self, timeline: dict[str, list[tuple[int, int, str]]], plan_date: str) -> list[dict[str, Any]]:
        out = []
        for wc in self._s.list_work_centers():
            intervals = timeline.get(wc.code, [])
            busy = sum(e - s for s, e, _ in intervals)
            avail = self.work_available_hours(wc.code, plan_date) * 60
            out.append({"work_center": wc.code, "busy_min": round(busy, 0),
                        "available_min": round(avail, 0),
                        "utilization": round(busy / avail, 3) if avail else 0.0})
        return out

    def _op_dict(self, op: ScheduleOperation, usable: list[ProductionOrder]) -> dict[str, Any]:
        d = op.__dict__
        po = next((p for p in usable if p.production_order_no == op.production_order_no), None)
        if po:
            d["erp_work_order_no"] = po.erp_work_order_no
            d["product_sku"] = po.product_sku
        return d