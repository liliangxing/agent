"""SQLite persistence layer.

The store keeps a single source of truth for both the simulated ERP
(work orders, materials, BOM) and the simulated MES (lines, tasks,
schedules). It uses the stdlib ``sqlite3`` driver so the server has no
database dependency beyond Python itself.

The database is auto-created and seeded when empty, which keeps the
server runnable out of the box for demos while still persisting state
between restarts when pointed at a file path.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional

from .models import (
    BomLine,
    Material,
    ProductionLine,
    ProductionTask,
    Schedule,
    ScheduleItem,
    WorkOrder,
)
from .seed import SEED_DATA


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    """Thin repository around a SQLite database."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.environ.get("MES_MCP_DB", ":memory:")
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self._seed_if_empty()

    # -- schema -----------------------------------------------------------------
    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS work_orders (
                order_no     TEXT PRIMARY KEY,
                product_sku  TEXT NOT NULL,
                product_name TEXT NOT NULL,
                quantity     INTEGER NOT NULL,
                priority     INTEGER NOT NULL DEFAULT 1,
                due_date     TEXT NOT NULL,
                customer     TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'DRAFT',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS materials (
                sku          TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                unit         TEXT NOT NULL,
                stock_qty    REAL NOT NULL DEFAULT 0,
                reserved_qty REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS bom_lines (
                product_sku   TEXT NOT NULL,
                material_sku  TEXT NOT NULL,
                qty_per_unit  REAL NOT NULL,
                PRIMARY KEY (product_sku, material_sku)
            );

            CREATE TABLE IF NOT EXISTS lines (
                line_id           TEXT PRIMARY KEY,
                name              TEXT NOT NULL,
                status            TEXT NOT NULL DEFAULT 'IDLE',
                capacity_per_hour REAL NOT NULL DEFAULT 0,
                shift_start       TEXT NOT NULL DEFAULT '08:00',
                shift_hours       REAL NOT NULL DEFAULT 12
            );

            CREATE TABLE IF NOT EXISTS schedules (
                plan_id    TEXT PRIMARY KEY,
                plan_date  TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'DRAFT',
                created_at TEXT NOT NULL,
                summary    TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS schedule_items (
                plan_id       TEXT NOT NULL,
                seq           INTEGER NOT NULL,
                order_no      TEXT NOT NULL,
                line_id       TEXT NOT NULL,
                planned_start TEXT NOT NULL,
                planned_end   TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'PLANNED',
                PRIMARY KEY (plan_id, seq)
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id       TEXT PRIMARY KEY,
                order_no      TEXT NOT NULL,
                line_id       TEXT NOT NULL,
                plan_id       TEXT NOT NULL,
                quantity      INTEGER NOT NULL,
                planned_start TEXT NOT NULL,
                planned_end   TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'PLANNED',
                actual_qty    INTEGER NOT NULL DEFAULT 0,
                updated_at    TEXT NOT NULL
            );
            """
        )

    def _seed_if_empty(self) -> None:
        cur = self.conn.execute("SELECT COUNT(*) AS n FROM work_orders")
        if cur.fetchone()["n"] > 0:
            return
        self._insert_many(
            "materials",
            [m for m in SEED_DATA["materials"]],
        )
        self._insert_many("bom_lines", SEED_DATA["bom_lines"])
        self._insert_many("work_orders", SEED_DATA["work_orders"])
        self._insert_many("lines", SEED_DATA["lines"])
        self.conn.commit()

    # -- helpers ----------------------------------------------------------------
    def _insert_many(self, table: str, rows: Iterable[dict[str, Any]]) -> None:
        if not rows:
            return
        rows = list(rows)
        cols = list(rows[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        self.conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])

    def close(self) -> None:
        self.conn.close()

    # -- ERP: work orders --------------------------------------------------------
    def list_work_orders(
        self,
        status: Optional[str] = None,
        due_date_from: Optional[str] = None,
        due_date_to: Optional[str] = None,
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
        row = self.conn.execute(
            "SELECT * FROM work_orders WHERE order_no = ?", (order_no,)
        ).fetchone()
        return self._row_to_work_order(row) if row else None

    def get_work_orders(self, order_nos: list[str]) -> list[WorkOrder]:
        if not order_nos:
            return []
        placeholders = ", ".join("?" for _ in order_nos)
        rows = self.conn.execute(
            f"SELECT * FROM work_orders WHERE order_no IN ({placeholders}) ORDER BY priority ASC, due_date ASC",
            order_nos,
        )
        return [self._row_to_work_order(r) for r in rows]

    def set_order_status(self, order_no: str, status: str) -> None:
        self.conn.execute(
            "UPDATE work_orders SET status = ? WHERE order_no = ?", (status, order_no)
        )

    @staticmethod
    def _row_to_work_order(row: sqlite3.Row) -> WorkOrder:
        return WorkOrder(
            order_no=row["order_no"],
            product_sku=row["product_sku"],
            product_name=row["product_name"],
            quantity=row["quantity"],
            priority=row["priority"],
            due_date=row["due_date"],
            customer=row["customer"],
            status=row["status"],
            created_at=row["created_at"],
        )

    # -- ERP: materials / BOM ----------------------------------------------------
    def list_materials(self, sku: Optional[str] = None) -> list[Material]:
        if sku:
            rows = self.conn.execute("SELECT * FROM materials WHERE sku = ?", (sku,))
        else:
            rows = self.conn.execute("SELECT * FROM materials ORDER BY sku")
        return [self._row_to_material(r) for r in rows]

    def get_material(self, sku: str) -> Optional[Material]:
        row = self.conn.execute("SELECT * FROM materials WHERE sku = ?", (sku,)).fetchone()
        return self._row_to_material(row) if row else None

    def get_bom(self, product_sku: str) -> list[BomLine]:
        rows = self.conn.execute(
            "SELECT * FROM bom_lines WHERE product_sku = ? ORDER BY material_sku", (product_sku,)
        )
        return [
            BomLine(r["product_sku"], r["material_sku"], r["qty_per_unit"]) for r in rows
        ]

    def reserve_material(self, sku: str, qty: float) -> None:
        self.conn.execute(
            "UPDATE materials SET reserved_qty = reserved_qty + ? WHERE sku = ?", (qty, sku)
        )

    @staticmethod
    def _row_to_material(row: sqlite3.Row) -> Material:
        return Material(
            sku=row["sku"],
            name=row["name"],
            unit=row["unit"],
            stock_qty=row["stock_qty"],
            reserved_qty=row["reserved_qty"],
        )

    # -- MES: lines --------------------------------------------------------------
    def list_lines(self, line_id: Optional[str] = None) -> list[ProductionLine]:
        if line_id:
            rows = self.conn.execute("SELECT * FROM lines WHERE line_id = ?", (line_id,))
        else:
            rows = self.conn.execute("SELECT * FROM lines ORDER BY line_id")
        return [self._row_to_line(r) for r in rows]

    def get_line(self, line_id: str) -> Optional[ProductionLine]:
        row = self.conn.execute("SELECT * FROM lines WHERE line_id = ?", (line_id,)).fetchone()
        return self._row_to_line(row) if row else None

    def set_line_status(self, line_id: str, status: str) -> None:
        self.conn.execute("UPDATE lines SET status = ? WHERE line_id = ?", (status, line_id))

    @staticmethod
    def _row_to_line(row: sqlite3.Row) -> ProductionLine:
        return ProductionLine(
            line_id=row["line_id"],
            name=row["name"],
            status=row["status"],
            capacity_per_hour=row["capacity_per_hour"],
            shift_start=row["shift_start"],
            shift_hours=row["shift_hours"],
        )

    # -- MES: tasks --------------------------------------------------------------
    def list_tasks(
        self, status: Optional[str] = None, line_id: Optional[str] = None
    ) -> list[ProductionTask]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if line_id:
            sql += " AND line_id = ?"
            args.append(line_id)
        sql += " ORDER BY planned_start ASC"
        return [self._row_to_task(r) for r in self.conn.execute(sql, args)]

    def get_task(self, task_id: str) -> Optional[ProductionTask]:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def create_task(self, task: ProductionTask) -> None:
        self._insert_many("tasks", [task.__dict__])

    def update_task(self, task_id: str, **changes: Any) -> None:
        fields = ", ".join(f"{k} = ?" for k in changes)
        self.conn.execute(
            f"UPDATE tasks SET {fields}, updated_at = ? WHERE task_id = ?",
            (*changes.values(), _now(), task_id),
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> ProductionTask:
        return ProductionTask(
            task_id=row["task_id"],
            order_no=row["order_no"],
            line_id=row["line_id"],
            plan_id=row["plan_id"],
            quantity=row["quantity"],
            planned_start=row["planned_start"],
            planned_end=row["planned_end"],
            status=row["status"],
            actual_qty=row["actual_qty"],
            updated_at=row["updated_at"],
        )

    # -- schedules ---------------------------------------------------------------
    def save_schedule(self, schedule: Schedule) -> None:
        import json

        self._insert_many(
            "schedules",
            [{
                "plan_id": schedule.plan_id,
                "plan_date": schedule.plan_date,
                "status": schedule.status,
                "created_at": schedule.created_at,
                "summary": json.dumps(schedule.summary, ensure_ascii=False),
            }],
        )

    def get_schedule(self, plan_id: str) -> Optional[Schedule]:
        import json

        row = self.conn.execute(
            "SELECT * FROM schedules WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if not row:
            return None
        return Schedule(
            plan_id=row["plan_id"],
            plan_date=row["plan_date"],
            status=row["status"],
            created_at=row["created_at"],
            summary=json.loads(row["summary"]),
        )

    def list_schedules(self, status: Optional[str] = None) -> list[Schedule]:
        import json

        if status:
            rows = self.conn.execute(
                "SELECT * FROM schedules WHERE status = ? ORDER BY created_at DESC", (status,)
            )
        else:
            rows = self.conn.execute("SELECT * FROM schedules ORDER BY created_at DESC")
        return [
            Schedule(
                plan_id=r["plan_id"],
                plan_date=r["plan_date"],
                status=r["status"],
                created_at=r["created_at"],
                summary=json.loads(r["summary"]),
            )
            for r in rows
        ]

    def set_schedule_status(self, plan_id: str, status: str) -> None:
        self.conn.execute("UPDATE schedules SET status = ? WHERE plan_id = ?", (status, plan_id))

    def save_schedule_items(self, items: list[ScheduleItem]) -> None:
        self._insert_many("schedule_items", [i.__dict__ for i in items])

    def list_schedule_items(self, plan_id: str) -> list[ScheduleItem]:
        rows = self.conn.execute(
            "SELECT * FROM schedule_items WHERE plan_id = ? ORDER BY seq ASC", (plan_id,)
        )
        return [
            ScheduleItem(
                plan_id=r["plan_id"],
                seq=r["seq"],
                order_no=r["order_no"],
                line_id=r["line_id"],
                planned_start=r["planned_start"],
                planned_end=r["planned_end"],
                status=r["status"],
            )
            for r in rows
        ]

    def set_schedule_item_status(self, plan_id: str, seq: int, status: str) -> None:
        self.conn.execute(
            "UPDATE schedule_items SET status = ? WHERE plan_id = ? AND seq = ?",
            (status, plan_id, seq),
        )

    # -- transactions ------------------------------------------------------------
    def commit(self) -> None:
        self.conn.commit()

    def reset(self) -> None:
        """Drop and recreate all data, returning the store to its seeded state."""
        tables = ["tasks", "schedule_items", "schedules", "lines", "work_orders", "bom_lines", "materials"]
        self.conn.execute("PRAGMA foreign_keys = OFF")
        for t in tables:
            self.conn.execute(f"DROP TABLE IF EXISTS {t}")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.commit()
        self._init_schema()
        self._seed_if_empty()
        self.conn.commit()


def new_plan_id() -> str:
    return uuid.uuid4().hex[:12]


def new_task_id() -> str:
    return uuid.uuid4().hex[:16]