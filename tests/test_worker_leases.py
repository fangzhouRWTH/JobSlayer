from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobslayer.adapters.sqlite_workers import SqliteWorkerLeaseStore
from jobslayer.workers import WorkerLeaseError, WorkerLeaseStatus


class WorkerLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "workers.sqlite3"
        self.store = SqliteWorkerLeaseStore(self.path)
        self.store.migrate()
        self.now = datetime(2026, 8, 12, 2, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_heartbeat_cancel_and_release_are_versioned_and_audited(self) -> None:
        acquired = self.store.acquire(
            worker_id="worker-1",
            run_id="run-1",
            lease_seconds=30,
            now=self.now,
        )
        heartbeat = self.store.heartbeat(
            acquired.lease_id,
            expected_version=1,
            lease_seconds=30,
            now=self.now + timedelta(seconds=10),
        )
        cancelled = self.store.request_cancel(
            acquired.lease_id,
            expected_version=2,
            now=self.now + timedelta(seconds=11),
        )
        released = self.store.release(
            acquired.lease_id,
            expected_version=3,
            now=self.now + timedelta(seconds=12),
        )

        self.assertEqual(released.status, WorkerLeaseStatus.RELEASED)
        self.assertEqual(
            tuple(item.version for item in self.store.events(acquired.lease_id)),
            (1, 2, 3, 4),
        )
        reopened = SqliteWorkerLeaseStore(self.path)
        self.assertEqual(reopened.get(acquired.lease_id), released)

    def test_only_one_live_worker_may_own_a_run(self) -> None:
        self.store.acquire(
            worker_id="worker-1",
            run_id="run-exclusive",
            lease_seconds=30,
            now=self.now,
        )
        with self.assertRaisesRegex(WorkerLeaseError, "already has"):
            self.store.acquire(
                worker_id="worker-2",
                run_id="run-exclusive",
                lease_seconds=30,
                now=self.now,
            )

    def test_stale_heartbeat_and_post_cancel_heartbeat_are_rejected(self) -> None:
        lease = self.store.acquire(
            worker_id="worker-1",
            run_id="run-stale",
            lease_seconds=30,
            now=self.now,
        )
        cancelled = self.store.request_cancel(
            lease.lease_id,
            expected_version=1,
            now=self.now + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(WorkerLeaseError, "concurrently"):
            self.store.heartbeat(
                lease.lease_id,
                expected_version=1,
                lease_seconds=30,
                now=self.now + timedelta(seconds=2),
            )
        with self.assertRaisesRegex(WorkerLeaseError, "active"):
            self.store.heartbeat(
                lease.lease_id,
                expected_version=cancelled.version,
                lease_seconds=30,
                now=self.now + timedelta(seconds=2),
            )

    def test_restart_expires_orphans_without_reassigning_them(self) -> None:
        lease = self.store.acquire(
            worker_id="worker-crashed",
            run_id="run-orphan",
            lease_seconds=5,
            now=self.now,
        )
        reopened = SqliteWorkerLeaseStore(self.path)
        recovered = reopened.recover_orphans(now=self.now + timedelta(seconds=5))

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].status, WorkerLeaseStatus.EXPIRED)
        self.assertEqual(recovered[0].lease_id, lease.lease_id)
        replacement = reopened.acquire(
            worker_id="worker-replacement",
            run_id="run-orphan",
            lease_seconds=5,
            now=self.now + timedelta(seconds=6),
        )
        self.assertEqual(replacement.status, WorkerLeaseStatus.ACTIVE)


if __name__ == "__main__":
    unittest.main()
