"""SQLite persistence layer for the full ERP / MES / APS integration.

The store holds a single source of truth shared by the three simulated
systems, plus the integration fabric (idempotency, outbox/inbox, dual-key
mapping, audit trail, reconciliation).  It uses the stdlib ``sqlite3`` driver
so the server has no external database dependency while still persisting
state between restarts when pointed at a file path.

Conventions:
- Foreign keys are enforced.
- All writes go through :meth:`transaction` (single connection, explicit
  commit / rollback) so multi-statement flows are atomic.
- Rows map to domain dataclasses explicitly (never via ``__dict__``), because
  several dataclasses carry derived or nested fields.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterable, Optional

from .models import (
    AuditEntry,
    BomHeader,
    BomLine,
    ChangeoverEntry,
    Customer,
    DowntimeEvent,
    ErpMesMapping,
    InventoryLot,
    Item,
    MaterialIssue,
    MrpProposal,
    OperationProgress,
    OutboxEvent,
    ProductionOrder,
    PurchaseOrder,
    PurchaseOrderLine,
    QualityRecord,
    RoutingOperation,
    SalesOrder,
    SalesOrderLine,
    ScheduleOperation,
    SchedulePlan,
    ShiftEntry,
    WorkCenter,
    WorkOrder,
)
from .seed import SEED_DATA


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id(prefix: str, n: int = 12) -> str:
    return f"{prefix}{uuid.uuid4().hex[:n]}"


class IntegrityError(Exception):
    """Raised when a uniqueness / reference constraint is violated."""


class Store:
    """Thin repository around a SQLite database."""

    # ------------------------------------------------------------------ setup
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.environ.get("MES_MCP_DB", ":memory:")
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        self._seed_if_empty()

    @contextmanager
    def transaction(self) -> Iterable[None]:
        """Run a block inside an explicit transaction (commit on success)."""
        self.conn.execute("BEGIN")
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()

    # ----------------------------------------------------------------- schema
    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            -- ============================ ERP ============================
            CREATE TABLE IF NOT EXISTS customers (
                customer_id    TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                region         TEXT NOT NULL,
                credit_rating  TEXT NOT NULL,
                payment_terms  TEXT NOT NULL DEFAULT 'NET30'
            );

            CREATE TABLE IF NOT EXISTS items (
                sku               TEXT PRIMARY KEY,
                name              TEXT NOT NULL,
                item_type         TEXT NOT NULL,
                unit              TEXT NOT NULL,
                lead_time_days    INTEGER NOT NULL DEFAULT 1,
                safety_stock      REAL NOT NULL DEFAULT 0,
                abc_class         TEXT NOT NULL DEFAULT 'C',
                default_bom_code  TEXT,
                default_routing_code TEXT
            );

            CREATE TABLE IF NOT EXISTS bom_headers (
                bom_code    TEXT PRIMARY KEY,
                product_sku TEXT NOT NULL,
                version     TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS bom_lines (
                bom_code      TEXT NOT NULL,
                product_sku   TEXT NOT NULL,
                seq           INTEGER NOT NULL,
                component_sku TEXT NOT NULL,
                qty_per       REAL NOT NULL,
                scrap_rate    REAL NOT NULL DEFAULT 0,
                operation_no  INTEGER,
                is_phantom    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (bom_code, seq)
            );

            CREATE TABLE IF NOT EXISTS inventory_lots (
                sku         TEXT NOT NULL,
                lot_no      TEXT NOT NULL,
                warehouse   TEXT NOT NULL,
                on_hand     REAL NOT NULL DEFAULT 0,
                reserved    REAL NOT NULL DEFAULT 0,
                in_transit  REAL NOT NULL DEFAULT 0,
                expiry_date TEXT,
                PRIMARY KEY (sku, lot_no)
            );

            CREATE TABLE IF NOT EXISTS sales_orders (
                order_no    TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                order_date  TEXT NOT NULL,
                due_date    TEXT NOT NULL,
                priority    TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'DRAFT',
                lines       TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS purchase_orders (
                po_no      TEXT PRIMARY KEY,
                supplier   TEXT NOT NULL,
                order_date TEXT NOT NULL,
                due_date   TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'DRAFT',
                lines      TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS work_orders (
                order_no      TEXT PRIMARY KEY,
                product_sku   TEXT NOT NULL,
                product_name  TEXT NOT NULL,
                quantity      INTEGER NOT NULL,
                completed_qty INTEGER NOT NULL DEFAULT 0,
                status        TEXT NOT NULL DEFAULT 'DRAFT',
                priority      TEXT NOT NULL DEFAULT 'NORMAL',
                due_date      TEXT NOT NULL,
                customer      TEXT NOT NULL,
                sales_order_no TEXT,
                bom_code      TEXT,
                routing_code  TEXT,
                created_at    TEXT NOT NULL,
                released_at   TEXT,
                completed_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS mrp_proposals (
                proposal_no      TEXT PRIMARY KEY,
                item_sku         TEXT NOT NULL,
                proposal_type    TEXT NOT NULL,
                quantity         REAL NOT NULL,
                due_date         TEXT NOT NULL,
                source_work_order TEXT,
                status           TEXT NOT NULL DEFAULT 'OPEN'
            );

            -- ============================ MES ============================
            CREATE TABLE IF NOT EXISTS work_centers (
                code                    TEXT PRIMARY KEY,
                name                    TEXT NOT NULL,
                wc_type                 TEXT NOT NULL,
                status                  TEXT NOT NULL DEFAULT 'IDLE',
                capacity_per_hour       REAL NOT NULL DEFAULT 0,
                setup_time_min          REAL NOT NULL DEFAULT 0,
                current_production_order TEXT
            );

            CREATE TABLE IF NOT EXISTS shift_entries (
                work_center TEXT NOT NULL,
                shift_date  TEXT NOT NULL,
                shift_no    TEXT NOT NULL,
                start_time  TEXT NOT NULL,
                end_time    TEXT NOT NULL,
                efficiency  REAL NOT NULL DEFAULT 1,
                PRIMARY KEY (work_center, shift_date, shift_no)
            );

            CREATE TABLE IF NOT EXISTS routing_operations (
                routing_code        TEXT NOT NULL,
                product_sku         TEXT NOT NULL,
                operation_no        INTEGER NOT NULL,
                operation_name      TEXT NOT NULL,
                work_center         TEXT NOT NULL,
                setup_time_min      REAL NOT NULL DEFAULT 0,
                run_time_min_per_unit REAL NOT NULL DEFAULT 0,
                tooling             TEXT,
                queue_time_min      REAL NOT NULL DEFAULT 0,
                inspection_required INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (routing_code, operation_no)
            );

            CREATE TABLE IF NOT EXISTS production_orders (
                production_order_no TEXT PRIMARY KEY,
                erp_work_order_no   TEXT NOT NULL,
                product_sku         TEXT NOT NULL,
                product_name        TEXT NOT NULL,
                quantity            INTEGER NOT NULL,
                completed_qty       INTEGER NOT NULL DEFAULT 0,
                status              TEXT NOT NULL DEFAULT 'DRAFT',
                priority            TEXT NOT NULL DEFAULT 'NORMAL',
                due_date            TEXT NOT NULL,
                scheduled_start     TEXT,
                scheduled_end       TEXT
            );

            CREATE TABLE IF NOT EXISTS operation_progress (
                production_order_no TEXT NOT NULL,
                operation_no        INTEGER NOT NULL,
                work_center         TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'PENDING',
                qty_good            INTEGER NOT NULL DEFAULT 0,
                qty_bad             INTEGER NOT NULL DEFAULT 0,
                started_at          TEXT,
                completed_at        TEXT,
                PRIMARY KEY (production_order_no, operation_no)
            );

            CREATE TABLE IF NOT EXISTS downtime_events (
                event_id     TEXT PRIMARY KEY,
                work_center  TEXT NOT NULL,
                reason       TEXT NOT NULL,
                start_time   TEXT NOT NULL,
                end_time     TEXT,
                duration_min REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS quality_records (
                record_no          TEXT PRIMARY KEY,
                production_order_no TEXT NOT NULL,
                operation_no       INTEGER NOT NULL,
                sample_qty         INTEGER NOT NULL,
                defect_qty         INTEGER NOT NULL,
                disposition        TEXT NOT NULL,
                inspector          TEXT NOT NULL DEFAULT '',
                inspected_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS material_issues (
                issue_no           TEXT PRIMARY KEY,
                production_order_no TEXT NOT NULL,
                item_sku           TEXT NOT NULL,
                quantity           REAL NOT NULL,
                issued_at          TEXT NOT NULL
            );

            -- ============================ APS ============================
            CREATE TABLE IF NOT EXISTS changeover_matrix (
                from_sku       TEXT NOT NULL,
                to_sku         TEXT NOT NULL,
                setup_time_min REAL NOT NULL,
                PRIMARY KEY (from_sku, to_sku)
            );

            CREATE TABLE IF NOT EXISTS schedule_plans (
                plan_id      TEXT PRIMARY KEY,
                plan_date    TEXT NOT NULL,
                horizon_days INTEGER NOT NULL DEFAULT 1,
                status       TEXT NOT NULL DEFAULT 'DRAFT',
                objective    TEXT NOT NULL DEFAULT 'on-time delivery',
                summary      TEXT NOT NULL DEFAULT '{}',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schedule_operations (
                plan_id              TEXT NOT NULL,
                seq                  INTEGER NOT NULL,
                production_order_no  TEXT NOT NULL,
                work_center          TEXT NOT NULL,
                operation_no         INTEGER NOT NULL,
                start_time           TEXT NOT NULL,
                end_time             TEXT NOT NULL,
                setup_min            REAL NOT NULL DEFAULT 0,
                run_min              REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (plan_id, seq)
            );

            -- ======================= Integration =========================
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                key        TEXT PRIMARY KEY,
                operation  TEXT NOT NULL,
                result     TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outbox_events (
                event_id       TEXT PRIMARY KEY,
                event_type     TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id   TEXT NOT NULL,
                payload        TEXT NOT NULL DEFAULT '{}',
                status         TEXT NOT NULL DEFAULT 'PENDING',
                attempts       INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT NOT NULL,
                published_at   TEXT,
                acked_at       TEXT,
                last_error     TEXT
            );

            CREATE TABLE IF NOT EXISTS inbox_events (
                event_id    TEXT PRIMARY KEY,
                outbox_id   TEXT,
                event_type  TEXT NOT NULL,
                payload     TEXT NOT NULL DEFAULT '{}',
                status      TEXT NOT NULL DEFAULT 'RECEIVED',
                received_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS erp_mes_mapping (
                erp_id        TEXT NOT NULL,
                mes_id        TEXT NOT NULL,
                target_type   TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'PENDING',
                created_at    TEXT NOT NULL,
                last_synced_at TEXT,
                PRIMARY KEY (erp_id, mes_id)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                audit_id       TEXT PRIMARY KEY,
                ts             TEXT NOT NULL,
                operator       TEXT NOT NULL DEFAULT 'system',
                operation      TEXT NOT NULL,
                resource       TEXT NOT NULL,
                resource_id    TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                before         TEXT,
                after          TEXT,
                success        INTEGER NOT NULL DEFAULT 1,
                error          TEXT
            );

            CREATE TABLE IF NOT EXISTS reconciliation_runs (
                run_id       TEXT PRIMARY KEY,
                ts           TEXT NOT NULL,
                matched      INTEGER NOT NULL DEFAULT 0,
                drift        INTEGER NOT NULL DEFAULT 0,
                missing_mes  INTEGER NOT NULL DEFAULT 0,
                missing_erp  INTEGER NOT NULL DEFAULT 0,
                detail       TEXT NOT NULL DEFAULT '[]'
            );
            """
        )

    def _seed_if_empty(self) -> None:
        cur = self.conn.execute("SELECT COUNT(*) AS n FROM work_orders")
        if cur.fetchone()["n"] > 0:
            return
        with self.transaction():
            self._insert_many("items", SEED_DATA["items"])
            self._insert_many("bom_headers", SEED_DATA["bom_headers"])
            self._insert_many("bom_lines", SEED_DATA["bom_lines"])
            self._insert_many("inventory_lots", SEED_DATA["inventory_lots"])
            self._insert_many("customers", SEED_DATA["customers"])
            self._insert_many("sales_orders", SEED_DATA["sales_orders"])
            self._insert_many("work_orders", SEED_DATA["work_orders"])
            self._insert_many("work_centers", SEED_DATA["work_centers"])
            self._insert_many("shift_entries", SEED_DATA["shift_entries"])
            self._insert_many("routing_operations", SEED_DATA["routing_operations"])
            self._insert_many("changeover_matrix", SEED_DATA["changeover_matrix"])
            self._insert_many("outbox_events", SEED_DATA["outbox_events"])

    # ------------------------------------------------------------- helpers
    def _insert_many(self, table: str, rows: Iterable[dict[str, Any]]) -> None:
        rows = list(rows)
        if not rows:
            return
        cols = list(rows[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        self.conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])

    @staticmethod
    def _dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, default=str)

    @staticmethod
    def _loads(text: str | None) -> Any:
        return json.loads(text) if text else None

    def last_insert_count(self) -> int:
        return self.conn.total_changes

    # ================================================================ ERP ==
    # customers
    def list_customers(self) -> list[Customer]:
        rows = self.conn.execute("SELECT * FROM customers ORDER BY customer_id")
        return [Customer(**dict(r)) for r in rows]

    def get_customer(self, customer_id: str) -> Optional[Customer]:
        r = self.conn.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
        return Customer(**dict(r)) if r else None

    # items
    def list_items(self, item_type: Optional[str] = None) -> list[Item]:
        if item_type:
            rows = self.conn.execute("SELECT * FROM items WHERE item_type = ? ORDER BY sku", (item_type,))
        else:
            rows = self.conn.execute("SELECT * FROM items ORDER BY sku")
        return [Item(**dict(r)) for r in rows]

    def get_item(self, sku: str) -> Optional[Item]:
        r = self.conn.execute("SELECT * FROM items WHERE sku = ?", (sku,)).fetchone()
        return Item(**dict(r)) if r else None

    def upsert_item(self, item: Item) -> None:
        self._insert_many("items", [item.__dict__])

    def update_item(self, sku: str, **changes: Any) -> None:
        self._update("items", "sku", sku, changes)

    # BOM
    def get_bom_header(self, bom_code: str) -> Optional[BomHeader]:
        r = self.conn.execute("SELECT * FROM bom_headers WHERE bom_code = ?", (bom_code,)).fetchone()
        return BomHeader(**dict(r)) if r else None

    def get_bom_lines(self, bom_code: str) -> list[BomLine]:
        rows = self.conn.execute("SELECT * FROM bom_lines WHERE bom_code = ? ORDER BY seq", (bom_code,))
        return [BomLine(**dict(r)) for r in rows]

    def list_bom_lines(self, product_sku: str | None = None) -> list[BomLine]:
        if product_sku:
            rows = self.conn.execute("SELECT * FROM bom_lines WHERE product_sku = ? ORDER BY bom_code, seq", (product_sku,))
        else:
            rows = self.conn.execute("SELECT * FROM bom_lines ORDER BY bom_code, seq")
        return [BomLine(**dict(r)) for r in rows]

    def upsert_bom_line(self, line: BomLine) -> None:
        self._insert_many("bom_lines", [line.__dict__])

    # inventory
    def list_inventory(self, sku: str | None = None) -> list[InventoryLot]:
        if sku:
            rows = self.conn.execute("SELECT * FROM inventory_lots WHERE sku = ? ORDER BY lot_no", (sku,))
        else:
            rows = self.conn.execute("SELECT * FROM inventory_lots ORDER BY sku, lot_no")
        return [InventoryLot(**dict(r)) for r in rows]

    def get_inventory_total(self, sku: str) -> float:
        r = self.conn.execute(
            "SELECT COALESCE(SUM(on_hand),0) AS t FROM inventory_lots WHERE sku = ?", (sku,)
        ).fetchone()
        return float(r["t"])

    def get_inventory_available(self, sku: str) -> float:
        r = self.conn.execute(
            "SELECT COALESCE(SUM(on_hand - reserved),0) AS t FROM inventory_lots WHERE sku = ?", (sku,)
        ).fetchone()
        return float(r["t"])

    def adjust_inventory(self, sku: str, lot_no: str, delta_on_hand: float, delta_reserved: float = 0.0) -> None:
        self.conn.execute(
            """UPDATE inventory_lots SET on_hand = on_hand + ?, reserved = reserved + ?
               WHERE sku = ? AND lot_no = ?""",
            (delta_on_hand, delta_reserved, sku, lot_no),
        )

    def upsert_inventory(self, sku: str, lot_no: str, warehouse: str,
                         delta_on_hand: float, delta_reserved: float = 0.0,
                         delta_in_transit: float = 0.0) -> None:
        """Add a lot if missing, then apply a delta (used by PO receipts)."""
        self.conn.execute(
            """INSERT INTO inventory_lots (sku, lot_no, warehouse, on_hand, reserved, in_transit)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(sku, lot_no) DO UPDATE SET
                 on_hand = on_hand + excluded.on_hand,
                 reserved = reserved + excluded.reserved,
                 in_transit = in_transit + excluded.in_transit""",
            (sku, lot_no, warehouse, delta_on_hand, delta_reserved, delta_in_transit),
        )

    def reserve_inventory(self, sku: str, qty: float) -> None:
        self.conn.execute(
            "UPDATE inventory_lots SET reserved = reserved + ? WHERE sku = ?", (qty, sku)
        )

    def reserve_inventory_by_sku(self, sku: str, qty: float) -> None:
        """Reserve across lots, consuming the requested quantity."""
        remaining = qty
        rows = self.conn.execute(
            "SELECT lot_no, on_hand, reserved FROM inventory_lots WHERE sku = ? ORDER BY lot_no", (sku,)
        ).fetchall()
        for r in rows:
            if remaining <= 0:
                break
            avail = r["on_hand"] - r["reserved"]
            take = min(avail, remaining)
            if take > 0:
                self.conn.execute(
                    "UPDATE inventory_lots SET reserved = reserved + ? WHERE sku = ? AND lot_no = ?",
                    (take, sku, r["lot_no"]),
                )
                remaining -= take
        if remaining > 0:
            raise IntegrityError(f"insufficient stock to reserve {sku} (short {remaining})")

    # sales orders
    def list_sales_orders(self, status: str | None = None) -> list[SalesOrder]:
        if status:
            rows = self.conn.execute("SELECT * FROM sales_orders WHERE status = ? ORDER BY due_date", (status,))
        else:
            rows = self.conn.execute("SELECT * FROM sales_orders ORDER BY due_date")
        return [self._row_to_sales_order(r) for r in rows]

    def get_sales_order(self, order_no: str) -> Optional[SalesOrder]:
        r = self.conn.execute("SELECT * FROM sales_orders WHERE order_no = ?", (order_no,)).fetchone()
        return self._row_to_sales_order(r) if r else None

    def insert_sales_order(self, order: SalesOrder) -> None:
        self._insert_many("sales_orders", [{
            "order_no": order.order_no,
            "customer_id": order.customer_id,
            "order_date": order.order_date,
            "due_date": order.due_date,
            "priority": order.priority,
            "status": order.status,
            "lines": self._dumps([ln.__dict__ for ln in order.lines]),
        }])

    @classmethod
    def _row_to_sales_order(cls, r: sqlite3.Row) -> SalesOrder:
        return SalesOrder(
            order_no=r["order_no"], customer_id=r["customer_id"], order_date=r["order_date"],
            due_date=r["due_date"], priority=r["priority"], status=r["status"],
            lines=[SalesOrderLine(**ln) for ln in (cls._loads(r["lines"]) or [])],
        )

    # purchase orders
    def list_purchase_orders(self, status: str | None = None) -> list[PurchaseOrder]:
        if status:
            rows = self.conn.execute("SELECT * FROM purchase_orders WHERE status = ?", (status,))
        else:
            rows = self.conn.execute("SELECT * FROM purchase_orders")
        return [self._row_to_purchase_order(r) for r in rows]

    def get_purchase_order(self, po_no: str) -> Optional[PurchaseOrder]:
        r = self.conn.execute("SELECT * FROM purchase_orders WHERE po_no = ?", (po_no,)).fetchone()
        return self._row_to_purchase_order(r) if r else None

    def insert_purchase_order(self, order: PurchaseOrder) -> None:
        self._insert_many("purchase_orders", [{
            "po_no": order.po_no, "supplier": order.supplier, "order_date": order.order_date,
            "due_date": order.due_date, "status": order.status,
            "lines": self._dumps([ln.__dict__ for ln in order.lines]),
        }])

    def update_purchase_order(self, po_no: str, **changes: Any) -> None:
        self._update("purchase_orders", "po_no", po_no, changes)

    @classmethod
    def _row_to_purchase_order(cls, r: sqlite3.Row) -> PurchaseOrder:
        return PurchaseOrder(
            po_no=r["po_no"], supplier=r["supplier"], order_date=r["order_date"],
            due_date=r["due_date"], status=r["status"],
            lines=[PurchaseOrderLine(**ln) for ln in (cls._loads(r["lines"]) or [])],
        )

    # work orders
    def list_work_orders(
        self, status: str | None = None, due_date_from: str | None = None, due_date_to: str | None = None
    ) -> list[WorkOrder]:
        sql = "SELECT * FROM work_orders WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if due_date_from:
            sql += " AND due_date >= ?"
            args.append(due_date_from)
        if due_date_to:
            sql += " AND due_date <= ?"
            args.append(due_date_to)
        sql += " ORDER BY priority ASC, due_date ASC"
        return [self._row_to_work_order(r) for r in self.conn.execute(sql, args)]

    def get_work_order(self, order_no: str) -> Optional[WorkOrder]:
        r = self.conn.execute("SELECT * FROM work_orders WHERE order_no = ?", (order_no,)).fetchone()
        return self._row_to_work_order(r) if r else None

    def insert_work_order(self, order: WorkOrder) -> None:
        self._insert_many("work_orders", [order.__dict__])

    def update_work_order(self, order_no: str, **changes: Any) -> None:
        self._update("work_orders", "order_no", order_no, changes)

    @staticmethod
    def _row_to_work_order(r: sqlite3.Row) -> WorkOrder:
        return WorkOrder(**dict(r))

    # MRP
    def insert_proposal(self, proposal: MrpProposal) -> None:
        self._insert_many("mrp_proposals", [proposal.__dict__])

    def list_proposals(self, status: str | None = None) -> list[MrpProposal]:
        if status:
            rows = self.conn.execute("SELECT * FROM mrp_proposals WHERE status = ?", (status,))
        else:
            rows = self.conn.execute("SELECT * FROM mrp_proposals")
        return [MrpProposal(**dict(r)) for r in rows]

    def clear_proposals(self) -> None:
        self.conn.execute("DELETE FROM mrp_proposals")

    # ================================================================ MES ==
    def list_work_centers(self, status: str | None = None) -> list[WorkCenter]:
        if status:
            rows = self.conn.execute("SELECT * FROM work_centers WHERE status = ? ORDER BY code", (status,))
        else:
            rows = self.conn.execute("SELECT * FROM work_centers ORDER BY code")
        return [WorkCenter(**dict(r)) for r in rows]

    def get_work_center(self, code: str) -> Optional[WorkCenter]:
        r = self.conn.execute("SELECT * FROM work_centers WHERE code = ?", (code,)).fetchone()
        return WorkCenter(**dict(r)) if r else None

    def update_work_center(self, code: str, **changes: Any) -> None:
        self._update("work_centers", "code", code, changes)

    def list_shifts(self, work_center: str | None = None, shift_date: str | None = None) -> list[ShiftEntry]:
        sql = "SELECT * FROM shift_entries WHERE 1=1"
        args: list[Any] = []
        if work_center:
            sql += " AND work_center = ?"
            args.append(work_center)
        if shift_date:
            sql += " AND shift_date = ?"
            args.append(shift_date)
        sql += " ORDER BY shift_date, shift_no"
        return [ShiftEntry(**dict(r)) for r in self.conn.execute(sql, args)]

    # routings
    def list_routing_operations(self, routing_code: str | None = None, product_sku: str | None = None) -> list[RoutingOperation]:
        sql = "SELECT * FROM routing_operations WHERE 1=1"
        args: list[Any] = []
        if routing_code:
            sql += " AND routing_code = ?"
            args.append(routing_code)
        if product_sku:
            sql += " AND product_sku = ?"
            args.append(product_sku)
        sql += " ORDER BY routing_code, operation_no"
        return [RoutingOperation(**dict(r)) for r in self.conn.execute(sql, args)]

    def get_routing_operation(self, routing_code: str, operation_no: int) -> Optional[RoutingOperation]:
        r = self.conn.execute(
            "SELECT * FROM routing_operations WHERE routing_code = ? AND operation_no = ?",
            (routing_code, operation_no),
        ).fetchone()
        return RoutingOperation(**dict(r)) if r else None

    # production orders
    def list_production_orders(self, status: str | None = None) -> list[ProductionOrder]:
        if status:
            rows = self.conn.execute("SELECT * FROM production_orders WHERE status = ? ORDER BY due_date", (status,))
        else:
            rows = self.conn.execute("SELECT * FROM production_orders ORDER BY due_date")
        return [ProductionOrder(**dict(r)) for r in rows]

    def get_production_order(self, production_order_no: str) -> Optional[ProductionOrder]:
        r = self.conn.execute(
            "SELECT * FROM production_orders WHERE production_order_no = ?", (production_order_no,)
        ).fetchone()
        return ProductionOrder(**dict(r)) if r else None

    def get_production_order_by_erp(self, erp_work_order_no: str) -> Optional[ProductionOrder]:
        r = self.conn.execute(
            "SELECT * FROM production_orders WHERE erp_work_order_no = ?", (erp_work_order_no,)
        ).fetchone()
        return ProductionOrder(**dict(r)) if r else None

    def insert_production_order(self, order: ProductionOrder) -> None:
        self._insert_many("production_orders", [order.__dict__])

    def update_production_order(self, production_order_no: str, **changes: Any) -> None:
        self._update("production_orders", "production_order_no", production_order_no, changes)

    # operation progress
    def insert_operation_progress(self, progress: OperationProgress) -> None:
        self._insert_many("operation_progress", [progress.__dict__])

    def list_operation_progress(self, production_order_no: str | None = None) -> list[OperationProgress]:
        if production_order_no:
            rows = self.conn.execute(
                "SELECT * FROM operation_progress WHERE production_order_no = ? ORDER BY operation_no",
                (production_order_no,),
            )
        else:
            rows = self.conn.execute("SELECT * FROM operation_progress ORDER BY production_order_no, operation_no")
        return [OperationProgress(**dict(r)) for r in rows]

    def update_operation_progress(self, production_order_no: str, operation_no: int, **changes: Any) -> None:
        sql = "UPDATE operation_progress SET " + ", ".join(f"{k} = ?" for k in changes)
        self.conn.execute(
            f"{sql} WHERE production_order_no = ? AND operation_no = ?",
            (*changes.values(), production_order_no, operation_no),
        )

    # downtime
    def insert_downtime(self, event: DowntimeEvent) -> None:
        self._insert_many("downtime_events", [event.__dict__])

    def list_downtime(self, work_center: str | None = None) -> list[DowntimeEvent]:
        if work_center:
            rows = self.conn.execute(
                "SELECT * FROM downtime_events WHERE work_center = ? ORDER BY start_time DESC", (work_center,)
            )
        else:
            rows = self.conn.execute("SELECT * FROM downtime_events ORDER BY start_time DESC")
        return [DowntimeEvent(**dict(r)) for r in rows]

    def update_downtime(self, event_id: str, **changes: Any) -> None:
        self._update("downtime_events", "event_id", event_id, changes)

    # quality
    def insert_quality_record(self, record: QualityRecord) -> None:
        self._insert_many("quality_records", [record.__dict__])

    def list_quality_records(self, production_order_no: str | None = None) -> list[QualityRecord]:
        if production_order_no:
            rows = self.conn.execute(
                "SELECT * FROM quality_records WHERE production_order_no = ? ORDER BY inspected_at", (production_order_no,)
            )
        else:
            rows = self.conn.execute("SELECT * FROM quality_records ORDER BY inspected_at")
        return [QualityRecord(**dict(r)) for r in rows]

    # material issues
    def insert_material_issue(self, issue: MaterialIssue) -> None:
        self._insert_many("material_issues", [issue.__dict__])

    def list_material_issues(self, production_order_no: str | None = None) -> list[MaterialIssue]:
        if production_order_no:
            rows = self.conn.execute(
                "SELECT * FROM material_issues WHERE production_order_no = ? ORDER BY issued_at", (production_order_no,)
            )
        else:
            rows = self.conn.execute("SELECT * FROM material_issues ORDER BY issued_at")
        return [MaterialIssue(**dict(r)) for r in rows]

    # ================================================================ APS ==
    def get_changeover(self, from_sku: str, to_sku: str) -> float:
        r = self.conn.execute(
            "SELECT setup_time_min FROM changeover_matrix WHERE from_sku = ? AND to_sku = ?",
            (from_sku, to_sku),
        ).fetchone()
        return float(r["setup_time_min"]) if r else 0.0

    def save_schedule_plan(self, plan: SchedulePlan) -> None:
        self._insert_many("schedule_plans", [{
            "plan_id": plan.plan_id, "plan_date": plan.plan_date, "horizon_days": plan.horizon_days,
            "status": plan.status, "objective": plan.objective,
            "summary": self._dumps(plan.summary), "created_at": plan.created_at,
        }])

    def get_schedule_plan(self, plan_id: str) -> Optional[SchedulePlan]:
        r = self.conn.execute("SELECT * FROM schedule_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if not r:
            return None
        return SchedulePlan(
            plan_id=r["plan_id"], plan_date=r["plan_date"], horizon_days=r["horizon_days"],
            status=r["status"], objective=r["objective"], summary=self._loads(r["summary"]),
            created_at=r["created_at"],
        )

    def list_schedule_plans(self, status: str | None = None) -> list[SchedulePlan]:
        if status:
            rows = self.conn.execute("SELECT * FROM schedule_plans WHERE status = ? ORDER BY created_at DESC", (status,))
        else:
            rows = self.conn.execute("SELECT * FROM schedule_plans ORDER BY created_at DESC")
        return [
            SchedulePlan(
                plan_id=r["plan_id"], plan_date=r["plan_date"], horizon_days=r["horizon_days"],
                status=r["status"], objective=r["objective"], summary=self._loads(r["summary"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def update_schedule_plan(self, plan_id: str, **changes: Any) -> None:
        self._update("schedule_plans", "plan_id", plan_id, changes)

    def save_schedule_operations(self, ops: list[ScheduleOperation]) -> None:
        self._insert_many("schedule_operations", [o.__dict__ for o in ops])

    def list_schedule_operations(self, plan_id: str) -> list[ScheduleOperation]:
        rows = self.conn.execute(
            "SELECT * FROM schedule_operations WHERE plan_id = ? ORDER BY seq", (plan_id,)
        )
        return [ScheduleOperation(**dict(r)) for r in rows]

    # ========================================================= Integration ==
    # idempotency
    def has_idempotency_key(self, key: str) -> Optional[str]:
        r = self.conn.execute("SELECT result FROM idempotency_keys WHERE key = ?", (key,)).fetchone()
        return r["result"] if r else None

    def record_idempotency(self, key: str, operation: str, result: Any) -> None:
        self._insert_many("idempotency_keys", [{
            "key": key, "operation": operation, "result": self._dumps(result), "created_at": _now(),
        }])

    # outbox / inbox
    def insert_outbox(self, event: OutboxEvent) -> None:
        self._insert_many("outbox_events", [{
            "event_id": event.event_id, "event_type": event.event_type, "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id, "payload": self._dumps(event.payload), "status": event.status,
            "attempts": event.attempts, "created_at": event.created_at, "published_at": event.published_at,
            "acked_at": event.acked_at, "last_error": event.last_error,
        }])

    def list_outbox(self, status: str | None = None) -> list[OutboxEvent]:
        if status:
            rows = self.conn.execute("SELECT * FROM outbox_events WHERE status = ? ORDER BY created_at", (status,))
        else:
            rows = self.conn.execute("SELECT * FROM outbox_events ORDER BY created_at")
        return [self._row_to_outbox(r) for r in rows]

    def update_outbox(self, event_id: str, **changes: Any) -> None:
        if "payload" in changes:
            changes["payload"] = self._dumps(changes["payload"])
        self._update("outbox_events", "event_id", event_id, changes)

    def insert_inbox(self, event: OutboxEvent) -> None:
        self._insert_many("inbox_events", [{
            "event_id": new_id("IN-"), "outbox_id": event.event_id, "event_type": event.event_type,
            "payload": self._dumps(event.payload), "status": "RECEIVED", "received_at": _now(),
        }])

    def list_inbox(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM inbox_events ORDER BY received_at")
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = self._loads(d["payload"])
            out.append(d)
        return out

    @staticmethod
    def _row_to_outbox(r: sqlite3.Row) -> OutboxEvent:
        d = dict(r)
        d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        return OutboxEvent(**d)

    # erp-mes mapping
    def upsert_mapping(self, mapping: ErpMesMapping) -> None:
        self._insert_many("erp_mes_mapping", [mapping.__dict__])

    def get_mapping_by_erp(self, erp_id: str) -> Optional[ErpMesMapping]:
        r = self.conn.execute(
            "SELECT * FROM erp_mes_mapping WHERE erp_id = ? ORDER BY created_at DESC LIMIT 1", (erp_id,)
        ).fetchone()
        return ErpMesMapping(**dict(r)) if r else None

    def get_mapping_by_mes(self, mes_id: str) -> Optional[ErpMesMapping]:
        r = self.conn.execute(
            "SELECT * FROM erp_mes_mapping WHERE mes_id = ? ORDER BY created_at DESC LIMIT 1", (mes_id,)
        ).fetchone()
        return ErpMesMapping(**dict(r)) if r else None

    def list_mappings(self) -> list[ErpMesMapping]:
        rows = self.conn.execute("SELECT * FROM erp_mes_mapping ORDER BY created_at")
        return [ErpMesMapping(**dict(r)) for r in rows]

    # audit
    def insert_audit(self, entry: AuditEntry) -> None:
        self._insert_many("audit_log", [{
            "audit_id": entry.audit_id, "ts": entry.ts, "operator": entry.operator,
            "operation": entry.operation, "resource": entry.resource, "resource_id": entry.resource_id,
            "correlation_id": entry.correlation_id, "before": self._dumps(entry.before),
            "after": self._dumps(entry.after), "success": int(entry.success), "error": entry.error,
        }])

    def list_audit(self, resource: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if resource:
            rows = self.conn.execute(
                "SELECT * FROM audit_log WHERE resource = ? ORDER BY ts DESC LIMIT ?", (resource, limit)
            )
        else:
            rows = self.conn.execute("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["before"] = self._loads(d["before"])
            d["after"] = self._loads(d["after"])
            out.append(d)
        return out

    # reconciliation
    def insert_reconciliation(self, run: dict[str, Any]) -> None:
        run["detail"] = self._dumps(run.get("detail", []))
        self._insert_many("reconciliation_runs", [run])

    def list_reconciliations(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM reconciliation_runs ORDER BY ts DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = self._loads(d["detail"])
            out.append(d)
        return out

    # ------------------------------------------------------------- generic
    def _update(self, table: str, key_col: str, key_val: Any, changes: dict[str, Any]) -> None:
        if not changes:
            return
        fields = ", ".join(f"{k} = ?" for k in changes)
        self.conn.execute(
            f"UPDATE {table} SET {fields} WHERE {key_col} = ?",
            (*changes.values(), key_val),
        )

    def reset(self) -> None:
        """Drop and recreate all tables, restoring the seeded state."""
        tables = [
            "audit_log", "reconciliation_runs", "idempotency_keys", "outbox_events", "inbox_events",
            "erp_mes_mapping", "schedule_operations", "schedule_plans", "changeover_matrix",
            "material_issues", "quality_records", "downtime_events", "operation_progress",
            "production_orders", "routing_operations", "shift_entries", "work_centers",
            "mrp_proposals", "work_orders", "purchase_orders", "sales_orders", "inventory_lots",
            "bom_lines", "bom_headers", "items", "customers",
        ]
        self.conn.execute("PRAGMA foreign_keys = OFF")
        for t in tables:
            self.conn.execute(f"DROP TABLE IF EXISTS {t}")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.commit()
        self._init_schema()
        self._seed_if_empty()
        self.conn.commit()