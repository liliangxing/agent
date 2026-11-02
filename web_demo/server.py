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
    if released:
        po = released[0]
        backend.mes.start_production_order(po.production_order_no)
        ops = sorted(backend.mes.list_operations(po.production_order_no),
                     key=lambda o: o.operation_no)
        for op in ops:
            backend.mes.report_progress(po.production_order_no, op.operation_no,
                                        qty_good=po.quantity, qty_bad=0, complete=True)
        progress = backend.synchronize_progress()
        reported = [f"{u['erp']} → 完工" for u in progress["updated"]]
    steps.append({
        "step": "7. 报工并回写 ERP 完工",
        "tool": "mes_report_progress + integration_sync_progress",
        "result": reported,
    })

    reconcile = backend.reconcile()
    steps.append({
        "step": "8. 对账（ERP ↔ MES）",
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


TOOL_INFO = [
    {"name": "erp_list_items", "group": "ERP", "desc": "查询物料主数据"},
    {"name": "erp_get_item", "group": "ERP", "desc": "按 SKU 查询物料"},
    {"name": "erp_create_item", "group": "ERP", "desc": "新建物料主数据"},
    {"name": "erp_get_bom", "group": "ERP", "desc": "查询产品 BOM"},
    {"name": "erp_bom_explode", "group": "ERP", "desc": "多级 BOM 展开与缺口计算"},
    {"name": "erp_check_material_availability", "group": "ERP", "desc": "工单齐套检查"},
    {"name": "erp_inventory_summary", "group": "ERP", "desc": "库存汇总"},
    {"name": "erp_inventory_adjust", "group": "ERP", "desc": "库存出入库调整"},
    {"name": "erp_list_sales_orders", "group": "ERP", "desc": "查询销售订单"},
    {"name": "erp_create_sales_order", "group": "ERP", "desc": "新建销售订单"},
    {"name": "erp_list_purchase_orders", "group": "ERP", "desc": "查询采购订单"},
    {"name": "erp_create_purchase_order", "group": "ERP", "desc": "新建采购订单"},
    {"name": "erp_receive_purchase_order", "group": "ERP", "desc": "采购收货"},
    {"name": "erp_list_work_orders", "group": "ERP", "desc": "查询生产工单"},
    {"name": "erp_get_work_order", "group": "ERP", "desc": "查询单个工单"},
    {"name": "erp_create_work_order", "group": "ERP", "desc": "新建工单"},
    {"name": "erp_release_work_order", "group": "ERP", "desc": "发布工单并广播 ORDER_RELEASE"},
    {"name": "erp_complete_work_order", "group": "ERP", "desc": "标记工单完工"},
    {"name": "erp_close_work_order", "group": "ERP", "desc": "关闭工单"},
    {"name": "erp_run_mrp", "group": "ERP", "desc": "运行 MRP 净算"},
    {"name": "erp_list_proposals", "group": "ERP", "desc": "查询 MRP 建议"},
    {"name": "mes_list_work_centers", "group": "MES", "desc": "查询工作中心状态"},
    {"name": "mes_get_work_center", "group": "MES", "desc": "查询单个工作中心"},
    {"name": "mes_set_work_center_status", "group": "MES", "desc": "设置工作中心状态"},
    {"name": "mes_list_shifts", "group": "MES", "desc": "查询班次日历"},
    {"name": "mes_list_routings", "group": "MES", "desc": "查询工艺路线"},
    {"name": "mes_get_routing", "group": "MES", "desc": "查询指定工艺路线"},
    {"name": "mes_list_production_orders", "group": "MES", "desc": "查询 MES 生产订单"},
    {"name": "mes_get_production_order", "group": "MES", "desc": "查询单个生产订单"},
    {"name": "mes_list_operations", "group": "MES", "desc": "查询工序进度"},
    {"name": "mes_start_production_order", "group": "MES", "desc": "生产订单开工"},
    {"name": "mes_report_progress", "group": "MES", "desc": "报工/完工"},
    {"name": "mes_report_downtime", "group": "MES", "desc": "上报设备停机"},
    {"name": "mes_oee_report", "group": "MES", "desc": "设备综合效率 OEE"},
    {"name": "mes_quality_inspect", "group": "MES", "desc": "质量检验登记"},
    {"name": "mes_list_quality_records", "group": "MES", "desc": "查询质量检验记录"},
    {"name": "mes_issue_material", "group": "MES", "desc": "生产领料/倒冲"},
    {"name": "capacity_query", "group": "排产", "desc": "查询产能与班次"},
    {"name": "schedule_generate", "group": "排产", "desc": "有限产能排产"},
    {"name": "schedule_dispatch", "group": "排产", "desc": "下发排产计划"},
    {"name": "schedule_detail", "group": "排产", "desc": "查询计划明细"},
    {"name": "schedule_list", "group": "排产", "desc": "查询历史计划"},
    {"name": "integration_sync_orders", "group": "集成", "desc": "ERP→MES 同步生产订单"},
    {"name": "integration_sync_progress", "group": "集成", "desc": "MES→ERP 回写完工"},
    {"name": "integration_reconcile", "group": "集成", "desc": "ERP↔MES 对账"},
    {"name": "integration_outbox_status", "group": "集成", "desc": "查看发件箱事件"},
    {"name": "integration_inbox_status", "group": "集成", "desc": "查看收件箱事件"},
    {"name": "integration_mappings", "group": "集成", "desc": "查看双主键映射"},
    {"name": "integration_audit_log", "group": "集成", "desc": "查询审计留痕"},
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