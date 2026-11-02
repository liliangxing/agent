"""排产 Agent 演示：用 MCP 客户端跑通「ERP → AI 排产 → MES → 回写 ERP」全流程。

对应案例里「排产员下班前手工录入工单」的场景，这里由一个 Agent 通过 MCP 工具
自动完成：查工单 → 同步到 MES → 齐套检查 → 查产能 → 有限产能排产 → 下发 →
报工 → 回写 → 对账。

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
            orders = await tool("erp_list_work_orders", {"status": "RELEASED"})
            for o in orders:
                print(f"    {o['order_no']}  {o['product_name']:<6} x{o['quantity']}  "
                      f"交期 {o['due_date']}  优先 {o['priority']}")

            print("\n[2] 同步 ERP 工单到 MES（消费 ORDER_RELEASE 发件箱事件）")
            sync = await tool("integration_sync_orders", {})
            print(f"    建立生产订单 {sync['count']} 条")
            for m in sync["created"]:
                print(f"    ERP {m['erp']} → MES {m['mes']}")

            print("\n[3] 齐套检查（法兰盘 WO-2026-0002，预期缺切削液）")
            check = await tool("erp_check_material_availability", {"order_no": "WO-2026-0002"})
            print(f"    fulfilled={check['fulfilled']}")
            for g in check["shortages"]:
                print(f"    缺料 {g['sku']} {g['name']}：需 {g['required']} / 有 {g['available']}，缺 {g['shortage']}")

            print("\n[4] 查询产线可用产能")
            for c in await tool("capacity_query", {}):
                print(f"    {c['code']:<9} {c['status']:<8} 产能 {c['capacity_per_hour']:>5}/h  "
                      f"可用 {c['available_hours']}h")

            print("\n[5] 生成有限产能排产计划")
            plan = await tool("schedule_generate", {"data": {}})
            print(f"    plan_id={plan['plan_id']}  汇总={plan['summary']}")
            seen = set()
            for x in plan["scheduled"]:
                key = x["erp_work_order_no"]
                if key not in seen:
                    seen.add(key)
                    print(f"    → {key} ({x['product_sku']}) 第一道工序 {x['work_center']}")
            if plan["shortages"]:
                print(f"    [缺料] 未排产工单: {[s['order_no'] for s in plan['shortages']]}")

            print("\n[6] 下发排产计划到 MES 产线")
            dispatched = await tool("schedule_dispatch", {"plan_id": plan["plan_id"]})
            print(f"    激活生产订单数 = {dispatched['dispatched_count']}")

            print("\n[7] 报工：完成第一个生产订单并回写 ERP")
            pos = await tool("mes_list_production_orders", {"status": "RELEASED"})
            po_no = pos[0]["production_order_no"]
            await tool("mes_start_production_order", {"production_order_no": po_no})
            ops = await tool("mes_list_operations", {"production_order_no": po_no})
            qty = pos[0]["quantity"]
            for op in sorted(ops, key=lambda o: o["operation_no"]):
                await tool("mes_report_progress", {"data": {
                    "production_order_no": po_no,
                    "operation_no": op["operation_no"],
                    "qty_good": qty, "qty_bad": 0, "complete": True,
                }})
            back = await tool("integration_sync_progress", {})
            print(f"    回写 ERP 完工 {len(back['updated'])} 条")
            for u in back["updated"]:
                print(f"    MES → ERP 完工: {u['erp']}")

            print("\n[8] 对账 + 健康检查")
            reconcile = await tool("integration_reconcile", {})
            health = await tool("health_check", {})
            print(f"    reconcile: {reconcile['summary']}")
            print(f"    erp={health['erp']}  mes={health['mes']}")

            print("\n" + "=" * 66)
            print("改造前：排产员下班前手工把 ERP 当日工单录入 MES。")
            print("改造后：以上流程由排产 Agent 通过 MCP Server 全自动完成。")
            print("=" * 66)


if __name__ == "__main__":
    asyncio.run(run())