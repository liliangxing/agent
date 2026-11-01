"""排产 Agent 演示：用 MCP 客户端跑通「MES → AI 排产 → ERP」全流程。

对应案例里「排产员下班前手工录入工单」的场景，这里由一个 Agent
通过 MCP 工具自动完成：查工单 → 齐套检查 → 查产能 → 排产 → 下发 → 报工。

用法：
    python examples/scheduling_agent_demo.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run() -> None:
    env = dict(os.environ)
    env["MES_MCP_DB"] = ":memory:"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mes_scheduling_mcp"],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def tool(name: str, args: dict) -> dict:
                res = await session.call_tool(name, args)
                return json.loads(res.content[0].text)

            print("=" * 66)
            print("排产 Agent 自动运行（江苏某精密零部件厂 · 改造后）")
            print("=" * 66)

            print("\n[1] 拉取 ERP 当日待排产工单")
            orders = await tool("work_order_query", {"status": "RELEASED"})
            for o in orders["items"]:
                print(f"    {o['order_no']}  {o['product_name']:<6} x{o['quantity']}  "
                      f"交期 {o['due_date']}  优先 {o['priority']}")

            print("\n[2] 齐套检查（法兰盘 WO-2026-0002，预期缺切削液）")
            check = await tool("erp_check_material_availability", {"order_no": "WO-2026-0002"})
            print(f"    fulfilled={check['fulfilled']}  缺口={check['shortages']}")

            print("\n[3] 查询产线可用产能")
            for c in await tool("capacity_query", {}):
                print(f"    {c['line_id']:<9} {c['status']:<12} 产能 {c['capacity_per_hour']:>5}/h  "
                      f"可用 {c['available_hours']}h")

            print("\n[4] 生成排产计划")
            plan = await tool("schedule_generate", {})
            print(f"    plan_id={plan['plan_id']}  汇总={plan['summary']}")
            for x in plan["scheduled"]:
                print(f"    → {x['order_no']} → {x['line_id']}  "
                      f"{x['planned_start'][11:]} ~ {x['planned_end'][11:]}")
            if plan["shortages"]:
                print(f"    [缺料] 物料缺口工单: {[s['order_no'] for s in plan['shortages']]}")

            print("\n[5] 下发排产计划到 MES 产线")
            dispatched = await tool("schedule_dispatch", {"plan_id": plan["plan_id"]})
            print(f"    下发任务数 = {dispatched['dispatched_count']}")

            print("\n[6] 报工：完成首条任务")
            tasks = await tool("production_task_query", {"status": "DISPATCHED"})
            if tasks["items"]:
                t = tasks["items"][0]
                updated = await tool(
                    "mes_report_progress",
                    {"task_id": t["task_id"], "actual_qty": t["quantity"], "complete": True},
                )
                print(f"    {updated['task_id']}  →  {updated['status']}")

            health = await tool("health_check", {})
            print("\n[7] 健康检查", health)
            print("\n" + "=" * 66)
            print("改造前：排产员下班前手工把 ERP 当日工单录入 MES。")
            print("改造后：以上 6 步由排产 Agent 通过 MCP Server 全自动完成。")
            print("=" * 66)


if __name__ == "__main__":
    asyncio.run(run())