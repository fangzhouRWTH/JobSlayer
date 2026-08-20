"""Append-only local persistence for TaskManager execution-run revisions."""

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
from jobslayer.task_manager.execution import (
    TaskManagerRunRevisionRecord,
    TaskManagerRunSnapshot,
)


class TaskManagerRunJournalError(RuntimeError):
    """Raised when an execution-run history cannot be trusted or appended."""


class TaskManagerRunRevisionConflictError(TaskManagerRunJournalError):
    """Raised when an append is not the exact next run revision."""


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_task_manager_run_record(
    history: tuple[TaskManagerRunRevisionRecord, ...],
    snapshot: TaskManagerRunSnapshot,
    *,
    actor_type: ActorType,
    actor_id: str,
    operation: str,
    node_id: str | None = None,
    occurred_at: datetime | None = None,
) -> TaskManagerRunRevisionRecord:
    expected = len(history) + 1
    if snapshot.revision != expected:
        raise TaskManagerRunRevisionConflictError(
            f"expected TaskManager run revision {expected}, got {snapshot.revision}"
        )
    if history:
        previous = history[-1].snapshot
        if (
            previous.run_id != snapshot.run_id
            or previous.plan_id != snapshot.plan_id
            or previous.plan_revision != snapshot.plan_revision
            or previous.plan_record_hash != snapshot.plan_record_hash
            or previous.execution_binding != snapshot.execution_binding
            or previous.created_at != snapshot.created_at
            or previous.created_by != snapshot.created_by
            or snapshot.updated_at < previous.updated_at
        ):
            raise TaskManagerRunRevisionConflictError(
                "TaskManager run append changed its immutable plan binding"
            )
        previous_definitions = tuple(
            (
                item.node,
                item.workflow_task_id,
                item.dependency_node_ids,
            )
            for item in previous.nodes
        )
        current_definitions = tuple(
            (
                item.node,
                item.workflow_task_id,
                item.dependency_node_ids,
            )
            for item in snapshot.nodes
        )
        if current_definitions != previous_definitions:
            raise TaskManagerRunRevisionConflictError(
                "TaskManager run append changed its immutable node graph"
            )
        for old, new in zip(previous.nodes, snapshot.nodes, strict=True):
            prefix = new.transition_history[: len(old.transition_history)]
            if prefix != old.transition_history:
                raise TaskManagerRunRevisionConflictError(
                    "TaskManager run append rewrote Kernel transition history"
                )
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "record_id": f"task-manager-run-record-{uuid4().hex}",
        "run_id": snapshot.run_id,
        "sequence": snapshot.revision,
        "snapshot": snapshot.model_dump(mode="json"),
        "actor_type": actor_type.value,
        "actor_id": actor_id,
        "operation": operation,
        "node_id": node_id,
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        "previous_hash": history[-1].record_hash if history else None,
        "record_hash": "0" * 64,
    }
    draft = TaskManagerRunRevisionRecord.model_validate(payload)
    unhashed = draft.model_dump(mode="json", exclude={"record_hash"})
    return draft.model_copy(update={"record_hash": _record_hash(unhashed)})


def verify_task_manager_run_history(
    history: tuple[TaskManagerRunRevisionRecord, ...]
    | list[TaskManagerRunRevisionRecord],
) -> None:
    previous_hash: str | None = None
    run_id: str | None = None
    for sequence, record in enumerate(history, start=1):
        if record.sequence != sequence:
            raise TaskManagerRunJournalError(
                f"unexpected TaskManager run sequence at position {sequence}"
            )
        if run_id is None:
            run_id = record.run_id
        elif record.run_id != run_id:
            raise TaskManagerRunJournalError("TaskManager run history mixes run ids")
        if record.previous_hash != previous_hash:
            raise TaskManagerRunJournalError(
                f"broken previous_hash at TaskManager run revision {sequence}"
            )
        # Preserve the exact field surface that was present when an older record
        # was written. Newly added optional snapshot fields must not retroactively
        # alter an append-only record's hash.
        unhashed = record.model_dump(
            mode="json",
            exclude={"record_hash"},
            exclude_unset=True,
        )
        if _record_hash(unhashed) != record.record_hash:
            raise TaskManagerRunJournalError(
                f"record hash mismatch at TaskManager run revision {sequence}"
            )
        previous_hash = record.record_hash


class LocalTaskManagerRunStore:
    """Atomically publish one complete hash-chain generation per execution run."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve(strict=False)
        self._write_lock = threading.Lock()

    def list_run_ids(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        run_ids = []
        for path in sorted(self.root.glob("*.jsonl")):
            history = self.history(path.stem)
            if history:
                run_ids.append(path.stem)
        return tuple(run_ids)

    def history(self, run_id: str) -> tuple[TaskManagerRunRevisionRecord, ...]:
        path = self._path(run_id)
        if not path.exists():
            return ()
        records: list[TaskManagerRunRevisionRecord] = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        raise TaskManagerRunJournalError(
                            f"blank TaskManager run journal line {line_number}"
                        )
                    try:
                        raw = json.loads(line)
                        if not isinstance(raw, dict):
                            raise ValueError("TaskManager run record must be an object")
                        supplied_hash = raw.get("record_hash")
                        unhashed = dict(raw)
                        unhashed.pop("record_hash", None)
                        if (
                            not isinstance(supplied_hash, str)
                            or _record_hash(unhashed) != supplied_hash
                        ):
                            raise TaskManagerRunJournalError(
                                "TaskManager run record hash mismatch at "
                                f"revision {line_number}"
                            )
                        records.append(TaskManagerRunRevisionRecord.model_validate(raw))
                    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                        raise TaskManagerRunJournalError(
                            f"invalid TaskManager run record at line {line_number}"
                        ) from exc
        except TaskManagerRunJournalError:
            raise
        except OSError as exc:
            raise TaskManagerRunJournalError(
                "could not read TaskManager run history"
            ) from exc
        verify_task_manager_run_history(records)
        if records and records[0].run_id != run_id:
            raise TaskManagerRunJournalError(
                "TaskManager run journal filename does not match run id"
            )
        return tuple(records)

    def append(
        self,
        snapshot: TaskManagerRunSnapshot,
        *,
        actor_type: ActorType,
        actor_id: str,
        operation: str,
        node_id: str | None = None,
    ) -> TaskManagerRunRevisionRecord:
        with self._write_lock:
            history = self.history(snapshot.run_id)
            record = build_task_manager_run_record(
                history,
                snapshot,
                actor_type=actor_type,
                actor_id=actor_id,
                operation=operation,
                node_id=node_id,
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
                raise TaskManagerRunJournalError(
                    "could not stage TaskManager run revision"
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
                raise TaskManagerRunJournalError(
                    "could not durably publish TaskManager run revision"
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
            raise TaskManagerRunJournalError("TaskManager run id is invalid")
        return self.root / f"{run_id}.jsonl"

    @staticmethod
    def _write_all(descriptor: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("could not write complete TaskManager run journal")
            offset += written

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "build_task_manager_run_record",
    "LocalTaskManagerRunStore",
    "TaskManagerRunJournalError",
    "TaskManagerRunRevisionConflictError",
    "verify_task_manager_run_history",
]
