"""Web 演示服务：把 MES 排产 MCP Server 的核心逻辑用 HTTP 暴露出来。

本服务直接复用 ``mes_scheduling_mcp`` 的领域服务（erp / scheduling / mes），
与 MCP 工具层走的是同一套代码，因此网页上跑通的流程即等价于 MCP Server 的能力。

用纯标准库实现，零额外依赖，方便在任何环境直接运行：
    python web_demo/server.py
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mes_scheduling_mcp.erp import ERPService
from mes_scheduling_mcp.mes import MESService
from mes_scheduling_mcp.scheduling import SchedulingService
from mes_scheduling_mcp.store import Store

ROOT = Path(__file__).resolve().parent

store = Store(os.environ.get("MES_MCP_DB", ":memory:"))
erp = ERPService(store)
sched = SchedulingService(store)
mes = MESService(store)


def run_all() -> dict:
    """一键跑通完整排产闭环，返回带叙事的结果。"""
    store.reset()
    steps = []

    orders = erp.list_work_orders(status="RELEASED")
    steps.append({
        "step": "1. 拉取 ERP 当日待排产工单",
        "tool": "work_order_query",
        "result": [f"{o['order_no']} {o['product_name']} x{o['quantity']}" for o in orders],
    })

    check = erp.check_material_availability("WO-2026-0002")
    steps.append({
        "step": "2. 齐套检查（法兰盘 WO-2026-0002）",
        "tool": "erp_check_material_availability",
        "result": {"fulfilled": check["fulfilled"], "shortages": check["shortages"]},
    })

    caps = sched.capacity_query()
    steps.append({
        "step": "3. 查询产线可用产能",
        "tool": "capacity_query",
        "result": [
            f"{c['line_id']} {c['status']} 可用 {c['available_hours']}h" for c in caps
        ],
    })

    plan = sched.generate_schedule()
    steps.append({
        "step": "4. 生成排产计划",
        "tool": "schedule_generate",
        "result": {
            "plan_id": plan["plan_id"],
            "summary": plan["summary"],
            "scheduled": [
                f"{x['order_no']} → {x['line_id']} {x['planned_start'][11:]}~{x['planned_end'][11:]}"
                for x in plan["scheduled"]
            ],
            "shortages": [s["order_no"] for s in plan["shortages"]],
        },
    })

    dispatched = mes.dispatch_schedule(plan["plan_id"])
    steps.append({
        "step": "5. 下发排产计划到 MES 产线",
        "tool": "schedule_dispatch",
        "result": {"dispatched_count": dispatched["dispatched_count"]},
    })

    tasks = mes.list_tasks(status="DISPATCHED")
    reported = []
    for t in tasks:
        updated = mes.report_progress(t["task_id"], t["quantity"], complete=True)
        reported.append(f"{t['order_no']} → {updated['status']}")
    steps.append({
        "step": "6. 报工（模拟执行完成）",
        "tool": "mes_report_progress",
        "result": reported,
    })

    health = mes.health()
    return {"steps": steps, "health": health,
            "before": "排产员下班前手工把 ERP 当日工单录入 MES",
            "after": "以上 6 步由排产 Agent 通过 MCP Server 自动完成"}


def dispatch_legacy(plan_id: str) -> dict:
    return mes.dispatch_schedule(plan_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "mes-mcp-demo/1.0"

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

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
            "/api/health": lambda: {"health": mes.health()},
            "/api/orders": lambda: erp.list_work_orders(status="RELEASED"),
            "/api/capacity": lambda: sched.capacity_query(),
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
            elif self.path == "/api/schedule":
                self._send(200, sched.generate_schedule())
            elif self.path == "/api/dispatch":
                body = self._read_body()
                self._send(200, mes.dispatch_schedule(body["plan_id"]))
            elif self.path == "/api/report":
                body = self._read_body()
                self._send(200, mes.report_progress(
                    body["task_id"], int(body["actual_qty"]), bool(body.get("complete", False))
                ))
            elif self.path == "/api/reset":
                self._send(200, {"status": "reset", **mes.health()})
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:  # pragma: no cover
            self._send(500, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


TOOL_INFO = [
    {"name": "work_order_query", "group": "ERP", "desc": "查询工单（可按状态/关键字过滤）"},
    {"name": "work_order_get", "group": "ERP", "desc": "查询单个工单"},
    {"name": "work_order_create", "group": "ERP", "desc": "新建工单（严格校验字段）"},
    {"name": "erp_release_work_order", "group": "ERP", "desc": "草稿工单发布"},
    {"name": "erp_check_material_availability", "group": "ERP", "desc": "BOM 齐套检查"},
    {"name": "material_query", "group": "ERP", "desc": "查询物料库存"},
    {"name": "capacity_query", "group": "排产", "desc": "查询产线产能"},
    {"name": "schedule_generate", "group": "排产", "desc": "生成排产计划"},
    {"name": "schedule_query", "group": "排产", "desc": "查询历史计划"},
    {"name": "schedule_detail", "group": "排产", "desc": "查询计划明细"},
    {"name": "production_line_query", "group": "MES", "desc": "查询产线状态"},
    {"name": "mes_import_work_orders", "group": "MES", "desc": "工单录入 MES"},
    {"name": "schedule_dispatch", "group": "MES", "desc": "下发排产计划"},
    {"name": "production_task_query", "group": "MES", "desc": "查询生产任务"},
    {"name": "mes_report_progress", "group": "MES", "desc": "报工/完工"},
    {"name": "list_resources", "group": "运维", "desc": "列出资源目录"},
    {"name": "get_resource_schema", "group": "运维", "desc": "查看资源字段模型"},
    {"name": "health_check", "group": "运维", "desc": "健康检查"},
    {"name": "usage_stats", "group": "运维", "desc": "工具调用统计"},
    {"name": "reset_demo_data", "group": "运维", "desc": "重置演示数据（admin）"},
]


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"mes-mcp web demo listening on 0.0.0.0:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()