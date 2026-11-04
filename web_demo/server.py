"""Web 演示服务：把 MES 排产 MCP Server 的核心逻辑用 HTTP 暴露出来。

演示与 MCP Server 共用同一个 ``ManufacturingBackend`` 和同一套工具注册表：
``gateway``（search / describe / call）直接调用 FastMCP 的 ToolManager，
因此网页「工具网关」里看到的行为与真实 MCP 客户端一致。

用纯标准库实现，零额外依赖：
    python web_demo/server.py
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mes_scheduling_mcp import tool_catalog
from mes_scheduling_mcp.backend import ManufacturingBackend
from mes_scheduling_mcp.gateway import call as gateway_call
from mes_scheduling_mcp.gateway import describe as gateway_describe
from mes_scheduling_mcp.server import build_server
from mes_scheduling_mcp.store import Store

ROOT = Path(__file__).resolve().parent

backend = ManufacturingBackend(Store(os.environ.get("MES_MCP_DB", ":memory:")))
mcp = build_server(backend=backend)

STATE: dict = {
    "ran_at": None,
    "plan_id": None,
    "plan": None,
    "order_dates": [],
    "utilization": [],
    "shortages": [],
    "capacity_blocked": [],
    "target_po": None,
    "recall_lot": None,
}

_SPECS = {
    "FG-1001": ("外圆直径 φ32", 32.0, 0.025),
    "FG-1002": ("外径 φ120", 120.0, 0.05),
    "FG-1003": ("轴颈 φ25k6", 25.0, 0.008),
    "FG-1004": ("喷孔 φ0.25", 0.25, 0.01),
    "SF-1001": ("外圆 φ18h6", 18.0, 0.006),
}
_OFFSETS = [-0.40, 0.20, -0.15, 0.35, 0.00, -0.25, 1.18, 0.12, -0.30, 0.05]
_DOWNTIME = {
    "CNC-01": ("BREAKDOWN", 18.0, False),
    "HT-01": ("MAINTENANCE", 12.0, True),
    "GRIND-01": ("QUALITY_STOP", 8.0, False),
    "DIE-01": ("SETUP", 15.0, True),
}


def _records(rows):
    return [asdict(row) if hasattr(row, "__dataclass_fields__") else row for row in rows]


def run_all() -> dict:
    """一键跑通完整 ERP→MES→APS→MRP→追溯 闭环，返回带叙事的结果。"""
    backend.reset()
    STATE.update({"ran_at": None, "plan_id": None, "plan": None, "order_dates": [],
                  "utilization": [], "shortages": [], "capacity_blocked": [],
                  "target_po": None, "recall_lot": None})
    steps = []

    orders = _records(backend.erp.list_work_orders(status="RELEASED"))
    steps.append({
        "step": "拉取 ERP 当日待排产工单",
        "tool": "erp_list_work_orders",
        "result": [f"{o['order_no']} {o['product_name']} x{o['quantity']}" for o in orders],
    })

    sync = backend.synchronize_orders()
    steps.append({
        "step": "同步 ERP 工单到 MES（消费 ORDER_RELEASE 发件箱事件）",
        "tool": "integration_sync_orders",
        "result": [f"{m['erp']} → {m['mes']}" for m in sync["created"]],
    })

    check = backend.erp.check_material_availability("WO-2026-0002")
    steps.append({
        "step": "齐套检查：法兰盘 WO-2026-0002 缺切削液",
        "tool": "erp_check_material_availability",
        "result": {"fulfilled": check["fulfilled"], "shortages": check["shortages"]},
    })

    caps = backend.aps.capacity_query()
    steps.append({
        "step": "查询产线可用产能",
        "tool": "capacity_query",
        "result": [f"{c['code']} {c['status']} 可用 {c['available_hours']}h" for c in caps],
    })

    plan = backend.aps.generate_schedule()
    steps.append({
        "step": "有限产能排产：班次窗口 + 换型矩阵 + 双日期",
        "tool": "schedule_generate",
        "result": {
            "plan_id": plan["plan_id"],
            "summary": plan["summary"],
            "scheduled": sorted({x["erp_work_order_no"] for x in plan["scheduled"]}),
            "shortages": [s["order_no"] for s in plan["shortages"]],
        },
    })

    mrp = backend.erp.run_mrp()
    steps.append({
        "step": "MRP 净算：聚合需求 + 逐层净算 + 在途/安全库存",
        "tool": "erp_run_mrp",
        "result": [
            f"{p['proposal_type']} {p['item_sku']} 净需求 {p['quantity']} "
            f"(毛需求 {p['gross_qty']} / 在库 {p['on_hand_qty']} / 在途 {p['on_order_qty']})"
            for p in mrp["proposals"]
        ],
    })

    dispatched = backend.aps.dispatch_schedule(plan["plan_id"])
    steps.append({
        "step": "下发排产计划到 MES 产线",
        "tool": "schedule_dispatch",
        "result": {"dispatched_count": dispatched["dispatched_count"]},
    })

    released = backend.mes.list_production_orders(status="RELEASED")
    released = sorted(released, key=lambda x: x.due_date or "")
    reported = []
    issued_total = []
    run_count = 0
    test_count = 0
    used_downtime: set[str] = set()
    target = released[0] if released else None
    for idx, po in enumerate(released):
        reqs = backend.erp.bom_explode(po.product_sku, po.quantity)
        for req in reqs:
            try:
                backend.mes.issue_material(po.production_order_no, req["sku"], req["required"])
                issued_total.append(f"{po.production_order_no}:{req['sku']}")
            except Exception:
                pass
        backend.mes.start_production_order(po.production_order_no)
        ops = sorted(backend.mes.list_operations(po.production_order_no),
                     key=lambda o: o.operation_no)
        for op in ops:
            run = backend.mes.start_run(po.production_order_no, op.operation_no)
            run_count += 1
            scrap = 1 if op is ops[-1] else 0
            if op.work_center in _DOWNTIME and op.work_center not in used_downtime:
                reason, minutes, planned = _DOWNTIME[op.work_center]
                backend.mes.log_run_downtime(run["run_no"], reason, minutes, planned=planned)
                used_downtime.add(op.work_center)
            backend.mes.log_run_output(run["run_no"], max(0, po.quantity - scrap), scrap)
            backend.mes.end_run(run["run_no"])
            backend.mes.report_progress(po.production_order_no, op.operation_no,
                                        qty_good=max(0, po.quantity - scrap),
                                        qty_bad=scrap, complete=True)
        if ops:
            final_op = ops[-1]
            characteristic, nominal, tolerance = _SPECS.get(po.product_sku, ("关键尺寸", 10.0, 0.02))
            fails = 0
            for sample, factor in enumerate(_OFFSETS, start=1):
                measured = round(nominal + factor * tolerance, 4)
                defect = ""
                description = ""
                if abs(factor) > 1:
                    defect, description = "DIM_OOT", "尺寸超差"
                elif idx == 1 and sample == 3:
                    defect, description = "SURFACE_SCRATCH", "表面划伤"
                result = "FAIL" if defect else "PASS"
                fails += 1 if defect else 0
                backend.mes.record_test(
                    po.production_order_no, final_op.operation_no,
                    unit_ref=f"{po.production_order_no}-{sample:03d}",
                    result=result, station="CMM-01", defect_code=defect,
                    defect_desc=description, characteristic=characteristic,
                    nominal=nominal, tolerance=tolerance, measured=measured,
                    inspector="QC-01")
                test_count += 1
            backend.mes.quality_inspect(
                po.production_order_no, final_op.operation_no, sample_qty=len(_OFFSETS),
                defect_qty=fails, disposition="REWORK" if fails else "PASS",
                defect_code="DIM_OOT" if fails else "", defect_desc="尺寸超差" if fails else "",
                station="CMM-01", inspector="QC-01")
    steps.append({
        "step": "按 BOM 领料并逐工序开工报工（记录停机与产出）",
        "tool": "mes_issue_material + mes_start_run + mes_log_run_output",
        "result": {"orders": len(released), "runs": run_count,
                   "issued_lines": len(issued_total)},
    })
    progress = backend.synchronize_progress()
    reported = [f"{u['erp']} → 完工" for u in progress["updated"]]
    steps.append({
        "step": "报工并回写 ERP 完工（末道工序生成成品批次）",
        "tool": "mes_report_progress + integration_sync_progress",
        "result": reported,
    })
    steps.append({
        "step": "终检采样：CMM 实测值、缺陷代码与一次合格率",
        "tool": "mes_record_test + mes_yield_report",
        "result": {"tests": test_count, "yield": backend.mes.yield_report()},
    })

    trace = None
    recall = None
    recall_lot = None
    if target:
        trace = backend.mes.trace_lot(f"LOT-{target.production_order_no}", direction="up")
        lot_counts: Counter[str] = Counter()
        for po in released:
            for consumption in backend.mes.list_consumptions(po.production_order_no):
                lot_counts[consumption["lot_no"]] += 1
        if lot_counts:
            recall_lot = lot_counts.most_common(1)[0][0]
            recall = backend.mes.recall_report(recall_lot)
        steps.append({
            "step": "成品批次向上追溯，并从来料批次做召回影响分析",
            "tool": "mes_trace_lot + mes_recall_report",
            "result": {
                "lot_no": trace["lot_no"],
                "upstream_lots": trace["upstream_lots"],
                "links": len(trace["parents"]),
                "recall_lot": recall_lot,
                "affected_customers": recall["affected_customers"] if recall else [],
            },
        })

    reconcile = backend.reconcile()
    steps.append({
        "step": "对账（ERP ↔ MES）",
        "tool": "integration_reconcile",
        "result": reconcile["summary"],
    })

    STATE.update({
        "ran_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "plan_id": plan["plan_id"],
        "plan": plan,
        "order_dates": plan["order_dates"],
        "utilization": plan["utilization"],
        "shortages": plan["shortages"],
        "capacity_blocked": plan["capacity_blocked"],
        "target_po": target.production_order_no if target else None,
        "recall_lot": recall_lot,
    })

    return {
        "steps": steps,
        "health": backend.health(),
        "before": "排产员下班前手工把 ERP 当日工单录入 MES，再逐单核对缺料",
        "after": "以上步骤由排产 Agent 通过 MCP Server 自动完成，异常在驾驶舱可见",
    }


_DOWNTIME_LABEL = {
    "BREAKDOWN": "设备故障",
    "MAINTENANCE": "计划保养",
    "SETUP": "换型准备",
    "QUALITY_STOP": "质量停线",
    "MATERIAL_SHORTAGE": "缺料停线",
    "IDLE": "待料",
}


def _execution_rows(backend, production_orders, runs, tests, downtime):
    rows = []
    for po in production_orders:
        po_no = po["production_order_no"]
        po_runs = [r for r in runs if r["production_order_no"] == po_no]
        routing = {op["operation_no"]: op for op in
                   backend.mes.list_routings(product_sku=po["product_sku"])}
        progress = sorted(backend.mes.list_operations(po_no),
                          key=lambda o: o.operation_no)
        operations = []
        for op in progress:
            route = routing.get(op.operation_no, {})
            op_runs = [r for r in po_runs if r["operation_no"] == op.operation_no]
            operations.append({
                "operation_no": op.operation_no,
                "name": route.get("operation_name", f"OP{op.operation_no}"),
                "work_center": op.work_center,
                "status": str(op.status),
                "qty_good": op.qty_good,
                "qty_bad": op.qty_bad,
                "inspection_required": bool(route.get("inspection_required")),
                "instructions": route.get("instructions", ""),
                "run_count": len(op_runs),
                "downtime_min": round(sum(
                    r["duration_min"] for r in downtime
                    if r.get("run_no") in {x["run_no"] for x in op_runs}), 1),
            })
        completed = [o for o in operations if o["status"] == "COMPLETED"]
        current_op = next((o for o in operations if o["status"] != "COMPLETED"), None)
        last_op = max((o["operation_no"] for o in operations), default=0)
        last_good = max((r["qty_good"] for r in po_runs
                         if r["operation_no"] == last_op), default=0)
        po_tests = [t for t in tests if t["production_order_no"] == po_no]
        rows.append({
            "production_order_no": po_no,
            "erp_work_order_no": po["erp_work_order_no"],
            "product_sku": po["product_sku"],
            "product_name": po["product_name"],
            "quantity": po["quantity"],
            "completed_qty": po["completed_qty"] or last_good,
            "status": str(po["status"]),
            "due_date": po["due_date"],
            "lot_no": f"LOT-{po_no}",
            "progress": round(len(completed) / len(operations), 3) if operations else 0,
            "current_operation": current_op,
            "operations": operations,
            "runs": len(po_runs),
            "scrap_qty": sum(r["qty_scrap"] for r in po_runs),
            "tests": len(po_tests),
            "test_fails": sum(1 for t in po_tests if str(t["result"]) == "FAIL"),
            "downtime_min": round(sum(
                d["duration_min"] for d in downtime
                if d.get("run_no") in {r["run_no"] for r in po_runs}), 1),
        })
    order = {"IN_PROCESS": 0, "RELEASED": 1, "COMPLETED": 2}
    rows.sort(key=lambda x: (order.get(x["status"], 3), x["due_date"]))
    return rows


def _andon_items(shortages, late_orders, downtime, tests, spc):
    items = []
    for d in downtime:
        planned = bool(d["planned"])
        severity = "info" if planned else (
            "critical" if str(d["reason"]) == "BREAKDOWN" else "warning")
        items.append({
            "severity": severity,
            "source": d["work_center"],
            "title": _DOWNTIME_LABEL.get(str(d["reason"]), str(d["reason"])),
            "detail": f"{d['duration_min']:.0f} 分钟 · {'计划停机' if planned else '非计划停机'}",
            "time": d["start_time"],
            "status": "已恢复" if not planned else "已计划",
        })
    fails = [t for t in tests if str(t["result"]) == "FAIL"]
    by_code: dict[str, list] = {}
    for t in fails:
        by_code.setdefault(t["defect_code"] or "UNSPECIFIED", []).append(t)
    for code, rows in by_code.items():
        items.append({
            "severity": "critical" if len(rows) >= 2 else "warning",
            "source": "CMM-01",
            "title": f"质量异常 {code}",
            "detail": f"{len(rows)} 件不良 · {rows[0]['defect_desc'] or '终检拦截'}",
            "time": rows[0]["tested_at"],
            "status": "待处置",
        })
    for row in spc.get("characteristics", []):
        if row.get("cpk") is not None and row["cpk"] < 1.0:
            items.append({
                "severity": "warning",
                "source": "SPC",
                "title": f"过程能力不足 {row['characteristic']}",
                "detail": f"Cpk {row['cpk']:.2f} < 1.00 · 超差 {row['out_of_spec']} 件",
                "time": "",
                "status": "待改善",
            })
    for shortage in shortages:
        detail = " / ".join(
            f"{x['sku']} 缺 {x['shortage']:.1f}" for x in shortage.get("shortages", []))
        items.append({
            "severity": "critical",
            "source": shortage["order_no"],
            "title": "齐套缺口，工单未排入",
            "detail": detail or "物料不足",
            "time": "",
            "status": "采购在途",
        })
    for row in late_orders:
        items.append({
            "severity": "warning",
            "source": row["erp_work_order_no"],
            "title": "预计迟单",
            "detail": f"延迟 {row['lateness_hours']:.1f} 小时 · 交期 {row['due_date']}",
            "time": "",
            "status": "待排产",
        })
    rank = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda x: rank.get(x["severity"], 3))
    return {
        "items": items[:10],
        "total": len(items),
        "critical": sum(1 for x in items if x["severity"] == "critical"),
        "warning": sum(1 for x in items if x["severity"] == "warning"),
        "info": sum(1 for x in items if x["severity"] == "info"),
    }


def _oee_loss(oee_rows):
    planned = sum(x["planned_minutes"] for x in oee_rows)
    run = sum(x["run_minutes"] for x in oee_rows)
    ideal = sum(x.get("ideal_minutes", 0) for x in oee_rows)
    good = sum(x["good"] for x in oee_rows)
    scrap = sum(x["scrap"] for x in oee_rows)
    availability = run / planned if planned else 0
    performance = min(1.0, ideal / run) if run else 0
    quality = good / (good + scrap) if (good + scrap) else 0
    oee = availability * performance * quality
    return {
        "oee": round(oee, 3),
        "availability": round(availability, 3),
        "performance": round(performance, 3),
        "quality": round(quality, 3),
        "availability_loss": round(1 - availability, 3),
        "performance_loss": round(availability * (1 - performance), 3),
        "quality_loss": round(availability * performance * (1 - quality), 3),
        "availability_loss_min": round(max(0, planned - run), 1),
        "performance_loss_min": round(max(0, run - ideal), 1),
        "quality_loss_min": round(ideal * (scrap / (good + scrap)) if good + scrap else 0, 1),
        "planned_minutes": round(planned, 1),
        "run_minutes": round(run, 1),
        "ideal_minutes": round(ideal, 1),
        "good": good,
        "scrap": scrap,
    }


def build_state() -> dict:
    work_centers = _records(backend.mes.list_work_centers())
    production_orders = _records(backend.mes.list_production_orders())
    work_orders = _records(backend.erp.list_work_orders())
    inventory = backend.erp.inventory_summary()
    proposals = _records(backend.erp.list_proposals())
    plan = STATE.get("plan")
    summary = plan["summary"] if plan else {}
    shifts = _records(backend.mes.list_shifts())
    shift_date = shifts[0]["shift_date"] if shifts else None
    day_shift = next((x for x in shifts if x.get("shift_no") == "DAY"), shifts[0] if shifts else {})

    runs = _records(backend.mes.list_runs())
    tests = _records(backend.mes.list_tests())
    downtime = backend.mes.list_downtime()

    oee = [backend.mes.oee_report(work_center=wc["code"]) for wc in work_centers]
    active_oee = [x for x in oee if x["runs"]]
    oee_avg = round(sum(x["oee"] for x in active_oee) / len(active_oee), 3) if active_oee else 0
    yield_report = backend.mes.yield_report()
    spc = backend.mes.spc_report()
    cpk_values = [row["cpk"] for row in spc["characteristics"] if row["cpk"] is not None]
    for row in spc["characteristics"]:
        row["points"] = [
            {"unit_ref": t["unit_ref"], "measured": t["measured"],
             "result": str(t["result"]), "tested_at": t["tested_at"]}
            for t in tests if t["characteristic"] == row["characteristic"]
        ]

    execution = _execution_rows(backend, production_orders, runs, tests, downtime)
    executed = [x for x in execution if x["runs"]]
    late_orders = [x for x in STATE.get("order_dates", []) if not x.get("on_time", True)]
    andon = _andon_items(STATE.get("shortages", []), late_orders, downtime, tests, spc)
    oee_loss = _oee_loss(oee)

    planned_qty = sum(x["quantity"] for x in executed)
    good_qty = sum(x["completed_qty"] for x in executed)
    scrap_qty = sum(x["scrap_qty"] for x in executed)
    shift_detail = {
        "date": shift_date,
        "shift_no": day_shift.get("shift_no", "DAY"),
        "window": f"{day_shift.get('start_time', '08:00')}-{day_shift.get('end_time', '16:00')}",
        "team": "一号线 A 班",
        "work_centers": len(work_centers),
        "planned_qty": planned_qty,
        "good_qty": good_qty,
        "scrap_qty": scrap_qty,
        "attainment": round(good_qty / planned_qty, 3) if planned_qty else 0,
        "fpy": yield_report["first_pass_yield"],
        "oee": oee_avg,
        "downtime_min": round(sum(d["duration_min"] for d in downtime), 1),
        "unplanned_downtime_min": round(
            sum(d["duration_min"] for d in downtime if not d["planned"]), 1),
        "downtime_events": len(downtime),
        "tests": yield_report["tested"],
        "defects": yield_report["failed"],
        "late_orders": len(late_orders),
    }

    trace = None
    if STATE.get("target_po"):
        lot_no = f"LOT-{STATE['target_po']}"
        trace = backend.mes.trace_lot(lot_no, direction="up")
        trace["consumptions"] = backend.mes.list_consumptions(STATE["target_po"])
    recall = backend.mes.recall_report(STATE["recall_lot"]) if STATE.get("recall_lot") else None

    reconcile = backend.reconcile()
    kpis = {
        "released_work_orders": len([w for w in work_orders if str(w["status"]) == "RELEASED"]),
        "kit_rate": round(
            (summary.get("candidate_orders", 0) - summary.get("shortage_orders", 0))
            / summary["candidate_orders"], 3) if summary.get("candidate_orders") else None,
        "scheduled_orders": summary.get("scheduled_orders", 0),
        "late_orders": summary.get("late_orders", 0),
        "oee": oee_avg,
        "fpy": yield_report["first_pass_yield"],
        "tests": yield_report["tested"],
        "defects": yield_report["failed"],
        "cpk_min": round(min(cpk_values), 3) if cpk_values else None,
        "wip_orders": len([p for p in production_orders
                           if str(p["status"]) in ("RELEASED", "IN_PROCESS")]),
        "shortage_orders": summary.get("shortage_orders", 0),
        "makespan_min": summary.get("makespan_min", 0),
        "planned_qty": planned_qty,
        "good_qty": good_qty,
        "scrap_qty": scrap_qty,
        "attainment": shift_detail["attainment"],
        "downtime_min": shift_detail["downtime_min"],
        "unplanned_downtime_min": shift_detail["unplanned_downtime_min"],
        "andon_critical": andon["critical"],
        "andon_warning": andon["warning"],
    }

    return {
        "ran_at": STATE.get("ran_at"),
        "shift": shift_detail,
        "health": backend.health(),
        "kpis": kpis,
        "summary": summary,
        "work_centers": work_centers,
        "work_orders": work_orders,
        "production_orders": production_orders,
        "inventory": inventory,
        "mrp": proposals,
        "order_dates": STATE.get("order_dates", []),
        "utilization": STATE.get("utilization", []),
        "shortages": STATE.get("shortages", []),
        "capacity_blocked": STATE.get("capacity_blocked", []),
        "schedule": {
            "plan": plan["plan_id"] if plan else None,
            "operations": backend.aps.schedule_detail(plan["plan_id"])["operations"] if plan else [],
        },
        "trace": trace,
        "recall": recall,
        "runs": runs,
        "downtime": downtime,
        "execution": execution,
        "andon": andon,
        "oee_loss": oee_loss,
        "shift_detail": shift_detail,
        "manifest": tool_catalog.manifest_summary(),
        "quality": {
            "records": _records(backend.mes.list_quality_records()),
            "tests": tests,
            "yield": yield_report,
            "spc": spc,
        },
        "integration": {
            "outbox": backend.outbox_status(),
            "inbox": backend.inbox_status(),
            "mappings": backend.mappings(),
            "reconcile": reconcile,
            "audit": backend.audit_log(limit=40),
        },
        "oee": oee,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "mes-mcp-demo/3.0"

    def _send(self, code: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            page = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return

        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        routes = {
            "/api/health": lambda: backend.health(),
            "/api/state": build_state,
            "/api/orders": lambda: _records(backend.erp.list_work_orders(status="RELEASED")),
            "/api/capacity": lambda: backend.aps.capacity_query(),
            "/api/tools": lambda: {
                "tools": tool_catalog.as_dicts(),
                "groups": tool_catalog.GROUPS,
                "total": len(tool_catalog.TOOLS),
                "manifest": tool_catalog.manifest_summary(),
            },
        }
        handler = routes.get(parsed.path)
        if handler is None:
            self._send(404, {"error": "not found"})
            return
        try:
            self._send(200, handler())
        except Exception as exc:  # pragma: no cover
            self._send(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/run_all":
                self._send(200, run_all())
            elif self.path == "/api/reset":
                backend.reset()
                STATE.update({"ran_at": None, "plan_id": None, "plan": None,
                              "order_dates": [], "utilization": [], "shortages": [],
                              "capacity_blocked": [], "target_po": None})
                self._send(200, {"status": "reset", **backend.health()})
            elif self.path == "/api/describe":
                body = self._body()
                names = body.get("names") or ([body["name"]] if body.get("name") else [])
                self._send(200, gateway_describe(mcp._tool_manager, names, registry=mcp._gateway_registry))
            elif self.path == "/api/call":
                body = self._body()
                result = asyncio.run(gateway_call(
                    mcp._tool_manager,
                    body.get("name", ""),
                    arguments=body.get("arguments"),
                    fields=body.get("fields"),
                    limit=body.get("limit"),
                    offset=int(body.get("offset") or 0),
                    truncate=body.get("truncate", True),
                    destructive=bool(body.get("destructive", False)),
                    registry=mcp._gateway_registry,
                ))
                self._send(200, result)
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:  # pragma: no cover
            self._send(500, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def main() -> None:
    if not backend.mes.list_runs():
        run_all()
    port = int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"mes-mcp web demo listening on 0.0.0.0:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()