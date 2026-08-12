from __future__ import annotations

import hashlib
import json
import os
import threading
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from jobslayer.domain.models import ActorType, TaskState, TransitionRecord


class AuditIntegrityError(RuntimeError):
    """Raised when an audit journal is malformed or its hash chain is broken."""


class AuditJournal(Protocol):
    """Provider-neutral append/read port consumed by the workflow kernel."""

    def records_for(self, task_id: str) -> list[TransitionRecord]:
        """Return the ordered, integrity-checked history for one task."""

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
        """Append one transition or fail without publishing a partial record."""


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_transition_record(
    records: list[TransitionRecord] | tuple[TransitionRecord, ...],
    *,
    task_id: str,
    from_state: TaskState,
    to_state: TaskState,
    actor_type: ActorType,
    actor_id: str,
    reason: str,
    evidence_ids: tuple[str, ...] = (),
    occurred_at: datetime | None = None,
) -> TransitionRecord:
    """Build the next hash-linked record for a journal-owned sequence."""

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
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        "previous_hash": previous_hash,
    }
    payload["record_hash"] = "0" * 64
    draft = TransitionRecord.model_validate(payload)
    unhashed = draft.model_dump(mode="json", exclude={"record_hash"})
    return draft.model_copy(update={"record_hash": _record_hash(unhashed)})


def verify_transition_sequence(
    records: list[TransitionRecord] | tuple[TransitionRecord, ...],
) -> None:
    """Verify sequence, previous-hash, and record-hash integrity."""

    previous_hash: str | None = None
    for sequence, record in enumerate(records, start=1):
        if record.sequence != sequence:
            raise AuditIntegrityError(
                f"unexpected sequence at position {sequence}: {record.sequence}"
            )
        if record.previous_hash != previous_hash:
            raise AuditIntegrityError(
                f"broken previous_hash at transition position {sequence}"
            )
        unhashed = record.model_dump(mode="json", exclude={"record_hash"})
        if _record_hash(unhashed) != record.record_hash:
            raise AuditIntegrityError(
                f"record hash mismatch at transition position {sequence}"
            )
        previous_hash = record.record_hash


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

                records.append(record)
        verify_transition_sequence(records)
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
            record = build_transition_record(
                records,
                task_id=task_id,
                from_state=from_state,
                to_state=to_state,
                actor_type=actor_type,
                actor_id=actor_id,
                reason=reason,
                evidence_ids=evidence_ids,
            )

            self.path.parent.mkdir(parents=True, exist_ok=True)
            encoded = _canonical_json(record.model_dump(mode="json")) + b"\n"
            try:
                previous_content = self.path.read_bytes() if self.path.exists() else b""
            except OSError as exc:
                raise AuditIntegrityError(
                    f"could not read audit journal before append: {exc}"
                ) from exc
            try:
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    dir=self.path.parent,
                )
            except OSError as exc:
                raise AuditIntegrityError(
                    f"could not stage the next audit journal generation: {exc}"
                ) from exc
            temporary = Path(temporary_name)
            try:
                try:
                    try:
                        self._write_all(descriptor, previous_content)
                        self._write_all(descriptor, encoded)
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    os.replace(temporary, self.path)
                    self._fsync_directory(self.path.parent)
                except OSError as exc:
                    raise AuditIntegrityError(
                        "could not durably publish the next audit journal generation"
                    ) from exc
            finally:
                temporary.unlink(missing_ok=True)
            return record

    @staticmethod
    def _write_all(descriptor: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("could not persist the complete audit journal generation")
            offset += written

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
