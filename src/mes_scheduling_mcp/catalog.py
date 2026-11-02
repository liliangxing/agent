"""Resource catalog and schema introspection.

Describes every entity the server exposes so discovery tools
(``list_resources`` / ``get_resource_schema``) and the ``mes://`` resources
share a single source of truth — the same idea as Kingdee's ``FORM_CATALOG``
and Odoo's model registry.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceSpec:
    name: str
    label: str
    module: str
    id_field: str
    description: str
    searchable: tuple[str, ...] = ()
    writable: tuple[str, ...] = ()


CATALOG: dict[str, ResourceSpec] = {
    "work_order": ResourceSpec(
        name="work_order", label="ERP 生产工单", module="ERP", id_field="order_no",
        description="生产工单：产品、数量、交期、优先级、状态、齐套结果",
        searchable=("order_no", "product_sku", "status", "customer", "due_date"),
        writable=("create", "update", "release"),
    ),
    "item": ResourceSpec(
        name="item", label="物料主数据", module="ERP", id_field="sku",
        description="物料/成品/半成品主数据：类型、单位、提前期、安全库存、ABC",
        searchable=("sku", "name", "item_type"),
        writable=("create", "update"),
    ),
    "sales_order": ResourceSpec(
        name="sales_order", label="销售订单", module="ERP", id_field="order_no",
        description="销售订单：客户、交期、优先级、明细行",
        searchable=("order_no", "customer_id", "status", "due_date"),
        writable=("create",),
    ),
    "purchase_order": ResourceSpec(
        name="purchase_order", label="采购订单", module="ERP", id_field="po_no",
        description="采购订单：供应商、交期、明细行、收货状态",
        searchable=("po_no", "supplier", "status"),
        writable=("create",),
    ),
    "work_center": ResourceSpec(
        name="work_center", label="工作中心（设备）", module="MES", id_field="code",
        description="设备/工位：类型、状态、产能、当前加工订单",
        searchable=("code", "name", "status", "wc_type"),
        writable=("update",),
    ),
    "routing": ResourceSpec(
        name="routing", label="工艺路线", module="MES", id_field="routing_code",
        description="产品工序配方：工序号、工作中心、装夹/加工时间、工装",
        searchable=("routing_code", "product_sku", "operation_no"),
        writable=(),
    ),
    "production_order": ResourceSpec(
        name="production_order", label="MES 生产订单", module="MES", id_field="production_order_no",
        description="MES 侧生产订单（对应 ERP 工单）：工序进度、状态、计划起止",
        searchable=("production_order_no", "erp_work_order_no", "product_sku", "status"),
        writable=("update",),
    ),
    "schedule": ResourceSpec(
        name="schedule", label="排产计划", module="排产", id_field="plan_id",
        description="有限产能排产计划及其工序明细",
        searchable=("plan_id", "plan_date", "status"),
        writable=(),
    ),
}


def get_spec(resource: str) -> ResourceSpec:
    return CATALOG[resource]


def catalog_as_dict() -> list[dict]:
    return [
        {
            "name": s.name, "label": s.label, "module": s.module, "id_field": s.id_field,
            "description": s.description, "searchable": list(s.searchable),
            "writable": list(s.writable),
        }
        for s in CATALOG.values()
    ]