"""MCP server exposing the ERP -> scheduling -> MES workflow as tools.

Tools are grouped with a ``domain_`` prefix (erp_ *, schedule_ *, mes_ *)
so agentic clients can discover the workflow by naming convention alone.
Every tool returns a single JSON text block (object or array) so the
output shape is identical across all MCP clients.
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .erp import ERPService
from .mes import MESService
from .scheduling import SchedulingService
from .store import Store


def _json(obj: Any) -> str:
    """Serialize a tool result into a single JSON text block."""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def build_server(db_path: str | None = None) -> FastMCP:
    mcp = FastMCP(
        "mes-scheduling",
        instructions=(
            "MES ↔ AI 排产 Agent ↔ ERP 桥接服务。"
            "典型流程：erp_list_work_orders 查工单 → "
            "erp_check_material_availability 齐套 → capacity_query 查产能 → "
            "schedule_generate 排产 → schedule_dispatch 下发 → "
            "mes_report_progress 报工。"
        ),
    )

    store = Store(db_path)
    erp = ERPService(store)
    sched = SchedulingService(store)
    mes = MESService(store)

    # ---- ERP -----------------------------------------------------------------
    @mcp.tool()
    def erp_list_work_orders(
        status: str | None = None,
        due_date_from: str | None = None,
        due_date_to: str | None = None,
    ) -> str:
        """查询 ERP 工单。可按状态(DRAFT/RELEASED/SCHEDULED/IMPORTED)和交期过滤。"""
        return _json(erp.list_work_orders(status, due_date_from, due_date_to))

    @mcp.tool()
    def erp_get_work_order(order_no: str) -> str:
        """按工单号查询单个 ERP 工单详情。"""
        return _json(erp.get_work_order(order_no))

    @mcp.tool()
    def erp_release_work_order(order_no: str) -> str:
        """将草稿工单(DRAFT)发布为待排产(RELEASED)。"""
        return _json(erp.release_work_order(order_no))

    @mcp.tool()
    def erp_list_materials(sku: str | None = None) -> str:
        """查询物料库存(可选按 sku 过滤)，返回库存/占用/可用数量。"""
        return _json(erp.list_materials(sku))

    @mcp.tool()
    def erp_check_material_availability(order_no: str) -> str:
        """对指定工单做 BOM 齐套检查，返回所需物料、缺口与是否满足。"""
        return _json(erp.check_material_availability(order_no))

    # ---- Scheduling ----------------------------------------------------------
    @mcp.tool()
    def capacity_query(plan_date: str | None = None) -> str:
        """查询各产线产能：状态、单位小时产能、班次时长、已占用与剩余可用小时。"""
        return _json(sched.capacity_query(plan_date))

    @mcp.tool()
    def schedule_generate(plan_date: str | None = None) -> str:
        """为所有 RELEASED 工单生成排产计划。自动做物料齐套与产能约束，返回计划ID、
        已排产明细、物料缺口与未排产工单。"""
        return _json(sched.generate_schedule(plan_date))

    @mcp.tool()
    def schedule_list(status: str | None = None) -> str:
        """查询历史排产计划(可按 DRAFT/DISPATCHED 过滤)。"""
        return _json(sched.list_schedules(status))

    @mcp.tool()
    def schedule_get(plan_id: str) -> str:
        """查询单个排产计划的明细行。"""
        return _json(sched.get_schedule(plan_id))

    # ---- MES -----------------------------------------------------------------
    @mcp.tool()
    def mes_list_lines(line_id: str | None = None) -> str:
        """查询 MES 产线(设备)状态。"""
        return _json(mes.list_lines(line_id))

    @mcp.tool()
    def mes_import_work_orders(order_nos: list[str] | None = None) -> str:
        """将 ERP 工单录入 MES 为生产任务(默认全部 RELEASED 工单)。对应案例中此前人工录入的步骤。"""
        return _json(mes.import_work_orders(order_nos))

    @mcp.tool()
    def schedule_dispatch(plan_id: str) -> str:
        """下发排产计划到产线：生成 MES 任务、产线置为 BUSY。"""
        return _json(mes.dispatch_schedule(plan_id))

    @mcp.tool()
    def mes_list_tasks(status: str | None = None, line_id: str | None = None) -> str:
        """查询 MES 生产任务(可按状态/产线过滤)。"""
        return _json(mes.list_tasks(status, line_id))

    @mcp.tool()
    def mes_report_progress(task_id: str, actual_qty: int, complete: bool = False) -> str:
        """报工：更新生产任务实际产量，产量达标或 complete=True 时置为完工并释放产线。"""
        return _json(mes.report_progress(task_id, actual_qty, complete))

    @mcp.tool()
    def health_check() -> str:
        """服务健康检查：返回工单/任务/产线等运行统计。"""
        return _json(mes.health())

    @mcp.tool()
    def reset_demo_data() -> str:
        """重置演示数据到初始种子状态(清空排产/任务/占用)。"""
        store.reset()
        return _json({"status": "reset", **mes.health()})

    return mcp


mcp = build_server()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()