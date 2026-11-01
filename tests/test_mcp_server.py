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


async def test_list_tools_over_stdio():
    async for session in _session():
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert "erp_list_work_orders" in names
        assert "schedule_generate" in names
        assert "schedule_dispatch" in names
        assert "mes_report_progress" in names


async def test_workflow_over_stdio():
    async for session in _session():
        # query today's orders
        res = await session.call_tool("erp_list_work_orders", {"status": "RELEASED"})
        assert res.content

        # generate schedule
        res = await session.call_tool("schedule_generate", {})
        text = res.content[0].text
        assert 'WO-2026-0002' in text  # the cooled shortage appears in output

        # health check
        res = await session.call_tool("health_check", {})
        assert "ok" in res.content[0].text