from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest

from jobslayer.adapters.sqlite_state import SqliteControlPlaneStore
from jobslayer.application.run_records import RunRecordStage
from jobslayer.domain.models import ActorType, ArtifactManifest, TaskState
from jobslayer.persistence import (
    OutboxEvent,
    StateConflictError,
    StateIntegrityError,
)
from jobslayer.workflow.kernel import WorkflowKernel
from tests.state_store_contract import ControlPlaneStoreContract


class SqliteControlPlaneStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "state.sqlite3"
        self.store = SqliteControlPlaneStore(self.database)
        self.store.migrate()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def _artifact(artifact_id: str, *, task_id: str, run_id: str) -> ArtifactManifest:
        return ArtifactManifest(
            artifact_id=artifact_id,
            task_id=task_id,
            run_id=run_id,
            artifact_type="fixture-evidence",
            uri="file:///fixture/evidence",
            sha256="a" * 64,
            size_bytes=1,
            producer="fixture",
        )

    @staticmethod
    def _event(event_id: str, *, task_id: str, run_id: str) -> OutboxEvent:
        return OutboxEvent(
            event_id=event_id,
            topic="control-plane.changed",
            task_id=task_id,
            run_id=run_id,
            payload={"task_id": task_id, "run_id": run_id},
        )

    def test_commits_workflow_run_artifact_and_outbox_across_restart(self) -> None:
        with self.store.transaction(
            task_id="task-1",
            run_id="run-1",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            transition = WorkflowKernel(transaction.journal).transition(
                task_id="task-1",
                to_state=TaskState.PLANNED,
                actor_type=ActorType.SYSTEM,
                actor_id="planner",
                reason="plan persisted",
                evidence_ids=("plan-1",),
            )
            record = transaction.append_run_record(
                stage=RunRecordStage.EXECUTION,
                payload={"status": "prepared"},
            )
            transaction.add_artifact(
                self._artifact("artifact-1", task_id="task-1", run_id="run-1")
            )
            transaction.enqueue(
                self._event("event-1", task_id="task-1", run_id="run-1")
            )
            transaction.commit()

        reopened = SqliteControlPlaneStore(self.database)
        reopened.migrate()

        self.assertEqual(reopened.task_history("task-1"), (transition,))
        self.assertEqual(reopened.run_history("run-1"), (record,))
        self.assertEqual(
            reopened.artifacts_for_run("run-1")[0].artifact_id,
            "artifact-1",
        )
        self.assertEqual(reopened.pending_outbox()[0].event_id, "event-1")

    def test_conflicting_metadata_rolls_back_the_complete_transaction(self) -> None:
        artifact = self._artifact(
            "artifact-duplicate",
            task_id="task-atomic",
            run_id="run-atomic",
        )
        with self.store.transaction(
            task_id="task-atomic",
            run_id="run-atomic",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            WorkflowKernel(transaction.journal).transition(
                task_id="task-atomic",
                to_state=TaskState.PLANNED,
                actor_type=ActorType.SYSTEM,
                actor_id="planner",
                reason="must roll back",
            )
            transaction.append_run_record(
                stage=RunRecordStage.EXECUTION,
                payload={"status": "must-roll-back"},
            )
            transaction.add_artifact(artifact)
            transaction.add_artifact(artifact)
            transaction.enqueue(
                self._event(
                    "event-atomic",
                    task_id="task-atomic",
                    run_id="run-atomic",
                )
            )
            with self.assertRaises(StateConflictError):
                transaction.commit()

        self.assertEqual(self.store.task_history("task-atomic"), ())
        self.assertEqual(self.store.run_history("run-atomic"), ())
        self.assertEqual(self.store.artifacts_for_run("run-atomic"), ())
        self.assertEqual(self.store.pending_outbox(), ())

    def test_rejects_truth_mutation_without_an_outbox_event(self) -> None:
        with self.store.transaction(
            task_id="task-no-event",
            run_id="run-no-event",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            WorkflowKernel(transaction.journal).transition(
                task_id="task-no-event",
                to_state=TaskState.PLANNED,
                actor_type=ActorType.SYSTEM,
                actor_id="planner",
                reason="missing event",
            )
            with self.assertRaisesRegex(StateIntegrityError, "outbox"):
                transaction.commit()

        self.assertEqual(self.store.task_history("task-no-event"), ())

    def test_stale_expected_sequence_is_rejected_without_state_change(self) -> None:
        with self.store.transaction(
            task_id="task-stale",
            run_id="run-stale",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            WorkflowKernel(transaction.journal).transition(
                task_id="task-stale",
                to_state=TaskState.PLANNED,
                actor_type=ActorType.SYSTEM,
                actor_id="planner",
                reason="first commit",
            )
            transaction.enqueue(
                self._event("event-stale", task_id="task-stale", run_id="run-stale")
            )
            transaction.commit()

        with self.assertRaises(StateConflictError):
            with self.store.transaction(
                task_id="task-stale",
                run_id="run-stale",
                expected_task_sequence=0,
                expected_run_sequence=0,
            ):
                self.fail("stale transaction must not open")

        self.assertEqual(len(self.store.task_history("task-stale")), 1)

    def test_concurrent_stale_writers_publish_exactly_one_sequence(self) -> None:
        barrier = threading.Barrier(2)
        results: list[str] = []
        result_lock = threading.Lock()

        def write(run_id: str) -> None:
            barrier.wait(timeout=5)
            try:
                with self.store.transaction(
                    task_id="task-race",
                    run_id=run_id,
                    expected_task_sequence=0,
                    expected_run_sequence=0,
                ) as transaction:
                    WorkflowKernel(transaction.journal).transition(
                        task_id="task-race",
                        to_state=TaskState.PLANNED,
                        actor_type=ActorType.SYSTEM,
                        actor_id=run_id,
                        reason="concurrent plan",
                    )
                    transaction.append_run_record(
                        stage=RunRecordStage.EXECUTION,
                        payload={"run_id": run_id},
                    )
                    transaction.enqueue(
                        self._event(
                            f"event-{run_id}",
                            task_id="task-race",
                            run_id=run_id,
                        )
                    )
                    transaction.commit()
                outcome = "committed"
            except StateConflictError:
                outcome = "conflict"
            with result_lock:
                results.append(outcome)

        threads = [
            threading.Thread(target=write, args=("run-race-a",)),
            threading.Thread(target=write, args=("run-race-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(sorted(results), ["committed", "conflict"])
        self.assertEqual(len(self.store.task_history("task-race")), 1)
        total_run_records = sum(
            len(self.store.run_history(run_id))
            for run_id in ("run-race-a", "run-race-b")
        )
        self.assertEqual(total_run_records, 1)
        self.assertEqual(len(self.store.pending_outbox()), 1)

    def test_outbox_publication_mark_is_idempotent(self) -> None:
        with self.store.transaction(
            task_id="task-event",
            run_id="run-event",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            transaction.enqueue(
                self._event("event-delivery", task_id="task-event", run_id="run-event")
            )
            transaction.commit()

        self.assertTrue(self.store.mark_outbox_published("event-delivery"))
        self.assertTrue(self.store.mark_outbox_published("event-delivery"))
        self.assertFalse(self.store.mark_outbox_published("event-missing"))
        self.assertEqual(self.store.pending_outbox(), ())

    def test_database_rejects_update_and_delete_of_owned_truth(self) -> None:
        with self.store.transaction(
            task_id="task-append-only",
            run_id="run-append-only",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            WorkflowKernel(transaction.journal).transition(
                task_id="task-append-only",
                to_state=TaskState.PLANNED,
                actor_type=ActorType.SYSTEM,
                actor_id="planner",
                reason="append-only fixture",
            )
            transaction.append_run_record(
                stage=RunRecordStage.EXECUTION,
                payload={"status": "persisted"},
            )
            transaction.add_artifact(
                self._artifact(
                    "artifact-append-only",
                    task_id="task-append-only",
                    run_id="run-append-only",
                )
            )
            transaction.enqueue(
                self._event(
                    "event-append-only",
                    task_id="task-append-only",
                    run_id="run-append-only",
                )
            )
            transaction.commit()

        connection = sqlite3.connect(self.database)
        try:
            for statement in (
                "UPDATE workflow_transitions SET task_id = 'changed'",
                "DELETE FROM run_records",
                "UPDATE artifact_manifests SET sha256 = 'changed'",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
                connection.rollback()
        finally:
            connection.close()

        self.assertEqual(len(self.store.task_history("task-append-only")), 1)
        self.assertEqual(len(self.store.run_history("run-append-only")), 1)
        self.assertEqual(len(self.store.artifacts_for_run("run-append-only")), 1)

    def test_changed_applied_migration_checksum_is_rejected(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE schema_migrations SET sha256 = ? WHERE version = ?",
                ("0" * 64, "001_initial.sql"),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(StateIntegrityError, "checksum changed"):
            self.store.migrate()


class SqliteSharedStateStoreContractTests(
    ControlPlaneStoreContract,
    unittest.TestCase,
):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "contract.sqlite3"
        self.store = SqliteControlPlaneStore(self.database)
        self.store.migrate()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def reopen_store(self) -> SqliteControlPlaneStore:
        reopened = SqliteControlPlaneStore(self.database)
        reopened.migrate()
        return reopened


if __name__ == "__main__":
    unittest.main()
