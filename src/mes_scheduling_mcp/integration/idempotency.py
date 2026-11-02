"""Idempotency guard for all write operations crossing the API boundary.

Manufacturing integrations are vulnerable to duplicate deliveries (a client
retrying after a timeout, a queue redelivering a message).  Every mutating MCP
tool therefore accepts an optional ``idempotency_key``; the server records the
key and replays the previously returned result instead of applying the write
twice.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable

from ..store import Store


class IdempotencyGuard:
    """Deduplicates side-effecting calls by a caller-supplied key."""

    def __init__(self, store: Store) -> None:
        self._store = store

    @staticmethod
    def new_key() -> str:
        return uuid.uuid4().hex

    def run(self, key: str | None, operation: str, fn: Callable[[], Any]) -> dict[str, Any]:
        """Execute ``fn`` exactly once per ``key``.

        If ``key`` is absent, generate one internally so callers that omit the
        key are still safe within a single request scope; if a matching key is
        found, return the previously stored result with ``replayed=True``.
        """
        key = key or self.new_key()
        cached = self._store.has_idempotency_key(key)
        if cached is not None:
            return {"replayed": True, "result": cached}
        result = fn()
        self._store.record_idempotency(key, operation, result)
        return {"replayed": False, "result": result}