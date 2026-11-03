"""APS: finite-capacity scheduling with shift calendars and dual dates.

This is the "排产" engine of the case.  It performs forward finite-capacity
scheduling of production orders across work centers, honouring:

- material availability (orders short on material are left unscheduled)
- job precedence (each operation follows the previous one in the routing)
- queue time between operations
- work-center shift calendars (jobs only start inside a working window)
- changeover/setup matrices between consecutive products on the same machine
- a multi-day horizon, so work that does not fit today rolls into later shifts

Two dates are tracked per order, mirroring real APS practice:

- ``due_date``         the need-by date promised to the customer
- ``projected_end``    when the finite-capacity plan actually finishes

Lateness is the gap between them, which is what planners react to.

The dispatcher is deterministic (priority -> due date -> order number) so the
same inputs always produce the same plan, which keeps tests and demos stable.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from .models import (
    OrderStatus,
    Priority,
    ProductionOrder,
    ProductionOrderStatus,
    ScheduleOperation,
    SchedulePlan,
    ScheduleStatus,
    ShiftEntry,
)


_PRIORITY_ORDER = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.NORMAL: 2, Priority.LOW: 3}
_MINUTES_PER_DAY = 24 * 60
_WINDOW_END = 23 * 60 + 59


class _Calendar:
    """Working windows + busy intervals for one work center (absolute minutes)."""

    def __init__(self, windows: list[tuple[int, int]]) -> None:
        self.windows = self._merge(windows)
        self.busy: list[tuple[int, int]] = []

    @staticmethod
    def _merge(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for start, end in sorted(windows):
            if end <= start:
                continue
            if out and start <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], end))
            else:
                out.append((start, end))
        return out

    def earliest(self, ready: int, duration: float) -> Optional[int]:
        """Earliest start >= ``ready`` that fits ``duration`` in a free gap."""
        need = max(1, round(duration))
        for win_start, win_end in self.windows:
            start = max(ready, win_start)
            if start + need > win_end:
                continue
            busy = sorted(self.busy)
            while True:
                conflict = None
                for busy_start, busy_end in busy:
                    if busy_end <= start:
                        continue
                    if busy_start >= start + need:
                        break
                    conflict = busy_end
                    break
                if conflict is None:
                    break
                start = conflict
            if start + need <= win_end:
                return start
        return None

    def occupy(self, start: int, end: int) -> None:
        self.busy.append((start, end))
        self.busy.sort()

    def snapshot(self) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        return list(self.windows), list(self.busy)

    def restore(self, snapshot: tuple[list[tuple[int, int]], list[tuple[int, int]]]) -> None:
        self.windows, self.busy = snapshot

    def busy_minutes(self) -> float:
        return float(sum(end - start for start, end in self.busy))

    def available_minutes(self) -> float:
        return float(sum(end - start for start, end in self.windows))


class APSService:
    def __init__(self, store: Any, erp: Any = None, integration: Any = None) -> None:
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
                          horizon_days: int = 3,
                          objective: str = "on-time delivery") -> dict[str, Any]:
        plan_date = plan_date or self._default_plan_date()
        horizon_days = max(1, horizon_days)
        plan_id = self._new_plan_id()

        # 1. candidates pending scheduling
        candidates = [
            p for p in self._s.list_production_orders()
            if p.status in (ProductionOrderStatus.DRAFT, ProductionOrderStatus.RELEASED)
        ]
        if not candidates:
            candidates = self._from_erp()

        # 2. material gate (齐套检查)
        usable: list[ProductionOrder] = []
        shortages: list[dict[str, Any]] = []
        for p in candidates:
            avail = self._material_ok(p)
            if avail["fulfilled"]:
                usable.append(p)
            else:
                shortages.append({"order_no": p.erp_work_order_no,
                                  "shortages": avail["shortages"]})

        # 3. deterministic dispatch order
        usable.sort(key=lambda p: (_PRIORITY_ORDER.get(p.priority, 2), p.due_date, p.production_order_no))

        calendars: dict[str, _Calendar] = {
            wc.code: _Calendar(self._build_windows(wc.code, plan_date, horizon_days))
            for wc in self._s.list_work_centers()
        }
        last_sku: dict[str, str] = {}
        operations: list[ScheduleOperation] = []
        order_dates: list[dict[str, Any]] = []
        capacity_blocked: list[dict[str, Any]] = []
        seq = 0

        for p in usable:
            ops = sorted(self._s.list_routing_operations(routing_code=self._routing_of(p)),
                         key=lambda o: o.operation_no)
            snapshots = {code: cal.snapshot() for code, cal in calendars.items()}
            sku_snapshot = dict(last_sku)
            order_ops: list[ScheduleOperation] = []
            prev_end: int | None = None
            failed = False

            for op in ops:
                cal = calendars.get(op.work_center)
                if cal is None:
                    failed = True
                    break
                to_sku = p.product_sku
                from_sku = last_sku.get(op.work_center)
                setup = self._setup_minutes(from_sku, to_sku, op)
                run = round(p.quantity * op.run_time_min_per_unit, 1)
                ready = (prev_end + op.queue_time_min) if prev_end is not None else op.queue_time_min
                start = cal.earliest(ready, setup + run)
                if start is None:
                    failed = True
                    break
                end = start + round(setup + run)
                cal.occupy(start, end)
                last_sku[op.work_center] = to_sku
                order_ops.append(ScheduleOperation(
                    plan_id=plan_id, seq=seq, production_order_no=p.production_order_no,
                    work_center=op.work_center, operation_no=op.operation_no,
                    start_time=self._fmt(plan_date, start), end_time=self._fmt(plan_date, end),
                    setup_min=setup, run_min=run))
                seq += 1
                prev_end = end

            if failed:
                for code, snap in snapshots.items():
                    calendars[code].restore(snap)
                last_sku = sku_snapshot
                capacity_blocked.append({
                    "order_no": p.erp_work_order_no,
                    "reason": f"no feasible window within {horizon_days} day(s)",
                })
                continue

            operations.extend(order_ops)
            order_dates.append(self._order_dates(p, order_ops, plan_date))

        scheduled_ids = {o.production_order_no for o in operations}
        late = [d for d in order_dates if not d["on_time"]]
        end_abs = max((self._parse_minutes(plan_date, o.end_time) for o in operations), default=0)
        summary = {
            "plan_date": plan_date,
            "horizon_days": horizon_days,
            "candidate_orders": len(candidates),
            "scheduled_orders": len(scheduled_ids),
            "shortage_orders": len(shortages),
            "capacity_blocked_orders": len(capacity_blocked),
            "operations": len(operations),
            "makespan_min": round(end_abs, 0),
            "schedule_end": self._fmt(plan_date, end_abs) if operations else None,
            "on_time_orders": len(order_dates) - len(late),
            "late_orders": len(late),
            "max_lateness_hours": round(max((d["lateness_hours"] for d in late), default=0.0), 1),
            "objective": objective,
        }
        plan = SchedulePlan(
            plan_id=plan_id, plan_date=plan_date, horizon_days=horizon_days,
            status=ScheduleStatus.OPTIMIZED if not shortages and not capacity_blocked
            else ScheduleStatus.PARTIAL,
            objective=objective, summary=summary,
        )
        with self._s.transaction():
            self._s.save_schedule_plan(plan)
            self._s.save_schedule_operations(operations)
        return {
            "plan_id": plan_id, "summary": summary,
            "scheduled": [self._op_dict(o, usable) for o in operations],
            "order_dates": order_dates,
            "shortages": shortages,
            "capacity_blocked": capacity_blocked,
            "utilization": self._utilization(calendars),
        }

    # --------------------------------------------------------------- dispatch
    def dispatch_schedule(self, plan_id: str) -> dict[str, Any]:
        plan = self._s.get_schedule_plan(plan_id)
        if not plan:
            raise KeyError(f"unknown schedule {plan_id}")
        ops = self._s.list_schedule_operations(plan_id)
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
    def _new_plan_id(self) -> str:
        from .store import new_id
        return new_id("PLAN-", 10)

    def _default_plan_date(self) -> str:
        """Anchor planning to the plant calendar, not the wall clock.

        The shift calendar is the authoritative source of working days, so a
        plan without an explicit date starts on the earliest scheduled day.
        This keeps demos and tests deterministic (the calendar is seeded, the
        host clock is not).
        """
        dates = sorted({s.shift_date for s in self._s.list_shifts()})
        return dates[0] if dates else datetime.now().strftime("%Y-%m-%d")

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

    def _setup_minutes(self, from_sku: str | None, to_sku: str, op: Any) -> float:
        """Setup for switching to ``to_sku``: matrix when known, else routing setup.

        The changeover matrix already accounts for teardown + setup, so it
        *replaces* the routing setup rather than adding to it (otherwise every
        switch would double-count the fixture change).
        """
        if from_sku is None or from_sku == to_sku:
            return 0.0 if from_sku == to_sku else float(op.setup_time_min)
        matrix = self._s.get_changeover(from_sku, to_sku)
        return float(matrix) if matrix > 0 else float(op.setup_time_min)

    def _build_windows(self, work_center: str, plan_date: str,
                       horizon_days: int) -> list[tuple[int, int]]:
        """Absolute-minutes working windows for the planning horizon.

        Missing future shift rows fall back to the plan-day pattern, so a
        multi-day horizon still has a usable (and deterministic) calendar.
        """
        base = date.fromisoformat(plan_date)
        pattern = self._s.list_shifts(work_center=work_center, shift_date=plan_date)
        windows: list[tuple[int, int]] = []
        for offset in range(horizon_days):
            day = base + timedelta(days=offset)
            shifts = self._s.list_shifts(work_center=work_center, shift_date=day.isoformat())
            if not shifts:
                shifts = self._project(pattern, offset)
            for s in shifts:
                start = offset * _MINUTES_PER_DAY + self._mins(s.start_time)
                end = offset * _MINUTES_PER_DAY + self._mins(s.end_time)
                windows.append((start, end))
        return windows

    @staticmethod
    def _project(pattern: list[ShiftEntry], offset: int) -> list[ShiftEntry]:
        return [ShiftEntry(work_center=s.work_center, shift_date=s.shift_date, shift_no=s.shift_no,
                           start_time=s.start_time, end_time=s.end_time, efficiency=s.efficiency)
                for s in pattern]

    def _order_dates(self, p: ProductionOrder, ops: list[ScheduleOperation],
                     plan_date: str) -> dict[str, Any]:
        projected_end = max((self._parse_minutes(plan_date, o.end_time) for o in ops), default=0)
        try:
            due_abs = ((date.fromisoformat(p.due_date) - date.fromisoformat(plan_date)).days
                       * _MINUTES_PER_DAY + _WINDOW_END)
        except ValueError:
            due_abs = projected_end
        lateness_min = projected_end - due_abs
        return {
            "production_order_no": p.production_order_no,
            "erp_work_order_no": p.erp_work_order_no,
            "product_sku": p.product_sku,
            "quantity": p.quantity,
            "priority": str(p.priority),
            "due_date": p.due_date,
            "projected_end": self._fmt(plan_date, projected_end),
            "lateness_hours": round(lateness_min / 60.0, 1),
            "on_time": lateness_min <= 0,
        }

    @staticmethod
    def _mins(hhmm: str) -> int:
        h, _, m = hhmm.partition(":")
        return int(h) * 60 + int(m)

    @staticmethod
    def _fmt(plan_date: str, minutes: float) -> str:
        base = datetime.fromisoformat(plan_date + "T00:00:00")
        return (base + timedelta(minutes=round(minutes))).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _parse_minutes(plan_date: str, text: str) -> int:
        base = datetime.fromisoformat(plan_date + "T00:00:00")
        return round((datetime.fromisoformat(text) - base).total_seconds() / 60.0)

    def _utilization(self, calendars: dict[str, _Calendar]) -> list[dict[str, Any]]:
        out = []
        for wc in self._s.list_work_centers():
            cal = calendars.get(wc.code)
            if cal is None:
                continue
            busy = cal.busy_minutes()
            avail = cal.available_minutes()
            out.append({"work_center": wc.code, "busy_min": round(busy, 0),
                        "available_min": round(avail, 0),
                        "utilization": round(busy / avail, 3) if avail else 0.0})
        return out

    def _op_dict(self, op: ScheduleOperation, usable: list[ProductionOrder]) -> dict[str, Any]:
        d = dict(op.__dict__)
        po = next((p for p in usable if p.production_order_no == op.production_order_no), None)
        if po:
            d["erp_work_order_no"] = po.erp_work_order_no
            d["product_sku"] = po.product_sku
        return d
