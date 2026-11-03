"""MCP-boundary result shaping.

Mirrors the boundary contract used by production MCP servers: list reads get a
default page size, a hard row/character budget, optional field projection and
recursive null-stripping.  Domain services keep returning full records; only
what crosses the MCP boundary is trimmed.

The goal is token economy for agents, not data loss: ``truncate=False`` (where
exposed) lifts the budget, and every shaped response reports the total row
count so a caller can page.
"""
from __future__ import annotations

import json
from typing import Any

DEFAULT_LIMIT = 25
MAX_ROWS = 100
MAX_CHARS = 20_000


def strip_nulls(value: Any) -> Any:
    """Drop null/undefined object entries recursively; keep array positions."""
    if isinstance(value, list):
        return [strip_nulls(item) for item in value]
    if isinstance(value, dict):
        return {k: strip_nulls(v) for k, v in value.items() if v is not None}
    return value


def project_fields(data: Any, fields: list[str] | None) -> Any:
    """Keep only the named top-level fields of each row (or of one object)."""
    if not fields:
        return data

    def pick(row: Any) -> Any:
        if not isinstance(row, dict):
            return row
        return {field: row[field] for field in fields if field in row}

    return [pick(row) for row in data] if isinstance(data, list) else pick(data)


def compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def shape(data: Any, fields: list[str] | None = None, limit: int | None = None,
          offset: int = 0, truncate: bool = True) -> dict[str, Any]:
    """Project, null-strip, page and budget a tool result.

    Returns an envelope ``{data, pagination, truncated, chars, budget}`` so the
    caller (and the agent) can see what was cut and how to page next.
    """
    projected = project_fields(strip_nulls(data), fields)
    pagination: dict[str, Any] | None = None
    truncated = False

    if isinstance(projected, list):
        total = len(projected)
        page_limit = DEFAULT_LIMIT if limit is None else max(0, int(limit))
        if truncate and page_limit > MAX_ROWS:
            page_limit = MAX_ROWS
        page_offset = max(0, int(offset))
        rows = projected[page_offset: page_offset + page_limit]
        projected = rows
        pagination = {
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "returned": len(rows),
            "has_more": page_offset + len(rows) < total,
        }

    text = compact(projected)
    if truncate and len(text) > MAX_CHARS:
        truncated = True
        if isinstance(projected, list) and projected:
            keep = projected
            while len(keep) > 1 and len(compact(keep)) > MAX_CHARS:
                keep = keep[: max(1, len(keep) // 2)]
            projected = keep
        text = compact(projected)
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
        if pagination is not None:
            pagination["returned"] = len(projected) if isinstance(projected, list) else 0
            pagination["has_more"] = True
            pagination["truncated_by_chars"] = True

    return {
        "data": projected,
        "pagination": pagination,
        "truncated": truncated,
        "chars": len(text),
        "budget": {"max_rows": MAX_ROWS, "max_chars": MAX_CHARS, "truncate": truncate},
    }