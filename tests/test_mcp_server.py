"""Integration test: exercise the MCP server over the real stdio protocol."""
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mes_scheduling_mcp import tool_catalog


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
        assert len(names) == len(tool_catalog.TOOLS) == 60
        assert names == {t["name"] for t in tool_catalog.TOOLS}
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
            "mes_trace_lot",
            "mes_list_consumptions",
            "search_tools",
            "describe_tool",
            "call_tool",
            "call_destructive_tool",
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

async def test_annotations_over_stdio():
    async for session in _session():
        tools = (await session.list_tools()).tools
        by_name = {t.name: t for t in tools}
        assert by_name["erp_list_items"].annotations.readOnlyHint is True
        assert by_name["erp_create_item"].annotations.readOnlyHint is False
        assert by_name["reset_demo_data"].annotations.destructiveHint is True
        assert by_name["call_destructive_tool"].annotations.destructiveHint is True


async def test_gateway_over_stdio():
    async for session in _session():
        res = await session.call_tool("describe_tool", {"names": ["erp_list_work_orders"]})
        desc = json.loads(res.content[0].text)
        assert desc["tools"][0]["classification"] == "READ"
        assert "input_schema" in desc["tools"][0]

        res = await session.call_tool("call_tool", {
            "name": "erp_list_work_orders", "limit": 2, "fields": ["order_no"]})
        shaped = json.loads(res.content[0].text)
        assert shaped["pagination"]["returned"] == 2
        assert list(shaped["data"][0]) == ["order_no"]
        assert shaped["chars"] > 0

        res = await session.call_tool("call_tool", {"name": "reset_demo_data"})
        refused = json.loads(res.content[0].text)
        assert refused["error"]["code"] == "destructive_required"

        res = await session.call_tool("call_tool", {
            "name": "erp_list_work_orders", "arguments": {"nope": 1}})
        refused = json.loads(res.content[0].text)
        assert refused["error"]["code"] == "unknown_argument"


async def test_destructive_gateway_over_stdio():
    async for session in _session():
        res = await session.call_tool("call_destructive_tool", {"name": "reset_demo_data"})
        payload = json.loads(res.content[0].text)
        assert payload["classification"] == "DESTRUCTIVE"
        assert payload["data"]["status"] == "reset"
