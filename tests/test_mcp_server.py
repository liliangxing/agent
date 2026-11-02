"""Integration test: exercise the MCP server over the real stdio protocol."""
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _session():
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
            yield session


async def _tool_names(session):
    tools = await session.list_tools()
    return {t.name for t in tools.tools}


async def test_list_tools_over_stdio():
    async for session in _session():
        names = await _tool_names(session)
        assert len(names) == 54
        for expect in (
            "erp_list_work_orders",
            "erp_check_material_availability",
            "integration_sync_orders",
            "integration_sync_progress",
            "integration_reconcile",
            "schedule_generate",
            "schedule_dispatch",
            "capacity_query",
            "mes_report_progress",
            "mes_oee_report",
            "health_check",
            "list_resources",
            "get_resource_schema",
        ):
            assert expect in names


async def test_workflow_over_stdio():
    async for session in _session():
        res = await session.call_tool("integration_sync_orders", {})
        assert not res.isError
        assert "WO-2026-0001" in res.content[0].text

        res = await session.call_tool("schedule_generate", {"data": {}})
        assert not res.isError
        assert "WO-2026-0002" in res.content[0].text  # the coolant shortage order

        res = await session.call_tool("health_check", {})
        assert not res.isError
        assert "ok" in res.content[0].text


async def test_write_tool_enforced_schema():
    async for session in _session():
        res = await session.call_tool(
            "erp_create_work_order",
            {"data": {
                "order_no": "WO-HACK",
                "product_sku": "FG-1001",
                "quantity": 1,
                "due_date": "2026-11-02",
                "bogus_field": 1,
            }},
        )
        assert res.isError
        assert "bogus_field" in res.content[0].text


async def test_resource_catalog_exposes_work_orders():
    async for session in _session():
        res = await session.call_tool("list_resources", {})
        text = res.content[0].text
        assert "work_order" in text
        assert "production_order" in text
        assert "schedule" in text