from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from jobslayer.agents.executor import (
    AgentExecutor,
    AgentExecutorError,
    AgentRunStillRunningError,
)
from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.domain.models import (
    ActorType,
    AgentInvocation,
    AgentRunResult,
    AgentRunStatus,
    ArtifactManifest,
    DecisionCard,
    DecisionKind,
    DecisionOption,
    EvidenceSummary,
    MergeReviewPackage,
    ReviewDisposition,
    ReviewDispositionStatus,
    ReviewReport,
    ReviewStatus,
    RiskLevel,
    TaskExecutionAuthorization,
    TaskExecutionOutcome,
    TaskExecutionStatus,
    TaskSpec,
    TaskState,
    ValidationProfile,
    WorkspaceManifest,
    WorkspacePatch,
    WorkspaceSpec,
)
from jobslayer.verification.engine import VerificationEngine
from jobslayer.workflow.kernel import WorkflowKernel
from jobslayer.workspace.manager import WorkspaceManager, WorkspaceOperationError


class ApplicationControllerError(RuntimeError):
    """Base error for rejected or interrupted application workflows."""


class ExecutionAuthorizationError(ApplicationControllerError):
    pass


class TaskExecutionError(ApplicationControllerError):
    pass


class ReviewPreparationError(ApplicationControllerError):
    pass


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class TaskExecutionController:
    """Own one Phase 0 implementation attempt through the human merge gate.

    The controller intentionally stops at ``MERGE_REVIEW``. It does not commit,
    push, merge, deploy, retry, or decide that a task is complete.
    """

    def __init__(
        self,
        *,
        kernel: WorkflowKernel,
        workspace_manager: WorkspaceManager,
        agent_executor: AgentExecutor,
        verification_engine: VerificationEngine,
        artifact_registry: ArtifactRegistry,
        poll_interval_seconds: float = 0.05,
        collection_grace_seconds: float = 2.0,
    ):
        if poll_interval_seconds <= 0 or collection_grace_seconds <= 0:
            raise ValueError("poll interval and collection grace must be positive")
        if verification_engine.artifact_registry is not artifact_registry:
            raise ValueError(
                "verification engine and controller must share one artifact registry"
            )
        self.kernel = kernel
        self.workspace_manager = workspace_manager
        self.agent_executor = agent_executor
        self.verification_engine = verification_engine
        self.artifact_registry = artifact_registry
        self.poll_interval_seconds = poll_interval_seconds
        self.collection_grace_seconds = collection_grace_seconds

    def execute_implementation(
        self,
        *,
        task: TaskSpec,
        invocation: AgentInvocation,
        validation_profile: ValidationProfile,
        authorization: TaskExecutionAuthorization,
        now: datetime | None = None,
    ) -> TaskExecutionOutcome:
        now = now or datetime.now(UTC)
        self._validate_execution_inputs(
            task, invocation, validation_profile, authorization, now
        )

        task_artifact = self._register_model(
            task,
            task_id=task.task_id,
            artifact_type="task-specification",
            producer="task-execution-controller",
        )
        authorization_artifact = self._register_model(
            authorization,
            task_id=task.task_id,
            artifact_type="task-execution-authorization",
            producer="task-execution-controller",
        )
        profile_artifact = self._register_model(
            validation_profile,
            task_id=task.task_id,
            artifact_type="validation-profile",
            producer="task-execution-controller",
        )
        workspace = self.workspace_manager.create(
            WorkspaceSpec(
                workspace_id=invocation.run_spec.workspace_id,
                task_id=task.task_id,
                base_commit=task.base_commit,
            )
        )
        self.kernel.transition(
            task_id=task.task_id,
            to_state=TaskState.PLANNED,
            actor_type=ActorType.SYSTEM,
            actor_id="task-execution-controller",
            reason="validated task specification and prepared an isolated workspace",
            evidence_ids=(task_artifact.artifact_id, profile_artifact.artifact_id),
        )
        self.kernel.transition(
            task_id=task.task_id,
            to_state=TaskState.IMPLEMENTING,
            actor_type=authorization.actor_type,
            actor_id=authorization.actor_id,
            reason="authorized one governed implementation attempt",
            evidence_ids=(
                authorization.authorization_id,
                authorization_artifact.artifact_id,
                workspace.workspace_id,
            ),
        )

        try:
            handle = self.agent_executor.start(invocation, workspace)
            if (
                handle.run_id != invocation.run_spec.run_id
                or handle.workspace_id != workspace.workspace_id
                or handle.executor_type != invocation.run_spec.executor_type
            ):
                raise TaskExecutionError(
                    "agent handle does not match its invocation and workspace"
                )
            agent_run = self._collect_agent(handle.run_id, invocation)
            if (
                agent_run.run_id != handle.run_id
                or agent_run.external_id != handle.external_id
                or agent_run.workspace_id != workspace.workspace_id
                or agent_run.executor_type != invocation.run_spec.executor_type
            ):
                raise TaskExecutionError(
                    "agent result does not match its run handle and workspace"
                )
        except (AgentExecutorError, TaskExecutionError, OSError) as exc:
            failure = self._register_failure(
                task_id=task.task_id,
                run_id=invocation.run_spec.run_id,
                code="agent_lifecycle_error",
                summary="agent executor did not produce a terminal result",
                details={"error_type": type(exc).__name__, "error": str(exc)},
            )
            self.kernel.transition(
                task_id=task.task_id,
                to_state=TaskState.FAILED,
                actor_type=ActorType.SYSTEM,
                actor_id="task-execution-controller",
                reason="agent lifecycle failed before a terminal result was available",
                evidence_ids=(failure.artifact_id,),
            )
            raise TaskExecutionError(
                "agent executor did not produce a terminal result"
            ) from exc

        agent_run_artifact = self._register_model(
            agent_run,
            task_id=task.task_id,
            run_id=agent_run.run_id,
            artifact_type="agent-run-result",
            producer="task-execution-controller",
        )
        raw_event_artifact = self.artifact_registry.register_file(
            agent_run.raw_event_log_path,
            task_id=task.task_id,
            run_id=agent_run.run_id,
            artifact_type="agent-raw-event-log",
            producer=agent_run.executor_type,
        )
        stderr_artifact = self.artifact_registry.register_file(
            agent_run.stderr_log_path,
            task_id=task.task_id,
            run_id=agent_run.run_id,
            artifact_type="agent-stderr-log",
            producer=agent_run.executor_type,
        )
        common = {
            "task_id": task.task_id,
            "workspace": workspace,
            "task_artifact": task_artifact,
            "authorization_artifact": authorization_artifact,
            "validation_profile_artifact": profile_artifact,
            "agent_run": agent_run,
            "agent_run_artifact": agent_run_artifact,
            "raw_event_artifact": raw_event_artifact,
            "stderr_artifact": stderr_artifact,
        }

        log_mismatches = []
        if raw_event_artifact.sha256 != agent_run.raw_event_log_sha256:
            log_mismatches.append("raw event log")
        if stderr_artifact.sha256 != agent_run.stderr_log_sha256:
            log_mismatches.append("stderr log")
        if log_mismatches:
            return self._fail_after_run(
                common=common,
                code="agent_log_integrity_mismatch",
                summary="agent result hashes did not match registered log bytes",
                details={"mismatched_logs": log_mismatches},
            )

        if agent_run.status is not AgentRunStatus.COMPLETED:
            return self._fail_after_run(
                common=common,
                code="agent_run_unsuccessful",
                summary=f"agent run ended with status {agent_run.status.value}",
                details={
                    "exit_code": agent_run.exit_code,
                    "error_summary": agent_run.error_summary,
                },
            )

        try:
            patch = self.workspace_manager.collect_patch(workspace, task)
        except WorkspaceOperationError as exc:
            return self._fail_after_run(
                common=common,
                code="workspace_patch_rejected",
                summary="workspace changes could not be admitted as a task patch",
                details={"error_type": type(exc).__name__, "error": str(exc)},
            )

        patch_artifact = self.artifact_registry.register_bytes(
            task_id=task.task_id,
            run_id=agent_run.run_id,
            artifact_type="workspace-patch",
            producer="task-execution-controller",
            content=patch.patch_text.encode("utf-8"),
            metadata={"changed_paths": list(patch.changed_paths)},
        )
        if patch_artifact.sha256 != patch.sha256:
            raise TaskExecutionError("registered patch hash differs from workspace patch")
        if not patch.changed_paths:
            return self._fail_after_run(
                common=common,
                code="empty_patch",
                summary="agent completed without producing an in-scope change",
                details={},
                patch=patch,
                patch_artifact=patch_artifact,
            )

        self.kernel.transition(
            task_id=task.task_id,
            to_state=TaskState.VERIFYING,
            actor_type=ActorType.SYSTEM,
            actor_id="task-execution-controller",
            reason="collected an in-scope patch for deterministic verification",
            evidence_ids=(
                agent_run_artifact.artifact_id,
                patch_artifact.artifact_id,
                profile_artifact.artifact_id,
            ),
        )
        verification_report = self.verification_engine.verify(
            task=task,
            workspace=workspace,
            patch=patch,
            profile=validation_profile,
        )
        verification_artifact = self._register_model(
            verification_report,
            task_id=task.task_id,
            artifact_type="verification-report",
            producer="verification-engine",
        )
        if verification_report.passes_gate:
            self.kernel.transition(
                task_id=task.task_id,
                to_state=TaskState.REVIEWING,
                actor_type=ActorType.SYSTEM,
                actor_id="task-execution-controller",
                reason="required deterministic checks passed",
                verification_report=verification_report,
                evidence_ids=(
                    verification_artifact.artifact_id,
                    patch_artifact.artifact_id,
                ),
            )
            status = TaskExecutionStatus.AWAITING_REVIEW
            state = TaskState.REVIEWING
        else:
            self.kernel.transition(
                task_id=task.task_id,
                to_state=TaskState.REPAIRING,
                actor_type=ActorType.SYSTEM,
                actor_id="task-execution-controller",
                reason="one or more required deterministic checks failed",
                verification_report=verification_report,
                evidence_ids=(
                    verification_artifact.artifact_id,
                    patch_artifact.artifact_id,
                ),
            )
            status = TaskExecutionStatus.REPAIR_REQUIRED
            state = TaskState.REPAIRING

        return TaskExecutionOutcome(
            **common,
            status=status,
            state=state,
            patch=patch,
            patch_artifact=patch_artifact,
            verification_report=verification_report,
            verification_artifact=verification_artifact,
        )

    def prepare_merge_review(
        self,
        *,
        task: TaskSpec,
        outcome: TaskExecutionOutcome,
        review_report: ReviewReport,
    ) -> ReviewDisposition:
        if self.kernel.current_state(task.task_id) is not TaskState.REVIEWING:
            raise ReviewPreparationError("task is not awaiting implementation review")
        if outcome.task_id != task.task_id or outcome.state is not TaskState.REVIEWING:
            raise ReviewPreparationError("execution outcome is not reviewable for this task")
        if (
            outcome.patch is None
            or outcome.patch_artifact is None
            or outcome.verification_report is None
            or outcome.verification_artifact is None
        ):
            raise ReviewPreparationError("execution outcome lacks patch or verification evidence")
        if not outcome.verification_report.passes_gate:
            raise ReviewPreparationError("merge review requires passing verification")
        if review_report.task_id != task.task_id:
            raise ReviewPreparationError("review report belongs to a different task")
        if review_report.patch_sha256 != outcome.patch.sha256:
            raise ReviewPreparationError("review report evaluated a different patch")
        if (
            review_report.status is ReviewStatus.ACCEPTED
            and outcome.verification_report.report_id
            not in review_report.evidence_ids
        ):
            raise ReviewPreparationError(
                "accepted review must cite the verification report it evaluated"
            )

        review_artifact = self._register_model(
            review_report,
            task_id=task.task_id,
            artifact_type="implementation-review-report",
            producer=review_report.reviewer_id,
        )
        if review_report.status is ReviewStatus.CHANGES_REQUESTED:
            self.kernel.transition(
                task_id=task.task_id,
                to_state=TaskState.REPAIRING,
                actor_type=review_report.reviewer_actor_type,
                actor_id=review_report.reviewer_id,
                reason="implementation review requested changes",
                evidence_ids=(review_report.review_id, review_artifact.artifact_id),
            )
            return ReviewDisposition(
                task_id=task.task_id,
                status=ReviewDispositionStatus.REPAIR_REQUIRED,
                state=TaskState.REPAIRING,
                review_report=review_report,
                review_artifact=review_artifact,
            )

        decision_card = self._build_merge_decision_card(
            task=task,
            outcome=outcome,
            review_report=review_report,
            review_artifact=review_artifact,
        )
        decision_card_artifact = self._register_model(
            decision_card,
            task_id=task.task_id,
            artifact_type="merge-decision-card",
            producer="task-execution-controller",
        )
        self.kernel.transition(
            task_id=task.task_id,
            to_state=TaskState.MERGE_REVIEW,
            actor_type=review_report.reviewer_actor_type,
            actor_id=review_report.reviewer_id,
            reason="implementation review accepted the patch for human merge decision",
            evidence_ids=(
                review_report.review_id,
                review_artifact.artifact_id,
                decision_card.card_id,
                decision_card_artifact.artifact_id,
            ),
        )
        package = MergeReviewPackage(
            task_id=task.task_id,
            patch=outcome.patch,
            verification_report=outcome.verification_report,
            review_report=review_report,
            review_artifact=review_artifact,
            decision_card=decision_card,
            decision_card_artifact=decision_card_artifact,
        )
        return ReviewDisposition(
            task_id=task.task_id,
            status=ReviewDispositionStatus.AWAITING_MERGE_DECISION,
            state=TaskState.MERGE_REVIEW,
            review_report=review_report,
            review_artifact=review_artifact,
            merge_review_package=package,
        )

    def _collect_agent(
        self, run_id: str, invocation: AgentInvocation
    ) -> AgentRunResult:
        deadline = (
            time.monotonic()
            + invocation.run_spec.timeout_seconds
            + self.collection_grace_seconds
        )
        cancellation_requested = False
        while True:
            try:
                return self.agent_executor.collect(run_id)
            except AgentRunStillRunningError:
                if time.monotonic() >= deadline:
                    if not cancellation_requested:
                        self.agent_executor.cancel(run_id)
                        cancellation_requested = True
                        deadline = time.monotonic() + self.collection_grace_seconds
                    else:
                        raise TaskExecutionError(
                            "agent did not become collectable after cancellation"
                        )
                time.sleep(self.poll_interval_seconds)

    def _fail_after_run(
        self,
        *,
        common: dict[str, Any],
        code: str,
        summary: str,
        details: dict[str, Any],
        patch: WorkspacePatch | None = None,
        patch_artifact: ArtifactManifest | None = None,
    ) -> TaskExecutionOutcome:
        agent_run = common["agent_run"]
        failure = self._register_failure(
            task_id=common["task_id"],
            run_id=agent_run.run_id,
            code=code,
            summary=summary,
            details=details,
        )
        self.kernel.transition(
            task_id=common["task_id"],
            to_state=TaskState.FAILED,
            actor_type=ActorType.SYSTEM,
            actor_id="task-execution-controller",
            reason=summary,
            evidence_ids=(
                common["agent_run_artifact"].artifact_id,
                failure.artifact_id,
                *((patch_artifact.artifact_id,) if patch_artifact else ()),
            ),
        )
        return TaskExecutionOutcome(
            **common,
            status=TaskExecutionStatus.FAILED,
            state=TaskState.FAILED,
            patch=patch,
            patch_artifact=patch_artifact,
            failure_artifact=failure,
        )

    def _register_failure(
        self,
        *,
        task_id: str,
        run_id: str,
        code: str,
        summary: str,
        details: dict[str, Any],
    ) -> ArtifactManifest:
        return self.artifact_registry.register_bytes(
            task_id=task_id,
            run_id=run_id,
            artifact_type="task-execution-failure",
            producer="task-execution-controller",
            content=_canonical_json_bytes(
                {
                    "schema_version": "1.0",
                    "task_id": task_id,
                    "run_id": run_id,
                    "code": code,
                    "summary": summary,
                    "details": details,
                }
            ),
            metadata={"failure_code": code},
        )

    def _register_model(
        self,
        model: Any,
        *,
        task_id: str,
        artifact_type: str,
        producer: str,
        run_id: str | None = None,
    ) -> ArtifactManifest:
        return self.artifact_registry.register_bytes(
            task_id=task_id,
            run_id=run_id,
            artifact_type=artifact_type,
            producer=producer,
            content=_canonical_json_bytes(model.model_dump(mode="json")),
        )

    def _validate_execution_inputs(
        self,
        task: TaskSpec,
        invocation: AgentInvocation,
        profile: ValidationProfile,
        authorization: TaskExecutionAuthorization,
        now: datetime,
    ) -> None:
        if now.tzinfo is None:
            raise ExecutionAuthorizationError("execution time must include a timezone")
        if self.kernel.current_state(task.task_id) is not TaskState.DRAFT:
            raise TaskExecutionError("Phase 0 controller only starts draft tasks")
        spec = invocation.run_spec
        if spec.task_id != task.task_id:
            raise TaskExecutionError("agent invocation belongs to a different task")
        if profile.profile_id != task.validation_profile:
            raise TaskExecutionError("validation profile does not match the task")
        if spec.max_attempts != 1:
            raise TaskExecutionError("Phase 0 controller supports exactly one attempt")
        if authorization.task_id != task.task_id:
            raise ExecutionAuthorizationError(
                "execution authorization belongs to a different task"
            )
        if now < authorization.issued_at or now >= authorization.valid_until:
            raise ExecutionAuthorizationError("execution authorization is not valid now")
        if _RISK_ORDER[task.risk] > _RISK_ORDER[authorization.maximum_risk]:
            raise ExecutionAuthorizationError(
                "task risk exceeds the execution authorization"
            )
        if task.deadline is not None:
            if task.deadline.tzinfo is None:
                raise ExecutionAuthorizationError("task deadline must include a timezone")
            if now >= task.deadline:
                raise ExecutionAuthorizationError("task deadline has passed")

    @staticmethod
    def _build_merge_decision_card(
        *,
        task: TaskSpec,
        outcome: TaskExecutionOutcome,
        review_report: ReviewReport,
        review_artifact: ArtifactManifest,
    ) -> DecisionCard:
        assert outcome.patch is not None
        assert outcome.patch_artifact is not None
        assert outcome.verification_report is not None
        assert outcome.verification_artifact is not None
        return DecisionCard(
            card_id=f"merge-card-{uuid4().hex}",
            task_id=task.task_id,
            decision_kind=DecisionKind.MERGE_REVIEW,
            title=f"合并审查：{task.title}",
            decision_required="是否授权将这份已验证、已审查的补丁集成到本地主分支",
            why_now="确定性验证已通过，独立实现审查已接受，控制器正在等待授权决定。",
            risk=task.risk,
            reversible=False,
            affected_artifact_ids=(
                outcome.patch_artifact.artifact_id,
                outcome.verification_artifact.artifact_id,
                review_artifact.artifact_id,
            ),
            evidence=(
                EvidenceSummary(
                    evidence_id=outcome.verification_report.report_id,
                    evidence_type="verification-report",
                    summary="所有必需的确定性检查均通过",
                    sha256=outcome.verification_artifact.sha256,
                ),
                EvidenceSummary(
                    evidence_id=outcome.patch_artifact.artifact_id,
                    evidence_type="workspace-patch",
                    summary=(
                        "受路径政策约束的补丁："
                        + ", ".join(outcome.patch.changed_paths)
                    ),
                    sha256=outcome.patch_artifact.sha256,
                ),
                EvidenceSummary(
                    evidence_id=review_report.review_id,
                    evidence_type="implementation-review",
                    summary=review_report.summary,
                    sha256=review_artifact.sha256,
                ),
            ),
            options=(
                DecisionOption(
                    option_id="approve",
                    label="批准集成",
                    description="接受当前补丁及其验证、审查证据。",
                    consequences=(
                        "工作流进入 integrating；只有补丁与基线复核通过并完成本地 Git "
                        "快进后才进入 completed。"
                    ),
                    recommended=True,
                ),
                DecisionOption(
                    option_id="request_changes",
                    label="要求修改",
                    description="将补丁退回修复，并保留全部证据。",
                    consequences="工作流进入 repairing，需要新的实现和验证。",
                ),
                DecisionOption(
                    option_id="reject",
                    label="拒绝任务",
                    description="不接受当前实现并终止任务。",
                    consequences="工作流进入 cancelled；工作区仍保留供审计。",
                ),
            ),
            default_option_id="approve",
        )
