"""Reusable behavioral contract for ControlPlaneStore adapters."""

from __future__ import annotations

import threading

from jobslayer.application.run_records import RunRecordStage
from jobslayer.domain.models import ActorType, ArtifactManifest, TaskState
from jobslayer.persistence import OutboxEvent, StateConflictError, StateIntegrityError
from jobslayer.persistence.transactional_journal import TransactionalAuditJournal
from jobslayer.workflow.kernel import WorkflowKernel


class ControlPlaneStoreContract:
    """Mixin instantiated by concrete SQLite and PostgreSQL test cases."""

    store: object

    @staticmethod
    def artifact(artifact_id: str, *, task_id: str, run_id: str) -> ArtifactManifest:
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
    def event(event_id: str, *, task_id: str, run_id: str) -> OutboxEvent:
        return OutboxEvent(
            event_id=event_id,
            topic="control-plane.changed",
            task_id=task_id,
            run_id=run_id,
            payload={"task_id": task_id, "run_id": run_id},
        )

    def reopen_store(self):
        raise NotImplementedError

    def test_contract_commits_all_metadata_and_outbox_across_restart(self) -> None:
        with self.store.transaction(
            task_id="contract-task-1",
            run_id="contract-run-1",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            transition = WorkflowKernel(transaction.journal).transition(
                task_id="contract-task-1",
                to_state=TaskState.PLANNED,
                actor_type=ActorType.SYSTEM,
                actor_id="planner",
                reason="contract plan persisted",
                evidence_ids=("plan-1",),
            )
            record = transaction.append_run_record(
                stage=RunRecordStage.EXECUTION,
                payload={"status": "prepared"},
            )
            transaction.add_artifact(
                self.artifact(
                    "contract-artifact-1",
                    task_id="contract-task-1",
                    run_id="contract-run-1",
                )
            )
            transaction.enqueue(
                self.event(
                    "contract-event-1",
                    task_id="contract-task-1",
                    run_id="contract-run-1",
                )
            )
            transaction.commit()

        reopened = self.reopen_store()
        self.assertEqual(reopened.task_history("contract-task-1"), (transition,))
        self.assertEqual(reopened.run_history("contract-run-1"), (record,))
        self.assertEqual(
            reopened.artifacts_for_run("contract-run-1")[0].artifact_id,
            "contract-artifact-1",
        )
        self.assertEqual(reopened.pending_outbox()[0].event_id, "contract-event-1")

    def test_contract_rolls_back_the_entire_conflicting_transaction(self) -> None:
        artifact = self.artifact(
            "contract-artifact-duplicate",
            task_id="contract-task-atomic",
            run_id="contract-run-atomic",
        )
        with self.store.transaction(
            task_id="contract-task-atomic",
            run_id="contract-run-atomic",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            WorkflowKernel(transaction.journal).transition(
                task_id="contract-task-atomic",
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
                self.event(
                    "contract-event-atomic",
                    task_id="contract-task-atomic",
                    run_id="contract-run-atomic",
                )
            )
            with self.assertRaises(StateConflictError):
                transaction.commit()

        self.assertEqual(self.store.task_history("contract-task-atomic"), ())
        self.assertEqual(self.store.run_history("contract-run-atomic"), ())
        self.assertEqual(self.store.artifacts_for_run("contract-run-atomic"), ())
        self.assertEqual(self.store.pending_outbox(), ())

    def test_contract_rejects_truth_without_atomic_outbox(self) -> None:
        with self.store.transaction(
            task_id="contract-task-no-event",
            run_id="contract-run-no-event",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            WorkflowKernel(transaction.journal).transition(
                task_id="contract-task-no-event",
                to_state=TaskState.PLANNED,
                actor_type=ActorType.SYSTEM,
                actor_id="planner",
                reason="missing event",
            )
            with self.assertRaisesRegex(StateIntegrityError, "outbox"):
                transaction.commit()
        self.assertEqual(self.store.task_history("contract-task-no-event"), ())

    def test_contract_rejects_stale_expected_sequence(self) -> None:
        with self.store.transaction(
            task_id="contract-task-stale",
            run_id="contract-run-stale",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            WorkflowKernel(transaction.journal).transition(
                task_id="contract-task-stale",
                to_state=TaskState.PLANNED,
                actor_type=ActorType.SYSTEM,
                actor_id="planner",
                reason="first commit",
            )
            transaction.enqueue(
                self.event(
                    "contract-event-stale",
                    task_id="contract-task-stale",
                    run_id="contract-run-stale",
                )
            )
            transaction.commit()

        with self.assertRaises(StateConflictError):
            with self.store.transaction(
                task_id="contract-task-stale",
                run_id="contract-run-stale",
                expected_task_sequence=0,
                expected_run_sequence=0,
            ):
                self.fail("stale transaction must not open")
        self.assertEqual(len(self.store.task_history("contract-task-stale")), 1)

    def test_contract_serializes_concurrent_stale_writers(self) -> None:
        barrier = threading.Barrier(2)
        results: list[str] = []
        result_lock = threading.Lock()

        def write(run_id: str) -> None:
            barrier.wait(timeout=5)
            try:
                with self.store.transaction(
                    task_id="contract-task-race",
                    run_id=run_id,
                    expected_task_sequence=0,
                    expected_run_sequence=0,
                ) as transaction:
                    WorkflowKernel(transaction.journal).transition(
                        task_id="contract-task-race",
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
                        self.event(
                            f"contract-event-{run_id}",
                            task_id="contract-task-race",
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
            threading.Thread(target=write, args=("contract-run-race-a",)),
            threading.Thread(target=write, args=("contract-run-race-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(sorted(results), ["committed", "conflict"])
        self.assertEqual(len(self.store.task_history("contract-task-race")), 1)
        self.assertEqual(
            sum(
                len(self.store.run_history(run_id))
                for run_id in ("contract-run-race-a", "contract-run-race-b")
            ),
            1,
        )
        self.assertEqual(len(self.store.pending_outbox()), 1)

    def test_contract_outbox_publication_is_idempotent(self) -> None:
        with self.store.transaction(
            task_id="contract-task-event",
            run_id="contract-run-event",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            transaction.enqueue(
                self.event(
                    "contract-event-delivery",
                    task_id="contract-task-event",
                    run_id="contract-run-event",
                )
            )
            transaction.commit()

        self.assertTrue(self.store.mark_outbox_published("contract-event-delivery"))
        self.assertTrue(self.store.mark_outbox_published("contract-event-delivery"))
        self.assertFalse(self.store.mark_outbox_published("contract-event-missing"))
        self.assertEqual(self.store.pending_outbox(), ())
        self.assertIn("contract-run-event", self.store.list_run_ids())
        persisted = self.store.events_for_run("contract-run-event")
        self.assertEqual(tuple(item.event_id for item in persisted), ("contract-event-delivery",))
        self.assertIsNotNone(persisted[0].published_at)

    def test_contract_commits_exact_kernel_buffer_after_long_running_boundary(self) -> None:
        buffered = TransactionalAuditJournal("contract-task-buffered", ())
        kernel = WorkflowKernel(buffered)
        kernel.transition(
            task_id="contract-task-buffered",
            to_state=TaskState.PLANNED,
            actor_type=ActorType.SYSTEM,
            actor_id="planner",
            reason="prepared before worker launch",
        )
        kernel.transition(
            task_id="contract-task-buffered",
            to_state=TaskState.IMPLEMENTING,
            actor_type=ActorType.POLICY,
            actor_id="execution-policy",
            reason="authorized buffered execution",
        )

        with self.store.transaction(
            task_id="contract-task-buffered",
            run_id="contract-run-buffered",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            for transition in buffered.staged:
                transaction.append_transition_record(transition)
            transaction.append_run_record(
                stage=RunRecordStage.EXECUTION,
                payload={"status": "worker-finished"},
            )
            transaction.enqueue(
                self.event(
                    "contract-event-buffered",
                    task_id="contract-task-buffered",
                    run_id="contract-run-buffered",
                )
            )
            transaction.commit()

        self.assertEqual(
            self.store.task_history("contract-task-buffered"), buffered.staged
        )

    def test_contract_rejects_tampered_buffered_transition(self) -> None:
        buffered = TransactionalAuditJournal("contract-task-tampered", ())
        record = WorkflowKernel(buffered).transition(
            task_id="contract-task-tampered",
            to_state=TaskState.PLANNED,
            actor_type=ActorType.SYSTEM,
            actor_id="planner",
            reason="valid before tampering",
        )
        tampered = record.model_copy(update={"reason": "changed after hashing"})

        with self.store.transaction(
            task_id="contract-task-tampered",
            run_id="contract-run-tampered",
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            with self.assertRaises(StateIntegrityError):
                transaction.append_transition_record(tampered)

        self.assertEqual(self.store.task_history("contract-task-tampered"), ())
