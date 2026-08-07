from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from jobslayer.domain.models import RunEvent


TERMINAL_EVENT_TYPES = frozenset(
    {"run.completed", "run.failed", "run.cancelled", "run.timed_out"}
)


class RunEventIntegrityError(RuntimeError):
    pass


def _event_hash(event: RunEvent) -> str:
    payload = event.model_dump(mode="json", exclude={"content_hash"})
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class RunEventBuffer:
    """Thread-safe append-only normalized events for one run."""

    def __init__(self, run_id: str):
        if not run_id:
            raise ValueError("run id must not be blank")
        self.run_id = run_id
        self._events: list[RunEvent] = []
        self._lock = threading.Lock()

    def append(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timestamp: datetime | None = None,
    ) -> RunEvent:
        with self._lock:
            if self._events and self._events[-1].event_type in TERMINAL_EVENT_TYPES:
                raise RunEventIntegrityError("cannot append after a terminal run event")
            draft = RunEvent(
                event_id=f"event-{uuid4().hex}",
                run_id=self.run_id,
                sequence=len(self._events) + 1,
                event_type=event_type,
                timestamp=timestamp or datetime.now(UTC),
                payload=payload or {},
            )
            event = draft.model_copy(update={"content_hash": _event_hash(draft)})
            self._events.append(event)
            return event

    def events(self, *, after_sequence: int = 0) -> tuple[RunEvent, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        with self._lock:
            return tuple(
                event for event in self._events if event.sequence > after_sequence
            )

    def is_terminal(self) -> bool:
        with self._lock:
            return bool(
                self._events
                and self._events[-1].event_type in TERMINAL_EVENT_TYPES
            )

    def verify(self) -> None:
        with self._lock:
            for expected_sequence, event in enumerate(self._events, start=1):
                if event.run_id != self.run_id or event.sequence != expected_sequence:
                    raise RunEventIntegrityError("run event sequence or ownership is invalid")
                if event.content_hash != _event_hash(event):
                    raise RunEventIntegrityError("run event content hash mismatch")
                if (
                    event.event_type in TERMINAL_EVENT_TYPES
                    and expected_sequence != len(self._events)
                ):
                    raise RunEventIntegrityError("terminal event is not the final event")
