"""PostgreSQL implementation of the transactional control-plane state port."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from importlib import resources
import json
import re
from typing import Any

from pydantic import ValidationError

from jobslayer.application.run_records import (
    RunRecord,
    RunRecordStage,
    build_run_record,
    validate_run_stage_sequence,
)
from jobslayer.domain.models import ArtifactManifest, TransitionRecord
from jobslayer.persistence import (
    OutboxEvent,
    StateConflictError,
    StateIntegrityError,
    StateStoreError,
)
from jobslayer.persistence.transactional_journal import TransactionalAuditJournal
from jobslayer.workflow.journal import AuditIntegrityError, verify_transition_sequence

try:  # Optional until the PostgreSQL adapter is selected by deployment config.
    import psycopg
except ModuleNotFoundError:  # pragma: no cover - exercised by the no-driver test
    psycopg = None


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _driver():
    if psycopg is None:
        raise StateStoreError(
            "PostgreSQL support requires the optional 'postgres' dependency"
        )
    return psycopg


class PostgresStateTransaction:
    """One task/run transaction protected by transaction-scoped advisory locks."""

    def __init__(
        self,
        store: PostgresControlPlaneStore,
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
        self.connection: Any | None = None
        self.journal: TransactionalAuditJournal
        self._records: list[RunRecord] = []
        self._initial_run_count = 0
        self._artifacts: list[ArtifactManifest] = []
        self._events: list[OutboxEvent] = []
        self._committed = False

    def __enter__(self) -> PostgresStateTransaction:
        connection = self.store._connect()
        self.connection = connection
        try:
            connection.execute("BEGIN")
            connection.execute("SET LOCAL lock_timeout = '10s'")
            for lock_name in sorted(
                (f"run:{self.run_id}", f"task:{self.task_id}")
            ):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (lock_name,),
                )
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
        driver = _driver()
        try:
            for record in self.journal.staged:
                connection.execute(
                    "INSERT INTO workflow_transitions "
                    "(task_id, sequence, record_hash, previous_hash, record_json) "
                    "VALUES (%s, %s, %s, %s, %s)",
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
                    "record_json) VALUES (%s, %s, %s, %s, %s, %s, %s)",
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
                    "manifest_json) VALUES (%s, %s, %s, %s, %s, %s)",
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
                    "published_at) VALUES (%s, %s, %s, %s, %s, %s, NULL)",
                    (
                        event.event_id,
                        event.topic,
                        event.task_id,
                        event.run_id,
                        _json_text(event.model_dump(mode="json")),
                        event.created_at,
                    ),
                )
            connection.commit()
            self._committed = True
        except (
            driver.IntegrityError,
            driver.errors.SerializationFailure,
            driver.errors.DeadlockDetected,
        ) as exc:
            connection.rollback()
            raise StateConflictError(
                "transaction conflicted with committed metadata"
            ) from exc
        except driver.Error as exc:
            connection.rollback()
            raise StateStoreError("could not commit control-plane state") from exc

    def _require_open(self):
        if self.connection is None:
            raise StateStoreError("state transaction is not open")
        return self.connection


class PostgresControlPlaneStore:
    """PostgreSQL state adapter with append-only truth and atomic outbox."""

    def __init__(
        self,
        dsn: str,
        *,
        connect_timeout_seconds: int = 10,
        schema: str = "public",
    ):
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        if connect_timeout_seconds < 1:
            raise ValueError("connect timeout must be positive")
        if re.fullmatch(r"[a-z_][a-z0-9_]*", schema) is None:
            raise ValueError("PostgreSQL schema must be a safe lowercase identifier")
        self.dsn = dsn
        self.connect_timeout_seconds = connect_timeout_seconds
        self.schema = schema

    def migrate(self) -> None:
        driver = _driver()
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("jobslayer:schema-migrations",),
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL)"
            )
            migration_root = resources.files(
                "jobslayer.persistence.postgres_migrations"
            )
            for migration in sorted(
                item for item in migration_root.iterdir() if item.name.endswith(".sql")
            ):
                content = migration.read_text(encoding="utf-8")
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                existing = connection.execute(
                    "SELECT sha256 FROM schema_migrations WHERE version = %s",
                    (migration.name,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != digest:
                        raise StateIntegrityError(
                            f"applied migration checksum changed: {migration.name}"
                        )
                    continue
                connection.execute(content)
                connection.execute(
                    "INSERT INTO schema_migrations (version, sha256, applied_at) "
                    "VALUES (%s, %s, %s)",
                    (migration.name, digest, datetime.now(UTC)),
                )
            connection.commit()
        except StateIntegrityError:
            connection.rollback()
            raise
        except driver.Error as exc:
            connection.rollback()
            raise StateStoreError("could not migrate PostgreSQL control plane") from exc
        finally:
            connection.close()

    def transaction(
        self,
        *,
        task_id: str,
        run_id: str,
        expected_task_sequence: int,
        expected_run_sequence: int,
    ) -> PostgresStateTransaction:
        if not task_id or not run_id:
            raise ValueError("task_id and run_id must not be blank")
        return PostgresStateTransaction(
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
        driver = _driver()
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT manifest_json FROM artifact_manifests "
                    "WHERE run_id = %s ORDER BY artifact_id",
                    (run_id,),
                ).fetchall()
            return tuple(ArtifactManifest.model_validate_json(row[0]) for row in rows)
        except (driver.Error, ValidationError) as exc:
            raise StateIntegrityError("artifact metadata is invalid") from exc

    def list_run_ids(self, *, limit: int = 1000) -> tuple[str, ...]:
        if limit < 1 or limit > 10_000:
            raise ValueError("run list limit must be between 1 and 10000")
        driver = _driver()
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT run_id FROM ("
                    "SELECT run_id FROM run_records UNION "
                    "SELECT run_id FROM artifact_manifests UNION "
                    "SELECT run_id FROM outbox_events"
                    ") AS persisted_runs ORDER BY run_id LIMIT %s",
                    (limit,),
                ).fetchall()
            return tuple(str(row[0]) for row in rows)
        except driver.Error as exc:
            raise StateStoreError("could not list persisted runs") from exc

    def events_for_run(self, run_id: str) -> tuple[OutboxEvent, ...]:
        driver = _driver()
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT event_json FROM outbox_events WHERE run_id = %s "
                    "ORDER BY commit_order",
                    (run_id,),
                ).fetchall()
            return tuple(OutboxEvent.model_validate_json(row[0]) for row in rows)
        except (driver.Error, ValidationError) as exc:
            raise StateIntegrityError("persisted run events are invalid") from exc

    def pending_outbox(self, *, limit: int = 100) -> tuple[OutboxEvent, ...]:
        if limit < 1 or limit > 10_000:
            raise ValueError("outbox limit must be between 1 and 10000")
        driver = _driver()
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT event_json FROM outbox_events "
                    "WHERE published_at IS NULL ORDER BY commit_order LIMIT %s",
                    (limit,),
                ).fetchall()
            return tuple(OutboxEvent.model_validate_json(row[0]) for row in rows)
        except (driver.Error, ValidationError) as exc:
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
        driver = _driver()
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT event_json, published_at FROM outbox_events "
                    "WHERE event_id = %s FOR UPDATE",
                    (event_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return False
                if row[1] is None:
                    event = OutboxEvent.model_validate_json(row[0]).model_copy(
                        update={"published_at": when}
                    )
                    connection.execute(
                        "UPDATE outbox_events SET published_at = %s, event_json = %s "
                        "WHERE event_id = %s AND published_at IS NULL",
                        (
                            when,
                            _json_text(event.model_dump(mode="json")),
                            event_id,
                        ),
                    )
                    connection.commit()
                else:
                    connection.rollback()
                return True
        except (driver.Error, ValidationError) as exc:
            raise StateStoreError("could not mark outbox event published") from exc

    def _connect(self):
        driver = _driver()
        try:
            return driver.connect(
                self.dsn,
                connect_timeout=self.connect_timeout_seconds,
                autocommit=False,
                options=f"-c search_path={self.schema}",
            )
        except driver.Error as exc:
            raise StateStoreError("could not connect to PostgreSQL control plane") from exc

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            if not connection.closed:
                connection.rollback()
            connection.close()

    @staticmethod
    def _task_history(connection, task_id: str) -> tuple[TransitionRecord, ...]:
        driver = _driver()
        try:
            rows = connection.execute(
                "SELECT record_json FROM workflow_transitions "
                "WHERE task_id = %s ORDER BY sequence",
                (task_id,),
            ).fetchall()
            records = tuple(
                TransitionRecord.model_validate_json(row[0]) for row in rows
            )
            verify_transition_sequence(records)
            return records
        except (driver.Error, ValidationError, AuditIntegrityError) as exc:
            raise StateIntegrityError("task history is invalid") from exc

    @staticmethod
    def _run_history(connection, run_id: str) -> tuple[RunRecord, ...]:
        driver = _driver()
        try:
            rows = connection.execute(
                "SELECT record_json FROM run_records "
                "WHERE run_id = %s ORDER BY sequence",
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
        except (driver.Error, ValidationError, ValueError) as exc:
            if isinstance(exc, StateIntegrityError):
                raise
            raise StateIntegrityError("run history is invalid") from exc


__all__ = ["PostgresControlPlaneStore", "PostgresStateTransaction"]
