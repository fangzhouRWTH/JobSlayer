from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.sqlite_long_runs import SqliteLongRunStore
from jobslayer.adapters.sqlite_workers import SqliteWorkerLeaseStore
from jobslayer.application.long_running_execution import (
    LongRunningExecutionError,
    LongRunningExecutionService,
)
from jobslayer.long_running import (
    BillingMode,
    BudgetEnforcement,
    LongRunBudgetDimension,
    LongRunBudgetLimit,
    LongRunEventType,
    LongRunIntegrityError,
    LongRunStatus,
    LongRunUsage,
    LongRunningExecutionPolicy,
    ProviderRunObservation,
    ProviderRunReference,
    ProviderRunStatus,
)
from jobslayer.workers import WorkerLeaseStatus


class LongRunningExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.long_runs_path = self.root / "long-runs.sqlite3"
        self.workers_path = self.root / "workers.sqlite3"
        self.store = SqliteLongRunStore(self.long_runs_path)
        self.store.migrate()
        self.workers = SqliteWorkerLeaseStore(self.workers_path)
        self.workers.migrate()
        self.artifacts = LocalArtifactRegistry(self.root / "artifacts")
        self.service = LongRunningExecutionService(
            self.store,
            self.workers,
            self.artifacts,
        )
        self.now = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def policy(
        self,
        *,
        task_elapsed_ms: int = 100_000,
        attempt_elapsed_ms: int = 50_000,
        max_attempts: int = 2,
        lease_seconds: int = 30,
    ) -> LongRunningExecutionPolicy:
        return LongRunningExecutionPolicy(
            policy_id="local-subscription-long-v1",
            billing_mode=BillingMode.SUBSCRIPTION,
            limits=(
                LongRunBudgetLimit(
                    dimension=LongRunBudgetDimension.TASK_ELAPSED_MS,
                    enforcement=BudgetEnforcement.HARD,
                    maximum=task_elapsed_ms,
                ),
                LongRunBudgetLimit(
                    dimension=LongRunBudgetDimension.ATTEMPT_ELAPSED_MS,
                    enforcement=BudgetEnforcement.HARD,
                    maximum=attempt_elapsed_ms,
                ),
                LongRunBudgetLimit(
                    dimension=LongRunBudgetDimension.OUTPUT_TOKENS,
                    enforcement=BudgetEnforcement.SOFT,
                    maximum=50,
                ),
                LongRunBudgetLimit(
                    dimension=LongRunBudgetDimension.INPUT_TOKENS,
                    enforcement=BudgetEnforcement.OBSERVE_ONLY,
                ),
                LongRunBudgetLimit(
                    dimension=LongRunBudgetDimension.COST_MICROUSD,
                    enforcement=BudgetEnforcement.UNAVAILABLE,
                ),
            ),
            max_attempts=max_attempts,
            lease_seconds=lease_seconds,
            checkpoint_interval_ms=10_000,
            progress_warning_after_ms=10_000,
        )

    def reference(
        self,
        *,
        run_id: str = "run-1",
        external_run_id: str = "provider-run-1",
        at: datetime | None = None,
        provider_start_key: str | None = None,
    ) -> ProviderRunReference:
        request = self.service.start_request(run_id)
        evidence = self.artifacts.register_bytes(
            task_id=request.task_id,
            run_id=run_id,
            artifact_type="test.provider_start_raw",
            producer="test-adapter",
            content=(
                f"start:{request.attempt_number}:{external_run_id}"
            ).encode(),
            metadata={"provider_start_key": request.provider_start_key},
        )
        return ProviderRunReference(
            provider_adapter="test-adapter",
            external_run_id=external_run_id,
            provider_start_key=(
                provider_start_key or request.provider_start_key
            ),
            start_evidence_artifact_id=evidence.artifact_id,
            provider_session_id="session-1",
            started_at=at or self.now + timedelta(seconds=1),
        )

    def observation(
        self,
        *,
        at: datetime,
        cursor: int,
        status: ProviderRunStatus = ProviderRunStatus.RUNNING,
        external_run_id: str = "provider-run-1",
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        run_id: str = "run-1",
    ) -> ProviderRunObservation:
        evidence = self.artifacts.register_bytes(
            task_id="task-1",
            run_id=run_id,
            artifact_type="test.provider_event_raw",
            producer="test-adapter",
            content=(
                f"event:{external_run_id}:{cursor}:{status.value}:{at.isoformat()}"
            ).encode(),
        )
        return ProviderRunObservation(
            provider_adapter="test-adapter",
            external_run_id=external_run_id,
            status=status,
            event_cursor=cursor,
            usage=LongRunUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=tool_calls,
            ),
            raw_event_artifact_ids=(evidence.artifact_id,),
            observed_at=at,
        )

    def admit_and_bind(
        self,
        *,
        run_id: str = "run-1",
        policy: LongRunningExecutionPolicy | None = None,
    ):
        self.service.admit(
            run_id=run_id,
            task_id="task-1",
            policy=policy or self.policy(),
            now=self.now,
        )
        return self.service.bind_provider(
            run_id,
            reference=self.reference(run_id=run_id),
            worker_id="worker-1",
            now=self.now + timedelta(seconds=1),
        )

    def test_policy_distinguishes_enforcement_and_rejects_false_precision(self) -> None:
        policy = self.policy()

        self.assertEqual(
            policy.limit_for(LongRunBudgetDimension.COST_MICROUSD).enforcement,
            BudgetEnforcement.UNAVAILABLE,
        )
        self.assertEqual(
            policy.limit_for(LongRunBudgetDimension.INPUT_TOKENS).enforcement,
            BudgetEnforcement.OBSERVE_ONLY,
        )
        with self.assertRaisesRegex(ValidationError, "must have a hard"):
            LongRunningExecutionPolicy(
                policy_id="missing-attempt-limit",
                limits=(
                    LongRunBudgetLimit(
                        dimension=LongRunBudgetDimension.TASK_ELAPSED_MS,
                        enforcement=BudgetEnforcement.HARD,
                        maximum=10,
                    ),
                    LongRunBudgetLimit(
                        dimension=LongRunBudgetDimension.OUTPUT_TOKENS,
                        enforcement=BudgetEnforcement.SOFT,
                        maximum=10,
                    ),
                ),
            )
        with self.assertRaisesRegex(ValidationError, "cannot use provider cost"):
            LongRunningExecutionPolicy(
                policy_id="subscription-cost-hard-gate",
                billing_mode=BillingMode.SUBSCRIPTION,
                limits=(
                    LongRunBudgetLimit(
                        dimension=LongRunBudgetDimension.TASK_ELAPSED_MS,
                        enforcement=BudgetEnforcement.HARD,
                        maximum=100,
                    ),
                    LongRunBudgetLimit(
                        dimension=LongRunBudgetDimension.ATTEMPT_ELAPSED_MS,
                        enforcement=BudgetEnforcement.HARD,
                        maximum=50,
                    ),
                    LongRunBudgetLimit(
                        dimension=LongRunBudgetDimension.COST_MICROUSD,
                        enforcement=BudgetEnforcement.HARD,
                        maximum=1,
                    ),
                ),
            )

    def test_start_request_is_stable_until_explicit_retry(self) -> None:
        self.service.admit(
            run_id="run-1",
            task_id="task-1",
            policy=self.policy(),
            now=self.now,
        )
        first = self.service.start_request("run-1")
        reopened = LongRunningExecutionService(
            SqliteLongRunStore(self.long_runs_path),
            SqliteWorkerLeaseStore(self.workers_path),
            self.artifacts,
        )
        self.assertEqual(reopened.start_request("run-1"), first)
        with self.assertRaisesRegex(
            LongRunningExecutionError,
            "start identity mismatch",
        ):
            self.service.bind_provider(
                "run-1",
                reference=self.reference(provider_start_key="wrong-start-key"),
                worker_id="worker-1",
                now=self.now + timedelta(seconds=1),
            )

        self.service.bind_provider(
            "run-1",
            reference=self.reference(),
            worker_id="worker-1",
            now=self.now + timedelta(seconds=1),
        )
        self.service.finish(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=2),
                cursor=1,
                status=ProviderRunStatus.FAILED,
            ),
        )
        self.service.authorize_retry(
            "run-1",
            reason="explicit retry",
            now=self.now + timedelta(seconds=3),
        )
        second = self.service.start_request("run-1")
        self.assertEqual(second.attempt_number, 2)
        self.assertNotEqual(second.provider_start_key, first.provider_start_key)

    def test_allowed_lifecycle_persists_checkpoint_and_completion(self) -> None:
        bound = self.admit_and_bind()
        observed = self.service.observe(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=5),
                cursor=1,
                input_tokens=30,
                output_tokens=20,
                tool_calls=2,
            ),
            made_progress=True,
            stage="drafting graph",
        )
        evidence = self.artifacts.register_bytes(
            task_id="task-1",
            run_id="run-1",
            artifact_type="test.progress",
            producer="test",
            content=b"durable progress",
        )
        checkpoint = self.service.checkpoint(
            "run-1",
            stage="graph drafted",
            summary="Provider-neutral plan draft is available.",
            referenced_artifact_ids=(evidence.artifact_id,),
            workspace_state_sha256="a" * 64,
            now=self.now + timedelta(seconds=6),
        )
        completed = self.service.finish(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=7),
                cursor=2,
                status=ProviderRunStatus.COMPLETED,
                input_tokens=40,
                output_tokens=30,
                tool_calls=3,
            ),
        )

        self.assertEqual(bound.attempt_number, 1)
        self.assertTrue(observed.handle.last_progress_at > bound.last_progress_at)
        self.assertEqual(completed.status, LongRunStatus.COMPLETED)
        self.assertEqual(completed.checkpoint_count, 1)
        self.assertEqual(completed.usage.input_tokens, 40)
        self.assertEqual(
            tuple(event.event_type for event in self.store.history("run-1")),
            (
                LongRunEventType.ADMITTED,
                LongRunEventType.PROVIDER_BOUND,
                LongRunEventType.OBSERVED,
                LongRunEventType.CHECKPOINTED,
                LongRunEventType.COMPLETED,
            ),
        )
        self.assertEqual(self.store.checkpoints("run-1"), (checkpoint,))
        checkpoint_manifest = self.artifacts.get(checkpoint.checkpoint_artifact_id)
        self.assertEqual(checkpoint_manifest.sha256, checkpoint.checkpoint_sha256)
        self.assertTrue(self.artifacts.verify(checkpoint_manifest))
        reopened = SqliteLongRunStore(self.long_runs_path)
        self.assertEqual(reopened.get("run-1"), completed)
        self.assertEqual(len(reopened.history("run-1")), 5)

    def test_rejected_transitions_and_provider_identity_are_fail_closed(self) -> None:
        self.service.admit(
            run_id="run-1",
            task_id="task-1",
            policy=self.policy(),
            now=self.now,
        )
        with self.assertRaisesRegex(LongRunningExecutionError, "running long run"):
            self.service.checkpoint(
                "run-1",
                stage="too early",
                summary="No provider is bound.",
                now=self.now,
            )
        self.service.bind_provider(
            "run-1",
            reference=self.reference(),
            worker_id="worker-1",
            now=self.now + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(LongRunningExecutionError, "admitted run"):
            self.service.bind_provider(
                "run-1",
                reference=self.reference(external_run_id="provider-run-2"),
                worker_id="worker-2",
                now=self.now + timedelta(seconds=2),
            )
        with self.assertRaisesRegex(LongRunningExecutionError, "identity mismatch"):
            self.service.observe(
                "run-1",
                self.observation(
                    at=self.now + timedelta(seconds=2),
                    cursor=1,
                    external_run_id="provider-run-other",
                ),
            )
        self.service.observe(
            "run-1",
            self.observation(at=self.now + timedelta(seconds=2), cursor=2),
        )
        with self.assertRaisesRegex(LongRunningExecutionError, "cursor moved backwards"):
            self.service.observe(
                "run-1",
                self.observation(at=self.now + timedelta(seconds=3), cursor=1),
            )
        self.service.finish(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=4),
                cursor=3,
                status=ProviderRunStatus.COMPLETED,
            ),
        )
        with self.assertRaisesRegex(LongRunningExecutionError, "running long run"):
            self.service.request_cancel(
                "run-1",
                reason="too late",
                now=self.now + timedelta(seconds=5),
            )

    def test_hard_limit_persists_cancellation_before_adapter_signal(self) -> None:
        bound = self.admit_and_bind(
            policy=self.policy(attempt_elapsed_ms=10_000)
        )
        result = self.service.observe(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=12),
                cursor=1,
            ),
        )

        self.assertTrue(result.cancel_required)
        self.assertEqual(result.handle.status, LongRunStatus.CANCEL_REQUESTED)
        self.assertEqual(
            result.hard_exceeded,
            (LongRunBudgetDimension.ATTEMPT_ELAPSED_MS,),
        )
        lease = self.workers.get(bound.worker_lease_id)
        self.assertIsNotNone(lease)
        self.assertEqual(lease.status, WorkerLeaseStatus.CANCEL_REQUESTED)
        self.assertEqual(
            self.store.history("run-1")[-1].event_type,
            LongRunEventType.LIMIT_EXCEEDED,
        )
        cancelled = self.service.finish(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=13),
                cursor=2,
                status=ProviderRunStatus.CANCELLED,
            ),
        )
        self.assertEqual(cancelled.status, LongRunStatus.CANCELLED)

    def test_soft_limit_and_stalled_progress_warn_without_cancellation(self) -> None:
        self.admit_and_bind()
        result = self.service.observe(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=11),
                cursor=1,
                input_tokens=1_000_000,
                output_tokens=51,
            ),
        )

        self.assertFalse(result.cancel_required)
        self.assertTrue(result.progress_stalled)
        self.assertEqual(
            result.soft_exceeded,
            (LongRunBudgetDimension.OUTPUT_TOKENS,),
        )
        self.assertEqual(result.handle.status, LongRunStatus.RUNNING)
        self.assertEqual(
            self.store.history("run-1")[-1].event_type,
            LongRunEventType.LIMIT_WARNING,
        )
        self.assertTrue(
            self.store.history("run-1")[-1].details["progress_stalled"]
        )

    def test_restart_recovery_preserves_provider_run_and_attempt(self) -> None:
        bound = self.admit_and_bind(policy=self.policy(lease_seconds=2))
        with self.assertRaisesRegex(LongRunningExecutionError, "live worker lease"):
            self.service.recover(
                "run-1",
                self.observation(at=self.now + timedelta(seconds=2), cursor=1),
                worker_id="worker-replacement",
            )

        reopened_store = SqliteLongRunStore(self.long_runs_path)
        reopened_workers = SqliteWorkerLeaseStore(self.workers_path)
        restarted = LongRunningExecutionService(
            reopened_store,
            reopened_workers,
            self.artifacts,
        )
        recovered = restarted.recover(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=3),
                cursor=1,
            ),
            worker_id="worker-replacement",
        )

        self.assertEqual(recovered.attempt_number, 1)
        self.assertEqual(recovered.provider_run, bound.provider_run)
        self.assertNotEqual(recovered.worker_lease_id, bound.worker_lease_id)
        self.assertEqual(
            reopened_workers.get(bound.worker_lease_id).status,
            WorkerLeaseStatus.EXPIRED,
        )
        self.assertEqual(
            reopened_store.history("run-1")[-1].event_type,
            LongRunEventType.RECOVERED,
        )

    def test_retry_requires_authorization_and_is_bounded(self) -> None:
        self.admit_and_bind()
        failed = self.service.finish(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=2),
                cursor=1,
                status=ProviderRunStatus.FAILED,
                input_tokens=100,
            ),
        )
        self.assertEqual(failed.status, LongRunStatus.FAILED)
        retried = self.service.authorize_retry(
            "run-1",
            reason="operator approved one retry",
            now=self.now + timedelta(seconds=3),
        )
        self.assertEqual(retried.status, LongRunStatus.ADMITTED)
        self.assertEqual(retried.attempt_number, 2)
        self.assertEqual(retried.usage.input_tokens, 100)
        self.assertEqual(retried.attempt_usage.input_tokens, 0)
        self.service.bind_provider(
            "run-1",
            reference=self.reference(
                external_run_id="provider-run-2",
                at=self.now + timedelta(seconds=3),
            ),
            worker_id="worker-2",
            now=self.now + timedelta(seconds=3),
        )
        second_failure = self.service.finish(
            "run-1",
            self.observation(
                at=self.now + timedelta(seconds=4),
                cursor=1,
                status=ProviderRunStatus.FAILED,
                external_run_id="provider-run-2",
                input_tokens=200,
            ),
        )
        self.assertEqual(second_failure.usage.input_tokens, 300)
        with self.assertRaisesRegex(LongRunningExecutionError, "attempt budget"):
            self.service.authorize_retry(
                "run-1",
                reason="unapproved third attempt",
                now=self.now + timedelta(seconds=5),
            )

    def test_checkpoint_rejects_artifact_from_another_run(self) -> None:
        self.admit_and_bind()
        foreign = self.artifacts.register_bytes(
            task_id="task-1",
            run_id="run-other",
            artifact_type="test.progress",
            producer="test",
            content=b"foreign progress",
        )

        with self.assertRaisesRegex(LongRunningExecutionError, "does not belong"):
            self.service.checkpoint(
                "run-1",
                stage="unsafe checkpoint",
                summary="This reference must be rejected.",
                referenced_artifact_ids=(foreign.artifact_id,),
                now=self.now + timedelta(seconds=2),
            )
        self.assertEqual(self.store.checkpoints("run-1"), ())

    def test_store_is_append_only_and_detects_projection_tampering(self) -> None:
        self.service.admit(
            run_id="run-1",
            task_id="task-1",
            policy=self.policy(),
            now=self.now,
        )
        with sqlite3.connect(self.long_runs_path) as connection:
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                connection.execute(
                    "UPDATE long_run_events SET event_json = event_json "
                    "WHERE run_id = 'run-1' AND sequence = 1"
                )

        with sqlite3.connect(self.long_runs_path) as connection:
            row = connection.execute(
                "SELECT handle_json FROM long_runs WHERE run_id = 'run-1'"
            ).fetchone()
            payload = json.loads(row[0])
            payload["task_id"] = "task-tampered"
            connection.execute(
                "UPDATE long_runs SET handle_json = ? WHERE run_id = 'run-1'",
                (json.dumps(payload),),
            )
        with self.assertRaisesRegex(
            LongRunIntegrityError,
            "projection does not match event truth",
        ):
            self.store.get("run-1")


if __name__ == "__main__":
    unittest.main()
