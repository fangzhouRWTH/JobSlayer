from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.local_orchestration import LocalTaskPlanStore
from jobslayer.adapters.local_planning_agent import LocalPlanningAgent
from jobslayer.adapters.local_task_manager_runs import (
    LocalTaskManagerRunStore,
    TaskManagerRunJournalError,
    TaskManagerRunRevisionConflictError,
)
from jobslayer.application.task_manager_execution import (
    StaleTaskManagerRunRevisionError,
    TaskManagerExecutionAdapterUnavailableError,
    TaskManagerExecutionNodeNotReadyError,
    TaskManagerExecutionProviderError,
    TaskManagerExecutionService,
    TaskManagerPlanNotFinalizedError,
    TaskManagerRunAlreadyExistsError,
)
from jobslayer.application.task_orchestration import TaskOrchestrationService
from jobslayer.domain.models import (
    ActorType,
    CheckStatus,
    CommandResult,
    CommandStatus,
    SourceIntegrationResult,
    TaskState,
    WorkspaceInspection,
)
from jobslayer.orchestration import TaskPlanNodeKind
from jobslayer.task_manager import (
    ManagedExecutionObservation,
    ManagedExecutionReference,
    ManagedExecutionRequest,
    ManagedExecutionStatus,
    ManagedCheckpointRequest,
    ManagedCheckpointResult,
    ManagedVerificationEvidence,
    ManagedValidationCheckEvidence,
    TaskManagerRunStage,
)
from tests.task_manager_fixtures import (
    FIXTURE_TARGET_ID,
    FixtureExecutionTargetRegistry,
)


class FixtureManagedExecutor:
    adapter_id = "fixture-managed-executor"

    def __init__(self, artifacts: LocalArtifactRegistry):
        self.artifacts = artifacts
        self.start_requests: list[ManagedExecutionRequest] = []
        self.status = ManagedExecutionStatus.RUNNING
        self.cursor = 1
        self.fail_start_once = False
        self.verification_changed_paths: tuple[str, ...] = ()
        self.verification_working_tree_clean = True

    def start_or_locate(
        self,
        request: ManagedExecutionRequest,
    ) -> ManagedExecutionReference:
        self.start_requests.append(request)
        if self.fail_start_once:
            self.fail_start_once = False
            raise RuntimeError("fixture provider connection dropped")
        manifest = self.artifacts.register_bytes(
            task_id=request.workflow_task_id,
            run_id=request.run_id,
            artifact_type="task-manager-executor-start",
            producer=self.adapter_id,
            content=request.model_dump_json().encode("utf-8"),
        )
        return ManagedExecutionReference(
            provider_start_key=request.provider_start_key,
            adapter_id=self.adapter_id,
            provider_run_id=f"provider-{request.provider_start_key}",
            started_at=datetime.now(UTC),
            evidence_artifact_ids=(manifest.artifact_id,),
        )

    def observe(
        self,
        reference: ManagedExecutionReference,
        *,
        after_cursor: str | None,
    ) -> ManagedExecutionObservation:
        del after_cursor
        request = self.start_requests[-1]
        manifest = self.artifacts.register_bytes(
            task_id=request.workflow_task_id,
            run_id=request.run_id,
            artifact_type="task-manager-executor-observation",
            producer=self.adapter_id,
            content=(
                f"{reference.provider_run_id}:{self.cursor}:{self.status.value}"
            ).encode("utf-8"),
        )
        return ManagedExecutionObservation(
            provider_run_id=reference.provider_run_id,
            status=self.status,
            cursor=f"cursor-{self.cursor}",
            summary=f"fixture execution is {self.status.value}",
            observed_at=datetime.now(UTC),
            evidence_artifact_ids=(manifest.artifact_id,),
        )

    def collect_verification_evidence(
        self,
        reference: ManagedExecutionReference,
    ) -> ManagedVerificationEvidence:
        request = self.start_requests[-1]
        inspection = WorkspaceInspection(
            workspace_id="fixture-workspace",
            task_id=request.execution_binding.task.task_id,
            head_commit=request.execution_binding.task.base_commit,
            branch_name="jobslayer/fixture-workspace",
            changed_paths=self.verification_changed_paths,
            working_tree_clean=self.verification_working_tree_clean,
        )
        manifest = self.artifacts.register_bytes(
            task_id=request.workflow_task_id,
            run_id=request.run_id,
            artifact_type="task-manager-verification-fixture",
            producer=self.adapter_id,
            content=inspection.model_dump_json().encode("utf-8"),
        )
        patch_hash = (
            "c" * 64 if self.verification_changed_paths else None
        )
        return ManagedVerificationEvidence(
            provider_run_id=reference.provider_run_id,
            source_commit=inspection.head_commit,
            source_patch_sha256=patch_hash,
            workspace=inspection,
            collected_at=datetime.now(UTC),
            evidence_artifact_ids=(manifest.artifact_id,),
        )


class FixtureManagedValidator:
    adapter_id = "fixture-local-validation"

    def __init__(self, artifacts: LocalArtifactRegistry):
        self.artifacts = artifacts
        self.start_requests: list[ManagedExecutionRequest] = []
        self.command_status = CommandStatus.PASSED

    def start_or_locate(
        self,
        request: ManagedExecutionRequest,
    ) -> ManagedExecutionReference:
        self.start_requests.append(request)
        artifact = self.artifacts.register_bytes(
            task_id=request.workflow_task_id,
            run_id=request.run_id,
            artifact_type="fixture-validation-start",
            producer=self.adapter_id,
            content=request.model_dump_json().encode("utf-8"),
        )
        return ManagedExecutionReference(
            provider_start_key=request.provider_start_key,
            adapter_id=self.adapter_id,
            provider_run_id=f"validation-{request.provider_start_key}",
            started_at=datetime.now(UTC),
            evidence_artifact_ids=(artifact.artifact_id,),
        )

    def observe(
        self,
        reference: ManagedExecutionReference,
        *,
        after_cursor: str | None,
    ) -> ManagedExecutionObservation:
        del after_cursor
        request = self.start_requests[-1]
        artifact = self.artifacts.register_bytes(
            task_id=request.workflow_task_id,
            run_id=request.run_id,
            artifact_type="fixture-validation-observation",
            producer=self.adapter_id,
            content=reference.provider_run_id.encode("utf-8"),
        )
        return ManagedExecutionObservation(
            provider_run_id=reference.provider_run_id,
            status=ManagedExecutionStatus.SUCCEEDED,
            cursor=f"cursor-{reference.provider_run_id}",
            summary="fixture validation commands terminated",
            observed_at=datetime.now(UTC),
            evidence_artifact_ids=(artifact.artifact_id,),
        )

    def collect_verification_evidence(
        self,
        reference: ManagedExecutionReference,
    ) -> ManagedVerificationEvidence:
        request = self.start_requests[-1]
        now = datetime.now(UTC)
        inspection = WorkspaceInspection(
            workspace_id="fixture-workspace",
            task_id=request.execution_binding.task.task_id,
            head_commit=request.execution_binding.task.base_commit,
            branch_name="jobslayer/fixture-workspace",
            changed_paths=(),
            working_tree_clean=True,
        )
        checks: list[ManagedValidationCheckEvidence] = []
        evidence_ids: list[str] = []
        for check in request.execution_binding.validation_profile.checks:
            result = CommandResult(
                command_id=f"validation-{check.check_id}",
                workspace_id=inspection.workspace_id,
                task_id=request.execution_binding.task.task_id,
                policy_id=(
                    request.execution_binding.validation_profile.command_policy.policy_id
                ),
                rule_id="complete-suite",
                argv=check.argv,
                cwd=check.cwd,
                status=self.command_status,
                exit_code=(0 if self.command_status is CommandStatus.PASSED else 1),
                started_at=now,
                finished_at=now,
                duration_ms=0,
                stdout="fixture validation output",
                stderr="",
                stdout_bytes=25,
                stderr_bytes=0,
                stdout_sha256=hashlib.sha256(
                    b"fixture validation output"
                ).hexdigest(),
                stderr_sha256=hashlib.sha256(b"").hexdigest(),
                stdout_truncated=False,
                stderr_truncated=False,
            )
            artifact = self.artifacts.register_bytes(
                task_id=request.workflow_task_id,
                run_id=request.run_id,
                artifact_type="fixture-validation-command-result",
                producer=self.adapter_id,
                content=result.model_dump_json().encode("utf-8"),
            )
            evidence_ids.append(artifact.artifact_id)
            checks.append(
                ManagedValidationCheckEvidence(
                    check_id=check.check_id,
                    required=check.required,
                    result=result,
                    evidence_artifact_id=artifact.artifact_id,
                )
            )
        inspection_artifact = self.artifacts.register_bytes(
            task_id=request.workflow_task_id,
            run_id=request.run_id,
            artifact_type="fixture-validation-workspace",
            producer=self.adapter_id,
            content=inspection.model_dump_json().encode("utf-8"),
        )
        evidence_ids.append(inspection_artifact.artifact_id)
        return ManagedVerificationEvidence(
            provider_run_id=reference.provider_run_id,
            source_commit=inspection.head_commit,
            workspace=inspection,
            collected_at=now,
            evidence_artifact_ids=tuple(evidence_ids),
            validation_checks=tuple(checks),
        )


class FixtureSourceIntegrator:
    adapter_id = "fixture-source-checkpoint"

    def __init__(self, artifacts: LocalArtifactRegistry):
        self.artifacts = artifacts
        self.requests: list[ManagedCheckpointRequest] = []

    def integrate_checkpoint(
        self,
        request: ManagedCheckpointRequest,
    ) -> ManagedCheckpointResult:
        self.requests.append(request)
        result = SourceIntegrationResult(
            integration_id=f"fixture-{request.integration_key}",
            task_id=request.workflow_task_id,
            workspace_id=request.verification_evidence.workspace.workspace_id,
            repository_root="/tmp/fixture-project",
            source_ref=f"verified-patch:{request.source_review.review_id}",
            target_ref=request.verification_evidence.workspace.branch_name,
            base_commit=request.verification_report.source_commit,
            commit="d" * 40,
            target_previous_commit=request.verification_report.source_commit,
            target_commit="d" * 40,
            source_patch_sha256=request.verification_report.source_patch_sha256,
            changed_paths=request.verification_evidence.workspace.changed_paths,
            approved_by=request.approved_by,
        )
        artifact = self.artifacts.register_bytes(
            task_id=request.workflow_task_id,
            run_id=request.run_id,
            artifact_type="fixture-source-checkpoint",
            producer=self.adapter_id,
            content=result.model_dump_json().encode("utf-8"),
        )
        return ManagedCheckpointResult(
            integration_key=request.integration_key,
            integration_result=result,
            evidence_artifact_ids=(artifact.artifact_id,),
        )


class TaskManagerExecutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.planning = TaskOrchestrationService(
            LocalTaskPlanStore(root / "plans"),
            LocalPlanningAgent(),
            actor_id="planner@example.invalid",
        )
        self.artifacts = LocalArtifactRegistry(root / "artifacts")
        self.store = LocalTaskManagerRunStore(root / "runs")
        self.executor = FixtureManagedExecutor(self.artifacts)
        self.validator = FixtureManagedValidator(self.artifacts)
        self.execution = TaskManagerExecutionService(
            self.store,
            self.artifacts,
            actor_id="executor@example.invalid",
            executor=self.executor,
            validator=self.validator,
            targets=FixtureExecutionTargetRegistry(),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _applied_plan(self, plan_id: str = "task-run-demo"):
        created = self.planning.create("开发一个可验证的悬挂系统案例", plan_id=plan_id)
        proposal = created.snapshot.pending_proposal
        assert proposal is not None
        applied = self.planning.apply_proposal(
            plan_id,
            proposal.proposal_id,
            expected_revision=created.sequence,
        )
        return self.planning.set_execution_target(
            plan_id,
            FIXTURE_TARGET_ID,
            self.execution.resolve_target(FIXTURE_TARGET_ID).source_bundle_sha256,
            expected_revision=applied.sequence,
        )

    def _finalized_plan(self, plan_id: str = "task-run-demo"):
        applied = self._applied_plan(plan_id)
        return self.planning.finalize(
            plan_id,
            expected_revision=applied.sequence,
        )

    def _accept_artifact_node(
        self,
        run_id: str,
        node_id: str,
        revision: int,
    ):
        started = self.execution.start_node(
            run_id,
            node_id,
            expected_run_revision=revision,
        )
        self.executor.status = ManagedExecutionStatus.SUCCEEDED
        self.executor.cursor += 1
        verifying = self.execution.observe_node(
            run_id,
            node_id,
            expected_run_revision=started.sequence,
        )
        reviewed = self.execution.verify_node(
            run_id,
            node_id,
            expected_run_revision=verifying.sequence,
        )
        return self.execution.accept_node_review(
            run_id,
            node_id,
            expected_run_revision=reviewed.sequence,
            rationale=f"Accepted deterministic fixture evidence for {node_id}.",
        )

    def _ready_validation_run(self, *, plan_id: str, run_id: str):
        finalized = self._finalized_plan(plan_id)
        current = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id=run_id,
        )
        for node_id in ("scope", "design", "implement"):
            current = self._accept_artifact_node(
                run_id,
                node_id,
                current.sequence,
            )
        return current

    def test_assembly_requires_and_binds_exact_finalized_plan_revision(self) -> None:
        applied = self._applied_plan()
        with self.assertRaises(TaskManagerPlanNotFinalizedError):
            self.execution.assemble(
                applied,
                expected_plan_revision=applied.sequence,
                run_id="tmrun-demo",
            )

        finalized = self.planning.finalize(
            applied.plan_id,
            expected_revision=applied.sequence,
        )
        with self.assertRaises(TaskManagerPlanNotFinalizedError):
            self.execution.assemble(
                finalized,
                expected_plan_revision=finalized.sequence - 1,
                run_id="tmrun-stale",
            )

        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-demo",
        )
        self.assertEqual(assembled.sequence, 1)
        self.assertEqual(assembled.snapshot.plan_id, finalized.plan_id)
        self.assertEqual(assembled.snapshot.plan_revision, finalized.sequence)
        self.assertEqual(assembled.snapshot.plan_record_hash, finalized.record_hash)
        self.assertEqual(assembled.snapshot.stage, TaskManagerRunStage.READY)
        for node in assembled.snapshot.nodes:
            self.assertEqual(
                node.transition_history[0].from_state,
                TaskState.DRAFT,
            )
            self.assertEqual(
                node.transition_history[0].to_state,
                TaskState.PLANNED,
            )
            if node.node.kind is TaskPlanNodeKind.HUMAN_GATE:
                self.assertEqual(node.workflow_state, TaskState.PLAN_REVIEW)
                self.assertEqual(len(node.transition_history), 2)
            else:
                self.assertEqual(node.workflow_state, TaskState.PLANNED)
                self.assertEqual(len(node.transition_history), 1)
        self.assertEqual(self.execution.get("tmrun-demo"), assembled)

        first = assembled.snapshot.nodes[0]
        changed_first = first.model_copy(
            update={"node": first.node.model_copy(update={"title": "rewritten"})}
        )
        changed_snapshot = assembled.snapshot.model_copy(
            update={
                "revision": 2,
                "updated_at": datetime.now(UTC),
                "nodes": (changed_first, *assembled.snapshot.nodes[1:]),
            }
        )
        with self.assertRaises(TaskManagerRunRevisionConflictError):
            self.store.append(
                changed_snapshot,
                actor_type=ActorType.SYSTEM,
                actor_id="tampering-fixture",
                operation="run.graph_rewritten",
            )

        with self.assertRaises(TaskManagerRunAlreadyExistsError):
            self.execution.assemble(
                finalized,
                expected_plan_revision=finalized.sequence,
                run_id="tmrun-another",
            )

    def test_execution_feedback_cannot_bypass_verification_or_dependencies(self) -> None:
        finalized = self._finalized_plan()
        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-feedback",
        )

        started = self.execution.start_node(
            assembled.run_id,
            "scope",
            expected_run_revision=assembled.sequence,
        )
        self.assertEqual(started.sequence, 3)
        scope = next(
            item for item in started.snapshot.nodes if item.node.node_id == "scope"
        )
        self.assertEqual(scope.workflow_state, TaskState.IMPLEMENTING)
        self.assertIsNotNone(scope.provider_start_key)
        self.assertIsNotNone(scope.provider_reference)
        self.assertEqual(started.snapshot.stage, TaskManagerRunStage.RUNNING)
        self.assertEqual(len(self.executor.start_requests), 1)

        running = self.execution.observe_node(
            assembled.run_id,
            "scope",
            expected_run_revision=started.sequence,
        )
        self.assertEqual(running.sequence, 4)
        running_scope = next(
            item for item in running.snapshot.nodes if item.node.node_id == "scope"
        )
        self.assertEqual(running_scope.workflow_state, TaskState.IMPLEMENTING)

        self.executor.status = ManagedExecutionStatus.SUCCEEDED
        self.executor.cursor = 2
        verifying = self.execution.observe_node(
            assembled.run_id,
            "scope",
            expected_run_revision=running.sequence,
        )
        verifying_scope = next(
            item for item in verifying.snapshot.nodes if item.node.node_id == "scope"
        )
        self.assertEqual(verifying_scope.workflow_state, TaskState.VERIFYING)
        self.assertNotEqual(verifying_scope.workflow_state, TaskState.COMPLETED)
        self.assertEqual(verifying.snapshot.stage, TaskManagerRunStage.VERIFYING)

        with self.assertRaises(TaskManagerExecutionNodeNotReadyError):
            self.execution.start_node(
                assembled.run_id,
                "design",
                expected_run_revision=verifying.sequence,
            )
        with self.assertRaises(StaleTaskManagerRunRevisionError):
            self.execution.observe_node(
                assembled.run_id,
                "scope",
                expected_run_revision=running.sequence,
            )

    def test_verified_artifact_review_acceptance_unlocks_the_next_node(self) -> None:
        finalized = self._finalized_plan("task-run-artifact-review")
        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-artifact-review",
        )
        started = self.execution.start_node(
            assembled.run_id,
            "scope",
            expected_run_revision=assembled.sequence,
        )
        self.executor.status = ManagedExecutionStatus.SUCCEEDED
        verifying = self.execution.observe_node(
            assembled.run_id,
            "scope",
            expected_run_revision=started.sequence,
        )

        reviewed = self.execution.verify_node(
            assembled.run_id,
            "scope",
            expected_run_revision=verifying.sequence,
        )
        reviewed_node = next(
            item for item in reviewed.snapshot.nodes if item.node.node_id == "scope"
        )
        self.assertEqual(reviewed_node.workflow_state, TaskState.REVIEWING)
        self.assertIsNotNone(reviewed_node.verification_report)
        self.assertTrue(reviewed_node.verification_report.passes_gate)
        assert reviewed_node.verification_artifact_id is not None
        self.assertTrue(
            self.artifacts.verify(
                self.artifacts.get(reviewed_node.verification_artifact_id)
            )
        )

        accepted = self.execution.accept_node_review(
            assembled.run_id,
            "scope",
            expected_run_revision=reviewed.sequence,
            rationale="Reviewed the evidence-backed artifact deliverables and criteria.",
        )
        accepted_node = next(
            item for item in accepted.snapshot.nodes if item.node.node_id == "scope"
        )
        self.assertEqual(
            accepted_node.workflow_state,
            TaskState.DELIVERABLE_ACCEPTED,
        )
        self.assertEqual(
            accepted_node.transition_history[-1].actor_type,
            ActorType.HUMAN,
        )
        assert accepted_node.review_artifact_id is not None
        self.assertTrue(
            self.artifacts.verify(self.artifacts.get(accepted_node.review_artifact_id))
        )

        next_started = self.execution.start_node(
            assembled.run_id,
            "design",
            expected_run_revision=accepted.sequence,
        )
        design = next(
            item for item in next_started.snapshot.nodes if item.node.node_id == "design"
        )
        self.assertEqual(design.workflow_state, TaskState.IMPLEMENTING)

    def test_source_changing_node_cannot_use_artifact_only_acceptance(self) -> None:
        finalized = self._finalized_plan("task-run-source-review")
        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-source-review",
        )
        started = self.execution.start_node(
            assembled.run_id,
            "scope",
            expected_run_revision=assembled.sequence,
        )
        self.executor.status = ManagedExecutionStatus.SUCCEEDED
        self.executor.verification_changed_paths = ("src/change.py",)
        self.executor.verification_working_tree_clean = False
        verifying = self.execution.observe_node(
            assembled.run_id,
            "scope",
            expected_run_revision=started.sequence,
        )
        reviewed = self.execution.verify_node(
            assembled.run_id,
            "scope",
            expected_run_revision=verifying.sequence,
        )

        with self.assertRaises(TaskManagerExecutionNodeNotReadyError):
            self.execution.accept_node_review(
                assembled.run_id,
                "scope",
                expected_run_revision=reviewed.sequence,
                rationale="must not bypass source integration",
            )
        self.assertEqual(
            self.execution.get(assembled.run_id).snapshot.stage,
            TaskManagerRunStage.VERIFYING,
        )

    def test_source_review_independent_approval_and_checkpoint_unlock_dependency(self) -> None:
        finalized = self._finalized_plan("task-run-source-checkpoint")
        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-source-checkpoint",
        )
        started = self.execution.start_node(
            assembled.run_id,
            "scope",
            expected_run_revision=assembled.sequence,
        )
        self.executor.status = ManagedExecutionStatus.SUCCEEDED
        self.executor.verification_changed_paths = ("src/change.py",)
        self.executor.verification_working_tree_clean = False
        verifying = self.execution.observe_node(
            assembled.run_id,
            "scope",
            expected_run_revision=started.sequence,
        )
        reviewed_facts = self.execution.verify_node(
            assembled.run_id,
            "scope",
            expected_run_revision=verifying.sequence,
        )
        reviewer = TaskManagerExecutionService(
            self.store,
            self.artifacts,
            actor_id="reviewer@example.invalid",
            targets=FixtureExecutionTargetRegistry(),
        )
        source_reviewed = reviewer.review_source_node(
            assembled.run_id,
            "scope",
            expected_run_revision=reviewed_facts.sequence,
            rationale="Reviewed the exact patch, target policy, and verification evidence.",
        )
        source_node = next(
            item
            for item in source_reviewed.snapshot.nodes
            if item.node.node_id == "scope"
        )
        self.assertEqual(source_node.workflow_state, TaskState.MERGE_REVIEW)
        self.assertEqual(source_node.source_review.reviewer_id, "reviewer@example.invalid")

        with self.assertRaises(TaskManagerExecutionNodeNotReadyError):
            reviewer.approve_source_checkpoint(
                assembled.run_id,
                "scope",
                expected_run_revision=source_reviewed.sequence,
                rationale="A reviewer must not approve the same patch.",
            )

        integrator = FixtureSourceIntegrator(self.artifacts)
        approver = TaskManagerExecutionService(
            self.store,
            self.artifacts,
            actor_id="approver@example.invalid",
            source_integrator=integrator,
            targets=FixtureExecutionTargetRegistry(),
        )
        approved = approver.approve_source_checkpoint(
            assembled.run_id,
            "scope",
            expected_run_revision=source_reviewed.sequence,
            rationale="Approve this exact patch only for the isolated run branch.",
        )
        approved_node = next(
            item for item in approved.snapshot.nodes if item.node.node_id == "scope"
        )
        self.assertEqual(approved_node.workflow_state, TaskState.INTEGRATING)
        self.assertEqual(approved_node.source_approved_by, "approver@example.invalid")
        self.assertTrue(approved_node.integration_key.startswith("tmintegrate-"))

        completed = approver.integrate_source_checkpoint(
            assembled.run_id,
            "scope",
            expected_run_revision=approved.sequence,
        )
        completed_node = next(
            item for item in completed.snapshot.nodes if item.node.node_id == "scope"
        )
        self.assertEqual(completed_node.workflow_state, TaskState.COMPLETED)
        self.assertEqual(completed_node.integration_result.approved_by, "approver@example.invalid")
        self.assertEqual(len(integrator.requests), 1)
        self.assertTrue(
            self.artifacts.verify(
                self.artifacts.get(completed_node.integration_artifact_id)
            )
        )

        next_started = self.execution.start_node(
            assembled.run_id,
            "design",
            expected_run_revision=completed.sequence,
        )
        design = next(
            item for item in next_started.snapshot.nodes if item.node.node_id == "design"
        )
        self.assertEqual(design.workflow_state, TaskState.IMPLEMENTING)

    def test_missing_adapter_rejects_dispatch_without_changing_run(self) -> None:
        finalized = self._finalized_plan()
        execution = TaskManagerExecutionService(
            self.store,
            self.artifacts,
            actor_id="executor@example.invalid",
            targets=FixtureExecutionTargetRegistry(),
        )
        assembled = execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-no-adapter",
        )

        with self.assertRaises(TaskManagerExecutionAdapterUnavailableError):
            execution.start_node(
                assembled.run_id,
                "scope",
                expected_run_revision=assembled.sequence,
            )
        self.assertEqual(execution.get(assembled.run_id), assembled)

    def test_validation_and_human_gate_nodes_cannot_be_dispatched_to_agent(self) -> None:
        finalized = self._finalized_plan()
        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-node-kind-gates",
        )

        for node_id in ("verify", "finalize"):
            with self.subTest(node_id=node_id):
                with self.assertRaises(TaskManagerExecutionNodeNotReadyError):
                    self.execution.start_node(
                        assembled.run_id,
                        node_id,
                        expected_run_revision=assembled.sequence,
                    )
        self.assertEqual(self.execution.get(assembled.run_id), assembled)
        self.assertEqual(self.executor.start_requests, [])

    def test_validation_node_runs_profile_and_requires_human_acceptance(self) -> None:
        ready = self._ready_validation_run(
            plan_id="task-run-validation",
            run_id="tmrun-validation",
        )

        started = self.execution.run_validation_node(
            ready.run_id,
            "verify",
            expected_run_revision=ready.sequence,
        )
        validation = next(
            item for item in started.snapshot.nodes if item.node.node_id == "verify"
        )
        self.assertEqual(validation.workflow_state, TaskState.IMPLEMENTING)
        self.assertTrue(validation.provider_start_key.startswith("tmvalidate-"))
        self.assertEqual(
            validation.provider_reference.adapter_id,
            self.validator.adapter_id,
        )
        self.assertEqual(len(self.validator.start_requests), 1)
        self.assertEqual(self.executor.start_requests[-1].node.node_id, "implement")

        verifying = self.execution.observe_node(
            ready.run_id,
            "verify",
            expected_run_revision=started.sequence,
        )
        reviewed = self.execution.verify_node(
            ready.run_id,
            "verify",
            expected_run_revision=verifying.sequence,
        )
        reviewed_node = next(
            item for item in reviewed.snapshot.nodes if item.node.node_id == "verify"
        )
        self.assertEqual(reviewed_node.workflow_state, TaskState.REVIEWING)
        self.assertTrue(reviewed_node.verification_report.passes_gate)
        self.assertEqual(
            tuple(
                check.check_id
                for check in reviewed_node.verification_report.checks
            ),
            (
                "complete-suite",
                "validation-run-terminal",
                "workspace-binding",
                "changed-path-policy",
                "immutable-evidence-integrity",
            ),
        )

        accepted = self.execution.accept_node_review(
            ready.run_id,
            "verify",
            expected_run_revision=reviewed.sequence,
            rationale="Reviewed the exact finalized validation profile and raw results.",
        )
        accepted_node = next(
            item for item in accepted.snapshot.nodes if item.node.node_id == "verify"
        )
        self.assertEqual(
            accepted_node.workflow_state,
            TaskState.DELIVERABLE_ACCEPTED,
        )
        self.assertEqual(
            accepted_node.transition_history[-1].actor_type,
            ActorType.HUMAN,
        )

    def test_failed_validation_enters_repair_and_cannot_be_accepted(self) -> None:
        ready = self._ready_validation_run(
            plan_id="task-run-validation-failure",
            run_id="tmrun-validation-failure",
        )
        self.validator.command_status = CommandStatus.FAILED
        started = self.execution.run_validation_node(
            ready.run_id,
            "verify",
            expected_run_revision=ready.sequence,
        )
        verifying = self.execution.observe_node(
            ready.run_id,
            "verify",
            expected_run_revision=started.sequence,
        )
        repairing = self.execution.verify_node(
            ready.run_id,
            "verify",
            expected_run_revision=verifying.sequence,
        )
        validation = next(
            item for item in repairing.snapshot.nodes if item.node.node_id == "verify"
        )
        self.assertEqual(validation.workflow_state, TaskState.REPAIRING)
        self.assertFalse(validation.verification_report.passes_gate)
        self.assertEqual(
            validation.verification_report.checks[0].status,
            CheckStatus.FAILED,
        )

        with self.assertRaises(TaskManagerExecutionNodeNotReadyError):
            self.execution.accept_node_review(
                ready.run_id,
                "verify",
                expected_run_revision=repairing.sequence,
                rationale="A failed required check must never be accepted.",
            )
        self.assertEqual(self.execution.get(ready.run_id), repairing)

    def test_missing_validator_rejects_ready_validation_without_state_change(self) -> None:
        ready = self._ready_validation_run(
            plan_id="task-run-no-validator",
            run_id="tmrun-no-validator",
        )
        execution_without_validator = TaskManagerExecutionService(
            self.store,
            self.artifacts,
            actor_id="executor@example.invalid",
            executor=self.executor,
            targets=FixtureExecutionTargetRegistry(),
        )

        with self.assertRaises(TaskManagerExecutionAdapterUnavailableError):
            execution_without_validator.run_validation_node(
                ready.run_id,
                "verify",
                expected_run_revision=ready.sequence,
            )
        self.assertEqual(execution_without_validator.get(ready.run_id), ready)

    def test_final_completion_gate_requires_verified_dependency_and_independent_actor(self) -> None:
        ready = self._ready_validation_run(
            plan_id="task-run-completion-gate",
            run_id="tmrun-completion-gate",
        )
        approver = TaskManagerExecutionService(
            self.store,
            self.artifacts,
            actor_id="final-approver@example.invalid",
            targets=FixtureExecutionTargetRegistry(),
        )

        with self.assertRaises(TaskManagerExecutionNodeNotReadyError):
            approver.approve_completion_gate(
                ready.run_id,
                "finalize",
                expected_run_revision=ready.sequence,
                rationale="must not approve before the validation dependency",
            )
        self.assertEqual(approver.get(ready.run_id), ready)

        started = self.execution.run_validation_node(
            ready.run_id,
            "verify",
            expected_run_revision=ready.sequence,
        )
        verifying = self.execution.observe_node(
            ready.run_id,
            "verify",
            expected_run_revision=started.sequence,
        )
        reviewed = self.execution.verify_node(
            ready.run_id,
            "verify",
            expected_run_revision=verifying.sequence,
        )
        accepted = self.execution.accept_node_review(
            ready.run_id,
            "verify",
            expected_run_revision=reviewed.sequence,
            rationale="Accepted the passing final validation evidence.",
        )

        with self.assertRaises(TaskManagerExecutionNodeNotReadyError):
            self.execution.approve_completion_gate(
                ready.run_id,
                "finalize",
                expected_run_revision=accepted.sequence,
                rationale="the dependency reviewer cannot self-approve completion",
            )
        self.assertEqual(self.execution.get(ready.run_id), accepted)

        completed = approver.approve_completion_gate(
            ready.run_id,
            "finalize",
            expected_run_revision=accepted.sequence,
            rationale="Independently reviewed the final report and accepted deliverables.",
        )
        gate = next(
            item for item in completed.snapshot.nodes if item.node.node_id == "finalize"
        )
        self.assertEqual(gate.workflow_state, TaskState.GATE_APPROVED)
        self.assertEqual(completed.snapshot.stage, TaskManagerRunStage.COMPLETED)
        self.assertEqual(gate.transition_history[-1].actor_type, ActorType.HUMAN)
        self.assertEqual(
            gate.transition_history[-1].actor_id,
            "final-approver@example.invalid",
        )
        decision_artifacts = tuple(
            evidence_id
            for evidence_id in gate.transition_history[-1].evidence_ids
            if evidence_id.startswith("artifact-")
        )
        self.assertGreaterEqual(len(decision_artifacts), 3)
        for artifact_id in decision_artifacts:
            self.assertTrue(self.artifacts.verify(self.artifacts.get(artifact_id)))

    def test_root_scope_gate_uses_kernel_evidence_and_unlocks_dependency(self) -> None:
        applied = self._applied_plan("task-run-scope-gate")
        scope = next(node for node in applied.snapshot.nodes if node.node_id == "scope")
        updated = self.planning.update_node(
            applied.plan_id,
            scope.node_id,
            expected_revision=applied.sequence,
            title=scope.title,
            description=scope.description,
            kind=TaskPlanNodeKind.HUMAN_GATE,
            executor_hint="authorized scope owner",
            acceptance_criteria=scope.acceptance_criteria,
            deliverables=scope.deliverables,
            constraints=scope.constraints,
            risks=scope.risks,
            verification_requirements=scope.verification_requirements,
            requires_human_decision=True,
        )
        finalized = self.planning.finalize(
            updated.plan_id,
            expected_revision=updated.sequence,
        )
        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-scope-gate",
        )

        confirmed = self.execution.confirm_scope_gate(
            assembled.run_id,
            "scope",
            expected_run_revision=assembled.sequence,
            rationale="The finalized revision fixes scope, validation, and path policy.",
        )
        gate = next(
            item for item in confirmed.snapshot.nodes if item.node.node_id == "scope"
        )
        self.assertEqual(gate.workflow_state, TaskState.GATE_APPROVED)
        self.assertEqual(gate.transition_history[-1].actor_type, ActorType.HUMAN)
        evidence_id = gate.transition_history[-1].evidence_ids[-1]
        self.assertTrue(self.artifacts.verify(self.artifacts.get(evidence_id)))

        started = self.execution.start_node(
            assembled.run_id,
            "design",
            expected_run_revision=confirmed.sequence,
        )
        design = next(
            item for item in started.snapshot.nodes if item.node.node_id == "design"
        )
        self.assertEqual(design.workflow_state, TaskState.IMPLEMENTING)
        with self.assertRaises(TaskManagerExecutionNodeNotReadyError):
            self.execution.confirm_scope_gate(
                assembled.run_id,
                "finalize",
                expected_run_revision=started.sequence,
                rationale="must not approve the final dependent gate early",
            )

    def test_start_recovery_reuses_persisted_provider_key(self) -> None:
        finalized = self._finalized_plan()
        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-recover-start",
        )
        self.executor.fail_start_once = True

        with self.assertRaises(TaskManagerExecutionProviderError):
            self.execution.start_node(
                assembled.run_id,
                "scope",
                expected_run_revision=assembled.sequence,
            )
        authorized = self.execution.get(assembled.run_id)
        self.assertEqual(authorized.sequence, 2)
        authorized_node = next(
            item for item in authorized.snapshot.nodes if item.node.node_id == "scope"
        )
        self.assertEqual(authorized_node.workflow_state, TaskState.IMPLEMENTING)
        self.assertIsNotNone(authorized_node.provider_start_key)
        self.assertIsNone(authorized_node.provider_reference)

        recovered = self.execution.start_node(
            assembled.run_id,
            "scope",
            expected_run_revision=authorized.sequence,
        )
        self.assertEqual(recovered.sequence, 3)
        self.assertEqual(len(self.executor.start_requests), 2)
        self.assertEqual(
            self.executor.start_requests[0].provider_start_key,
            self.executor.start_requests[1].provider_start_key,
        )

    def test_local_run_store_rejects_tampered_hash_chain(self) -> None:
        finalized = self._finalized_plan()
        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-tamper",
        )
        path = Path(self.temporary_directory.name) / "runs" / "tmrun-tamper.jsonl"
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace("tmrun-tamper", "tmrun-changed", 1), encoding="utf-8")

        with self.assertRaises(TaskManagerRunJournalError):
            self.store.history(assembled.run_id)

    def test_run_store_preserves_hashes_from_the_pre_verification_schema(self) -> None:
        finalized = self._finalized_plan("task-run-legacy-hash")
        assembled = self.execution.assemble(
            finalized,
            expected_plan_revision=finalized.sequence,
            run_id="tmrun-legacy-hash",
        )
        path = Path(self.temporary_directory.name) / "runs" / "tmrun-legacy-hash.jsonl"
        raw = json.loads(path.read_text(encoding="utf-8"))
        for node in raw["snapshot"]["nodes"]:
            for field in (
                "verification_evidence",
                "verification_report",
                "verification_artifact_id",
                "review_artifact_id",
                "source_review",
                "source_review_artifact_id",
                "source_approval_artifact_id",
                "source_approved_by",
                "integration_key",
                "integration_result",
                "integration_artifact_id",
            ):
                node.pop(field)
        unhashed = dict(raw)
        unhashed.pop("record_hash")
        raw["record_hash"] = hashlib.sha256(
            json.dumps(
                unhashed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        path.write_text(
            json.dumps(
                raw,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

        loaded = self.store.history(assembled.run_id)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].record_hash, raw["record_hash"])
        self.assertIsNone(loaded[0].snapshot.nodes[0].verification_report)


if __name__ == "__main__":
    unittest.main()
