"""Restart-safe local worker leases backed by SQLite."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from pydantic import ValidationError

from jobslayer.workers import (
    WorkerLease,
    WorkerLeaseError,
    WorkerLeaseStatus,
)


def _json_text(lease: WorkerLease) -> str:
    return json.dumps(
        lease.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class SqliteWorkerLeaseStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve(strict=False)

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS worker_leases (
                    lease_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    expires_at TEXT NOT NULL,
                    lease_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_live_lease_per_run
                ON worker_leases(run_id)
                WHERE status IN ('active', 'cancel_requested');
                CREATE TABLE IF NOT EXISTS worker_lease_events (
                    event_order INTEGER PRIMARY KEY AUTOINCREMENT,
                    lease_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    lease_json TEXT NOT NULL,
                    UNIQUE (lease_id, version)
                );
                """
            )

    def acquire(
        self,
        *,
        worker_id: str,
        run_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        when = self._when(now)
        self._duration(lease_seconds)
        lease = WorkerLease(
            lease_id=f"lease-{uuid4().hex}",
            worker_id=worker_id,
            run_id=run_id,
            status=WorkerLeaseStatus.ACTIVE,
            version=1,
            acquired_at=when,
            last_heartbeat_at=when,
            expires_at=when + timedelta(seconds=lease_seconds),
        )
        try:
            with self._write_connection() as connection:
                connection.execute(
                    "INSERT INTO worker_leases "
                    "(lease_id, worker_id, run_id, status, version, expires_at, lease_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        lease.lease_id,
                        lease.worker_id,
                        lease.run_id,
                        lease.status.value,
                        lease.version,
                        lease.expires_at.isoformat(),
                        _json_text(lease),
                    ),
                )
                self._append_event(connection, lease, "acquired")
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise WorkerLeaseError("run already has a live worker lease") from exc
        return lease

    def heartbeat(
        self,
        lease_id: str,
        *,
        expected_version: int,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        when = self._when(now)
        self._duration(lease_seconds)
        with self._write_connection() as connection:
            current = self._load_for_update(connection, lease_id, expected_version)
            if current.status is not WorkerLeaseStatus.ACTIVE:
                raise WorkerLeaseError("only an active lease can heartbeat")
            if when >= current.expires_at:
                raise WorkerLeaseError("expired lease cannot heartbeat")
            updated = current.model_copy(
                update={
                    "version": current.version + 1,
                    "last_heartbeat_at": when,
                    "expires_at": when + timedelta(seconds=lease_seconds),
                }
            )
            self._update(connection, updated, expected_version, "heartbeat")
            connection.commit()
            return updated

    def request_cancel(
        self,
        lease_id: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        when = self._when(now)
        with self._write_connection() as connection:
            current = self._load_for_update(connection, lease_id, expected_version)
            if current.status is not WorkerLeaseStatus.ACTIVE:
                raise WorkerLeaseError("only an active lease accepts cancellation")
            if when >= current.expires_at:
                raise WorkerLeaseError("expired lease cannot accept cancellation")
            updated = current.model_copy(
                update={
                    "status": WorkerLeaseStatus.CANCEL_REQUESTED,
                    "version": current.version + 1,
                    "last_heartbeat_at": when,
                }
            )
            self._update(connection, updated, expected_version, "cancel_requested")
            connection.commit()
            return updated

    def release(
        self,
        lease_id: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        when = self._when(now)
        with self._write_connection() as connection:
            current = self._load_for_update(connection, lease_id, expected_version)
            if current.status not in {
                WorkerLeaseStatus.ACTIVE,
                WorkerLeaseStatus.CANCEL_REQUESTED,
            }:
                raise WorkerLeaseError("only a live lease can be released")
            updated = current.model_copy(
                update={
                    "status": WorkerLeaseStatus.RELEASED,
                    "version": current.version + 1,
                    "last_heartbeat_at": min(when, current.expires_at - timedelta(microseconds=1)),
                }
            )
            self._update(connection, updated, expected_version, "released")
            connection.commit()
            return updated

    def recover_orphans(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[WorkerLease, ...]:
        when = self._when(now)
        recovered: list[WorkerLease] = []
        with self._write_connection() as connection:
            rows = connection.execute(
                "SELECT lease_json FROM worker_leases "
                "WHERE status IN ('active', 'cancel_requested') AND expires_at <= ? "
                "ORDER BY lease_id",
                (when.isoformat(),),
            ).fetchall()
            for row in rows:
                current = self._decode(row[0])
                updated = current.model_copy(
                    update={
                        "status": WorkerLeaseStatus.EXPIRED,
                        "version": current.version + 1,
                    }
                )
                self._update(connection, updated, current.version, "expired")
                recovered.append(updated)
            connection.commit()
        return tuple(recovered)

    def get(self, lease_id: str) -> WorkerLease | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT lease_json FROM worker_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
        return None if row is None else self._decode(row[0])

    def events(self, lease_id: str) -> tuple[WorkerLease, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT lease_json FROM worker_lease_events "
                "WHERE lease_id = ? ORDER BY version",
                (lease_id,),
            ).fetchall()
        records = tuple(self._decode(row[0]) for row in rows)
        if tuple(item.version for item in records) != tuple(
            range(1, len(records) + 1)
        ):
            raise WorkerLeaseError("worker lease event history is not contiguous")
        return records

    def _update(
        self,
        connection: sqlite3.Connection,
        lease: WorkerLease,
        expected_version: int,
        event_type: str,
    ) -> None:
        cursor = connection.execute(
            "UPDATE worker_leases SET status = ?, version = ?, expires_at = ?, "
            "lease_json = ? WHERE lease_id = ? AND version = ?",
            (
                lease.status.value,
                lease.version,
                lease.expires_at.isoformat(),
                _json_text(lease),
                lease.lease_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise WorkerLeaseError("worker lease changed concurrently")
        self._append_event(connection, lease, event_type)

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        lease: WorkerLease,
        event_type: str,
    ) -> None:
        connection.execute(
            "INSERT INTO worker_lease_events "
            "(lease_id, version, event_type, lease_json) VALUES (?, ?, ?, ?)",
            (lease.lease_id, lease.version, event_type, _json_text(lease)),
        )

    def _load_for_update(
        self,
        connection: sqlite3.Connection,
        lease_id: str,
        expected_version: int,
    ) -> WorkerLease:
        row = connection.execute(
            "SELECT lease_json FROM worker_leases WHERE lease_id = ?",
            (lease_id,),
        ).fetchone()
        if row is None:
            raise WorkerLeaseError("worker lease does not exist")
        current = self._decode(row[0])
        if current.version != expected_version:
            raise WorkerLeaseError("worker lease changed concurrently")
        return current

    @staticmethod
    def _decode(value: str) -> WorkerLease:
        try:
            return WorkerLease.model_validate_json(value)
        except ValidationError as exc:
            raise WorkerLeaseError("worker lease persistence is invalid") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write_connection(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _when(now: datetime | None) -> datetime:
        when = now or datetime.now(UTC)
        if when.tzinfo is None:
            raise WorkerLeaseError("worker lease time needs a timezone")
        return when

    @staticmethod
    def _duration(lease_seconds: int) -> None:
        if lease_seconds < 1 or lease_seconds > 3600:
            raise WorkerLeaseError("lease duration must be between 1 and 3600 seconds")


__all__ = ["SqliteWorkerLeaseStore"]
