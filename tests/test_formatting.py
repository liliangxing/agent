"""Result shaping at the MCP boundary."""
from mes_scheduling_mcp import formatting, tool_catalog


def test_strip_nulls_recursively_keeps_array_positions():
    data = {"a": None, "b": {"c": None, "d": 1}, "e": [None, {"f": None, "g": 2}]}
    assert formatting.strip_nulls(data) == {"b": {"d": 1}, "e": [None, {"g": 2}]}


def test_projection_keeps_only_named_fields():
    rows = [{"id": 1, "name": "A", "secret": "x"}, {"id": 2, "name": "B"}]
    assert formatting.project_fields(rows, ["id"]) == [{"id": 1}, {"id": 2}]


def test_default_page_and_has_more():
    rows = [{"i": i} for i in range(40)]
    out = formatting.shape(rows)
    assert out["pagination"]["limit"] == formatting.DEFAULT_LIMIT
    assert out["pagination"]["returned"] == formatting.DEFAULT_LIMIT
    assert out["pagination"]["total"] == 40
    assert out["pagination"]["has_more"] is True


def test_explicit_limit_is_clamped_when_truncating():
    rows = [{"i": i} for i in range(200)]
    out = formatting.shape(rows, limit=500, truncate=True)
    assert out["pagination"]["limit"] == formatting.MAX_ROWS
    out = formatting.shape(rows, limit=500, truncate=False)
    assert out["pagination"]["limit"] == 500


def test_character_budget_cuts_rows():
    rows = [{"payload": "x" * 400} for _ in range(100)]
    out = formatting.shape(rows, limit=100, truncate=True)
    assert out["chars"] <= formatting.MAX_CHARS
    assert out["truncated"] is True
    assert out["pagination"]["truncated_by_chars"] is True


def test_catalog_is_classified_and_gateway_is_present():
    assert len(tool_catalog.TOOLS) == 73
    assert tool_catalog.classification("reset_demo_data") == "DESTRUCTIVE"
    assert tool_catalog.classification("erp_list_items") == "READ"
    assert tool_catalog.classification("erp_create_item") == "WRITE"
    assert {t["name"] for t in tool_catalog.TOOLS} >= {
        "search_tools", "describe_tool", "call_tool", "call_destructive_tool"}
