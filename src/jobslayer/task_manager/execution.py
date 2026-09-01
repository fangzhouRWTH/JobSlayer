"""Provider-neutral execution contracts bound to one finalized task-plan revision."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import (
    ActorType,
    CommandResult,
    DomainModel,
    ReviewReport,
    ReviewStatus,
    SourceIntegrationResult,
    TaskState,
    TransitionRecord,
    VerificationReport,
    WorkspaceInspection,
)
from jobslayer.orchestration import IDENTIFIER_PATTERN, TaskPlanNode
from jobslayer.task_manager.binding import (
    TaskManagerDependencyAttachment,
    TaskManagerExecutionBinding,
)
from jobslayer.workflow.journal import verify_transition_sequence


class TaskManagerRunStage(str, Enum):
    READY = "ready"
    RUNNING = "running"
    NEEDS_ATTENTION = "needs_attention"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ManagedExecutionStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ManagedExecutionRequest(DomainModel):
    schema_version: str = "1.0"
    provider_start_key: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    run_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    plan_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    plan_revision: int = Field(ge=1)
    plan_record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow_task_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    execution_binding: TaskManagerExecutionBinding
    node: TaskPlanNode
    dependency_node_ids: tuple[str, ...] = ()
    prompt: str = Field(min_length=1, max_length=200_000)


class ManagedExecutionReference(DomainModel):
    schema_version: str = "1.0"
    provider_start_key: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    adapter_id: str = Field(min_length=1, max_length=160)
    provider_run_id: str = Field(min_length=1, max_length=256)
    started_at: datetime
    evidence_artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_reference(self) -> ManagedExecutionReference:
        if self.started_at.tzinfo is None:
            raise ValueError("execution reference start time needs a timezone")
        if len(self.evidence_artifact_ids) != len(set(self.evidence_artifact_ids)):
            raise ValueError("execution reference evidence ids must be unique")
        return self


class ManagedExecutionObservation(DomainModel):
    schema_version: str = "1.0"
    provider_run_id: str = Field(min_length=1, max_length=256)
    status: ManagedExecutionStatus
    cursor: str = Field(min_length=1, max_length=512)
    summary: str = Field(min_length=1, max_length=12_000)
    observed_at: datetime
    evidence_artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_observation(self) -> ManagedExecutionObservation:
        if self.observed_at.tzinfo is None:
            raise ValueError("execution observation time needs a timezone")
        if len(self.evidence_artifact_ids) != len(set(self.evidence_artifact_ids)):
            raise ValueError("execution observation evidence ids must be unique")
        return self


class ManagedValidationCheckEvidence(DomainModel):
    """One policy-constrained command result observed by a validation adapter."""

    schema_version: str = "1.0"
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    required: bool = True
    result: CommandResult
    evidence_artifact_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_check_result(self) -> ManagedValidationCheckEvidence:
        if self.result.command_id != f"validation-{self.check_id}":
            raise ValueError("validation result command id does not match its check")
        return self


class ManagedVerificationEvidence(DomainModel):
    """Adapter-observed facts; TaskManager still owns pass/fail and completion."""

    schema_version: str = "1.0"
    provider_run_id: str = Field(min_length=1, max_length=256)
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    source_patch_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    workspace: WorkspaceInspection
    collected_at: datetime
    evidence_artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    validation_checks: tuple[ManagedValidationCheckEvidence, ...] = ()
    dependency_attachments: tuple[TaskManagerDependencyAttachment, ...] = ()

    @model_validator(mode="after")
    def validate_evidence(self) -> ManagedVerificationEvidence:
        if self.collected_at.tzinfo is None:
            raise ValueError("verification evidence time needs a timezone")
        if self.source_commit.lower() != self.workspace.head_commit.lower():
            raise ValueError("verification source commit does not match workspace facts")
        if len(self.evidence_artifact_ids) != len(set(self.evidence_artifact_ids)):
            raise ValueError("verification evidence ids must be unique")
        if bool(self.workspace.changed_paths) != bool(self.source_patch_sha256):
            raise ValueError("changed paths and source patch evidence must agree")
        check_ids = tuple(item.check_id for item in self.validation_checks)
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("validation check evidence ids must be unique")
        if any(
            item.evidence_artifact_id not in self.evidence_artifact_ids
            for item in self.validation_checks
        ):
            raise ValueError("validation check artifacts must be included in evidence ids")
        dependency_ids = tuple(
            item.attachment_id for item in self.dependency_attachments
        )
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError("dependency attachment evidence ids must be unique")
        return self


class ManagedCheckpointRequest(DomainModel):
    """Exact reviewed patch authorized for one isolated run-branch checkpoint."""

    schema_version: str = "1.0"
    integration_key: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    run_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    workflow_task_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    node_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=96)
    provider_reference: ManagedExecutionReference
    execution_binding: TaskManagerExecutionBinding
    verification_report: VerificationReport
    verification_evidence: ManagedVerificationEvidence
    source_review: ReviewReport
    approved_by: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_checkpoint_request(self) -> ManagedCheckpointRequest:
        if (
            self.verification_report.task_id != self.workflow_task_id
            or self.verification_evidence.provider_run_id
            != self.provider_reference.provider_run_id
            or self.source_review.task_id != self.workflow_task_id
            or self.source_review.status is not ReviewStatus.ACCEPTED
            or self.source_review.patch_sha256
            != self.verification_report.source_patch_sha256
            or self.verification_report.source_patch_sha256
            != self.verification_evidence.source_patch_sha256
            or not self.verification_evidence.workspace.changed_paths
        ):
            raise ValueError("checkpoint request evidence is not one reviewed source patch")
        if self.verification_report.report_id not in self.source_review.evidence_ids:
            raise ValueError("checkpoint review does not cite the verification report")
        if not self.verification_report.passes_gate:
            raise ValueError("checkpoint request requires a passing verification report")
        return self


class ManagedCheckpointResult(DomainModel):
    schema_version: str = "1.0"
    integration_key: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    integration_result: SourceIntegrationResult
    evidence_artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_checkpoint_result(self) -> ManagedCheckpointResult:
        if len(self.evidence_artifact_ids) != len(set(self.evidence_artifact_ids)):
            raise ValueError("checkpoint evidence ids must be unique")
        return self


class TaskManagerExecutor(Protocol):
    adapter_id: str

    def start_or_locate(
        self,
        request: ManagedExecutionRequest,
    ) -> ManagedExecutionReference:
        """Idempotently start or find the exact provider run for one persisted key."""

    def observe(
        self,
        reference: ManagedExecutionReference,
        *,
        after_cursor: str | None,
    ) -> ManagedExecutionObservation:
        """Return evidence-backed progress without deciding workflow completion."""

    def collect_verification_evidence(
        self,
        reference: ManagedExecutionReference,
    ) -> ManagedVerificationEvidence:
        """Collect structured workspace facts without deciding their disposition."""


class TaskManagerValidator(Protocol):
    """Run source-controlled validation without owning pass/fail workflow truth."""

    adapter_id: str

    def start_or_locate(
        self,
        request: ManagedExecutionRequest,
    ) -> ManagedExecutionReference:
        """Idempotently run or find one exact policy-constrained validation attempt."""

    def observe(
        self,
        reference: ManagedExecutionReference,
        *,
        after_cursor: str | None,
    ) -> ManagedExecutionObservation:
        """Return terminal runner evidence without deciding whether checks pass."""

    def collect_verification_evidence(
        self,
        reference: ManagedExecutionReference,
    ) -> ManagedVerificationEvidence:
        """Return command and workspace facts for TaskManager-owned verification."""


class TaskManagerSourceIntegrator(Protocol):
    adapter_id: str

    def integrate_checkpoint(
        self,
        request: ManagedCheckpointRequest,
    ) -> ManagedCheckpointResult:
        """Idempotently commit the exact approved patch to its isolated run branch."""


class TaskManagerNodeExecution(DomainModel):
    schema_version: str = "1.0"
    node: TaskPlanNode
    workflow_task_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    dependency_node_ids: tuple[str, ...] = ()
    workflow_state: TaskState
    transition_history: tuple[TransitionRecord, ...]
    provider_start_key: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
        max_length=160,
    )
    provider_reference: ManagedExecutionReference | None = None
    latest_observation: ManagedExecutionObservation | None = None
    verification_evidence: ManagedVerificationEvidence | None = None
    verification_report: VerificationReport | None = None
    verification_artifact_id: str | None = None
    review_artifact_id: str | None = None
    source_review: ReviewReport | None = None
    source_review_artifact_id: str | None = None
    source_approval_artifact_id: str | None = None
    source_approved_by: str | None = Field(default=None, min_length=1, max_length=160)
    integration_key: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
        max_length=160,
    )
    integration_result: SourceIntegrationResult | None = None
    integration_artifact_id: str | None = None

    @model_validator(mode="after")
    def validate_node_execution(self) -> TaskManagerNodeExecution:
        if not self.transition_history:
            raise ValueError("managed node execution needs a WorkflowKernel history")
        verify_transition_sequence(self.transition_history)
        if any(
            record.task_id != self.workflow_task_id
            for record in self.transition_history
        ):
            raise ValueError("node transition history belongs to another workflow task")
        if self.workflow_state is not self.transition_history[-1].to_state:
            raise ValueError("node workflow state does not match its transition history")
        if self.node.node_id in self.dependency_node_ids:
            raise ValueError("managed node cannot depend on itself")
        if self.provider_reference is not None:
            if self.provider_start_key != self.provider_reference.provider_start_key:
                raise ValueError("provider reference does not match the persisted start key")
        if self.latest_observation is not None:
            if self.provider_reference is None:
                raise ValueError("execution observation requires a provider reference")
            if (
                self.latest_observation.provider_run_id
                != self.provider_reference.provider_run_id
            ):
                raise ValueError("execution observation belongs to another provider run")
        verification_fields = (
            self.verification_evidence,
            self.verification_report,
            self.verification_artifact_id,
        )
        if any(item is not None for item in verification_fields) and not all(
            item is not None for item in verification_fields
        ):
            raise ValueError("managed verification evidence must be persisted atomically")
        if self.verification_evidence is not None:
            if self.provider_reference is None:
                raise ValueError("managed verification requires a provider reference")
            if (
                self.verification_evidence.provider_run_id
                != self.provider_reference.provider_run_id
            ):
                raise ValueError("managed verification belongs to another provider run")
            assert self.verification_report is not None
            if self.verification_report.task_id != self.workflow_task_id:
                raise ValueError("verification report belongs to another workflow task")
            if (
                self.verification_report.source_commit.lower()
                != self.verification_evidence.source_commit.lower()
                or self.verification_report.source_patch_sha256
                != self.verification_evidence.source_patch_sha256
            ):
                raise ValueError("verification report does not match collected source facts")
        if self.workflow_state is TaskState.DELIVERABLE_ACCEPTED:
            if self.review_artifact_id is None or self.verification_evidence is None:
                raise ValueError("accepted deliverable requires review and verification evidence")
            if (
                self.verification_evidence.source_patch_sha256 is not None
                or self.verification_evidence.workspace.changed_paths
                or not self.verification_evidence.workspace.working_tree_clean
            ):
                raise ValueError("source-changing work cannot use deliverable acceptance")
        elif self.review_artifact_id is not None:
            raise ValueError("review evidence is only valid for an accepted deliverable")
        source_review_fields = (self.source_review, self.source_review_artifact_id)
        if any(item is not None for item in source_review_fields) and not all(
            item is not None for item in source_review_fields
        ):
            raise ValueError("source review report and artifact must be persisted together")
        if self.source_review is not None:
            if self.verification_report is None:
                raise ValueError("source review requires a verification report")
            if (
                self.source_review.task_id != self.workflow_task_id
                or self.source_review.status is not ReviewStatus.ACCEPTED
                or self.source_review.patch_sha256
                != self.verification_report.source_patch_sha256
                or self.verification_report.report_id
                not in self.source_review.evidence_ids
            ):
                raise ValueError("source review does not match verified node evidence")
            if self.workflow_state not in {
                TaskState.MERGE_REVIEW,
                TaskState.INTEGRATING,
                TaskState.COMPLETED,
            }:
                raise ValueError("source review is invalid before merge review")
        approval_fields = (
            self.source_approval_artifact_id,
            self.source_approved_by,
            self.integration_key,
        )
        if any(item is not None for item in approval_fields) and not all(
            item is not None for item in approval_fields
        ):
            raise ValueError("source approval evidence must be persisted atomically")
        if self.source_approval_artifact_id is not None:
            if self.source_review is None:
                raise ValueError("source approval requires a review and integration key")
            if self.source_approved_by == self.source_review.reviewer_id:
                raise ValueError("source approval must be independent from source review")
        if self.workflow_state in {TaskState.INTEGRATING, TaskState.COMPLETED}:
            if self.source_approval_artifact_id is None or self.integration_key is None:
                raise ValueError("source integration state requires approved checkpoint intent")
        integration_fields = (self.integration_result, self.integration_artifact_id)
        if any(item is not None for item in integration_fields) and not all(
            item is not None for item in integration_fields
        ):
            raise ValueError("integration result and artifact must be persisted together")
        if self.integration_result is not None:
            if self.verification_report is None:
                raise ValueError("source integration requires verified source evidence")
            if (
                self.integration_result.task_id != self.workflow_task_id
                or self.integration_result.source_patch_sha256
                != self.verification_report.source_patch_sha256
                or self.integration_result.approved_by != self.source_approved_by
            ):
                raise ValueError("integration result does not match the verified patch")
            if self.workflow_state is not TaskState.COMPLETED:
                raise ValueError("integration result is only valid for a completed source node")
        return self


class TaskManagerRunSnapshot(DomainModel):
    schema_version: str = "1.0"
    run_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    revision: int = Field(ge=1)
    plan_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    plan_revision: int = Field(ge=1)
    plan_record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_binding: TaskManagerExecutionBinding
    stage: TaskManagerRunStage
    nodes: tuple[TaskManagerNodeExecution, ...] = Field(min_length=1)
    created_by: str = Field(min_length=1, max_length=160)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_run_snapshot(self) -> TaskManagerRunSnapshot:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("TaskManager run timestamps need a timezone")
        if self.updated_at < self.created_at:
            raise ValueError("TaskManager run update precedes creation")
        if self.execution_binding.task.project_id != (
            self.execution_binding.testbed_inspection.testbed_id
        ):
            raise ValueError("TaskManager run has an invalid execution target binding")
        node_ids = tuple(item.node.node_id for item in self.nodes)
        workflow_ids = tuple(item.workflow_task_id for item in self.nodes)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("TaskManager run node ids must be unique")
        if len(workflow_ids) != len(set(workflow_ids)):
            raise ValueError("TaskManager workflow task ids must be unique")
        known = set(node_ids)
        if any(
            dependency not in known
            for item in self.nodes
            for dependency in item.dependency_node_ids
        ):
            raise ValueError("TaskManager run dependency references an unknown node")
        if self.stage is not derive_run_stage(self.nodes):
            raise ValueError("TaskManager run stage does not match node workflow truth")
        return self


class TaskManagerRunRevisionRecord(DomainModel):
    schema_version: str = "1.0"
    record_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    run_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    sequence: int = Field(ge=1)
    snapshot: TaskManagerRunSnapshot
    actor_type: ActorType
    actor_id: str = Field(min_length=1, max_length=160)
    operation: str = Field(min_length=1, max_length=200)
    node_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
        max_length=96,
    )
    occurred_at: datetime
    previous_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_record(self) -> TaskManagerRunRevisionRecord:
        if (
            self.run_id != self.snapshot.run_id
            or self.sequence != self.snapshot.revision
        ):
            raise ValueError("TaskManager run record is not snapshot-bound")
        if self.occurred_at.tzinfo is None:
            raise ValueError("TaskManager run record time needs a timezone")
        if self.node_id is not None and self.node_id not in {
            item.node.node_id for item in self.snapshot.nodes
        }:
            raise ValueError("TaskManager run record references an unknown node")
        return self


class TaskManagerRunStore(Protocol):
    def list_run_ids(self) -> tuple[str, ...]:
        """Return run ids whose complete histories pass integrity validation."""

    def history(self, run_id: str) -> tuple[TaskManagerRunRevisionRecord, ...]:
        """Return one hash-verified append-only run history."""

    def append(
        self,
        snapshot: TaskManagerRunSnapshot,
        *,
        actor_type: ActorType,
        actor_id: str,
        operation: str,
        node_id: str | None = None,
    ) -> TaskManagerRunRevisionRecord:
        """Append the exact next run revision or reject a stale writer."""


def derive_run_stage(
    nodes: tuple[TaskManagerNodeExecution, ...],
) -> TaskManagerRunStage:
    states = {item.workflow_state for item in nodes}
    if states and states <= {
        TaskState.COMPLETED,
        TaskState.GATE_APPROVED,
        TaskState.DELIVERABLE_ACCEPTED,
    }:
        return TaskManagerRunStage.COMPLETED
    if TaskState.IMPLEMENTING in states:
        return TaskManagerRunStage.RUNNING
    if states & {TaskState.BLOCKED, TaskState.FAILED, TaskState.REPAIRING}:
        return TaskManagerRunStage.NEEDS_ATTENTION
    if states & {
        TaskState.VERIFYING,
        TaskState.REVIEWING,
        TaskState.MERGE_REVIEW,
        TaskState.INTEGRATING,
    }:
        return TaskManagerRunStage.VERIFYING
    if states and states <= {TaskState.COMPLETED, TaskState.CANCELLED}:
        return TaskManagerRunStage.CANCELLED
    return TaskManagerRunStage.READY


__all__ = [
    "derive_run_stage",
    "ManagedExecutionObservation",
    "ManagedExecutionReference",
    "ManagedExecutionRequest",
    "ManagedExecutionStatus",
    "ManagedCheckpointRequest",
    "ManagedCheckpointResult",
    "ManagedVerificationEvidence",
    "ManagedValidationCheckEvidence",
    "TaskManagerExecutor",
    "TaskManagerSourceIntegrator",
    "TaskManagerValidator",
    "TaskManagerNodeExecution",
    "TaskManagerRunRevisionRecord",
    "TaskManagerRunSnapshot",
    "TaskManagerRunStage",
    "TaskManagerRunStore",
]
