"""MES-domain operations: import, dispatch, line status, and reporting.

The import path maps directly onto the pain point in the case study:
the scheduler used to hand-key ERP work orders into MES every evening.
"""
from __future__ import annotations

from typing import Optional

from .models import (
    LineStatus,
    OrderStatus,
    ProductionTask,
    TaskStatus,
    to_dict,
)
from .store import Store, new_task_id


class MESService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def list_lines(self, line_id: Optional[str] = None) -> list[dict]:
        return to_dict(self.store.list_lines(line_id))

    def import_work_orders(self, order_nos: Optional[list[str]] = None) -> dict:
        """录入 MES：把指定的 ERP 工单（默认全部 RELEASED）转成 MES 生产任务。

        任务保持 PLANNED 状态，等待排产后下发到产线。
        """
        if order_nos:
            orders = self.store.get_work_orders(order_nos)
            missing = [n for n in order_nos if all(o.order_no != n for o in orders)]
            if missing:
                raise ValueError(f"工单不存在: {', '.join(missing)}")
        else:
            orders = self.store.list_work_orders(status=OrderStatus.RELEASED.value)

        imported = []
        for order in orders:
            task = ProductionTask(
                task_id=new_task_id(),
                order_no=order.order_no,
                line_id="",
                plan_id="",
                quantity=order.quantity,
                planned_start="",
                planned_end="",
                status=TaskStatus.PLANNED.value,
            )
            self.store.create_task(task)
            self.store.set_order_status(order.order_no, OrderStatus.IMPORTED.value)
            imported.append(to_dict(self.store.get_task(task.task_id)))

        self.store.commit()
        return {"imported_count": len(imported), "tasks": imported}

    def dispatch_schedule(self, plan_id: str) -> dict:
        """下发排产：将计划行落为 MES 任务并置产线为 BUSY."""
        schedule = self.store.get_schedule(plan_id)
        if schedule is None:
            raise ValueError(f"排产计划不存在: {plan_id}")

        items = self.store.list_schedule_items(plan_id)
        if not items:
            raise ValueError(f"排产计划 {plan_id} 无计划行")

        dispatched = []
        for item in items:
            task = ProductionTask(
                task_id=new_task_id(),
                order_no=item.order_no,
                line_id=item.line_id,
                plan_id=plan_id,
                quantity=self._order_quantity(item.order_no),
                planned_start=item.planned_start,
                planned_end=item.planned_end,
                status=TaskStatus.DISPATCHED.value,
            )
            self.store.create_task(task)
            self.store.set_line_status(item.line_id, LineStatus.BUSY.value)
            self.store.set_order_status(item.order_no, OrderStatus.IMPORTED.value)
            self.store.set_schedule_item_status(plan_id, item.seq, TaskStatus.DISPATCHED.value)
            dispatched.append(to_dict(self.store.get_task(task.task_id)))

        self.store.set_schedule_status(plan_id, "DISPATCHED")
        self.store.commit()
        return {"plan_id": plan_id, "dispatched_count": len(dispatched), "tasks": dispatched}

    def _order_quantity(self, order_no: str) -> int:
        order = self.store.get_work_order(order_no)
        return order.quantity if order else 0

    def list_tasks(
        self, status: Optional[str] = None, line_id: Optional[str] = None
    ) -> list[dict]:
        return to_dict(self.store.list_tasks(status, line_id))

    def report_progress(
        self, task_id: str, actual_qty: int, complete: bool = False
    ) -> dict:
        """报工：更新任务实际产量，选择是否完工."""
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"生产任务不存在: {task_id}")
        if actual_qty < 0:
            raise ValueError("实际产量不能为负")
        if actual_qty > task.quantity:
            raise ValueError(
                f"报工数量 {actual_qty} 超过任务计划数量 {task.quantity}"
            )

        new_status = (
            TaskStatus.COMPLETED.value
            if complete or actual_qty >= task.quantity
            else TaskStatus.RUNNING.value
        )
        self.store.update_task(task_id, actual_qty=actual_qty, status=new_status)
        if new_status == TaskStatus.COMPLETED.value:
            self.store.set_line_status(task.line_id, LineStatus.IDLE.value)
        self.store.commit()
        return to_dict(self.store.get_task(task_id))

    def health(self) -> dict:
        orders = self.store.list_work_orders()
        tasks = self.store.list_tasks()
        lines = self.store.list_lines()
        return {
            "status": "ok",
            "work_orders": len(orders),
            "released_orders": len([o for o in orders if o.status == OrderStatus.RELEASED.value]),
            "production_tasks": len(tasks),
            "lines": len(lines),
            "lines_busy": len([l for l in lines if l.status == LineStatus.BUSY.value]),
        }