"""Resource catalog and schema introspection.

Describes every resource the server exposes (id field, labels, searchable
fields, read-only flags) so the generic query/get/count tools and the
``mes://`` resources can be generated from a single source of truth. It also
powers the discovery tools (``list_resources`` / ``get_resource_schema``)
that let an agent learn the data model at runtime — the same idea as
Kingdee's ``FORM_CATALOG``.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResourceSpec:
    name: str                      # wire name, e.g. "work_order"
    label: str                     # human label, e.g. "ERP 工单"
    module: str                    # grouping: ERP / MES / 排产 / 基础数据
    id_field: str                  # primary key field name
    description: str
    searchable: tuple[str, ...] = ()   # fields addressable in query filters
    writable: tuple[str, ...] = ()     # subset of {"create", "update"}


CATALOG: dict[str, ResourceSpec] = {
    "work_order": ResourceSpec(
        name="work_order",
        label="ERP 工单",
        module="ERP",
        id_field="order_no",
        description="生产工单：产品、数量、交期、优先级、状态",
        searchable=("status", "order_no", "product_sku", "product_name", "customer", "due_date"),
        writable=("create", "update"),
    ),
    "material": ResourceSpec(
        name="material",
        label="物料",
        module="ERP",
        id_field="sku",
        description="物料主数据与库存：库存 / 占用 / 可用",
        searchable=("sku", "name"),
        writable=("create", "update"),
    ),
    "production_line": ResourceSpec(
        name="production_line",
        label="产线（设备）",
        module="MES",
        id_field="line_id",
        description="产线的状态、产能、班次",
        searchable=("line_id", "name", "status"),
        writable=("update",),
    ),
    "production_task": ResourceSpec(
        name="production_task",
        label="生产任务",
        module="MES",
        id_field="task_id",
        description="下发到产线的生产任务：计划起止、状态、实际产量",
        searchable=("task_id", "order_no", "line_id", "status"),
        writable=("update",),
    ),
    "schedule": ResourceSpec(
        name="schedule",
        label="排产计划",
        module="排产",
        id_field="plan_id",
        description="排产计划及其明细行（只读，由 schedule_generate 生成）",
        searchable=("plan_id", "plan_date", "status"),
        writable=(),
    ),
    "process_routing": ResourceSpec(
        name="process_routing",
        label="工序路由",
        module="MES",
        id_field="routing_id",
        description="产品的工序配方：工序顺序、加工中心、标准工时",
        searchable=("routing_id", "product_sku", "operation_no"),
        writable=(),
    ),
}


def list_specs() -> list[ResourceSpec]:
    return list(CATALOG.values())


def get_spec(resource: str) -> ResourceSpec:
    return CATALOG[resource]


def catalog_as_dict() -> list[dict]:
    out = []
    for spec in CATALOG.values():
        out.append({
            "name": spec.name,
            "label": spec.label,
            "module": spec.module,
            "id_field": spec.id_field,
            "description": spec.description,
            "searchable": list(spec.searchable),
            "writable": list(spec.writable),
        })
    return out