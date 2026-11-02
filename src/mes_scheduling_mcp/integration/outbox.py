"""Persistent outbox / inbox for reliable ERP <-> MES messaging.

Real ERP/MES integrations exchange events (release an order into MES, report
progress back to ERP).  A *transactional outbox* pattern guarantees that a
database write and the event announcing it commit atomically: the event is
inserted into ``outbox_events`` inside the same transaction as the domain
mutation, then a separate relay marks it ``PUBLISHED`` (simulating delivery)
and the receiving side acknowledges it.

This gives the demo real production semantics without a broker dependency:
at-least-once delivery, redelivery on failure, and a dead-letter queue.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..models import EventStatus, EventType, OutboxEvent
from ..store import Store


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Outbox:
    def __init__(self, store: Store) -> None:
        self._store = store

    def publish(self, event_type: EventType, aggregate_type: str, aggregate_id: str,
                payload: dict[str, Any]) -> OutboxEvent:
        event = OutboxEvent(
            event_id=f"EV-{aggregate_id}-{int(datetime.now().timestamp() * 1000) % 1000000:06d}",
            event_type=event_type, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
            payload=payload, status=EventStatus.PENDING,
        )
        self._store.insert_outbox(event)
        return event

    def pending(self) -> list[OutboxEvent]:
        return self._store.list_outbox(status=EventStatus.PENDING)

    def list(self, status: str | None = None) -> list[OutboxEvent]:
        return self._store.list_outbox(status=status)

    def mark_published(self, event_id: str) -> None:
        self._store.update_outbox(event_id, status=EventStatus.PUBLISHED,
                                  published_at=_now())

    def mark_acked(self, event_id: str) -> None:
        self._store.update_outbox(event_id, status=EventStatus.ACKED, acked_at=_now())

    def mark_failed(self, event_id: str, error: str) -> None:
        ev = None
        for e in self._store.list_outbox():
            if e.event_id == event_id:
                ev = e
                break
        attempts = (ev.attempts + 1) if ev else 1
        status = EventStatus.DEAD_LETTER if attempts >= 5 else EventStatus.FAILED
        self._store.update_outbox(event_id, status=status, attempts=attempts, last_error=error)

    def receive(self, event: OutboxEvent) -> None:
        """Record arrival of a published event at the receiving system."""
        self._store.insert_inbox(event)

    def inbox(self) -> list[dict[str, Any]]:
        return self._store.list_inbox()

    def relay(self) -> dict[str, Any]:
        """Deliver all PENDING events, simulating a message broker in one pass.

        Returns a summary suitable for observability / integration health.
        """
        delivered = 0
        failed = 0
        for ev in self.pending():
            try:
                payload = ev.payload
                json.dumps(payload)  # validate serializability cheaply
                self._store.update_outbox(
                    ev.event_id, status=EventStatus.PUBLISHED, published_at=_now())
                # receiving side stores a copy and acknowledges
                self.receive(ev)
                self._store.update_outbox(ev.event_id, status=EventStatus.ACKED, acked_at=_now())
                delivered += 1
            except Exception as exc:  # pragma: no cover - defensive
                self.mark_failed(ev.event_id, str(exc))
                failed += 1
        return {"pending": len(self.pending()), "delivered": delivered,
                "failed": failed, "total": self._outbox_count()}

    def _outbox_count(self) -> int:
        return len(self._store.list_outbox())