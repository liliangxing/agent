"""Seed data for the precision-parts manufacturing plant.

The plant manufactures pneumatic valve bodies, flanges, drive shafts and
precision fuel nozzles on CNC lathes, a machining center, a grinder, a die
caster, a heat-treatment furnace and an assembly line.

The seed is deliberately *realistic* rather than toy-like:

- multi-level BOM (finished good -> semi-finished spool -> raw bar stock)
- inventory lots with reservations
- process routings with setup / run times and inspection checkpoints
- a changeover matrix used by the finite-capacity scheduler
- a work order that is genuinely short on coolant, so the APS engine must
  leave it unscheduled (the case's central "齐套检查" story)

All dates are anchored to a deterministic ``TODAY`` so tests and demos are
reproducible.
"""
from __future__ import annotations

from datetime import date, timedelta

TODAY = "2026-11-02"


def _d(n: int) -> str:
    return (date.fromisoformat(TODAY) + timedelta(days=n)).isoformat()


SEED_DATA = {
    "items": [
        {"sku": "FG-1001", "name": "气动阀体", "item_type": "FINISHED", "unit": "PC",
         "lead_time_days": 3, "safety_stock": 20, "abc_class": "A",
         "default_bom_code": "BOM-FG1001", "default_routing_code": "RT-FG1001"},
        {"sku": "FG-1002", "name": "法兰盘", "item_type": "FINISHED", "unit": "PC",
         "lead_time_days": 2, "safety_stock": 30, "abc_class": "A",
         "default_bom_code": "BOM-FG1002", "default_routing_code": "RT-FG1002"},
        {"sku": "FG-1003", "name": "传动轴", "item_type": "FINISHED", "unit": "PC",
         "lead_time_days": 4, "safety_stock": 15, "abc_class": "B",
         "default_bom_code": "BOM-FG1003", "default_routing_code": "RT-FG1003"},
        {"sku": "FG-1004", "name": "精密喷油嘴", "item_type": "FINISHED", "unit": "PC",
         "lead_time_days": 2, "safety_stock": 25, "abc_class": "A",
         "default_bom_code": "BOM-FG1004", "default_routing_code": "RT-FG1004"},
        {"sku": "SF-1001", "name": "阀芯（半成品）", "item_type": "SEMI", "unit": "PC",
         "lead_time_days": 2, "safety_stock": 40, "abc_class": "B",
         "default_bom_code": "BOM-SF1001", "default_routing_code": "RT-SF1001"},
        {"sku": "RM-1001", "name": "铝合金棒材 AL-6061", "item_type": "RAW", "unit": "KG",
         "lead_time_days": 7, "safety_stock": 200, "abc_class": "A"},
        {"sku": "RM-1002", "name": "不锈钢棒材 SS-304", "item_type": "RAW", "unit": "KG",
         "lead_time_days": 9, "safety_stock": 150, "abc_class": "A"},
        {"sku": "RM-1003", "name": "铜棒 CU-3604", "item_type": "RAW", "unit": "KG",
         "lead_time_days": 5, "safety_stock": 40, "abc_class": "B"},
        {"sku": "RM-1004", "name": "切削液", "item_type": "CONSUMABLE", "unit": "L",
         "lead_time_days": 3, "safety_stock": 60, "abc_class": "B"},
        {"sku": "RM-1005", "name": "密封圈", "item_type": "RAW", "unit": "PC",
         "lead_time_days": 4, "safety_stock": 100, "abc_class": "C"},
    ],
    "bom_headers": [
        {"bom_code": "BOM-FG1001", "product_sku": "FG-1001", "version": "A", "description": "气动阀体总成"},
        {"bom_code": "BOM-FG1002", "product_sku": "FG-1002", "version": "A", "description": "法兰盘"},
        {"bom_code": "BOM-FG1003", "product_sku": "FG-1003", "version": "A", "description": "传动轴"},
        {"bom_code": "BOM-FG1004", "product_sku": "FG-1004", "version": "A", "description": "精密喷油嘴"},
        {"bom_code": "BOM-SF1001", "product_sku": "SF-1001", "version": "A", "description": "阀芯"},
    ],
    "bom_lines": [
        # 气动阀体：铝棒 + 切削液 + 密封圈 + 阀芯(半成品)
        {"bom_code": "BOM-FG1001", "product_sku": "FG-1001", "seq": 10, "component_sku": "RM-1001",
         "qty_per": 2.5, "scrap_rate": 0.02, "operation_no": 10, "is_phantom": 0},
        {"bom_code": "BOM-FG1001", "product_sku": "FG-1001", "seq": 20, "component_sku": "RM-1004",
         "qty_per": 0.3, "scrap_rate": 0.0, "operation_no": 20, "is_phantom": 0},
        {"bom_code": "BOM-FG1001", "product_sku": "FG-1001", "seq": 30, "component_sku": "RM-1005",
         "qty_per": 2.0, "scrap_rate": 0.01, "operation_no": 30, "is_phantom": 0},
        {"bom_code": "BOM-FG1001", "product_sku": "FG-1001", "seq": 40, "component_sku": "SF-1001",
         "qty_per": 1.0, "scrap_rate": 0.0, "operation_no": 30, "is_phantom": 0},
        # 法兰盘：铝棒 + 切削液
        {"bom_code": "BOM-FG1002", "product_sku": "FG-1002", "seq": 10, "component_sku": "RM-1001",
         "qty_per": 1.2, "scrap_rate": 0.03, "operation_no": 10, "is_phantom": 0},
        {"bom_code": "BOM-FG1002", "product_sku": "FG-1002", "seq": 20, "component_sku": "RM-1004",
         "qty_per": 0.35, "scrap_rate": 0.0, "operation_no": 10, "is_phantom": 0},
        # 传动轴：不锈钢棒 + 切削液
        {"bom_code": "BOM-FG1003", "product_sku": "FG-1003", "seq": 10, "component_sku": "RM-1002",
         "qty_per": 3.0, "scrap_rate": 0.04, "operation_no": 10, "is_phantom": 0},
        {"bom_code": "BOM-FG1003", "product_sku": "FG-1003", "seq": 20, "component_sku": "RM-1004",
         "qty_per": 0.3, "scrap_rate": 0.0, "operation_no": 10, "is_phantom": 0},
        # 喷油嘴：铜棒 + 切削液 + 密封圈
        {"bom_code": "BOM-FG1004", "product_sku": "FG-1004", "seq": 10, "component_sku": "RM-1003",
         "qty_per": 0.8, "scrap_rate": 0.05, "operation_no": 10, "is_phantom": 0},
        {"bom_code": "BOM-FG1004", "product_sku": "FG-1004", "seq": 20, "component_sku": "RM-1004",
         "qty_per": 0.3, "scrap_rate": 0.0, "operation_no": 10, "is_phantom": 0},
        {"bom_code": "BOM-FG1004", "product_sku": "FG-1004", "seq": 30, "component_sku": "RM-1005",
         "qty_per": 1.0, "scrap_rate": 0.01, "operation_no": 30, "is_phantom": 0},
        # 阀芯（半成品）：不锈钢棒
        {"bom_code": "BOM-SF1001", "product_sku": "SF-1001", "seq": 10, "component_sku": "RM-1002",
         "qty_per": 0.6, "scrap_rate": 0.03, "operation_no": 10, "is_phantom": 0},
    ],
    "inventory_lots": [
        {"sku": "RM-1001", "lot_no": "AL-260001", "warehouse": "WH-RAW", "on_hand": 800, "reserved": 0, "in_transit": 0},
        {"sku": "RM-1002", "lot_no": "SS-260002", "warehouse": "WH-RAW", "on_hand": 500, "reserved": 0, "in_transit": 0},
        {"sku": "RM-1003", "lot_no": "CU-260003", "warehouse": "WH-RAW", "on_hand": 160, "reserved": 0, "in_transit": 0},
        {"sku": "RM-1004", "lot_no": "CL-260004", "warehouse": "WH-CON", "on_hand": 60, "reserved": 0, "in_transit": 0},
        {"sku": "RM-1005", "lot_no": "SR-260005", "warehouse": "WH-RAW", "on_hand": 400, "reserved": 0, "in_transit": 0},
        {"sku": "SF-1001", "lot_no": "SP-260006", "warehouse": "WH-WIP", "on_hand": 100, "reserved": 0, "in_transit": 0},
    ],
    "customers": [
        {"customer_id": "C001", "name": "常州某气动自动化公司", "region": "华东", "credit_rating": "AAA", "payment_terms": "NET30"},
        {"customer_id": "C002", "name": "无锡某泵业股份", "region": "华东", "credit_rating": "AA", "payment_terms": "NET45"},
        {"customer_id": "C003", "name": "苏州某汽车零部件厂", "region": "华东", "credit_rating": "A", "payment_terms": "NET30"},
        {"customer_id": "C004", "name": "昆山某精密机械", "region": "华东", "credit_rating": "AA", "payment_terms": "NET30"},
    ],
    "sales_orders": [
        {"order_no": "SO-2026-1001", "customer_id": "C001", "order_date": _d(-2), "due_date": _d(0),
         "priority": "HIGH", "status": "CONFIRMED", "lines": '[{"seq": 10, "item_sku": "FG-1001", "quantity": 120, "unit_price": 85.0, "status": "OPEN"}]'},
        {"order_no": "SO-2026-1002", "customer_id": "C003", "order_date": _d(-3), "due_date": _d(0),
         "priority": "NORMAL", "status": "CONFIRMED", "lines": '[{"seq": 10, "item_sku": "FG-1002", "quantity": 200, "unit_price": 42.0, "status": "OPEN"}]'},
        {"order_no": "SO-2026-1003", "customer_id": "C003", "order_date": _d(-1), "due_date": _d(1),
         "priority": "NORMAL", "status": "CONFIRMED", "lines": '[{"seq": 10, "item_sku": "FG-1003", "quantity": 60, "unit_price": 130.0, "status": "OPEN"}]'},
        {"order_no": "SO-2026-1004", "customer_id": "C002", "order_date": _d(-1), "due_date": _d(0),
         "priority": "NORMAL", "status": "CONFIRMED", "lines": '[{"seq": 10, "item_sku": "FG-1004", "quantity": 150, "unit_price": 96.0, "status": "OPEN"}]'},
    ],
    "work_orders": [
        {"order_no": "WO-2026-0001", "product_sku": "FG-1001", "product_name": "气动阀体", "quantity": 120,
         "completed_qty": 0, "status": "RELEASED", "priority": "HIGH", "due_date": _d(0), "customer": "常州某气动自动化公司",
         "sales_order_no": "SO-2026-1001", "bom_code": "BOM-FG1001", "routing_code": "RT-FG1001",
         "created_at": _d(-2) + "T08:00:00", "released_at": _d(-1) + "T09:00:00", "completed_at": None},
        {"order_no": "WO-2026-0002", "product_sku": "FG-1002", "product_name": "法兰盘", "quantity": 200,
         "completed_qty": 0, "status": "RELEASED", "priority": "NORMAL", "due_date": _d(0), "customer": "苏州某汽车零部件厂",
         "sales_order_no": "SO-2026-1002", "bom_code": "BOM-FG1002", "routing_code": "RT-FG1002",
         "created_at": _d(-2) + "T08:30:00", "released_at": _d(-1) + "T09:30:00", "completed_at": None},
        {"order_no": "WO-2026-0003", "product_sku": "FG-1003", "product_name": "传动轴", "quantity": 60,
         "completed_qty": 0, "status": "RELEASED", "priority": "NORMAL", "due_date": _d(1), "customer": "苏州某汽车零部件厂",
         "sales_order_no": "SO-2026-1003", "bom_code": "BOM-FG1003", "routing_code": "RT-FG1003",
         "created_at": _d(-1) + "T10:00:00", "released_at": _d(-1) + "T10:00:00", "completed_at": None},
        {"order_no": "WO-2026-0004", "product_sku": "FG-1004", "product_name": "精密喷油嘴", "quantity": 150,
         "completed_qty": 0, "status": "RELEASED", "priority": "NORMAL", "due_date": _d(0), "customer": "无锡某泵业股份",
         "sales_order_no": "SO-2026-1004", "bom_code": "BOM-FG1004", "routing_code": "RT-FG1004",
         "created_at": _d(-1) + "T11:00:00", "released_at": _d(-1) + "T11:00:00", "completed_at": None},
        {"order_no": "WO-2026-0005", "product_sku": "SF-1001", "product_name": "阀芯（半成品）", "quantity": 80,
         "completed_qty": 0, "status": "DRAFT", "priority": "LOW", "due_date": _d(2), "customer": "内部补库",
         "sales_order_no": None, "bom_code": "BOM-SF1001", "routing_code": "RT-SF1001",
         "created_at": _d(0) + "T07:00:00", "released_at": None, "completed_at": None},
    ],
    "work_centers": [
        {"code": "CNC-01", "name": "数控车床 A", "wc_type": "CNC_LATHE", "status": "IDLE", "capacity_per_hour": 20, "setup_time_min": 30, "current_production_order": None},
        {"code": "CNC-02", "name": "数控车床 B", "wc_type": "CNC_LATHE", "status": "IDLE", "capacity_per_hour": 18, "setup_time_min": 30, "current_production_order": None},
        {"code": "MC-01", "name": "卧式加工中心", "wc_type": "MACHINING_CENTER", "status": "IDLE", "capacity_per_hour": 15, "setup_time_min": 45, "current_production_order": None},
        {"code": "GRIND-01", "name": "数控磨床", "wc_type": "GRINDER", "status": "IDLE", "capacity_per_hour": 12, "setup_time_min": 40, "current_production_order": None},
        {"code": "DIE-01", "name": "压铸机", "wc_type": "DIE_CAST", "status": "IDLE", "capacity_per_hour": 40, "setup_time_min": 60, "current_production_order": None},
        {"code": "HT-01", "name": "热处理炉", "wc_type": "HEAT_TREAT", "status": "IDLE", "capacity_per_hour": 25, "setup_time_min": 20, "current_production_order": None},
        {"code": "ASM-01", "name": "装配线", "wc_type": "ASSEMBLY", "status": "IDLE", "capacity_per_hour": 30, "setup_time_min": 20, "current_production_order": None},
        {"code": "QC-01", "name": "三坐标检测", "wc_type": "INSPECTION", "status": "IDLE", "capacity_per_hour": 50, "setup_time_min": 10, "current_production_order": None},
    ],
    "shift_entries": [
        {"work_center": "CNC-01", "shift_date": _d(0), "shift_no": "DAY", "start_time": "08:00", "end_time": "16:00", "efficiency": 0.9},
        {"work_center": "CNC-01", "shift_date": _d(0), "shift_no": "NIGHT", "start_time": "16:00", "end_time": "24:00", "efficiency": 0.85},
        {"work_center": "CNC-02", "shift_date": _d(0), "shift_no": "DAY", "start_time": "08:00", "end_time": "16:00", "efficiency": 0.9},
        {"work_center": "CNC-02", "shift_date": _d(0), "shift_no": "NIGHT", "start_time": "16:00", "end_time": "24:00", "efficiency": 0.85},
        {"work_center": "MC-01", "shift_date": _d(0), "shift_no": "DAY", "start_time": "08:00", "end_time": "16:00", "efficiency": 0.9},
        {"work_center": "GRIND-01", "shift_date": _d(0), "shift_no": "DAY", "start_time": "08:00", "end_time": "16:00", "efficiency": 0.88},
        {"work_center": "DIE-01", "shift_date": _d(0), "shift_no": "DAY", "start_time": "08:00", "end_time": "16:00", "efficiency": 0.92},
        {"work_center": "HT-01", "shift_date": _d(0), "shift_no": "DAY", "start_time": "08:00", "end_time": "16:00", "efficiency": 0.95},
        {"work_center": "ASM-01", "shift_date": _d(0), "shift_no": "DAY", "start_time": "08:00", "end_time": "16:00", "efficiency": 0.93},
        {"work_center": "QC-01", "shift_date": _d(0), "shift_no": "DAY", "start_time": "08:00", "end_time": "16:00", "efficiency": 0.95},
    ],
    "routing_operations": [
        # 气动阀体
        {"routing_code": "RT-FG1001", "product_sku": "FG-1001", "operation_no": 10, "operation_name": "压铸",
         "work_center": "DIE-01", "setup_time_min": 60, "run_time_min_per_unit": 1.5, "tooling": "模具 D101", "queue_time_min": 15, "inspection_required": 0},
        {"routing_code": "RT-FG1001", "product_sku": "FG-1001", "operation_no": 20, "operation_name": "车削",
         "work_center": "CNC-01", "setup_time_min": 30, "run_time_min_per_unit": 3.0, "tooling": "车刀 L202", "queue_time_min": 10, "inspection_required": 0},
        {"routing_code": "RT-FG1001", "product_sku": "FG-1001", "operation_no": 30, "operation_name": "装配",
         "work_center": "ASM-01", "setup_time_min": 20, "run_time_min_per_unit": 2.0, "tooling": None, "queue_time_min": 5, "inspection_required": 0},
        {"routing_code": "RT-FG1001", "product_sku": "FG-1001", "operation_no": 40, "operation_name": "终检",
         "work_center": "QC-01", "setup_time_min": 10, "run_time_min_per_unit": 1.2, "tooling": None, "queue_time_min": 0, "inspection_required": 1},
        # 法兰盘
        {"routing_code": "RT-FG1002", "product_sku": "FG-1002", "operation_no": 10, "operation_name": "车削",
         "work_center": "CNC-01", "setup_time_min": 30, "run_time_min_per_unit": 1.8, "tooling": "车刀 L201", "queue_time_min": 10, "inspection_required": 0},
        {"routing_code": "RT-FG1002", "product_sku": "FG-1002", "operation_no": 20, "operation_name": "钻孔攻丝",
         "work_center": "MC-01", "setup_time_min": 45, "run_time_min_per_unit": 2.2, "tooling": "钻头 D451", "queue_time_min": 15, "inspection_required": 0},
        {"routing_code": "RT-FG1002", "product_sku": "FG-1002", "operation_no": 30, "operation_name": "终检",
         "work_center": "QC-01", "setup_time_min": 10, "run_time_min_per_unit": 0.8, "tooling": None, "queue_time_min": 0, "inspection_required": 1},
        # 传动轴
        {"routing_code": "RT-FG1003", "product_sku": "FG-1003", "operation_no": 10, "operation_name": "车削",
         "work_center": "CNC-02", "setup_time_min": 30, "run_time_min_per_unit": 4.0, "tooling": "车刀 L203", "queue_time_min": 10, "inspection_required": 0},
        {"routing_code": "RT-FG1003", "product_sku": "FG-1003", "operation_no": 20, "operation_name": "热处理",
         "work_center": "HT-01", "setup_time_min": 20, "run_time_min_per_unit": 2.4, "tooling": None, "queue_time_min": 30, "inspection_required": 0},
        {"routing_code": "RT-FG1003", "product_sku": "FG-1003", "operation_no": 30, "operation_name": "精磨",
         "work_center": "GRIND-01", "setup_time_min": 40, "run_time_min_per_unit": 2.5, "tooling": "砂轮 G315", "queue_time_min": 20, "inspection_required": 0},
        {"routing_code": "RT-FG1003", "product_sku": "FG-1003", "operation_no": 40, "operation_name": "终检",
         "work_center": "QC-01", "setup_time_min": 10, "run_time_min_per_unit": 1.0, "tooling": None, "queue_time_min": 0, "inspection_required": 1},
        # 喷油嘴
        {"routing_code": "RT-FG1004", "product_sku": "FG-1004", "operation_no": 10, "operation_name": "车削",
         "work_center": "CNC-01", "setup_time_min": 30, "run_time_min_per_unit": 2.0, "tooling": "车刀 L204", "queue_time_min": 10, "inspection_required": 0},
        {"routing_code": "RT-FG1004", "product_sku": "FG-1004", "operation_no": 20, "operation_name": "精磨",
         "work_center": "GRIND-01", "setup_time_min": 40, "run_time_min_per_unit": 1.6, "tooling": "砂轮 G316", "queue_time_min": 20, "inspection_required": 0},
        {"routing_code": "RT-FG1004", "product_sku": "FG-1004", "operation_no": 30, "operation_name": "装配",
         "work_center": "ASM-01", "setup_time_min": 20, "run_time_min_per_unit": 1.0, "tooling": None, "queue_time_min": 5, "inspection_required": 0},
        {"routing_code": "RT-FG1004", "product_sku": "FG-1004", "operation_no": 40, "operation_name": "终检",
         "work_center": "QC-01", "setup_time_min": 10, "run_time_min_per_unit": 0.9, "tooling": None, "queue_time_min": 0, "inspection_required": 1},
        # 阀芯（半成品）
        {"routing_code": "RT-SF1001", "product_sku": "SF-1001", "operation_no": 10, "operation_name": "车削",
         "work_center": "CNC-02", "setup_time_min": 30, "run_time_min_per_unit": 2.0, "tooling": "车刀 L205", "queue_time_min": 10, "inspection_required": 0},
        {"routing_code": "RT-SF1001", "product_sku": "SF-1001", "operation_no": 20, "operation_name": "精磨",
         "work_center": "GRIND-01", "setup_time_min": 40, "run_time_min_per_unit": 1.2, "tooling": "砂轮 G317", "queue_time_min": 20, "inspection_required": 0},
    ],
    "changeover_matrix": [
        {"from_sku": "FG-1001", "to_sku": "FG-1004", "setup_time_min": 20},
        {"from_sku": "FG-1004", "to_sku": "FG-1001", "setup_time_min": 20},
        {"from_sku": "FG-1002", "to_sku": "FG-1001", "setup_time_min": 25},
        {"from_sku": "FG-1003", "to_sku": "FG-1004", "setup_time_min": 30},
    ],
    # 4 released work orders were released overnight; their ORDER_RELEASE
    # events are pending in the outbox, awaiting the MES to consume them
    # (transactional-outbox semantics: ERP committed, MES has not pulled yet).
    "outbox_events": [
        {"event_id": "EV-WO-2026-0001-000001", "event_type": "ORDER_RELEASE",
         "aggregate_type": "work_order", "aggregate_id": "WO-2026-0001",
         "payload": '{"order_no": "WO-2026-0001", "product_sku": "FG-1001", "quantity": 120, "due_date": "' + _d(0) + '"}',
         "status": "PENDING", "attempts": 0, "created_at": _d(-1) + "T09:00:00",
         "published_at": None, "acked_at": None, "last_error": None},
        {"event_id": "EV-WO-2026-0002-000001", "event_type": "ORDER_RELEASE",
         "aggregate_type": "work_order", "aggregate_id": "WO-2026-0002",
         "payload": '{"order_no": "WO-2026-0002", "product_sku": "FG-1002", "quantity": 200, "due_date": "' + _d(0) + '"}',
         "status": "PENDING", "attempts": 0, "created_at": _d(-1) + "T09:30:00",
         "published_at": None, "acked_at": None, "last_error": None},
        {"event_id": "EV-WO-2026-0003-000001", "event_type": "ORDER_RELEASE",
         "aggregate_type": "work_order", "aggregate_id": "WO-2026-0003",
         "payload": '{"order_no": "WO-2026-0003", "product_sku": "FG-1003", "quantity": 60, "due_date": "' + _d(1) + '"}',
         "status": "PENDING", "attempts": 0, "created_at": _d(-1) + "T10:00:00",
         "published_at": None, "acked_at": None, "last_error": None},
        {"event_id": "EV-WO-2026-0004-000001", "event_type": "ORDER_RELEASE",
         "aggregate_type": "work_order", "aggregate_id": "WO-2026-0004",
         "payload": '{"order_no": "WO-2026-0004", "product_sku": "FG-1004", "quantity": 150, "due_date": "' + _d(0) + '"}',
         "status": "PENDING", "attempts": 0, "created_at": _d(-1) + "T11:00:00",
         "published_at": None, "acked_at": None, "last_error": None},
    ],
}

_WORK_INSTRUCTIONS = {
    ("RT-FG1001", 10): "核对模具 D101 与铝锭批次；模温 180±10℃，压射压力 65MPa；首件标记待检。",
    ("RT-FG1001", 20): "装夹阀体毛坯，找正跳动≤0.05mm；车刀 L202，转速 1800r/min，进给 0.12mm/r；每 20 件抽检 φ32H7。",
    ("RT-FG1001", 30): "装入阀芯 SF-1001 与密封圈 RM-1005；扭矩 8±0.5N·m；做气密 0.8MPa 保压 30s。",
    ("RT-FG1001", 40): "三坐标 QC-01 检测 φ32H7、同轴度 φ0.03、密封面粗糙度 Ra1.6；记录实测值。",
    ("RT-FG1002", 10): "法兰盘车削，车刀 L201，端面跳动≤0.03mm；首件确认后批量。",
    ("RT-FG1002", 20): "钻头 D451，4×M8 均布；攻丝深度 12mm；去毛刺并吹净。",
    ("RT-FG1002", 30): "检测外径 φ120±0.05、孔位度 φ0.2、平面度 0.03；超差开 NCR。",
    ("RT-FG1003", 10): "40Cr 棒料车削，留磨量 0.3mm；车刀 L203；检查中心孔。",
    ("RT-FG1003", 20): "装炉随炉试样；淬火 850℃×40min，回火 560℃×90min；硬度 28-32HRC。",
    ("RT-FG1003", 30): "砂轮 G315 修整后磨削；磨削量 0.02mm/刀；冷却液充分；检测 φ25k6。",
    ("RT-FG1003", 40): "检测 φ25k6、圆度 0.005、表面 Ra0.8；磁粉探伤无裂纹。",
    ("RT-FG1004", 10): "铜棒 CU-3604 车削，车刀 L204；内孔留磨量 0.15mm。",
    ("RT-FG1004", 20): "砂轮 G316 精磨喷孔座面；Ra0.4；每 10 件用塞规检查。",
    ("RT-FG1004", 30): "装配密封圈，注意方向；流量测试 120±5mL/min。",
    ("RT-FG1004", 40): "检测喷孔 φ0.25±0.01、座面 Ra0.4、流量；记录 CMM 实测。",
    ("RT-SF1001", 10): "SS-304 阀芯车削，车刀 L205；外圆留磨 0.2mm。",
    ("RT-SF1001", 20): "砂轮 G317 磨削 φ18h6；圆度 0.004；首件送 QC。",
}

for _op in SEED_DATA["routing_operations"]:
    _op["instructions"] = _WORK_INSTRUCTIONS.get(
        (_op["routing_code"], _op["operation_no"]),
        f"按《作业指导书》执行{_op['operation_name']}；首件确认，过程中每 20 件自检并记录。",
    )
