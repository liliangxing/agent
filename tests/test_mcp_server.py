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
        assert "work_order_query" in names
        assert "schedule_generate" in names
        assert "schedule_dispatch" in names
        assert "mes_report_progress" in names
        assert "health_check" in names
        assert "list_resources" in names
        assert "get_resource_schema" in names


async def test_workflow_over_stdio():
    async for session in _session():
        res = await session.call_tool("work_order_query", {"status": "RELEASED"})
        assert not res.isError
        assert res.content

        res = await session.call_tool("schedule_generate", {})
        assert not res.isError
        text = res.content[0].text
        assert "WO-2026-0002" in text  # the cooled shortage appears in output

        res = await session.call_tool("health_check", {})
        assert not res.isError
        assert "ok" in res.content[0].text


async def test_write_tool_enforced_schema():
    async for session in _session():
        # work_order_create rejects unknown fields (extra="forbid")
        res = await session.call_tool(
            "work_order_create",
            {"data": {"no": "WC-HACK", "bogus_field": 1}},
        )
        assert res.isError
        assert "bogus_field" in res.content[0].text


async def test_resource_catalog_exposes_work_orders():
    async for session in _session():
        res = await session.call_tool("list_resources", {})
        text = res.content[0].text
        assert "work_order" in text