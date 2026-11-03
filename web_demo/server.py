"""Web 演示服务：把 MES 排产 MCP Server 的核心逻辑用 HTTP 暴露出来。

本服务直接复用 ``mes_scheduling_mcp`` 的 ``ManufacturingBackend`` 门面（ERP /
MES / APS / integration），与 MCP 工具层走的是同一套代码，因此网页上跑通的
流程即等价于 MCP Server 的能力。

用纯标准库实现，零额外依赖，方便在任何环境直接运行：
    python web_demo/server.py
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mes_scheduling_mcp.backend import ManufacturingBackend
from mes_scheduling_mcp.store import Store
from mes_scheduling_mcp import tool_catalog

ROOT = Path(__file__).resolve().parent

backend = ManufacturingBackend(Store(os.environ.get("MES_MCP_DB", ":memory:")))


def run_all() -> dict:
    """一键跑通完整 ERP→MES→APS 闭环，返回带叙事的结果。"""
    backend.reset()
    steps = []

    orders = [o.__dict__ for o in backend.erp.list_work_orders(status="RELEASED")]
    steps.append({
        "step": "1. 拉取 ERP 当日待排产工单",
        "tool": "erp_list_work_orders",
        "result": [f"{o['order_no']} {o['product_name']} x{o['quantity']}" for o in orders],
    })

    sync = backend.synchronize_orders()
    steps.append({
        "step": "2. 同步 ERP 工单到 MES（消费 ORDER_RELEASE 发件箱事件）",
        "tool": "integration_sync_orders",
        "result": [f"{m['erp']} → {m['mes']}" for m in sync["created"]],
    })

    check = backend.erp.check_material_availability("WO-2026-0002")
    steps.append({
        "step": "3. 齐套检查（法兰盘 WO-2026-0002，缺切削液）",
        "tool": "erp_check_material_availability",
        "result": {"fulfilled": check["fulfilled"], "shortages": check["shortages"]},
    })

    caps = backend.aps.capacity_query()
    steps.append({
        "step": "4. 查询产线可用产能",
        "tool": "capacity_query",
        "result": [f"{c['code']} {c['status']} 可用 {c['available_hours']}h" for c in caps],
    })

    plan = backend.aps.generate_schedule()
    steps.append({
        "step": "5. 有限产能排产",
        "tool": "schedule_generate",
        "result": {
            "plan_id": plan["plan_id"],
            "summary": plan["summary"],
            "scheduled": sorted({x["erp_work_order_no"] for x in plan["scheduled"]}),
            "shortages": [s["order_no"] for s in plan["shortages"]],
        },
    })

    dispatched = backend.aps.dispatch_schedule(plan["plan_id"])
    steps.append({
        "step": "6. 下发排产计划到 MES 产线",
        "tool": "schedule_dispatch",
        "result": {"dispatched_count": dispatched["dispatched_count"]},
    })

    # 报工：完成第一个激活的生产订单并回写 ERP
    released = backend.mes.list_production_orders(status="RELEASED")
    reported = []
    target = None
    if released:
        target = sorted(released, key=lambda x: x.due_date or "")[0]
        reqs = backend.erp.bom_explode(target.product_sku, target.quantity)
        issued = []
        for req in reqs:
            try:
                backend.mes.issue_material(target.production_order_no, req["sku"], req["required"])
                issued.append(f"{req['sku']} x{req['required']}")
            except Exception:
                pass
        steps.append({
            "step": "7. 按 BOM 领料，按批次 FIFO 占用并记录批次谱系",
            "tool": "mes_issue_material",
            "result": issued,
        })
        backend.mes.start_production_order(target.production_order_no)
        ops = sorted(backend.mes.list_operations(target.production_order_no),
                     key=lambda o: o.operation_no)
        for op in ops:
            backend.mes.report_progress(target.production_order_no, op.operation_no,
                                        qty_good=target.quantity, qty_bad=0, complete=True)
        progress = backend.synchronize_progress()
        reported = [f"{u['erp']} → 完工" for u in progress["updated"]]
    steps.append({
        "step": "8. 报工并回写 ERP 完工（末道工序生成成品批次）",
        "tool": "mes_report_progress + integration_sync_progress",
        "result": reported,
    })

    if target:
        trace = backend.mes.trace_lot(f"LOT-{target.production_order_no}", direction="up")
        steps.append({
            "step": "9. 成品批次向上追溯到来料批次",
            "tool": "mes_trace_lot",
            "result": {
                "lot_no": trace["lot_no"],
                "upstream_lots": trace["upstream_lots"],
                "links": len(trace["parents"]),
            },
        })

    reconcile = backend.reconcile()
    steps.append({
        "step": "10. 对账（ERP ↔ MES）",
        "tool": "integration_reconcile",
        "result": reconcile["summary"],
    })

    return {
        "steps": steps,
        "health": backend.health(),
        "before": "排产员下班前手工把 ERP 当日工单录入 MES",
        "after": "以上步骤由排产 Agent 通过 MCP Server 自动完成",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "mes-mcp-demo/2.0"

    def _send(self, code: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            page = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return

        routes = {
            "/api/health": lambda: backend.health(),
            "/api/orders": lambda: [o.__dict__ for o in backend.erp.list_work_orders(status="RELEASED")],
            "/api/capacity": lambda: backend.aps.capacity_query(),
            "/api/tools": lambda: {"tools": TOOL_INFO},
        }
        handler = routes.get(self.path)
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
                self._send(200, {"status": "reset", **backend.health()})
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:  # pragma: no cover
            self._send(500, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


TOOL_INFO = tool_catalog.as_dicts()

def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"mes-mcp web demo listening on 0.0.0.0:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()