from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Protocol

from pydantic import Field, ValidationError, model_validator

from jobslayer.domain.models import DomainModel


class RunRecordError(RuntimeError):
    """Raised when the append-only local run ledger cannot be trusted."""


class RunRecordStage(str, Enum):
    EXECUTION = "execution"
    IMPLEMENTATION_REVIEW = "implementation_review"
    DECISION_APPLICATION = "decision_application"
    SOURCE_INTEGRATION = "source_integration"
    WORKSPACE_CLEANUP = "workspace_cleanup"


def validate_run_stage_sequence(stages: tuple[RunRecordStage, ...]) -> None:
    allowed = (
        (),
        (RunRecordStage.EXECUTION,),
        (RunRecordStage.EXECUTION, RunRecordStage.IMPLEMENTATION_REVIEW),
        (
            RunRecordStage.EXECUTION,
            RunRecordStage.IMPLEMENTATION_REVIEW,
            RunRecordStage.DECISION_APPLICATION,
        ),
        (
            RunRecordStage.EXECUTION,
            RunRecordStage.IMPLEMENTATION_REVIEW,
            RunRecordStage.DECISION_APPLICATION,
            RunRecordStage.SOURCE_INTEGRATION,
        ),
        (
            RunRecordStage.EXECUTION,
            RunRecordStage.IMPLEMENTATION_REVIEW,
            RunRecordStage.DECISION_APPLICATION,
            RunRecordStage.SOURCE_INTEGRATION,
            RunRecordStage.WORKSPACE_CLEANUP,
        ),
    )
    if stages not in allowed:
        raise RunRecordError("run ledger stage sequence is invalid or already final")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class RunRecord(DomainModel):
    """One hash-linked operational snapshot; workflow state remains in the kernel."""

    schema_version: str = "1.0"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    stage: RunRecordStage
    payload: dict[str, Any]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime
    previous_hash: str | None = None
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hashes(self) -> RunRecord:
        if hashlib.sha256(_canonical_json(self.payload)).hexdigest() != self.payload_sha256:
            raise ValueError("run record payload hash mismatch")
        unhashed = self.model_dump(mode="json", exclude={"record_hash"})
        if hashlib.sha256(_canonical_json(unhashed)).hexdigest() != self.record_hash:
            raise ValueError("run record hash mismatch")
        return self


# Compatibility name for Phase 0 callers; the record contract itself is
# provider-neutral and is also used by transactional adapters.
LocalRunRecord = RunRecord


class RunLedger(Protocol):
    """Provider-neutral ordered operational record port for one run."""

    def read_all(self) -> tuple[RunRecord, ...]:
        """Return the integrity-checked records for this run."""

    def append(
        self,
        *,
        task_id: str,
        stage: RunRecordStage,
        payload: dict[str, Any],
    ) -> RunRecord:
        """Append one valid next-stage record atomically."""


def build_run_record(
    records: tuple[RunRecord, ...] | list[RunRecord],
    *,
    run_id: str,
    task_id: str,
    stage: RunRecordStage,
    payload: dict[str, Any],
    recorded_at: datetime | None = None,
) -> RunRecord:
    """Build the next hash-linked operational record for one run."""

    payload_copy = json.loads(_canonical_json(payload))
    raw: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "task_id": task_id,
        "sequence": len(records) + 1,
        "stage": stage.value,
        "payload": payload_copy,
        "payload_sha256": hashlib.sha256(_canonical_json(payload_copy)).hexdigest(),
        "recorded_at": (recorded_at or datetime.now(UTC)).isoformat().replace(
            "+00:00", "Z"
        ),
        "previous_hash": records[-1].record_hash if records else None,
    }
    raw["record_hash"] = hashlib.sha256(_canonical_json(raw)).hexdigest()
    return RunRecord.model_validate(raw)


class LocalRunLedger:
    """Append execution/review snapshots without becoming workflow authority."""

    def __init__(self, path: str | Path, *, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        self._lock = threading.Lock()

    def read_all(self) -> tuple[RunRecord, ...]:
        if not self.path.exists():
            return ()
        records: list[RunRecord] = []
        previous_hash: str | None = None
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        raise RunRecordError(
                            f"blank line in run ledger at line {line_number}"
                        )
                    try:
                        record = RunRecord.model_validate_json(line)
                    except ValidationError as exc:
                        raise RunRecordError(
                            f"invalid run record at line {line_number}: {exc}"
                        ) from exc
                    if record.run_id != self.run_id:
                        raise RunRecordError("run ledger contains a different run id")
                    if record.sequence != line_number:
                        raise RunRecordError("run ledger sequence is not contiguous")
                    if record.previous_hash != previous_hash:
                        raise RunRecordError("run ledger previous hash is broken")
                    records.append(record)
                    previous_hash = record.record_hash
        except OSError as exc:
            raise RunRecordError(f"could not read run ledger: {exc}") from exc
        self._validate_stage_sequence(tuple(records))
        return tuple(records)

    def append(
        self,
        *,
        task_id: str,
        stage: RunRecordStage,
        payload: dict[str, Any],
    ) -> RunRecord:
        with self._lock:
            records = self.read_all()
            if records and records[0].task_id != task_id:
                raise RunRecordError("run ledger belongs to a different task")
            stages = tuple(record.stage for record in records) + (stage,)
            self._validate_stage_sequence_values(stages)
            record = build_run_record(
                records,
                run_id=self.run_id,
                task_id=task_id,
                stage=stage,
                payload=payload,
            )
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            encoded = _canonical_json(record.model_dump(mode="json")) + b"\n"
            try:
                previous_content = self.path.read_bytes() if self.path.exists() else b""
            except OSError as exc:
                raise RunRecordError(
                    f"could not read run ledger before append: {exc}"
                ) from exc
            try:
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    dir=self.path.parent,
                )
            except OSError as exc:
                raise RunRecordError(
                    f"could not stage the next run ledger generation: {exc}"
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
                    raise RunRecordError(
                        "could not durably publish the next run ledger generation"
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
                raise OSError("could not persist the complete run ledger generation")
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

    @classmethod
    def _validate_stage_sequence(
        cls, records: tuple[RunRecord, ...]
    ) -> None:
        cls._validate_stage_sequence_values(tuple(record.stage for record in records))

    @staticmethod
    def _validate_stage_sequence_values(stages: tuple[RunRecordStage, ...]) -> None:
        validate_run_stage_sequence(stages)
