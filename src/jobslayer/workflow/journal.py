from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jobslayer.domain.models import ActorType, TaskState, TransitionRecord


class AuditIntegrityError(RuntimeError):
    """Raised when an audit journal is malformed or its hash chain is broken."""


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


class JsonlAuditJournal:
    """A local append-only transition journal with a per-file hash chain.

    The Phase 0 implementation serializes writers within one process. A durable
    database adapter must provide equivalent transactional sequencing before the
    system supports multiple controller processes.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._write_lock = threading.Lock()

    def read_all(self) -> list[TransitionRecord]:
        if not self.path.exists():
            return []

        records: list[TransitionRecord] = []
        previous_hash: str | None = None
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise AuditIntegrityError(
                        f"blank line at journal line {line_number}"
                    )
                try:
                    raw = json.loads(line)
                    record = TransitionRecord.model_validate(raw)
                except (json.JSONDecodeError, ValidationError) as exc:
                    raise AuditIntegrityError(
                        f"invalid record at journal line {line_number}: {exc}"
                    ) from exc

                if record.sequence != line_number:
                    raise AuditIntegrityError(
                        f"unexpected sequence at line {line_number}: {record.sequence}"
                    )
                if record.previous_hash != previous_hash:
                    raise AuditIntegrityError(
                        f"broken previous_hash at journal line {line_number}"
                    )
                unhashed = record.model_dump(mode="json", exclude={"record_hash"})
                if _record_hash(unhashed) != record.record_hash:
                    raise AuditIntegrityError(
                        f"record hash mismatch at journal line {line_number}"
                    )
                records.append(record)
                previous_hash = record.record_hash
        return records

    def records_for(self, task_id: str) -> list[TransitionRecord]:
        return [record for record in self.read_all() if record.task_id == task_id]

    def append_transition(
        self,
        *,
        task_id: str,
        from_state: TaskState,
        to_state: TaskState,
        actor_type: ActorType,
        actor_id: str,
        reason: str,
        evidence_ids: tuple[str, ...] = (),
    ) -> TransitionRecord:
        with self._write_lock:
            records = self.read_all()
            previous_hash = records[-1].record_hash if records else None
            payload: dict[str, Any] = {
                "schema_version": "1.0",
                "task_id": task_id,
                "sequence": len(records) + 1,
                "from_state": from_state.value,
                "to_state": to_state.value,
                "actor_type": actor_type.value,
                "actor_id": actor_id,
                "reason": reason,
                "evidence_ids": list(evidence_ids),
                "occurred_at": datetime.now(UTC).isoformat(),
                "previous_hash": previous_hash,
            }
            # Normalize values (notably UTC datetimes) through the contract before
            # hashing so a later parse produces exactly the same canonical bytes.
            payload["record_hash"] = "0" * 64
            draft = TransitionRecord.model_validate(payload)
            unhashed = draft.model_dump(mode="json", exclude={"record_hash"})
            record = draft.model_copy(update={"record_hash": _record_hash(unhashed)})

            self.path.parent.mkdir(parents=True, exist_ok=True)
            encoded = _canonical_json(record.model_dump(mode="json")) + b"\n"
            descriptor = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return record
