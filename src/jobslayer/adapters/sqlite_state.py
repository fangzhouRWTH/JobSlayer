from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from importlib import resources
import json
from pathlib import Path
import sqlite3
from typing import Any

from pydantic import ValidationError

from jobslayer.application.run_records import (
    RunRecord,
    RunRecordStage,
    build_run_record,
    validate_run_stage_sequence,
)
from jobslayer.domain.models import (
    ActorType,
    ArtifactManifest,
    TaskState,
    TransitionRecord,
)
from jobslayer.persistence import (
    OutboxEvent,
    StateConflictError,
    StateIntegrityError,
    StateStoreError,
)
from jobslayer.persistence.transactional_journal import TransactionalAuditJournal
from jobslayer.workflow.journal import (
    AuditIntegrityError,
    verify_transition_sequence,
)


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class SqliteStateTransaction:
    """Explicit single-task/run SQLite transaction with staged kernel writes."""

    def __init__(
        self,
        store: SqliteControlPlaneStore,
        *,
        task_id: str,
        run_id: str,
        expected_task_sequence: int,
        expected_run_sequence: int,
    ):
        if expected_task_sequence < 0 or expected_run_sequence < 0:
            raise ValueError("expected sequences must not be negative")
        self.store = store
        self.task_id = task_id
        self.run_id = run_id
        self.expected_task_sequence = expected_task_sequence
        self.expected_run_sequence = expected_run_sequence
        self.connection: sqlite3.Connection | None = None
        self.journal: TransactionalAuditJournal
        self._records: list[RunRecord] = []
        self._initial_run_count = 0
        self._artifacts: list[ArtifactManifest] = []
        self._events: list[OutboxEvent] = []
        self._committed = False

    def __enter__(self) -> SqliteStateTransaction:
        connection = self.store._connect()
        self.connection = connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            task_history = self.store._task_history(connection, self.task_id)
            run_history = self.store._run_history(connection, self.run_id)
            if len(task_history) != self.expected_task_sequence:
                raise StateConflictError(
                    "task sequence changed before transaction acquisition"
                )
            if len(run_history) != self.expected_run_sequence:
                raise StateConflictError(
                    "run sequence changed before transaction acquisition"
                )
            if run_history and run_history[0].task_id != self.task_id:
                raise StateIntegrityError("run belongs to a different task")
            self.journal = TransactionalAuditJournal(self.task_id, task_history)
            self._records = list(run_history)
            self._initial_run_count = len(run_history)
            return self
        except Exception:
            connection.rollback()
            connection.close()
            self.connection = None
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.connection is None:
            return
        try:
            if not self._committed:
                self.connection.rollback()
        finally:
            self.connection.close()
            self.connection = None

    def run_records(self) -> tuple[RunRecord, ...]:
        self._require_open()
        return tuple(self._records)

    def append_run_record(
        self,
        *,
        stage: RunRecordStage,
        payload: dict[str, Any],
    ) -> RunRecord:
        self._require_open()
        stages = tuple(record.stage for record in self._records) + (stage,)
        validate_run_stage_sequence(stages)
        record = build_run_record(
            self._records,
            run_id=self.run_id,
            task_id=self.task_id,
            stage=stage,
            payload=payload,
        )
        self._records.append(record)
        return record

    def add_artifact(self, manifest: ArtifactManifest) -> None:
        self._require_open()
        if manifest.task_id != self.task_id or manifest.run_id != self.run_id:
            raise StateIntegrityError("artifact does not belong to this transaction")
        self._artifacts.append(manifest)

    def append_transition_record(self, record: TransitionRecord) -> None:
        self._require_open()
        self.journal.stage_record(record)

    def enqueue(self, event: OutboxEvent) -> None:
        self._require_open()
        if event.task_id != self.task_id or event.run_id != self.run_id:
            raise StateIntegrityError("outbox event does not belong to this transaction")
        if event.published_at is not None:
            raise StateIntegrityError("new outbox event must be unpublished")
        self._events.append(event)

    def commit(self) -> None:
        connection = self._require_open()
        if self._committed:
            raise StateConflictError("transaction was already committed")
        has_truth_mutation = bool(
            self.journal.staged
            or len(self._records) > self._initial_run_count
            or self._artifacts
        )
        if has_truth_mutation and not self._events:
            raise StateIntegrityError(
                "control-plane metadata changes require an atomic outbox event"
            )
        try:
            for record in self.journal.staged:
                connection.execute(
                    "INSERT INTO workflow_transitions "
                    "(task_id, sequence, record_hash, previous_hash, record_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        record.task_id,
                        record.sequence,
                        record.record_hash,
                        record.previous_hash,
                        _json_text(record.model_dump(mode="json")),
                    ),
                )
            for record in self._records[self._initial_run_count :]:
                connection.execute(
                    "INSERT INTO run_records "
                    "(run_id, sequence, task_id, stage, record_hash, previous_hash, "
                    "record_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.run_id,
                        record.sequence,
                        record.task_id,
                        record.stage.value,
                        record.record_hash,
                        record.previous_hash,
                        _json_text(record.model_dump(mode="json")),
                    ),
                )
            for manifest in self._artifacts:
                connection.execute(
                    "INSERT INTO artifact_manifests "
                    "(artifact_id, task_id, run_id, artifact_type, sha256, "
                    "manifest_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        manifest.artifact_id,
                        manifest.task_id,
                        manifest.run_id,
                        manifest.artifact_type,
                        manifest.sha256,
                        _json_text(manifest.model_dump(mode="json")),
                    ),
                )
            for event in self._events:
                connection.execute(
                    "INSERT INTO outbox_events "
                    "(event_id, topic, task_id, run_id, event_json, created_at, "
                    "published_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (
                        event.event_id,
                        event.topic,
                        event.task_id,
                        event.run_id,
                        _json_text(event.model_dump(mode="json")),
                        event.created_at.isoformat(),
                    ),
                )
            connection.commit()
            self._committed = True
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise StateConflictError(
                f"transaction conflicted with committed metadata: {exc}"
            ) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise StateStoreError(f"could not commit control-plane state: {exc}") from exc

    def _require_open(self) -> sqlite3.Connection:
        if self.connection is None:
            raise StateStoreError("state transaction is not open")
        return self.connection


class SqliteControlPlaneStore:
    """Cross-platform transactional development adapter using stdlib SQLite."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve(strict=False)

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = self._connect()
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            migration_root = resources.files("jobslayer.persistence.migrations")
            for migration in sorted(
                item for item in migration_root.iterdir() if item.name.endswith(".sql")
            ):
                content = migration.read_text(encoding="utf-8")
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                existing = connection.execute(
                    "SELECT sha256 FROM schema_migrations WHERE version = ?",
                    (migration.name,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != digest:
                        raise StateIntegrityError(
                            f"applied migration checksum changed: {migration.name}"
                        )
                    continue
                values = tuple(
                    value.replace("'", "''")
                    for value in (
                        migration.name,
                        digest,
                        datetime.now(UTC).isoformat(),
                    )
                )
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + content
                    + "\nINSERT INTO schema_migrations "
                    "(version, sha256, applied_at) VALUES "
                    f"('{values[0]}', '{values[1]}', '{values[2]}');\n"
                    "COMMIT;"
                )
            connection.commit()
        except (OSError, sqlite3.DatabaseError) as exc:
            connection.rollback()
            if isinstance(exc, StateIntegrityError):
                raise
            raise StateStoreError(f"could not migrate control-plane store: {exc}") from exc
        finally:
            connection.close()

    def transaction(
        self,
        *,
        task_id: str,
        run_id: str,
        expected_task_sequence: int,
        expected_run_sequence: int,
    ) -> SqliteStateTransaction:
        if not task_id or not run_id:
            raise ValueError("task_id and run_id must not be blank")
        return SqliteStateTransaction(
            self,
            task_id=task_id,
            run_id=run_id,
            expected_task_sequence=expected_task_sequence,
            expected_run_sequence=expected_run_sequence,
        )

    def task_history(self, task_id: str) -> tuple[TransitionRecord, ...]:
        with self._connection() as connection:
            return self._task_history(connection, task_id)

    def run_history(self, run_id: str) -> tuple[RunRecord, ...]:
        with self._connection() as connection:
            return self._run_history(connection, run_id)

    def artifacts_for_run(self, run_id: str) -> tuple[ArtifactManifest, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT manifest_json FROM artifact_manifests "
                    "WHERE run_id = ? ORDER BY artifact_id",
                    (run_id,),
                ).fetchall()
            return tuple(ArtifactManifest.model_validate_json(row[0]) for row in rows)
        except (sqlite3.DatabaseError, ValidationError) as exc:
            raise StateIntegrityError("artifact metadata is invalid") from exc

    def list_run_ids(self, *, limit: int = 1000) -> tuple[str, ...]:
        if limit < 1 or limit > 10_000:
            raise ValueError("run list limit must be between 1 and 10000")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT run_id FROM ("
                    "SELECT run_id FROM run_records UNION "
                    "SELECT run_id FROM artifact_manifests UNION "
                    "SELECT run_id FROM outbox_events"
                    ") ORDER BY run_id LIMIT ?",
                    (limit,),
                ).fetchall()
            return tuple(str(row[0]) for row in rows)
        except sqlite3.DatabaseError as exc:
            raise StateStoreError("could not list persisted runs") from exc

    def events_for_run(self, run_id: str) -> tuple[OutboxEvent, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT event_json FROM outbox_events WHERE run_id = ? "
                    "ORDER BY commit_order",
                    (run_id,),
                ).fetchall()
            return tuple(OutboxEvent.model_validate_json(row[0]) for row in rows)
        except (sqlite3.DatabaseError, ValidationError) as exc:
            raise StateIntegrityError("persisted run events are invalid") from exc

    def pending_outbox(self, *, limit: int = 100) -> tuple[OutboxEvent, ...]:
        if limit < 1 or limit > 10_000:
            raise ValueError("outbox limit must be between 1 and 10000")
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT event_json FROM outbox_events WHERE published_at IS NULL "
                    "ORDER BY commit_order LIMIT ?",
                    (limit,),
                ).fetchall()
            return tuple(OutboxEvent.model_validate_json(row[0]) for row in rows)
        except (sqlite3.DatabaseError, ValidationError) as exc:
            raise StateIntegrityError("outbox metadata is invalid") from exc

    def mark_outbox_published(
        self,
        event_id: str,
        *,
        published_at: datetime | None = None,
    ) -> bool:
        when = published_at or datetime.now(UTC)
        if when.tzinfo is None:
            raise ValueError("published_at must include a timezone")
        try:
            with self._connection() as connection:
                existing = connection.execute(
                    "SELECT event_json, published_at FROM outbox_events "
                    "WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if existing is None:
                    return False
                if existing[1] is None:
                    event = OutboxEvent.model_validate_json(existing[0]).model_copy(
                        update={"published_at": when}
                    )
                    connection.execute(
                        "UPDATE outbox_events SET published_at = ?, event_json = ? "
                        "WHERE event_id = ? AND published_at IS NULL",
                        (
                            when.isoformat(),
                            _json_text(event.model_dump(mode="json")),
                            event_id,
                        ),
                    )
                    connection.commit()
                return True
        except (sqlite3.DatabaseError, ValidationError) as exc:
            raise StateStoreError("could not mark outbox event published") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=10,
                isolation_level=None,
            )
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except sqlite3.DatabaseError as exc:
            raise StateStoreError(f"could not open control-plane store: {exc}") from exc

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _task_history(
        connection: sqlite3.Connection,
        task_id: str,
    ) -> tuple[TransitionRecord, ...]:
        try:
            rows = connection.execute(
                "SELECT record_json FROM workflow_transitions "
                "WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
            records = tuple(
                TransitionRecord.model_validate_json(row[0]) for row in rows
            )
            verify_transition_sequence(records)
            return records
        except (sqlite3.DatabaseError, ValidationError, AuditIntegrityError) as exc:
            raise StateIntegrityError("task history is invalid") from exc

    @staticmethod
    def _run_history(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> tuple[RunRecord, ...]:
        try:
            rows = connection.execute(
                "SELECT record_json FROM run_records WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
            records = tuple(RunRecord.model_validate_json(row[0]) for row in rows)
            previous_hash: str | None = None
            for sequence, record in enumerate(records, start=1):
                if record.sequence != sequence or record.previous_hash != previous_hash:
                    raise StateIntegrityError("run record chain is not contiguous")
                previous_hash = record.record_hash
            validate_run_stage_sequence(tuple(record.stage for record in records))
            return records
        except (sqlite3.DatabaseError, ValidationError, ValueError) as exc:
            if isinstance(exc, StateIntegrityError):
                raise
            raise StateIntegrityError("run history is invalid") from exc
