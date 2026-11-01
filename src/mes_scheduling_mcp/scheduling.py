"""Production scheduling engine.

Implements a deterministic, constraint-aware greedy scheduler:

1. Sort RELEASED orders by (priority ascending, due date ascending).
2. For each order, run a BOM material check; orders that cannot be
   fulfilled are recorded as shortages and skipped.
3. Assign each feasible order to the eligible line that can finish it
   earliest while respecting the line's shift and available capacity.
4. Reserve consumed material and advance the line's occupied clock.

The result is a :class:`Schedule` plus its items. Material reservations
are persisted so a second run on the same day reflects prior allocation.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .models import (
    LineStatus,
    OrderStatus,
    Schedule,
    ScheduleItem,
    to_dict,
)
from .store import Store, new_plan_id


class SchedulingService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def capacity_query(self, plan_date: Optional[str] = None) -> list[dict]:
        """Return each line's capacity, its scheduled load, and remaining hours."""
        plan_date = plan_date or datetime.now().strftime("%Y-%m-%d")
        lines = self.store.list_lines()
        result = []
        for line in lines:
            tasks = [
                t for t in self.store.list_tasks(line_id=line.line_id)
                if t.planned_start.startswith(plan_date)
            ]
            occupied_hours = sum(t.quantity / line.capacity_per_hour for t in tasks) if line.capacity_per_hour else 0.0
            available_hours = line.shift_hours if line.status != LineStatus.MAINTENANCE.value else 0.0
            result.append({
                "line_id": line.line_id,
                "name": line.name,
                "status": line.status,
                "capacity_per_hour": line.capacity_per_hour,
                "shift_start": line.shift_start,
                "shift_hours": line.shift_hours,
                "planned_task_count": len(tasks),
                "occupied_hours": round(occupied_hours, 4),
                "available_hours": round(max(0.0, available_hours - occupied_hours), 4),
            })
        return result

    def generate_schedule(self, plan_date: Optional[str] = None) -> dict:
        """Generate a schedule for all RELEASED orders on ``plan_date``."""
        plan_date = plan_date or datetime.now().strftime("%Y-%m-%d")
        orders = self.store.list_work_orders(status=OrderStatus.RELEASED.value)
        lines = {l.line_id: l for l in self.store.list_lines()}

        # Per-line occupied clock, anchored to each line's shift start.
        line_clock: dict[str, datetime] = {}
        for l in lines.values():
            line_clock[l.line_id] = datetime.fromisoformat(f"{plan_date}T{l.shift_start}:00")

        plan_id = new_plan_id()
        items: list[ScheduleItem] = []
        scheduled: list[dict] = []
        shortages: list[dict] = []
        unscheduled: list[dict] = []

        order_start = datetime.fromisoformat(f"{plan_date}T08:00:00")
        for order in orders:
            self._schedule_one(
                order, lines, line_clock, plan_date, order_start,
                plan_id, items, scheduled, shortages, unscheduled,
            )

        summary = {
            "plan_date": plan_date,
            "order_count": len(orders),
            "scheduled_count": len(scheduled),
            "shortage_count": len(shortages),
            "unscheduled_count": len(unscheduled),
        }
        schedule = Schedule(plan_id=plan_id, plan_date=plan_date, summary=summary)
        self.store.save_schedule(schedule)
        self.store.save_schedule_items(items)
        self.store.commit()

        return {
            "plan_id": plan_id,
            "plan_date": plan_date,
            "status": schedule.status,
            "summary": summary,
            "scheduled": scheduled,
            "shortages": shortages,
            "unscheduled": unscheduled,
        }

    def _schedule_one(self, order, lines, line_clock, plan_date, order_start, plan_id, items, scheduled, shortages, unscheduled):
        # 1. Material check.
        bom = self.store.get_bom(order.product_sku)
        needed_materials: dict[str, float] = {}
        missing: list[dict] = []
        if bom:
            for bl in bom:
                needed = round(bl.qty_per_unit * order.quantity, 6)
                mat = self.store.get_material(bl.material_sku)
                available = mat.available_qty if mat else 0.0
                needed_materials[bl.material_sku] = needed
                if available < needed:
                    missing.append({
                        "material_sku": bl.material_sku,
                        "needed_qty": needed,
                        "available_qty": available,
                    })
        if missing:
            shortages.append({
                "order_no": order.order_no,
                "product_sku": order.product_sku,
                "quantity": order.quantity,
                "due_date": order.due_date,
                "priority": order.priority,
                "shortages": missing,
            })
            return

        # 2. Eligible lines: not in maintenance and able to run within the day.
        eligible = [
            l for l in lines.values()
            if l.status != LineStatus.MAINTENANCE.value
        ]
        if not eligible:
            unscheduled.append(self._fmt_unscheduled(order, "无可用产线"))
            return

        # 3. Pick the line that can finish earliest.
        best: tuple[datetime, datetime, object] | None = None
        for line in eligible:
            duration_h = order.quantity / line.capacity_per_hour
            start = max(line_clock[line.line_id], order_start)
            end = start + timedelta(hours=duration_h)
            shift_end = datetime.fromisoformat(f"{plan_date}T{line.shift_start}:00") + timedelta(hours=line.shift_hours)
            if end > shift_end:
                continue
            if best is None or end < best[1]:
                best = (start, end, line)

        if best is None:
            unscheduled.append(self._fmt_unscheduled(order, "产能不足：无产线可在班次内完成"))
            return

        start, end, line = best
        line_clock[line.line_id] = end
        for sku, qty in needed_materials.items():
            self.store.reserve_material(sku, qty)

        self.store.set_order_status(order.order_no, OrderStatus.SCHEDULED.value)
        seq = len(items) + 1
        items.append(ScheduleItem(
            plan_id=plan_id,
            seq=seq,
            order_no=order.order_no,
            line_id=line.line_id,
            planned_start=start.isoformat(timespec="minutes"),
            planned_end=end.isoformat(timespec="minutes"),
            status="PLANNED",
        ))
        scheduled.append({
            "order_no": order.order_no,
            "product_sku": order.product_sku,
            "product_name": order.product_name,
            "quantity": order.quantity,
            "priority": order.priority,
            "due_date": order.due_date,
            "line_id": line.line_id,
            "line_name": line.name,
            "planned_start": start.isoformat(timespec="minutes"),
            "planned_end": end.isoformat(timespec="minutes"),
            "duration_hours": round((end - start).total_seconds() / 3600, 4),
        })

    @staticmethod
    def _fmt_unscheduled(order, reason: str) -> dict:
        return {
            "order_no": order.order_no,
            "product_sku": order.product_sku,
            "quantity": order.quantity,
            "due_date": order.due_date,
            "priority": order.priority,
            "reason": reason,
        }

    def list_schedules(self, status: Optional[str] = None) -> list[dict]:
        return to_dict(self.store.list_schedules(status))

    def get_schedule(self, plan_id: str) -> dict:
        schedule = self.store.get_schedule(plan_id)
        if schedule is None:
            raise ValueError(f"排产计划不存在: {plan_id}")
        items = [to_dict(i) for i in self.store.list_schedule_items(plan_id)]
        data = to_dict(schedule)
        data["items"] = items
        return data