"""Manifest contract: every tool carries a risk class and approval flag."""
from mes_scheduling_mcp import tool_catalog

EXPECTED_APPROVALS = {
    "erp_release_work_order",
    "erp_close_work_order",
    "erp_cancel_work_order",
    "mes_set_work_center_status",
    "schedule_dispatch",
    "reset_demo_data",
    "call_destructive_tool",
}


def test_every_tool_has_manifest_fields():
    assert len(tool_catalog.TOOLS) == 73
    for tool in tool_catalog.TOOLS:
        assert tool["risk_class"] in tool_catalog.RISK_CLASSES
        assert isinstance(tool["requires_approval"], bool)
        assert tool["risk_class"] == tool_catalog.risk_class_for(tool["name"])
        assert tool["requires_approval"] is tool_catalog.requires_approval_for(tool["name"])


def test_manifest_summary_is_consistent():
    summary = tool_catalog.manifest_summary()
    assert summary["total"] == 73
    assert sum(summary["by_risk_class"].values()) == 73
    assert sum(summary["by_classification"].values()) == 73
    assert summary["by_risk_class"] == {
        "read_only": 43, "internal_write": 28, "destructive": 2}
    assert set(summary["approval_required"]) == EXPECTED_APPROVALS
    assert summary["writes_allowed"] == "gated"
    assert "tool_approval" in summary["governance_gates"]

    full = tool_catalog.manifest()
    assert len(full["tools"]) == 73
    assert all("risk_class" in t for t in full["tools"])


def test_destructive_tools_are_approval_gated():
    destructive = {t["name"] for t in tool_catalog.TOOLS
                   if t["risk_class"] == "destructive"}
    assert destructive
    assert destructive <= set(tool_catalog.manifest_summary()["approval_required"])


def test_search_filters_risk_and_approval():
    read_only = tool_catalog.search(risk_class="read_only")
    assert read_only and all(t["risk_class"] == "read_only" for t in read_only)

    gated = tool_catalog.search(requires_approval=True)
    assert {t["name"] for t in gated} == EXPECTED_APPROVALS

    free = tool_catalog.search(requires_approval=False)
    assert free and all(not t["requires_approval"] for t in free)

    combo = tool_catalog.search(group="ERP", risk_class="internal_write",
                                requires_approval=True)
    assert {t["name"] for t in combo} == {
        "erp_release_work_order", "erp_close_work_order", "erp_cancel_work_order"}
