"""Observability: per-tool usage logging and aggregate stats.

Writes one JSON line per tool call (with sensitive parameter values masked)
and keeps in-memory counters plus error-type tallies, so an operator can ask
the server for its own usage report — the same feedback loop Kingdee's MCP
server ships with (``usage_log.jsonl`` + ``kingdee_usage_stats``).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

SENSITIVE_KEYS = {"password", "secret", "api_key", "apikey", "token", "cookie", "session", "app_sec"}


class UsageLogger:
    def __init__(self, log_file: Optional[str] = None) -> None:
        self.log_file = log_file or os.environ.get("MES_MCP_USAGE_LOG", "usage_log.jsonl")
        self._stats: dict[str, dict[str, Any]] = {}
        self._error_stats: dict[str, int] = {}
        self._lock = threading.Lock()

    def _sanitize(self, value: Any, depth: int = 0) -> Any:
        if depth > 4:
            return "..."
        if isinstance(value, dict):
            return {
                k: ("***" if k.lower() in SENSITIVE_KEYS else self._sanitize(v, depth + 1))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            if len(value) > 10:
                return [self._sanitize(v, depth + 1) for v in value[:10]] + [f"...({len(value) - 10} more)"]
            return [self._sanitize(v, depth + 1) for v in value]
        if isinstance(value, str) and len(value) > 200:
            return value[:200] + "..."
        return value

    def record(self, tool: str, params: dict, duration_ms: float, success: bool,
               error_type: str = "", result_preview: str = "") -> None:
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "tool": tool,
            "params": self._sanitize(params),
            "duration_ms": round(duration_ms, 2),
            "success": success,
            "error_type": error_type,
            "result_preview": (result_preview or "")[:400],
        }
        with self._lock:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass
            st = self._stats.setdefault(tool, {"total": 0, "success": 0, "failed": 0, "total_ms": 0.0})
            st["total"] += 1
            st["total_ms"] += duration_ms
            if success:
                st["success"] += 1
            else:
                st["failed"] += 1
                if error_type:
                    self._error_stats[error_type] = self._error_stats.get(error_type, 0) + 1

    def stats(self) -> dict:
        with self._lock:
            return {
                "log_file": self.log_file,
                "tool_stats": {
                    k: {
                        "total": v["total"],
                        "success": v["success"],
                        "failed": v["failed"],
                        "avg_ms": round(v["total_ms"] / v["total"], 2) if v["total"] else 0,
                    }
                    for k, v in self._stats.items()
                },
                "error_stats": dict(self._error_stats),
            }


def timed(usage: UsageLogger, tool_name: str, fn: Callable, params: dict) -> Any:
    """Invoke ``fn`` and record usage/outcome; re-raise on failure."""
    start = time.perf_counter()
    try:
        result = fn()
    except BaseException as exc:
        usage.record(tool_name, params, (time.perf_counter() - start) * 1000,
                     success=False, error_type=type(exc).__name__)
        raise
    usage.record(
        tool_name, params, (time.perf_counter() - start) * 1000,
        success=True, result_preview=_preview(result),
    )
    return result


def _preview(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)