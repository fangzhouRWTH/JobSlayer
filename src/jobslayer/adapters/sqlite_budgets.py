"""Transactional local budget reservations and usage accounting."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from pydantic import ValidationError

from jobslayer.governance import (
    BudgetError,
    BudgetExceededError,
    BudgetSnapshot,
    BudgetStatus,
    ExecutionBudget,
)


def _json_text(snapshot: BudgetSnapshot) -> str:
    return json.dumps(
        snapshot.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class SqliteBudgetStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve(strict=False)

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS budget_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    budget_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS budget_events (
                    event_order INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    UNIQUE (reservation_id, version)
                );
                """
            )

    def reserve(
        self,
        budget: ExecutionBudget,
        *,
        now: datetime | None = None,
    ) -> BudgetSnapshot:
        when = self._when(now)
        snapshot = BudgetSnapshot(
            reservation_id=f"reservation-{uuid4().hex}",
            budget=budget,
            version=1,
            status=BudgetStatus.ACTIVE,
            spent_input_tokens=0,
            spent_output_tokens=0,
            spent_cost_microusd=0,
            spent_duration_ms=0,
            attempts_started=0,
            repairs_started=0,
            reserved_at=when,
            updated_at=when,
        )
        try:
            with self._write_connection() as connection:
                connection.execute(
                    "INSERT INTO budget_reservations "
                    "(reservation_id, budget_id, task_id, run_id, status, version, "
                    "snapshot_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        snapshot.reservation_id,
                        budget.budget_id,
                        budget.task_id,
                        budget.run_id,
                        snapshot.status.value,
                        snapshot.version,
                        _json_text(snapshot),
                    ),
                )
                self._append_event(connection, snapshot, "reserved")
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise BudgetError("run or budget already has a reservation") from exc
        return snapshot

    def authorize_attempt(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        repair: bool = False,
        now: datetime | None = None,
    ) -> BudgetSnapshot:
        when = self._when(now)
        with self._write_connection() as connection:
            current = self._load(connection, reservation_id, expected_version)
            self._require_active(current)
            attempts = current.attempts_started + 1
            repairs = current.repairs_started + (1 if repair else 0)
            denial = None
            if attempts > current.budget.maximum_attempts:
                denial = "attempt budget is exhausted"
            elif repair and current.attempts_started == 0:
                denial = "repair cannot be the first attempt"
            elif repairs > current.budget.maximum_repairs:
                denial = "repair budget is exhausted"
            status = BudgetStatus.EXHAUSTED if denial else BudgetStatus.ACTIVE
            updated = current.model_copy(
                update={
                    "version": current.version + 1,
                    "status": status,
                    "attempts_started": attempts,
                    "repairs_started": repairs,
                    "updated_at": when,
                }
            )
            self._update(
                connection,
                updated,
                expected_version,
                "attempt_denied" if denial else ("repair_started" if repair else "attempt_started"),
            )
            connection.commit()
        if denial:
            raise BudgetExceededError(denial, updated)
        return updated

    def charge(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_microusd: int = 0,
        duration_ms: int = 0,
        now: datetime | None = None,
    ) -> BudgetSnapshot:
        values = (input_tokens, output_tokens, cost_microusd, duration_ms)
        if any(value < 0 for value in values):
            raise BudgetError("budget charge deltas must not be negative")
        when = self._when(now)
        with self._write_connection() as connection:
            current = self._load(connection, reservation_id, expected_version)
            self._require_active(current)
            totals = {
                "spent_input_tokens": current.spent_input_tokens + input_tokens,
                "spent_output_tokens": current.spent_output_tokens + output_tokens,
                "spent_cost_microusd": current.spent_cost_microusd + cost_microusd,
                "spent_duration_ms": current.spent_duration_ms + duration_ms,
            }
            limits = {
                "input tokens": (
                    totals["spent_input_tokens"],
                    current.budget.maximum_input_tokens,
                ),
                "output tokens": (
                    totals["spent_output_tokens"],
                    current.budget.maximum_output_tokens,
                ),
                "cost": (
                    totals["spent_cost_microusd"],
                    current.budget.maximum_cost_microusd,
                ),
                "duration": (
                    totals["spent_duration_ms"],
                    current.budget.maximum_duration_ms,
                ),
            }
            exceeded = tuple(
                name for name, (spent, maximum) in limits.items() if spent > maximum
            )
            updated = current.model_copy(
                update={
                    "version": current.version + 1,
                    "status": BudgetStatus.EXHAUSTED if exceeded else BudgetStatus.ACTIVE,
                    **totals,
                    "updated_at": when,
                }
            )
            self._update(
                connection,
                updated,
                expected_version,
                "limit_exceeded" if exceeded else "usage_charged",
            )
            connection.commit()
        if exceeded:
            raise BudgetExceededError(
                "budget exceeded: " + ", ".join(exceeded),
                updated,
            )
        return updated

    def release(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> BudgetSnapshot:
        when = self._when(now)
        with self._write_connection() as connection:
            current = self._load(connection, reservation_id, expected_version)
            self._require_active(current)
            updated = current.model_copy(
                update={
                    "version": current.version + 1,
                    "status": BudgetStatus.RELEASED,
                    "updated_at": when,
                }
            )
            self._update(connection, updated, expected_version, "released")
            connection.commit()
            return updated

    def get(self, reservation_id: str) -> BudgetSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM budget_reservations "
                "WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
        return None if row is None else self._decode(row[0])

    def events(self, reservation_id: str) -> tuple[BudgetSnapshot, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT snapshot_json FROM budget_events "
                "WHERE reservation_id = ? ORDER BY version",
                (reservation_id,),
            ).fetchall()
        snapshots = tuple(self._decode(row[0]) for row in rows)
        if tuple(item.version for item in snapshots) != tuple(
            range(1, len(snapshots) + 1)
        ):
            raise BudgetError("budget event history is not contiguous")
        return snapshots

    def _load(
        self,
        connection: sqlite3.Connection,
        reservation_id: str,
        expected_version: int,
    ) -> BudgetSnapshot:
        row = connection.execute(
            "SELECT snapshot_json FROM budget_reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise BudgetError("budget reservation does not exist")
        snapshot = self._decode(row[0])
        if snapshot.version != expected_version:
            raise BudgetError("budget reservation changed concurrently")
        return snapshot

    @staticmethod
    def _require_active(snapshot: BudgetSnapshot) -> None:
        if snapshot.status is not BudgetStatus.ACTIVE:
            raise BudgetError("budget reservation is not active")

    def _update(
        self,
        connection: sqlite3.Connection,
        snapshot: BudgetSnapshot,
        expected_version: int,
        event_type: str,
    ) -> None:
        cursor = connection.execute(
            "UPDATE budget_reservations SET status = ?, version = ?, snapshot_json = ? "
            "WHERE reservation_id = ? AND version = ?",
            (
                snapshot.status.value,
                snapshot.version,
                _json_text(snapshot),
                snapshot.reservation_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise BudgetError("budget reservation changed concurrently")
        self._append_event(connection, snapshot, event_type)

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        snapshot: BudgetSnapshot,
        event_type: str,
    ) -> None:
        connection.execute(
            "INSERT INTO budget_events "
            "(reservation_id, version, event_type, snapshot_json) VALUES (?, ?, ?, ?)",
            (
                snapshot.reservation_id,
                snapshot.version,
                event_type,
                _json_text(snapshot),
            ),
        )

    @staticmethod
    def _decode(value: str) -> BudgetSnapshot:
        try:
            return BudgetSnapshot.model_validate_json(value)
        except ValidationError as exc:
            raise BudgetError("budget persistence is invalid") from exc

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
            raise BudgetError("budget time needs a timezone")
        return when


__all__ = ["SqliteBudgetStore"]
