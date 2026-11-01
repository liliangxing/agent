"""Seed data simulating a precision parts factory for the demo.

The seed models a realistic "day in the life": a handful of released
ERP work orders with different priorities and due dates, a small shop
floor with a few production lines, materials with one deliberately short
stock item, and the BOM that ties products to materials. The shortage is
intentional so the scheduling engine can demonstrate its material check.
"""
from __future__ import annotations

from datetime import date, timedelta

TODAY = date.today()


def _d(days_from_today: int) -> str:
    return (TODAY + timedelta(days=days_from_today)).isoformat()


SEED_DATA: dict = {
    "materials": [
        {"sku": "M-304S", "name": "304 不锈钢棒料", "unit": "kg", "stock_qty": 1200.0, "reserved_qty": 0.0},
        {"sku": "M-AL7075", "name": "7075 铝合金棒料", "unit": "kg", "stock_qty": 800.0, "reserved_qty": 0.0},
        {"sku": "M-BRASS", "name": "黄铜棒料", "unit": "kg", "stock_qty": 200.0, "reserved_qty": 0.0},
        {"sku": "M-COOLANT", "name": "切削液", "unit": "L", "stock_qty": 60.0, "reserved_qty": 0.0},
        {"sku": "M-INSERT", "name": "硬质合金刀片", "unit": "pcs", "stock_qty": 60.0, "reserved_qty": 0.0},
    ],
    "bom_lines": [
        {"product_sku": "P-VALVE-BODY", "material_sku": "M-304S", "qty_per_unit": 2.5},
        {"product_sku": "P-VALVE-BODY", "material_sku": "M-INSERT", "qty_per_unit": 0.2},
        {"product_sku": "P-FLANGE", "material_sku": "M-AL7075", "qty_per_unit": 1.8},
        {"product_sku": "P-FLANGE", "material_sku": "M-COOLANT", "qty_per_unit": 0.5},
        {"product_sku": "P-SHAFT", "material_sku": "M-304S", "qty_per_unit": 3.0},
        {"product_sku": "P-SHAFT", "material_sku": "M-COOLANT", "qty_per_unit": 0.4},
        {"product_sku": "P-NOZZLE", "material_sku": "M-BRASS", "qty_per_unit": 0.9},
        {"product_sku": "P-NOZZLE", "material_sku": "M-INSERT", "qty_per_unit": 0.15},
    ],
    "work_orders": [
        {
            "order_no": "WO-2026-0001",
            "product_sku": "P-VALVE-BODY",
            "product_name": "气动阀体",
            "quantity": 120,
            "priority": 1,
            "due_date": _d(0),
            "customer": "江苏某精密零部件厂 / 客户 A",
            "status": "RELEASED",
            "created_at": _d(0) + "T07:30:00",
        },
        {
            "order_no": "WO-2026-0002",
            "product_sku": "P-FLANGE",
            "product_name": "法兰盘",
            "quantity": 200,
            "priority": 1,
            "due_date": _d(0),
            "customer": "江苏某精密零部件厂 / 客户 B",
            "status": "RELEASED",
            "created_at": _d(0) + "T08:00:00",
        },
        {
            "order_no": "WO-2026-0003",
            "product_sku": "P-SHAFT",
            "product_name": "传动轴",
            "quantity": 60,
            "priority": 2,
            "due_date": _d(1),
            "customer": "江苏某精密零部件厂 / 客户 C",
            "status": "RELEASED",
            "created_at": _d(0) + "T09:15:00",
        },
        {
            "order_no": "WO-2026-0004",
            "product_sku": "P-NOZZLE",
            "product_name": "精密喷油嘴",
            "quantity": 150,
            "priority": 1,
            "due_date": _d(0),
            "customer": "江苏某精密零部件厂 / 客户 D",
            "status": "RELEASED",
            "created_at": _d(0) + "T10:00:00",
        },
        {
            "order_no": "WO-2026-0005",
            "product_sku": "P-VALVE-BODY",
            "product_name": "气动阀体",
            "quantity": 80,
            "priority": 3,
            "due_date": _d(2),
            "customer": "江苏某精密零部件厂 / 客户 E",
            "status": "DRAFT",
            "created_at": _d(0) + "T11:00:00",
        },
    ],
    "lines": [
        {"line_id": "CNC-01", "name": "1 号数控加工中心", "status": "IDLE", "capacity_per_hour": 20.0, "shift_start": "08:00", "shift_hours": 12.0},
        {"line_id": "CNC-02", "name": "2 号数控加工中心", "status": "IDLE", "capacity_per_hour": 18.0, "shift_start": "08:00", "shift_hours": 12.0},
        {"line_id": "LATHE-01", "name": "1 号车削中心", "status": "IDLE", "capacity_per_hour": 15.0, "shift_start": "08:00", "shift_hours": 12.0},
        {"line_id": "MILL-01", "name": "1 号五轴铣床", "status": "MAINTENANCE", "capacity_per_hour": 12.0, "shift_start": "08:00", "shift_hours": 8.0},
    ],
    "process_routings": [
        {"routing_id": "RT-VALVE-001", "product_sku": "P-VALVE-BODY", "operation_no": 10, "operation_name": "下料", "work_center": "SAW-01", "standard_time_min": 2.0},
        {"routing_id": "RT-VALVE-002", "product_sku": "P-VALVE-BODY", "operation_no": 20, "operation_name": "CNC 粗加工", "work_center": "CNC-01", "standard_time_min": 14.0},
        {"routing_id": "RT-VALVE-003", "product_sku": "P-VALVE-BODY", "operation_no": 30, "operation_name": "精加工", "work_center": "CNC-01", "standard_time_min": 12.0},
        {"routing_id": "RT-FLANGE-001", "product_sku": "P-FLANGE", "operation_no": 10, "operation_name": "下料", "work_center": "SAW-01", "standard_time_min": 1.5},
        {"routing_id": "RT-FLANGE-002", "product_sku": "P-FLANGE", "operation_no": 20, "operation_name": "钻孔攻丝", "work_center": "CNC-02", "standard_time_min": 10.0},
        {"routing_id": "RT-SHAFT-001", "product_sku": "P-SHAFT", "operation_no": 10, "operation_name": "车削", "work_center": "LATHE-01", "standard_time_min": 18.0},
        {"routing_id": "RT-SHAFT-002", "product_sku": "P-SHAFT", "operation_no": 20, "operation_name": "磨削", "work_center": "GRIND-01", "standard_time_min": 8.0},
        {"routing_id": "RT-NOZZLE-001", "product_sku": "P-NOZZLE", "operation_no": 10, "operation_name": "车铣复合", "work_center": "LATHE-01", "standard_time_min": 9.0},
    ],
}