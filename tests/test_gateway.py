"""Thin-gateway behaviour: registry, gateway-only mode, shaping."""
import asyncio

from mes_scheduling_mcp import gateway
from mes_scheduling_mcp.server import build_server


def test_gateway_only_mode_hides_domain_tools(monkeypatch):
    monkeypatch.setenv("MES_MCP_GATEWAY_ONLY", "1")
    mcp = build_server()
    names = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert names == gateway.META_TOOLS

    result = asyncio.run(gateway.call(
        mcp._tool_manager, "erp_list_work_orders", {}, limit=2,
        registry=mcp._gateway_registry))
    assert result["pagination"]["total"] >= 1
    assert result["pagination"]["returned"] == 2
    assert result["classification"] == "READ"

    described = gateway.describe(
        mcp._tool_manager, ["erp_list_work_orders"], registry=mcp._gateway_registry)
    assert described["tools"][0]["input_schema"]


def test_search_uses_registry(monkeypatch):
    monkeypatch.setenv("MES_MCP_GATEWAY_ONLY", "1")
    mcp = build_server()
    found = gateway.search(
        mcp._tool_manager, query="inventory", registry=mcp._gateway_registry)
    assert found["total"] >= 1
    assert all("inventory" in t["name"] or "库存" in t["description"]
               for t in found["results"])


def test_search_and_describe_expose_manifest_fields(monkeypatch):
    monkeypatch.setenv("MES_MCP_GATEWAY_ONLY", "1")
    mcp = build_server()

    found = gateway.search(
        mcp._tool_manager, requires_approval=True, limit=50,
        registry=mcp._gateway_registry)
    assert found["total"] == 7
    assert all(t["requires_approval"] and t["risk_class"] in (
        "read_only", "internal_write", "destructive") for t in found["results"])

    destructive = gateway.search(
        mcp._tool_manager, risk_class="destructive",
        registry=mcp._gateway_registry)
    assert destructive["total"] == 2
    assert all(t["risk_class"] == "destructive" for t in destructive["results"])

    described = gateway.describe(
        mcp._tool_manager, ["erp_release_work_order"],
        registry=mcp._gateway_registry)["tools"][0]
    assert described["risk_class"] == "internal_write"
    assert described["requires_approval"] is True
