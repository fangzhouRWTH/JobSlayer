"""Local append-only persistence for task-orchestration revisions."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from jobslayer.domain.models import ActorType
from jobslayer.orchestration import TaskPlanRevisionRecord, TaskPlanSnapshot


class TaskPlanJournalError(RuntimeError):
    """Raised when a plan history cannot be trusted or durably extended."""


class TaskPlanRevisionConflictError(TaskPlanJournalError):
    """Raised when an append is not the exact next plan revision."""


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _record_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_task_plan_revision_record(
    history: tuple[TaskPlanRevisionRecord, ...],
    snapshot: TaskPlanSnapshot,
    *,
    actor_type: ActorType,
    actor_id: str,
    operation: str,
    occurred_at: datetime | None = None,
) -> TaskPlanRevisionRecord:
    expected_revision = len(history) + 1
    if snapshot.revision != expected_revision:
        raise TaskPlanRevisionConflictError(
            f"expected task-plan revision {expected_revision}, got {snapshot.revision}"
        )
    if history and history[-1].plan_id != snapshot.plan_id:
        raise TaskPlanRevisionConflictError("task-plan append changed plan id")
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "record_id": f"plan-record-{uuid4().hex}",
        "plan_id": snapshot.plan_id,
        "sequence": snapshot.revision,
        "snapshot": snapshot.model_dump(mode="json"),
        "actor_type": actor_type.value,
        "actor_id": actor_id,
        "operation": operation,
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        "previous_hash": history[-1].record_hash if history else None,
        "record_hash": "0" * 64,
    }
    draft = TaskPlanRevisionRecord.model_validate(payload)
    unhashed = draft.model_dump(mode="json", exclude={"record_hash"})
    return draft.model_copy(update={"record_hash": _record_hash(unhashed)})


def verify_task_plan_revision_history(
    history: tuple[TaskPlanRevisionRecord, ...]
    | list[TaskPlanRevisionRecord],
) -> None:
    previous_hash: str | None = None
    plan_id: str | None = None
    for sequence, record in enumerate(history, start=1):
        if record.sequence != sequence:
            raise TaskPlanJournalError(
                f"unexpected task-plan sequence at position {sequence}"
            )
        if plan_id is None:
            plan_id = record.plan_id
        elif record.plan_id != plan_id:
            raise TaskPlanJournalError("task-plan history mixes plan ids")
        if record.previous_hash != previous_hash:
            raise TaskPlanJournalError(
                f"broken previous_hash at task-plan revision {sequence}"
            )
        unhashed = record.model_dump(mode="json", exclude={"record_hash"})
        if _record_hash(unhashed) != record.record_hash:
            raise TaskPlanJournalError(
                f"record hash mismatch at task-plan revision {sequence}"
            )
        previous_hash = record.record_hash


class LocalTaskPlanStore:
    """One append-only, atomically published JSONL history per task plan."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve(strict=False)
        self._write_lock = threading.Lock()

    def list_plan_ids(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        plan_ids: list[str] = []
        for path in sorted(self.root.glob("*.jsonl")):
            plan_id = path.stem
            history = self.history(plan_id)
            if history:
                plan_ids.append(plan_id)
        return tuple(plan_ids)

    def history(self, plan_id: str) -> tuple[TaskPlanRevisionRecord, ...]:
        path = self._path(plan_id)
        if not path.exists():
            return ()
        records: list[TaskPlanRevisionRecord] = []
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        raise TaskPlanJournalError(
                            f"blank task-plan journal line {line_number}"
                        )
                    try:
                        raw_record = json.loads(line)
                        if not isinstance(raw_record, dict):
                            raise ValueError("task-plan record must be an object")
                        supplied_hash = raw_record.get("record_hash")
                        unhashed = dict(raw_record)
                        unhashed.pop("record_hash", None)
                        if (
                            not isinstance(supplied_hash, str)
                            or _record_hash(unhashed) != supplied_hash
                        ):
                            raise TaskPlanJournalError(
                                f"record hash mismatch at task-plan revision {line_number}"
                            )
                        record = TaskPlanRevisionRecord.model_validate(raw_record)
                    except (json.JSONDecodeError, ValidationError) as exc:
                        raise TaskPlanJournalError(
                            f"invalid task-plan record at line {line_number}"
                        ) from exc
                    except ValueError as exc:
                        raise TaskPlanJournalError(
                            f"invalid task-plan record at line {line_number}"
                        ) from exc
                    records.append(record)
        except TaskPlanJournalError:
            raise
        except OSError as exc:
            raise TaskPlanJournalError("could not read task-plan history") from exc
        self._verify_revision_links(records)
        if records and records[0].plan_id != plan_id:
            raise TaskPlanJournalError("task-plan journal filename does not match plan id")
        return tuple(records)

    @staticmethod
    def _verify_revision_links(records: list[TaskPlanRevisionRecord]) -> None:
        previous_hash: str | None = None
        plan_id: str | None = None
        for sequence, record in enumerate(records, start=1):
            if record.sequence != sequence:
                raise TaskPlanJournalError(
                    f"unexpected task-plan sequence at position {sequence}"
                )
            if plan_id is None:
                plan_id = record.plan_id
            elif record.plan_id != plan_id:
                raise TaskPlanJournalError("task-plan history mixes plan ids")
            if record.previous_hash != previous_hash:
                raise TaskPlanJournalError(
                    f"broken previous_hash at task-plan revision {sequence}"
                )
            previous_hash = record.record_hash

    def append(
        self,
        snapshot: TaskPlanSnapshot,
        *,
        actor_type: ActorType,
        actor_id: str,
        operation: str,
    ) -> TaskPlanRevisionRecord:
        with self._write_lock:
            history = self.history(snapshot.plan_id)
            record = build_task_plan_revision_record(
                history,
                snapshot,
                actor_type=actor_type,
                actor_id=actor_id,
                operation=operation,
            )
            path = self._path(snapshot.plan_id)
            try:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                previous_content = path.read_bytes() if path.exists() else b""
                encoded = _canonical_json(record.model_dump(mode="json")) + b"\n"
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
                )
            except OSError as exc:
                raise TaskPlanJournalError(
                    "could not stage task-plan revision"
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
                raise TaskPlanJournalError(
                    "could not durably publish task-plan revision"
                ) from exc
            finally:
                temporary.unlink(missing_ok=True)
            return record

    def _path(self, plan_id: str) -> Path:
        if (
            not plan_id
            or plan_id in {".", ".."}
            or "/" in plan_id
            or "\\" in plan_id
            or not plan_id[0].isalnum()
            or any(not (character.isalnum() or character in "._-") for character in plan_id)
        ):
            raise TaskPlanJournalError("task-plan id is invalid")
        return self.root / f"{plan_id}.jsonl"

    @staticmethod
    def _write_all(descriptor: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("could not write complete task-plan journal")
            offset += written

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "LocalTaskPlanStore",
    "TaskPlanJournalError",
    "TaskPlanRevisionConflictError",
    "build_task_plan_revision_record",
    "verify_task_plan_revision_history",
]
