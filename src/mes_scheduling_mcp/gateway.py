"""Thin MCP gateway: search / describe / call over the registered tool set.

The explicit domain tools stay registered for compatibility.  This module adds
the agent-facing meta surface used by production MCP servers:

- ``search_tools``   ranked-ish discovery over the catalog
- ``describe_tool``  full contract (description, annotations, input schema)
- ``call_tool``      dispatch with field projection, paging and budgeting
- ``call_destructive_tool``  the same, gated to DESTRUCTIVE tools

The gateway reads tools from a FastMCP ``ToolManager`` so the schemas come from
the live registrations and cannot drift from the hand-written catalog.
"""
from __future__ import annotations

import difflib
import json
from typing import Any

from . import tool_catalog
from .formatting import MAX_CHARS, MAX_ROWS, shape

META_TOOLS = {"search_tools", "describe_tool", "call_tool", "call_destructive_tool"}

_ANNOTATIONS = {
    "READ": {"readOnlyHint": True, "destructiveHint": False,
             "idempotentHint": True, "openWorldHint": False},
    "WRITE": {"readOnlyHint": False, "destructiveHint": False,
              "idempotentHint": False, "openWorldHint": False},
    "DESTRUCTIVE": {"readOnlyHint": False, "destructiveHint": True,
                    "idempotentHint": True, "openWorldHint": False},
}


def annotation_hints(classification: str) -> dict[str, bool]:
    return dict(_ANNOTATIONS.get(classification, _ANNOTATIONS["WRITE"]))


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **extra}}


def _suggest(name: str, names: list[str]) -> list[str]:
    return difflib.get_close_matches(name, names, n=3, cutoff=0.5)


def _registry(manager: Any, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    if registry is not None:
        return registry
    return {tool.name: tool for tool in manager.list_tools()}


def _schemas(manager: Any, registry: dict[str, Any] | None = None) -> list[str]:
    return list(_registry(manager, registry))


def search(manager: Any, query: str | None = None, group: str | None = None,
           classification: str | None = None, risk_class: str | None = None,
           requires_approval: bool | None = None, limit: int = 20,
           offset: int = 0, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registered = set(_schemas(manager, registry))
    results = [item for item in tool_catalog.search(
                   query, group, classification, risk_class, requires_approval)
               if item["name"] in registered]
    total = len(results)
    page = results[offset: offset + max(1, limit)]
    return {"query": query, "group": group, "classification": classification,
            "risk_class": risk_class, "requires_approval": requires_approval,
            "total": total, "offset": offset, "limit": limit, "results": page}


def describe(manager: Any, names: list[str],
             registry: dict[str, Any] | None = None) -> dict[str, Any]:
    requested = [n for n in names if n][:10]
    if not requested:
        return _error("validation_error", "pass name or names (max 10)")
    tools_by_name = _registry(manager, registry)
    known = list(tools_by_name)
    tools: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for name in requested:
        tool = tools_by_name.get(name)
        if tool is None:
            missing.append({"name": name, "suggestions": _suggest(name, known)})
            continue
        entry = tool_catalog.get(name) or {}
        classification = tool_catalog.classification(name)
        schema = tool.parameters or {}
        tools.append({
            "name": name,
            "group": entry.get("group", ""),
            "description": tool.description or entry.get("description", ""),
            "classification": classification,
            "risk_class": entry.get("risk_class", tool_catalog.risk_class_for(name)),
            "requires_approval": entry.get(
                "requires_approval", tool_catalog.requires_approval_for(name)),
            "annotations": annotation_hints(classification),
            "required": schema.get("required", []),
            "input_schema": schema,
        })
    payload: dict[str, Any] = {"tools": tools}
    if missing:
        payload["missing"] = missing
        if not tools:
            payload = _error("not_found", "no matching tools", missing=missing)
    return payload


def _parse_arguments(arguments: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if arguments is None:
        return {}, None
    if isinstance(arguments, str):
        if not arguments.strip():
            return {}, None
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as exc:
            return None, _error("validation_error", f"arguments is not valid JSON: {exc}")
        if not isinstance(parsed, dict):
            return None, _error("validation_error", "arguments must be a JSON object")
        return parsed, None
    if isinstance(arguments, dict):
        return arguments, None
    return None, _error("validation_error", "arguments must be a JSON object")


async def call(manager: Any, name: str, arguments: Any = None,
               fields: list[str] | None = None, limit: int | None = None,
               offset: int = 0, truncate: bool = True,
               context: Any = None, destructive: bool = False,
               registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch one registered tool and shape its result at the boundary."""
    if name in META_TOOLS:
        return _error("validation_error", f"{name} is a gateway tool; call it directly")
    tools_by_name = _registry(manager, registry)
    tool = tools_by_name.get(name)
    if tool is None:
        return _error("not_found", f"unknown tool {name}", suggestions=_suggest(name, list(tools_by_name)))

    classification = tool_catalog.classification(name)
    if classification == "DESTRUCTIVE" and not destructive:
        return _error("destructive_required",
                      f"{name} is DESTRUCTIVE: call it with call_destructive_tool; nothing ran")
    if destructive and classification != "DESTRUCTIVE":
        return _error("not_destructive",
                      f"{name} is not destructive: call it with call_tool; nothing ran")

    args, problem = _parse_arguments(arguments)
    if problem is not None:
        return problem

    properties = (tool.parameters or {}).get("properties") or {}
    unknown = [key for key in args if properties and key not in properties]
    if unknown:
        allowed = ", ".join(properties) or "(none)"
        return _error("unknown_argument",
                      f"{name} does not take {', '.join(unknown)}, so nothing ran. Its arguments: {allowed}")

    try:
        raw = await tool.run(args, context=context)
    except Exception as exc:  # noqa: BLE001 - surface a structured error to the agent
        return _error("tool_error", str(exc))

    if isinstance(raw, str):
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            data = {"text": raw}
    else:
        data = raw

    if isinstance(data, dict) and "error" in data:
        return data

    shaped = shape(data, fields=fields, limit=limit, offset=offset, truncate=truncate)
    shaped["tool"] = name
    shaped["classification"] = classification
    shaped["risk_class"] = tool_catalog.risk_class_for(name)
    shaped["requires_approval"] = tool_catalog.requires_approval_for(name)
    shaped["annotations"] = annotation_hints(classification)
    shaped["budget"] = {"max_rows": MAX_ROWS, "max_chars": MAX_CHARS, "truncate": truncate}
    return shaped