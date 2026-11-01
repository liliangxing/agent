"""Domain models for the ERP / scheduling / MES bridge.

All models are plain dataclasses. They serialise to JSON-friendly dicts
via :func:`to_dict` so the MCP layer can return them directly.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class OrderStatus(StrEnum):
    """Lifecycle of an ERP work order."""

    DRAFT = "DRAFT"          # 草稿，尚未发布给排产
    RELEASED = "RELEASED"    # 已发布，等待排产/录入
    SCHEDULED = "SCHEDULED"  # 已生成排产计划
    IMPORTED = "IMPORTED"    # 已录入 MES


class LineStatus(StrEnum):
    """Lifecycle of a shop-floor production line."""

    IDLE = "IDLE"
    BUSY = "BUSY"
    MAINTENANCE = "MAINTENANCE"


class TaskStatus(StrEnum):
    """Lifecycle of a MES production task."""

    PLANNED = "PLANNED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


class ScheduleStatus(StrEnum):
    """Lifecycle of a generated schedule."""

    DRAFT = "DRAFT"
    DISPATCHED = "DISPATCHED"


@dataclass(frozen=True)
class WorkOrder:
    order_no: str
    product_sku: str
    product_name: str
    quantity: int
    priority: int
    due_date: str
    customer: str
    status: str = OrderStatus.DRAFT.value
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass(frozen=True)
class Material:
    sku: str
    name: str
    unit: str
    stock_qty: float
    reserved_qty: float = 0.0

    @property
    def available_qty(self) -> float:
        return round(self.stock_qty - self.reserved_qty, 6)


@dataclass(frozen=True)
class BomLine:
    product_sku: str
    material_sku: str
    qty_per_unit: float


@dataclass(frozen=True)
class ProductionLine:
    line_id: str
    name: str
    status: str = LineStatus.IDLE.value
    capacity_per_hour: float = 0.0
    shift_start: str = "08:00"
    shift_hours: float = 12.0

    @property
    def daily_capacity(self) -> float:
        return round(self.capacity_per_hour * self.shift_hours, 6)


@dataclass(frozen=True)
class ProductionTask:
    task_id: str
    order_no: str
    line_id: str
    plan_id: str
    quantity: int
    planned_start: str
    planned_end: str
    status: str = TaskStatus.PLANNED.value
    actual_qty: int = 0
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass(frozen=True)
class Schedule:
    plan_id: str
    plan_date: str
    status: str = ScheduleStatus.DRAFT.value
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScheduleItem:
    plan_id: str
    seq: int
    order_no: str
    line_id: str
    planned_start: str
    planned_end: str
    status: str = TaskStatus.PLANNED.value


def to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses and lists to plain dict/json values."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    if isinstance(obj, tuple):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj