"""Pydantic input models for MCP tools (strict, ``extra="forbid"``).

Mirrors the Kingdee MCP pattern: every write tool and every query filter has
a dedicated model so the MCP handshake itself enforces types, ranges, and
rejects unknown fields before any business logic runs.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- query / pagination -------------------------------------------------------
class QueryFilter(StrictModel):
    status: str | None = Field(default=None, description="按状态过滤")
    keyword: str | None = Field(default=None, description="关键字模糊匹配")
    limit: int = Field(default=25, ge=1, le=200, description="返回条数上限")
    offset: int = Field(default=0, ge=0, description="分页偏移")


# -- work order ---------------------------------------------------------------
class WorkOrderCreate(StrictModel):
    order_no: str = Field(description="工单号（唯一）")
    product_sku: str = Field(description="产品 SKU")
    product_name: str = Field(description="产品名称")
    quantity: int = Field(ge=1, le=1_000_000, description="数量")
    priority: int = Field(default=1, ge=1, le=9, description="优先级，数字越小越优先")
    due_date: str = Field(description="交期 YYYY-MM-DD")
    customer: str = Field(default="", description="客户")


class WorkOrderUpdate(StrictModel):
    order_no: str = Field(description="工单号")
    quantity: int | None = Field(default=None, ge=1, le=1_000_000, description="数量")
    priority: int | None = Field(default=None, ge=1, le=9, description="优先级")
    due_date: str | None = Field(default=None, description="交期 YYYY-MM-DD")
    customer: str | None = Field(default=None, description="客户")


class WorkOrderRelease(StrictModel):
    order_no: str = Field(description="要发布的工单号")


class OrderNosInput(StrictModel):
    order_nos: list[str] | None = Field(default=None, description="工单号列表，缺省表示全部")


# -- material ---------------------------------------------------------------
class MaterialCreate(StrictModel):
    sku: str = Field(description="物料 SKU（唯一）")
    name: str = Field(description="物料名称")
    unit: str = Field(default="pcs", description="计量单位")
    stock_qty: float = Field(default=0.0, ge=0.0, description="初始库存")


class MaterialUpdate(StrictModel):
    sku: str = Field(description="物料 SKU")
    name: str | None = Field(default=None, description="物料名称")
    stock_qty: float | None = Field(default=None, ge=0.0, description="库存调整后的值")


# -- production line ----------------------------------------------------------
class ProductionLineUpdate(StrictModel):
    line_id: str = Field(description="产线编号")
    status: str = Field(description="IDLE / BUSY / MAINTENANCE")


# -- production task ----------------------------------------------------------
class ProductionTaskUpdate(StrictModel):
    task_id: str = Field(description="任务编号")


class ReportProgressInput(StrictModel):
    task_id: str = Field(description="任务编号")
    actual_qty: int = Field(ge=0, description="实际产量")
    complete: bool = Field(default=False, description="是否完工")


# -- scheduling ---------------------------------------------------------------
class PlanFilter(StrictModel):
    status: str | None = Field(default=None, description="DRAFT / DISPATCHED")


class PlanIdInput(StrictModel):
    plan_id: str = Field(description="排产计划编号")


class ScheduleGenerateInput(StrictModel):
    plan_date: str | None = Field(default=None, description="排产日期 YYYY-MM-DD，缺省今天")


class CapacityQueryInput(StrictModel):
    plan_date: str | None = Field(default=None, description="排产日期 YYYY-MM-DD，缺省今天")


# -- discovery ----------------------------------------------------------------
class ResourceSchemaInput(StrictModel):
    resource: str = Field(description="资源名，如 work_order / material / production_line")