"""Append-only local persistence for TaskManager serial coordinator cursors."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from uuid import uuid4

from pydantic import ValidationError

from jobslayer.domain.models import ActorType
from jobslayer.task_manager.coordinator import (
    TaskManagerCoordinatorRevisionRecord,
    TaskManagerCoordinatorSnapshot,
)


class TaskManagerCoordinatorJournalError(RuntimeError):
    """Raised when a coordinator cursor history cannot be trusted."""


class TaskManagerCoordinatorRevisionConflictError(
    TaskManagerCoordinatorJournalError
):
    """Raised when an append is not the exact next cursor revision."""


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_task_manager_coordinator_record(
    history: tuple[TaskManagerCoordinatorRevisionRecord, ...],
    snapshot: TaskManagerCoordinatorSnapshot,
    *,
    actor_type: ActorType,
    actor_id: str,
    operation: str,
    occurred_at: datetime | None = None,
) -> TaskManagerCoordinatorRevisionRecord:
    expected = len(history) + 1
    if snapshot.revision != expected:
        raise TaskManagerCoordinatorRevisionConflictError(
            f"expected coordinator revision {expected}, got {snapshot.revision}"
        )
    if history:
        previous = history[-1].snapshot
        if (
            previous.run_id != snapshot.run_id
            or previous.created_at != snapshot.created_at
            or snapshot.updated_at < previous.updated_at
            or snapshot.run_revision < previous.run_revision
        ):
            raise TaskManagerCoordinatorRevisionConflictError(
                "coordinator append changed immutable identity or moved backwards"
            )
        if (
            previous.pending_intent is not None
            and snapshot.pending_intent is not None
            and previous.pending_intent != snapshot.pending_intent
        ):
            raise TaskManagerCoordinatorRevisionConflictError(
                "coordinator append replaced an unresolved intent"
            )
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "record_id": f"task-manager-coordinator-record-{uuid4().hex}",
        "run_id": snapshot.run_id,
        "sequence": snapshot.revision,
        "snapshot": snapshot.model_dump(mode="json"),
        "actor_type": actor_type.value,
        "actor_id": actor_id,
        "operation": operation,
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        "previous_hash": history[-1].record_hash if history else None,
        "record_hash": "0" * 64,
    }
    draft = TaskManagerCoordinatorRevisionRecord.model_validate(payload)
    unhashed = draft.model_dump(mode="json", exclude={"record_hash"})
    return draft.model_copy(update={"record_hash": _record_hash(unhashed)})


def verify_task_manager_coordinator_history(
    history: tuple[TaskManagerCoordinatorRevisionRecord, ...]
    | list[TaskManagerCoordinatorRevisionRecord],
) -> None:
    previous_hash: str | None = None
    run_id: str | None = None
    for sequence, record in enumerate(history, start=1):
        if record.sequence != sequence:
            raise TaskManagerCoordinatorJournalError(
                f"unexpected coordinator sequence at position {sequence}"
            )
        if run_id is None:
            run_id = record.run_id
        elif record.run_id != run_id:
            raise TaskManagerCoordinatorJournalError(
                "coordinator history mixes run ids"
            )
        if record.previous_hash != previous_hash:
            raise TaskManagerCoordinatorJournalError(
                f"broken previous_hash at coordinator revision {sequence}"
            )
        unhashed = record.model_dump(
            mode="json",
            exclude={"record_hash"},
            exclude_unset=True,
        )
        if _record_hash(unhashed) != record.record_hash:
            raise TaskManagerCoordinatorJournalError(
                f"record hash mismatch at coordinator revision {sequence}"
            )
        previous_hash = record.record_hash


class LocalTaskManagerCoordinatorStore:
    """Atomically publish one complete coordinator hash chain per run."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve(strict=False)
        self._write_lock = threading.Lock()

    def history(
        self,
        run_id: str,
    ) -> tuple[TaskManagerCoordinatorRevisionRecord, ...]:
        path = self._path(run_id)
        if not path.exists():
            return ()
        records: list[TaskManagerCoordinatorRevisionRecord] = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        raise TaskManagerCoordinatorJournalError(
                            f"blank coordinator journal line {line_number}"
                        )
                    raw = json.loads(line)
                    if not isinstance(raw, dict):
                        raise ValueError("coordinator record must be an object")
                    supplied_hash = raw.get("record_hash")
                    unhashed = dict(raw)
                    unhashed.pop("record_hash", None)
                    if (
                        not isinstance(supplied_hash, str)
                        or _record_hash(unhashed) != supplied_hash
                    ):
                        raise TaskManagerCoordinatorJournalError(
                            "coordinator record hash mismatch at "
                            f"revision {line_number}"
                        )
                    records.append(
                        TaskManagerCoordinatorRevisionRecord.model_validate(raw)
                    )
        except TaskManagerCoordinatorJournalError:
            raise
        except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise TaskManagerCoordinatorJournalError(
                "could not read a valid coordinator history"
            ) from exc
        verify_task_manager_coordinator_history(records)
        if records and records[0].run_id != run_id:
            raise TaskManagerCoordinatorJournalError(
                "coordinator journal filename does not match run id"
            )
        return tuple(records)

    def append(
        self,
        snapshot: TaskManagerCoordinatorSnapshot,
        *,
        actor_type: ActorType,
        actor_id: str,
        operation: str,
    ) -> TaskManagerCoordinatorRevisionRecord:
        with self._write_lock:
            history = self.history(snapshot.run_id)
            record = build_task_manager_coordinator_record(
                history,
                snapshot,
                actor_type=actor_type,
                actor_id=actor_id,
                operation=operation,
            )
            path = self._path(snapshot.run_id)
            try:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                previous_content = path.read_bytes() if path.exists() else b""
                encoded = _canonical_json(record.model_dump(mode="json")) + b"\n"
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
                )
            except OSError as exc:
                raise TaskManagerCoordinatorJournalError(
                    "could not stage coordinator revision"
                ) from exc
            temporary = Path(temporary_name)
            try:
                try:
                    self._write_all(descriptor, previous_content)
                    self._write_all(descriptor, encoded)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.replace(temporary, path)
                if os.name != "nt":
                    path.chmod(0o600)
                    self._fsync_directory(path.parent)
            except OSError as exc:
                raise TaskManagerCoordinatorJournalError(
                    "could not durably publish coordinator revision"
                ) from exc
            finally:
                temporary.unlink(missing_ok=True)
            return record

    def _path(self, run_id: str) -> Path:
        if (
            not run_id
            or run_id in {".", ".."}
            or "/" in run_id
            or "\\" in run_id
            or not run_id[0].isalnum()
            or any(
                not (character.isalnum() or character in "._-")
                for character in run_id
            )
        ):
            raise TaskManagerCoordinatorJournalError("coordinator run id is invalid")
        return self.root / f"{run_id}.jsonl"

    @staticmethod
    def _write_all(descriptor: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("could not write complete coordinator journal")
            offset += written

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "build_task_manager_coordinator_record",
    "LocalTaskManagerCoordinatorStore",
    "TaskManagerCoordinatorJournalError",
    "TaskManagerCoordinatorRevisionConflictError",
    "verify_task_manager_coordinator_history",
]
