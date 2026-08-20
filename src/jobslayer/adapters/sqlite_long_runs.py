"""SQLite adapter for append-only resumable long-run state and checkpoints."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from jobslayer.long_running import (
    LongRunConflictError,
    LongRunEvent,
    LongRunEventType,
    LongRunIntegrityError,
    LongRunStatus,
    ProgressCheckpoint,
    ResumableRunHandle,
    TERMINAL_LONG_RUN_STATUSES,
    long_run_event_hash,
)


def _json_text(model: ResumableRunHandle | LongRunEvent | ProgressCheckpoint) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class SqliteLongRunStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve(strict=False)

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS long_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    handle_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS long_run_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK (sequence >= 1),
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES long_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS long_run_checkpoints (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK (sequence >= 1),
                    checkpoint_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence),
                    FOREIGN KEY (run_id) REFERENCES long_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_long_runs_status
                    ON long_runs(status, run_id);
                CREATE TRIGGER IF NOT EXISTS long_run_events_no_update
                    BEFORE UPDATE ON long_run_events
                    BEGIN SELECT RAISE(ABORT, 'long-run events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS long_run_events_no_delete
                    BEFORE DELETE ON long_run_events
                    BEGIN SELECT RAISE(ABORT, 'long-run events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS long_run_checkpoints_no_update
                    BEFORE UPDATE ON long_run_checkpoints
                    BEGIN SELECT RAISE(ABORT, 'long-run checkpoints are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS long_run_checkpoints_no_delete
                    BEFORE DELETE ON long_run_checkpoints
                    BEGIN SELECT RAISE(ABORT, 'long-run checkpoints are append-only'); END;
                """
            )

    def create(self, event: LongRunEvent) -> ResumableRunHandle:
        if (
            event.sequence != 1
            or event.previous_hash is not None
            or event.event_type is not LongRunEventType.ADMITTED
            or event.handle.status is not LongRunStatus.ADMITTED
        ):
            raise LongRunIntegrityError("first long-run event must admit sequence one")
        self._verify_event_hash(event)
        try:
            with self._write_connection() as connection:
                connection.execute(
                    "INSERT INTO long_runs "
                    "(run_id, task_id, status, version, handle_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        event.run_id,
                        event.handle.task_id,
                        event.handle.status.value,
                        event.handle.version,
                        _json_text(event.handle),
                    ),
                )
                connection.execute(
                    "INSERT INTO long_run_events (run_id, sequence, event_json) "
                    "VALUES (?, ?, ?)",
                    (event.run_id, event.sequence, _json_text(event)),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise LongRunConflictError("long run already exists") from exc
        return event.handle

    def append(
        self,
        event: LongRunEvent,
        *,
        expected_version: int,
        checkpoint: ProgressCheckpoint | None = None,
    ) -> ResumableRunHandle:
        if expected_version < 1 or event.sequence != expected_version + 1:
            raise LongRunConflictError("long-run expected version is stale")
        self._verify_event_hash(event)
        with self._write_connection() as connection:
            row = connection.execute(
                "SELECT handle_json FROM long_runs WHERE run_id = ?",
                (event.run_id,),
            ).fetchone()
            if row is None:
                raise LongRunConflictError("long run does not exist")
            current = self._decode_handle(row[0])
            if current.version != expected_version:
                raise LongRunConflictError("long-run expected version is stale")
            if (
                current.status in TERMINAL_LONG_RUN_STATUSES
                and event.event_type is not LongRunEventType.RETRY_AUTHORIZED
            ):
                raise LongRunConflictError("terminal long run rejects this transition")
            if (
                event.handle.run_id != current.run_id
                or event.handle.task_id != current.task_id
                or event.handle.policy != current.policy
                or event.handle.created_at != current.created_at
            ):
                raise LongRunIntegrityError("long-run immutable identity changed")
            previous = connection.execute(
                "SELECT event_json FROM long_run_events "
                "WHERE run_id = ? AND sequence = ?",
                (event.run_id, expected_version),
            ).fetchone()
            if previous is None:
                raise LongRunIntegrityError("long-run event prefix is incomplete")
            previous_event = self._decode_event(previous[0])
            if event.previous_hash != previous_event.record_hash:
                raise LongRunIntegrityError("long-run event hash chain is broken")
            self._validate_checkpoint(event, checkpoint)
            updated = connection.execute(
                "UPDATE long_runs SET status = ?, version = ?, handle_json = ? "
                "WHERE run_id = ? AND version = ?",
                (
                    event.handle.status.value,
                    event.handle.version,
                    _json_text(event.handle),
                    event.run_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise LongRunConflictError("long-run expected version is stale")
            connection.execute(
                "INSERT INTO long_run_events (run_id, sequence, event_json) "
                "VALUES (?, ?, ?)",
                (event.run_id, event.sequence, _json_text(event)),
            )
            if checkpoint is not None:
                connection.execute(
                    "INSERT INTO long_run_checkpoints "
                    "(run_id, sequence, checkpoint_json) VALUES (?, ?, ?)",
                    (checkpoint.run_id, checkpoint.sequence, _json_text(checkpoint)),
                )
            connection.commit()
        return event.handle

    def get(self, run_id: str) -> ResumableRunHandle | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT handle_json FROM long_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        projection = self._decode_handle(row[0])
        history = self.history(run_id)
        if not history or history[-1].handle != projection:
            raise LongRunIntegrityError("long-run projection does not match event truth")
        return projection

    def history(self, run_id: str) -> tuple[LongRunEvent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event_json FROM long_run_events "
                "WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        events = tuple(self._decode_event(row[0]) for row in rows)
        previous_hash: str | None = None
        for sequence, event in enumerate(events, start=1):
            if event.run_id != run_id or event.sequence != sequence:
                raise LongRunIntegrityError("long-run event sequence is invalid")
            if event.previous_hash != previous_hash:
                raise LongRunIntegrityError("long-run event hash chain is broken")
            self._verify_event_hash(event)
            previous_hash = event.record_hash
        return events

    def checkpoints(self, run_id: str) -> tuple[ProgressCheckpoint, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT checkpoint_json FROM long_run_checkpoints "
                "WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        checkpoints = tuple(self._decode_checkpoint(row[0]) for row in rows)
        for sequence, checkpoint in enumerate(checkpoints, start=1):
            if checkpoint.run_id != run_id or checkpoint.sequence != sequence:
                raise LongRunIntegrityError("long-run checkpoint sequence is invalid")
        return checkpoints

    def list_active(self) -> tuple[ResumableRunHandle, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT run_id FROM long_runs WHERE status IN "
                "('admitted', 'running', 'cancel_requested') ORDER BY run_id"
            ).fetchall()
        handles = tuple(self.get(row[0]) for row in rows)
        return tuple(handle for handle in handles if handle is not None)

    @staticmethod
    def _validate_checkpoint(
        event: LongRunEvent, checkpoint: ProgressCheckpoint | None
    ) -> None:
        is_checkpoint_event = event.event_type is LongRunEventType.CHECKPOINTED
        if is_checkpoint_event != (checkpoint is not None):
            raise LongRunIntegrityError(
                "checkpoint metadata must be atomically bound to its event"
            )
        if checkpoint is None:
            return
        if (
            checkpoint.run_id != event.run_id
            or checkpoint.sequence != event.handle.checkpoint_count
            or checkpoint.attempt_number != event.handle.attempt_number
            or checkpoint.event_cursor != event.handle.event_cursor
        ):
            raise LongRunIntegrityError("checkpoint does not bind the latest handle")

    @staticmethod
    def _verify_event_hash(event: LongRunEvent) -> None:
        if event.record_hash != long_run_event_hash(event):
            raise LongRunIntegrityError("long-run event record hash mismatch")

    @staticmethod
    def _decode_handle(value: str) -> ResumableRunHandle:
        try:
            return ResumableRunHandle.model_validate_json(value)
        except ValidationError as exc:
            raise LongRunIntegrityError("long-run projection is invalid") from exc

    @staticmethod
    def _decode_event(value: str) -> LongRunEvent:
        try:
            return LongRunEvent.model_validate_json(value)
        except ValidationError as exc:
            raise LongRunIntegrityError("long-run event is invalid") from exc

    @staticmethod
    def _decode_checkpoint(value: str) -> ProgressCheckpoint:
        try:
            return ProgressCheckpoint.model_validate_json(value)
        except ValidationError as exc:
            raise LongRunIntegrityError("long-run checkpoint is invalid") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write_connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


__all__ = ["SqliteLongRunStore"]
