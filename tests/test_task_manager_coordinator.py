from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.local_orchestration import LocalTaskPlanStore
from jobslayer.adapters.local_planning_agent import LocalPlanningAgent
from jobslayer.adapters.local_human_action_assistant import LocalHumanActionAssistant
from jobslayer.adapters.local_task_manager_coordinator import (
    LocalTaskManagerCoordinatorStore,
    TaskManagerCoordinatorJournalError,
)
from jobslayer.adapters.local_task_manager_runs import LocalTaskManagerRunStore
from jobslayer.adapters.sqlite_workers import SqliteWorkerLeaseStore
from jobslayer.application.task_manager_coordinator import (
    TaskManagerCoordinatorBusyError,
    TaskManagerSerialCoordinator,
)
from jobslayer.application.task_manager import TaskManagerService
from jobslayer.application.task_manager_execution import (
    StaleTaskManagerRunRevisionError,
    TaskManagerExecutionService,
)
from jobslayer.application.task_orchestration import TaskOrchestrationService
from jobslayer.domain.models import ActorType, TaskState
from jobslayer.task_manager import (
    ManagedExecutionStatus,
    TaskManagerCoordinatorAction,
    TaskManagerCoordinatorIntent,
    TaskManagerCoordinatorSnapshot,
    TaskManagerCoordinatorStage,
    TaskManagerHumanActionKind,
    TaskManagerHumanInteractionKind,
    TaskManagerRunStage,
)
from tests.task_manager_fixtures import FIXTURE_TARGET_ID, FixtureExecutionTargetRegistry
from tests.test_task_manager_execution import (
    FixtureManagedExecutor,
    FixtureManagedValidator,
)


class TaskManagerSerialCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.planning = TaskOrchestrationService(
            LocalTaskPlanStore(root / "plans"),
            LocalPlanningAgent(),
            actor_id="planner@example.invalid",
        )
        self.artifacts = LocalArtifactRegistry(root / "artifacts")
        self.run_store = LocalTaskManagerRunStore(root / "runs")
        self.executor = FixtureManagedExecutor(self.artifacts)
        self.validator = FixtureManagedValidator(self.artifacts)
        self.targets = FixtureExecutionTargetRegistry()
        self.execution = TaskManagerExecutionService(
            self.run_store,
            self.artifacts,
            actor_id="operator@example.invalid",
            executor=self.executor,
            validator=self.validator,
            targets=self.targets,
            human_action_assistant=LocalHumanActionAssistant(),
        )
        self.cursor_store = LocalTaskManagerCoordinatorStore(root / "coordinators")
        self.leases = SqliteWorkerLeaseStore(root / "coordinator-leases.sqlite3")
        self.leases.migrate()
        self.coordinator = TaskManagerSerialCoordinator(
            self.execution,
            self.cursor_store,
            self.leases,
            worker_id="fixture-serial-coordinator",
            lease_seconds=30,
        )

        created = self.planning.create(
            "开发一个可验证的小型应用",
            plan_id="serial-task",
        )
        assert created.snapshot.pending_proposal is not None
        applied = self.planning.apply_proposal(
            created.plan_id,
            created.snapshot.pending_proposal.proposal_id,
            expected_revision=created.sequence,
        )
        target = self.execution.resolve_target(FIXTURE_TARGET_ID)
        selected = self.planning.set_execution_target(
            applied.plan_id,
            FIXTURE_TARGET_ID,
            target.source_bundle_sha256,
            expected_revision=applied.sequence,
        )
        finalized = self.planning.finalize(
            selected.plan_id,
            expected_revision=selected.sequence,
        )
        self.run = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="serial-run",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def tick(self):
        current = self.execution.get(self.run.run_id)
        return self.coordinator.tick(
            self.run.run_id,
            expected_run_revision=current.sequence,
        )

    def accept_current_artifact(self, node_id: str) -> None:
        current = self.execution.get(self.run.run_id)
        self.execution.accept_node_review(
            self.run.run_id,
            node_id,
            expected_run_revision=current.sequence,
            rationale=f"Accepted deterministic evidence for {node_id}.",
        )

    def complete_executor_node(self, node_id: str) -> None:
        started = self.tick()
        self.assertEqual(
            started.performed_action,
            TaskManagerCoordinatorAction.START_NODE,
        )
        self.assertEqual(started.coordinator.cursor_node_id, node_id)
        self.executor.status = ManagedExecutionStatus.SUCCEEDED
        self.executor.cursor += 1
        observed = self.tick()
        self.assertEqual(
            observed.performed_action,
            TaskManagerCoordinatorAction.OBSERVE_NODE,
        )
        verified = self.tick()
        self.assertEqual(
            verified.performed_action,
            TaskManagerCoordinatorAction.VERIFY_NODE,
        )
        self.assertEqual(
            verified.coordinator.stage,
            TaskManagerCoordinatorStage.WAITING_REVIEW,
        )
        waiting = self.tick()
        self.assertFalse(waiting.side_effect_performed)
        self.assertEqual(
            waiting.performed_action,
            TaskManagerCoordinatorAction.WAIT_REVIEW,
        )
        self.accept_current_artifact(node_id)
        self.executor.status = ManagedExecutionStatus.RUNNING

    def reach_final_gate(self):
        for node_id in ("scope", "design", "implement"):
            self.complete_executor_node(node_id)
        self.tick()
        self.tick()
        self.tick()
        self.accept_current_artifact("verify")
        return self.tick()

    def test_serial_ticks_reach_validation_and_stop_at_human_completion_gate(self) -> None:
        gate = self.reach_final_gate()
        self.assertEqual(gate.coordinator.stage, TaskManagerCoordinatorStage.WAITING_HUMAN)
        self.assertEqual(gate.performed_action, TaskManagerCoordinatorAction.WAIT_HUMAN)
        self.assertFalse(gate.side_effect_performed)
        detail = TaskManagerService(
            self.planning,
            self.execution,
            self.coordinator,
        ).get(self.run.snapshot.plan_id)
        self.assertEqual(len(detail.human_actions), 1)
        guidance = detail.human_actions[0]
        self.assertEqual(guidance.kind, TaskManagerHumanActionKind.COMPLETION_APPROVAL)
        self.assertEqual(guidance.node_id, "finalize")
        self.assertEqual(guidance.expected_run_revision, gate.run.revision)
        self.assertEqual(guidance.permitted_actor_types, (ActorType.HUMAN,))
        self.assertGreaterEqual(len(guidance.steps), 6)
        self.assertTrue(guidance.evidence_to_review)
        self.assertTrue(
            any("main merge" in item for item in guidance.prohibited_actions)
        )

        approver = TaskManagerExecutionService(
            self.run_store,
            self.artifacts,
            actor_id="independent-approver@example.invalid",
            targets=self.targets,
        )
        current = approver.get(self.run.run_id)
        completed = approver.approve_completion_gate(
            self.run.run_id,
            "finalize",
            expected_run_revision=current.sequence,
            rationale="Independently accepted the complete verified run.",
        )
        self.assertEqual(completed.snapshot.stage, TaskManagerRunStage.COMPLETED)

        projected = self.tick()
        self.assertEqual(projected.coordinator.stage, TaskManagerCoordinatorStage.COMPLETED)
        self.assertEqual(projected.performed_action, TaskManagerCoordinatorAction.COMPLETE)
        self.assertFalse(projected.side_effect_performed)
        self.assertTrue(
            all(
                node.workflow_state
                in {
                    TaskState.COMPLETED,
                    TaskState.DELIVERABLE_ACCEPTED,
                    TaskState.GATE_APPROVED,
                }
                for node in projected.run.nodes
            )
        )

    def test_human_feedback_and_assistance_are_append_only_and_do_not_decide(self) -> None:
        gate = self.reach_final_gate()
        manager = TaskManagerService(
            self.planning,
            self.execution,
            self.coordinator,
        )
        detail = manager.get(self.run.snapshot.plan_id)
        guidance = detail.human_actions[0]
        decision = next(
            item for item in guidance.decisions if item.command is None
        )

        feedback = manager.record_human_action_feedback(
            detail.task.task_id,
            gate.run.run_id,
            guidance.guidance_id,
            decision_id=decision.decision_id,
            content="缺少窗口截图；请补充截图并再次请求最终验收。",
            expected_plan_revision=guidance.expected_plan_revision,
            expected_run_revision=guidance.expected_run_revision,
        )
        assert feedback.execution_run is not None
        self.assertEqual(feedback.execution_run.revision, gate.run.revision + 1)
        final_node = next(
            node for node in feedback.execution_run.nodes if node.node.node_id == "finalize"
        )
        self.assertEqual(final_node.workflow_state, TaskState.PLAN_REVIEW)
        self.assertEqual(
            final_node.human_interactions[-1].kind,
            TaskManagerHumanInteractionKind.FEEDBACK,
        )
        self.assertTrue(
            self.artifacts.verify(
                self.artifacts.get(
                    final_node.human_interactions[-1].evidence_artifact_ids[0]
                )
            )
        )
        with self.assertRaisesRegex(StaleTaskManagerRunRevisionError, "stale"):
            manager.record_human_action_feedback(
                detail.task.task_id,
                gate.run.run_id,
                guidance.guidance_id,
                decision_id=decision.decision_id,
                content="旧 revision 不得追加。",
                expected_plan_revision=guidance.expected_plan_revision,
                expected_run_revision=guidance.expected_run_revision,
            )

        current_guidance = feedback.human_actions[0]
        assisted = manager.request_human_action_assistance(
            detail.task.task_id,
            gate.run.run_id,
            current_guidance.guidance_id,
            content="请帮我整理下一轮验收需要补齐的内容。",
            expected_plan_revision=current_guidance.expected_plan_revision,
            expected_run_revision=current_guidance.expected_run_revision,
        )
        assert assisted.execution_run is not None
        self.assertEqual(assisted.execution_run.revision, gate.run.revision + 3)
        assisted_node = next(
            node for node in assisted.execution_run.nodes if node.node.node_id == "finalize"
        )
        self.assertEqual(assisted_node.workflow_state, TaskState.PLAN_REVIEW)
        self.assertEqual(
            tuple(item.kind for item in assisted_node.human_interactions[-2:]),
            (
                TaskManagerHumanInteractionKind.ASSISTANT_REQUEST,
                TaskManagerHumanInteractionKind.ASSISTANT_RESPONSE,
            ),
        )
        self.assertIn("不能替你作出", assisted_node.human_interactions[-1].content)

    def test_failed_provider_stops_until_explicit_retry(self) -> None:
        self.tick()
        self.executor.status = ManagedExecutionStatus.FAILED
        self.executor.cursor += 1
        failed = self.tick()
        self.assertEqual(failed.coordinator.stage, TaskManagerCoordinatorStage.NEEDS_ATTENTION)
        before = self.execution.get(self.run.run_id)
        stopped = self.tick()
        self.assertEqual(stopped.performed_action, TaskManagerCoordinatorAction.NEEDS_ATTENTION)
        self.assertFalse(stopped.side_effect_performed)
        self.assertEqual(self.execution.get(self.run.run_id), before)
        detail = TaskManagerService(
            self.planning,
            self.execution,
            self.coordinator,
        ).get(self.run.snapshot.plan_id)
        self.assertEqual(
            detail.human_actions[0].kind,
            TaskManagerHumanActionKind.FAILURE_RECOVERY,
        )
        self.assertEqual(detail.human_actions[0].node_id, "scope")
        self.assertGreaterEqual(len(detail.human_actions[0].steps), 5)

        retried = self.execution.start_node(
            self.run.run_id,
            "scope",
            expected_run_revision=before.sequence,
            retry=True,
        )
        self.assertEqual(
            next(node for node in retried.snapshot.nodes if node.node.node_id == "scope").workflow_state,
            TaskState.IMPLEMENTING,
        )

    def test_source_review_and_checkpoint_guidance_are_explicit_and_independent(
        self,
    ) -> None:
        self.tick()
        self.executor.status = ManagedExecutionStatus.SUCCEEDED
        self.executor.cursor += 1
        self.executor.verification_changed_paths = ("src/output.txt",)
        self.executor.verification_working_tree_clean = False
        self.tick()
        reviewed = self.tick()

        manager = TaskManagerService(
            self.planning,
            self.execution,
            self.coordinator,
        )
        source_guidance = manager.get(self.run.snapshot.plan_id).human_actions[0]
        self.assertEqual(
            source_guidance.kind,
            TaskManagerHumanActionKind.SOURCE_REVIEW,
        )
        self.assertEqual(
            source_guidance.permitted_actor_types,
            (ActorType.HUMAN, ActorType.AGENT),
        )
        self.assertTrue(
            any(value.startswith("patch:") for value in source_guidance.evidence_to_review)
        )
        self.assertTrue(any("完整 patch" in step for step in source_guidance.steps))

        agent_reviewer = TaskManagerExecutionService(
            self.run_store,
            self.artifacts,
            actor_id="independent-agent-reviewer",
            targets=self.targets,
        )
        agent_reviewer.review_source_node(
            self.run.run_id,
            "scope",
            expected_run_revision=reviewed.run.revision,
            rationale="Reviewed the exact fixture patch and accepted its bounded change.",
            reviewer_actor_type=ActorType.AGENT,
        )
        checkpoint_guidance = manager.get(self.run.snapshot.plan_id).human_actions[0]
        self.assertEqual(
            checkpoint_guidance.kind,
            TaskManagerHumanActionKind.SOURCE_CHECKPOINT_APPROVAL,
        )
        self.assertEqual(
            checkpoint_guidance.permitted_actor_types,
            (ActorType.HUMAN,),
        )
        self.assertTrue(
            any("不得由同一 Reviewer" in item for item in checkpoint_guidance.prohibited_actions)
        )

    def test_live_lease_rejects_a_second_tick_without_run_change(self) -> None:
        lease = self.leases.acquire(
            worker_id="other-worker",
            run_id=self.run.run_id,
            lease_seconds=30,
        )
        with self.assertRaises(TaskManagerCoordinatorBusyError):
            self.coordinator.tick(
                self.run.run_id,
                expected_run_revision=self.run.sequence,
            )
        self.assertEqual(self.execution.get(self.run.run_id), self.run)
        self.leases.release(lease.lease_id, expected_version=lease.version)

    def test_newer_run_revision_reconciles_pending_intent_without_duplicate_start(self) -> None:
        now = datetime.now(UTC)
        base = TaskManagerCoordinatorSnapshot(
            run_id=self.run.run_id,
            revision=1,
            run_revision=self.run.sequence,
            stage=TaskManagerCoordinatorStage.READY,
            cursor_node_id="scope",
            next_action=TaskManagerCoordinatorAction.START_NODE,
            reason="fixture ready action",
            created_at=now,
            updated_at=now,
        )
        self.cursor_store.append(
            base,
            actor_type=ActorType.SYSTEM,
            actor_id="fixture",
            operation="coordinator.cursor_initialized",
        )
        intent = TaskManagerCoordinatorIntent(
            intent_id="tmcoord-fixture-intent",
            run_id=self.run.run_id,
            node_id="scope",
            action=TaskManagerCoordinatorAction.START_NODE,
            expected_run_revision=self.run.sequence,
            created_at=now,
        )
        pending = base.model_copy(
            update={
                "revision": 2,
                "stage": TaskManagerCoordinatorStage.ADVANCING,
                "pending_intent": intent,
                "updated_at": now,
            }
        )
        self.cursor_store.append(
            pending,
            actor_type=ActorType.SYSTEM,
            actor_id="fixture",
            operation="coordinator.intent_recorded:start_node",
        )
        started = self.execution.start_node(
            self.run.run_id,
            "scope",
            expected_run_revision=self.run.sequence,
        )
        start_count = len(self.executor.start_requests)

        recovered = self.coordinator.tick(
            self.run.run_id,
            expected_run_revision=started.sequence,
        )
        self.assertTrue(recovered.recovered_intent)
        self.assertFalse(recovered.side_effect_performed)
        self.assertIsNone(recovered.coordinator.pending_intent)
        self.assertEqual(len(self.executor.start_requests), start_count)
        self.assertEqual(
            recovered.coordinator.next_action,
            TaskManagerCoordinatorAction.OBSERVE_NODE,
        )

    def test_partial_dispatch_resumes_the_same_pending_start_intent(self) -> None:
        self.executor.fail_start_once = True
        with self.assertRaises(RuntimeError):
            self.tick()

        partially_authorized = self.execution.get(self.run.run_id)
        node = next(
            item
            for item in partially_authorized.snapshot.nodes
            if item.node.node_id == "scope"
        )
        self.assertEqual(node.workflow_state, TaskState.IMPLEMENTING)
        self.assertIsNone(node.provider_reference)
        pending = self.coordinator.snapshot(self.run.run_id)
        assert pending is not None
        self.assertIsNotNone(pending.pending_intent)
        self.assertEqual(len(self.executor.start_requests), 1)

        recovered = self.coordinator.tick(
            self.run.run_id,
            expected_run_revision=partially_authorized.sequence,
        )

        self.assertTrue(recovered.recovered_intent)
        self.assertTrue(recovered.side_effect_performed)
        self.assertEqual(
            recovered.performed_action,
            TaskManagerCoordinatorAction.START_NODE,
        )
        self.assertIsNone(recovered.coordinator.pending_intent)
        self.assertEqual(
            recovered.coordinator.next_action,
            TaskManagerCoordinatorAction.OBSERVE_NODE,
        )
        self.assertEqual(len(self.executor.start_requests), 2)
        recovered_node = next(
            item
            for item in recovered.run.nodes
            if item.node.node_id == "scope"
        )
        self.assertIsNotNone(recovered_node.provider_reference)

    def test_cursor_hash_chain_rejects_tampering(self) -> None:
        self.tick()
        path = (
            Path(self.temporary_directory.name)
            / "coordinators"
            / f"{self.run.run_id}.jsonl"
        )
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[0]["snapshot"]["reason"] = "tampered"
        path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(TaskManagerCoordinatorJournalError):
            self.cursor_store.history(self.run.run_id)


if __name__ == "__main__":
    unittest.main()
