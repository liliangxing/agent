"""Backend abstraction: the seam behind which a real ERP/MES lives.

The MCP layer talks only to :class:`ManufacturingBackend`. Today the shipped
implementation is :class:`MemoryBackend` (the SQLite demo store), which makes
the server runnable with zero external systems. In production you provide a
backend that implements the same contract against the real 金蝶 / Odoo /
SAP / custom MES WebAPI — the MCP tools do not change.

Every backend method raises typed errors from :mod:`errors` so the transport
layer never leaks internal detail.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from .errors import NotFoundError, ValidationError
from .models import Material, WorkOrder
from .store import Store


class ManufacturingBackend(ABC):
    """Contract for generic record access, keyed by catalog resource name."""

    @abstractmethod
    def list_records(self, resource: str, filters: dict[str, Any]) -> list[dict]:
        ...

    @abstractmethod
    def get_record(self, resource: str, record_id: str) -> dict:
        ...

    @abstractmethod
    def count_records(self, resource: str, filters: dict[str, Any]) -> int:
        ...

    @abstractmethod
    def create_record(self, resource: str, data: Any) -> dict:
        ...

    @abstractmethod
    def update_record(self, resource: str, record_id: str, data: Any) -> dict:
        ...


class MemoryBackend(ManufacturingBackend):
    """Reference backend backed by the in-memory / SQLite demo store."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- read ----------------------------------------------------------------
    def list_records(self, resource: str, filters: dict[str, Any]) -> list[dict]:
        store = self.store
        from .models import to_dict

        if resource == "work_order":
            rows = store.list_work_orders(
                status=filters.get("status"),
                due_date_from=filters.get("due_date_from"),
                due_date_to=filters.get("due_date_to"),
            )
            return _keyword_filter([to_dict(r) for r in rows], filters)
        if resource == "material":
            rows = store.list_materials(filters.get("sku"))
            return _keyword_filter([to_dict(r) for r in rows], filters)
        if resource == "production_line":
            rows = store.list_lines(filters.get("line_id"))
            return _keyword_filter([to_dict(r) for r in rows], filters)
        if resource == "production_task":
            rows = store.list_tasks(filters.get("status"), filters.get("line_id"))
            return _keyword_filter([to_dict(r) for r in rows], filters)
        if resource == "schedule":
            rows = store.list_schedules(filters.get("status"))
            return _keyword_filter([to_dict(r) for r in rows], filters)
        if resource == "process_routing":
            rows = store.list_routings(filters.get("product_sku"))
            return _keyword_filter([to_dict(r) for r in rows], filters)
        raise ValidationError(f"未知资源: {resource}")

    def get_record(self, resource: str, record_id: str) -> dict:
        from .models import to_dict

        store = self.store
        finder = {
            "work_order": store.get_work_order,
            "material": store.get_material,
            "production_line": store.get_line,
            "production_task": store.get_task,
            "schedule": store.get_schedule,
            "process_routing": store.get_routing,
        }.get(resource)
        if finder is None:
            raise ValidationError(f"未知资源: {resource}")
        record = finder(record_id)
        if record is None:
            raise NotFoundError(f"{resource}({record_id}) 不存在")
        return to_dict(record)

    def count_records(self, resource: str, filters: dict[str, Any]) -> int:
        return len(self.list_records(resource, filters))

    # -- write ---------------------------------------------------------------
    def create_record(self, resource: str, data: Any) -> dict:
        store = self.store
        from .models import to_dict

        if resource == "work_order":
            if store.get_work_order(data.order_no) is not None:
                raise ValidationError(f"工单已存在: {data.order_no}")
            order = WorkOrder(**data.model_dump())
            store.insert_work_order(order)
            store.commit()
            return to_dict(order)
        if resource == "material":
            if store.get_material(data.sku) is not None:
                raise ValidationError(f"物料已存在: {data.sku}")
            material = Material(sku=data.sku, name=data.name, unit=data.unit, stock_qty=data.stock_qty)
            store.insert_material(material)
            store.commit()
            return to_dict(material)
        raise ValidationError(f"资源 {resource} 不支持创建")

    def update_record(self, resource: str, record_id: str, data: Any) -> dict:
        store = self.store
        from .models import to_dict

        changes = {k: v for k, v in data.model_dump().items() if v is not None}
        changes.pop("order_no", None)
        changes.pop("sku", None)
        changes.pop("line_id", None)
        changes.pop("task_id", None)

        if resource == "work_order":
            if store.get_work_order(record_id) is None:
                raise NotFoundError(f"工单不存在: {record_id}")
            store.update_work_order(record_id, **changes)
        elif resource == "material":
            if store.get_material(record_id) is None:
                raise NotFoundError(f"物料不存在: {record_id}")
            store.update_material(record_id, **changes)
        elif resource == "production_line":
            if store.get_line(record_id) is None:
                raise NotFoundError(f"产线不存在: {record_id}")
            status = changes.pop("status", None)
            if status is not None:
                store.set_line_status(record_id, status)
        elif resource == "production_task":
            if store.get_task(record_id) is None:
                raise NotFoundError(f"任务不存在: {record_id}")
            store.update_task(record_id, **changes)
        else:
            raise ValidationError(f"资源 {resource} 不支持更新")
        store.commit()
        return self.get_record(resource, record_id)


def _keyword_filter(records: list[dict], filters: dict[str, Any]) -> list[dict]:
    """Apply an optional case-insensitive keyword match across string fields."""
    keyword = (filters.get("keyword") or "").strip().lower()
    if not keyword:
        return records
    return [
        r for r in records
        if any(keyword in str(v).lower() for v in r.values())
    ]