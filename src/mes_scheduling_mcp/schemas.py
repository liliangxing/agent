"""Strict Pydantic input models for mutating tools.

Every field is validated and unknown fields are rejected (``extra="forbid"``)
so a malformed or spoofed payload fails loudly at the protocol boundary rather
than being silently coerced.  This mirrors the per-endpoint request schemas of
production ERP/MES MCP servers (Odoo's ``schemas.py``, Kingdee's 75 Pydantic
models).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemCreate(_Strict):
    sku: str = Field(..., min_length=2)
    name: str
    item_type: str = Field(..., pattern="^(FINISHED|SEMI|RAW|CONSUMABLE|TOOLING)$")
    unit: str = "PC"
    lead_time_days: int = Field(1, ge=0)
    safety_stock: float = Field(0.0, ge=0)
    abc_class: str = Field("C", pattern="^(A|B|C)$")
    default_bom_code: Optional[str] = None
    default_routing_code: Optional[str] = None


class WorkOrderCreate(_Strict):
    order_no: str = Field(..., min_length=3)
    product_sku: str
    quantity: int = Field(..., gt=0)
    due_date: str
    priority: str = Field("NORMAL", pattern="^(CRITICAL|HIGH|NORMAL|LOW)$")
    customer: str = ""
    sales_order_no: Optional[str] = None


class SalesOrderLineCreate(_Strict):
    item_sku: str
    quantity: int = Field(..., gt=0)
    unit_price: float = 0.0
    seq: Optional[int] = None


class SalesOrderCreate(_Strict):
    order_no: str = Field(..., min_length=3)
    customer_id: str
    due_date: str
    priority: str = Field("NORMAL", pattern="^(CRITICAL|HIGH|NORMAL|LOW)$")
    lines: list[SalesOrderLineCreate] = Field(..., min_length=1)
    order_date: Optional[str] = None


class PurchaseOrderLineCreate(_Strict):
    item_sku: str
    quantity: float = Field(..., gt=0)
    seq: Optional[int] = None


class PurchaseOrderCreate(_Strict):
    po_no: str = Field(..., min_length=3)
    supplier: str
    due_date: str
    lines: list[PurchaseOrderLineCreate] = Field(..., min_length=1)


class InventoryAdjust(_Strict):
    sku: str
    lot_no: str
    delta_on_hand: float
    delta_reserved: float = 0.0


class ReportProgress(_Strict):
    production_order_no: str
    operation_no: int = Field(..., ge=0)
    qty_good: int = Field(..., ge=0)
    qty_bad: int = Field(0, ge=0)
    complete: bool = False


class ReportDowntime(_Strict):
    work_center: str
    reason: str = Field(..., pattern="^(BREAKDOWN|MAINTENANCE|SETUP|MATERIAL_SHORTAGE|QUALITY_STOP|IDLE)$")
    start_time: str
    end_time: Optional[str] = None


class QualityInspect(_Strict):
    production_order_no: str
    operation_no: int = Field(..., ge=0)
    sample_qty: int = Field(..., ge=0)
    defect_qty: int = Field(0, ge=0)
    disposition: str = Field(..., pattern="^(PASS|REWORK|SCRAP|CONCESSION)$")
    defect_code: str = ""
    defect_desc: str = ""
    unit_ref: str = ""
    station: str = ""
    inspector: str = ""


class StartRun(_Strict):
    production_order_no: str
    operation_no: int = Field(..., ge=0)
    planned_minutes: Optional[float] = Field(None, gt=0)
    ideal_cycle_sec: Optional[float] = Field(None, gt=0)


class RunOutput(_Strict):
    run_no: str
    qty_good: int = Field(..., ge=0)
    qty_scrap: int = Field(0, ge=0)


class RunDowntime(_Strict):
    run_no: str
    reason: str = Field(..., pattern="^(BREAKDOWN|MAINTENANCE|SETUP|MATERIAL_SHORTAGE|QUALITY_STOP|IDLE)$")
    minutes: float = Field(..., gt=0)
    planned: bool = False
    start_time: Optional[str] = None


class RecordTest(_Strict):
    production_order_no: str
    operation_no: int = Field(..., ge=0)
    unit_ref: str = Field(..., min_length=1)
    result: str = Field(..., pattern="^(PASS|FAIL)$")
    station: str = ""
    defect_code: str = ""
    defect_desc: str = ""
    characteristic: str = ""
    nominal: Optional[float] = None
    tolerance: Optional[float] = Field(None, ge=0)
    measured: Optional[float] = None
    inspector: str = ""


class MaterialIssue(_Strict):
    production_order_no: str
    item_sku: str
    quantity: float = Field(..., gt=0)


class ScheduleGenerate(_Strict):
    plan_date: Optional[date] = None
    horizon_days: int = Field(1, ge=1, le=30)
    objective: str = Field("on-time delivery")