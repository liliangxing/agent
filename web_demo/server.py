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

    oee = [backend.mes.oee_report(work_center=wc["code"]) for wc in work_centers]
    active_oee = [x for x in oee if x["runs"]]
    oee_avg = round(sum(x["oee"] for x in active_oee) / len(active_oee), 3) if active_oee else 0
    yield_report = backend.mes.yield_report()
    spc = backend.mes.spc_report()
    cpk_values = [row["cpk"] for row in spc["characteristics"] if row["cpk"] is not None]

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
    }

    return {
        "ran_at": STATE.get("ran_at"),
        "shift": {"date": shift_date, "work_centers": len(work_centers)},
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
        "runs": _records(backend.mes.list_runs()),
        "quality": {
            "records": _records(backend.mes.list_quality_records()),
            "tests": _records(backend.mes.list_tests()),
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