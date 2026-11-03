"""Domain model for a precision-parts manufacturing plant.

This module defines the canonical data model spanning three systems that the
case integrates:

- ERP   (sales orders, items, multi-level BOM, inventory/lots, purchase
         orders, production work orders, MRP)
- MES   (work centers, shift calendars, process routings, production orders,
         operation progress, downtime/OEE, quality, material issues)
- APS   (finite-capacity scheduling with changeover optimization)

All enums use :class:`str` so they round-trip cleanly through JSON (the MCP
protocol boundary) and through SQLite.  Dataclasses are ``slots``-free plain
objects used as value carriers at the domain boundary; persistence maps fields
explicitly in ``store.py`` rather than relying on ``__dict__``.

"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class StrEnum(str, Enum):
    """A string enum whose ``str()`` is its value (JSON/SQLite friendly)."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    RELEASED = "RELEASED"
    IN_PROCESS = "IN_PROCESS"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    ON_HOLD = "ON_HOLD"


class ItemType(StrEnum):
    FINISHED = "FINISHED"
    SEMI = "SEMI"
    RAW = "RAW"
    CONSUMABLE = "CONSUMABLE"
    TOOLING = "TOOLING"


class AbcClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class Priority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class SalesOrderStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    PARTIALLY_SHIPPED = "PARTIALLY_SHIPPED"
    SHIPPED = "SHIPPED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class PurchaseOrderStatus(StrEnum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CLOSED = "CLOSED"


class WorkCenterStatus(StrEnum):
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    SETUP = "SETUP"
    BREAKDOWN = "BREAKDOWN"
    MAINTENANCE = "MAINTENANCE"
    OFFLINE = "OFFLINE"


class WorkCenterType(StrEnum):
    CNC_LATHE = "CNC_LATHE"
    MACHINING_CENTER = "MACHINING_CENTER"
    GRINDER = "GRINDER"
    DIE_CAST = "DIE_CAST"
    HEAT_TREAT = "HEAT_TREAT"
    ASSEMBLY = "ASSEMBLY"
    INSPECTION = "INSPECTION"


class ProductionOrderStatus(StrEnum):
    DRAFT = "DRAFT"
    RELEASED = "RELEASED"
    IN_PROCESS = "IN_PROCESS"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class OperationStatus(StrEnum):
    PENDING = "PENDING"
    SETUP = "SETUP"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


class DowntimeReason(StrEnum):
    BREAKDOWN = "BREAKDOWN"
    MAINTENANCE = "MAINTENANCE"
    SETUP = "SETUP"
    MATERIAL_SHORTAGE = "MATERIAL_SHORTAGE"
    QUALITY_STOP = "QUALITY_STOP"
    IDLE = "IDLE"


class QualityDisposition(StrEnum):
    PASS = "PASS"
    REWORK = "REWORK"
    SCRAP = "SCRAP"
    CONCESSION = "CONCESSION"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    ENDED = "ENDED"


class TestResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class ScheduleStatus(StrEnum):
    DRAFT = "DRAFT"
    OPTIMIZED = "OPTIMIZED"
    DISPATCHED = "DISPATCHED"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"


class MrpProposalType(StrEnum):
    PURCHASE = "PURCHASE"
    PRODUCE = "PRODUCE"


class EventStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    ACKED = "ACKED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class EventType(StrEnum):
    MASTER_DATA_SYNC = "MASTER_DATA_SYNC"
    ORDER_RELEASE = "ORDER_RELEASE"
    ORDER_DISPATCH = "ORDER_DISPATCH"
    PROGRESS_REPORT = "PROGRESS_REPORT"
    COMPLETION = "COMPLETION"
    MATERIAL_ISSUE = "MATERIAL_ISSUE"
    QUALITY_RESULT = "QUALITY_RESULT"
    RECONCILIATION = "RECONCILIATION"


class SyncStatus(StrEnum):
    PENDING = "PENDING"
    SYNCED = "SYNCED"
    DRIFT = "DRIFT"
    FAILED = "FAILED"


# --------------------------------------------------------------------------- #
# ERP models
# --------------------------------------------------------------------------- #
@dataclass
class Customer:
    customer_id: str
    name: str
    region: str
    credit_rating: str
    payment_terms: str = "NET30"


@dataclass
class Item:
    sku: str
    name: str
    item_type: ItemType
    unit: str
    lead_time_days: int = 1
    safety_stock: float = 0.0
    abc_class: AbcClass = AbcClass.C
    default_bom_code: str | None = None
    default_routing_code: str | None = None


@dataclass
class BomHeader:
    bom_code: str
    product_sku: str
    version: str
    description: str = ""


@dataclass
class BomLine:
    bom_code: str
    product_sku: str
    seq: int
    component_sku: str
    qty_per: float
    scrap_rate: float = 0.0
    operation_no: int | None = None
    is_phantom: bool = False


@dataclass
class InventoryLot:
    sku: str
    lot_no: str
    warehouse: str
    on_hand: float
    reserved: float = 0.0
    in_transit: float = 0.0
    expiry_date: str | None = None


@dataclass
class SalesOrderLine:
    seq: int
    item_sku: str
    quantity: int
    unit_price: float = 0.0
    status: str = "OPEN"


@dataclass
class SalesOrder:
    order_no: str
    customer_id: str
    order_date: str
    due_date: str
    priority: Priority
    status: SalesOrderStatus = SalesOrderStatus.DRAFT
    lines: list[SalesOrderLine] = field(default_factory=list)


@dataclass
class PurchaseOrderLine:
    seq: int
    item_sku: str
    quantity: float
    received_qty: float = 0.0


@dataclass
class PurchaseOrder:
    po_no: str
    supplier: str
    order_date: str
    due_date: str
    status: PurchaseOrderStatus = PurchaseOrderStatus.DRAFT
    lines: list[PurchaseOrderLine] = field(default_factory=list)


@dataclass
class WorkOrder:
    order_no: str
    product_sku: str
    product_name: str
    quantity: int
    completed_qty: int = 0
    status: OrderStatus = OrderStatus.DRAFT
    priority: Priority = Priority.NORMAL
    due_date: str = ""
    customer: str = ""
    sales_order_no: str | None = None
    bom_code: str | None = None
    routing_code: str | None = None
    created_at: str = field(default_factory=_now)
    released_at: str | None = None
    completed_at: str | None = None


@dataclass
class MrpProposal:
    proposal_no: str
    item_sku: str
    proposal_type: MrpProposalType
    quantity: float
    due_date: str
    source_work_order: str | None = None
    status: str = "OPEN"
    gross_qty: float = 0.0
    on_hand_qty: float = 0.0
    on_order_qty: float = 0.0
    safety_stock: float = 0.0
    pegged_orders: str = ""
    release_date: str | None = None


# --------------------------------------------------------------------------- #
# MES models
# --------------------------------------------------------------------------- #
@dataclass
class WorkCenter:
    code: str
    name: str
    wc_type: WorkCenterType
    status: WorkCenterStatus = WorkCenterStatus.IDLE
    capacity_per_hour: float = 0.0
    setup_time_min: float = 0.0
    current_production_order: str | None = None


@dataclass
class ShiftEntry:
    work_center: str
    shift_date: str
    shift_no: str
    start_time: str
    end_time: str
    efficiency: float = 1.0


@dataclass
class RoutingOperation:
    routing_code: str
    product_sku: str
    operation_no: int
    operation_name: str
    work_center: str
    setup_time_min: float
    run_time_min_per_unit: float
    tooling: str | None = None
    queue_time_min: float = 0.0
    inspection_required: bool = False
    instructions: str = ""


@dataclass
class ProductionOrder:
    production_order_no: str
    erp_work_order_no: str
    product_sku: str
    product_name: str
    quantity: int
    completed_qty: int = 0
    status: ProductionOrderStatus = ProductionOrderStatus.DRAFT
    priority: Priority = Priority.NORMAL
    due_date: str = ""
    scheduled_start: str | None = None
    scheduled_end: str | None = None


@dataclass
class OperationProgress:
    production_order_no: str
    operation_no: int
    work_center: str
    status: OperationStatus = OperationStatus.PENDING
    qty_good: int = 0
    qty_bad: int = 0
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class ProductionRun:
    run_no: str
    production_order_no: str
    operation_no: int
    work_center: str
    planned_minutes: float
    ideal_cycle_sec: float
    qty_good: int = 0
    qty_scrap: int = 0
    status: RunStatus = RunStatus.RUNNING
    started_at: str = field(default_factory=_now)
    ended_at: str | None = None


@dataclass
class DowntimeEvent:
    event_id: str
    work_center: str
    reason: DowntimeReason
    start_time: str
    end_time: str | None = None
    duration_min: float = 0.0
    run_no: str | None = None
    planned: bool = False


@dataclass
class QualityRecord:
    record_no: str
    production_order_no: str
    operation_no: int
    sample_qty: int
    defect_qty: int
    disposition: QualityDisposition
    defect_code: str = ""
    defect_desc: str = ""
    unit_ref: str = ""
    station: str = ""
    inspector: str = ""
    inspected_at: str = field(default_factory=_now)


@dataclass
class TestRecord:
    test_no: str
    production_order_no: str
    operation_no: int
    unit_ref: str
    station: str
    result: TestResult
    defect_code: str = ""
    defect_desc: str = ""
    characteristic: str = ""
    nominal: float | None = None
    tolerance: float | None = None
    measured: float | None = None
    inspector: str = ""
    tested_at: str = field(default_factory=_now)


@dataclass
class MaterialIssue:
    issue_no: str
    production_order_no: str
    item_sku: str
    quantity: float
    issued_at: str = field(default_factory=_now)


@dataclass
class MaterialConsumption:
    production_order_no: str
    item_sku: str
    lot_no: str
    quantity: float
    issued_at: str = field(default_factory=_now)


@dataclass
class LotGenealogyLink:
    genealogy_id: str
    parent_sku: str
    parent_lot: str
    child_sku: str
    child_lot: str
    production_order_no: str
    quantity: float
    created_at: str = field(default_factory=_now)


# --------------------------------------------------------------------------- #
# APS models
# --------------------------------------------------------------------------- #
@dataclass
class ChangeoverEntry:
    from_sku: str
    to_sku: str
    setup_time_min: float


@dataclass
class ScheduleOperation:
    plan_id: str
    seq: int
    production_order_no: str
    work_center: str
    operation_no: int
    start_time: str
    end_time: str
    setup_min: float
    run_min: float


@dataclass
class SchedulePlan:
    plan_id: str
    plan_date: str
    horizon_days: int = 1
    status: ScheduleStatus = ScheduleStatus.DRAFT
    objective: str = "on-time delivery"
    summary: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)


# --------------------------------------------------------------------------- #
# Integration models
# --------------------------------------------------------------------------- #
@dataclass
class OutboxEvent:
    event_id: str
    event_type: EventType
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    status: EventStatus = EventStatus.PENDING
    attempts: int = 0
    created_at: str = field(default_factory=_now)
    published_at: str | None = None
    acked_at: str | None = None
    last_error: str | None = None


@dataclass
class ErpMesMapping:
    erp_id: str
    mes_id: str
    target_type: str
    status: SyncStatus = SyncStatus.PENDING
    created_at: str = field(default_factory=_now)
    last_synced_at: str | None = None


@dataclass
class AuditEntry:
    audit_id: str
    ts: str
    operator: str
    operation: str
    resource: str
    resource_id: str
    correlation_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    success: bool
    error: str | None = None