"""Provider-neutral TaskManager read contracts for the focused product surface."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from jobslayer.domain.models import ActorType, DomainModel
from jobslayer.orchestration import (
    IDENTIFIER_PATTERN,
    TaskPlanAssessment,
    TaskPlanNode,
    TaskPlanSnapshot,
)
from jobslayer.task_manager.execution import (
    ManagedExecutionObservation,
    ManagedCheckpointRequest,
    ManagedCheckpointResult,
    ManagedExecutionReference,
    ManagedExecutionRequest,
    ManagedExecutionStatus,
    ManagedValidationCheckEvidence,
    ManagedVerificationEvidence,
    TaskManagerExecutor,
    TaskManagerSourceIntegrator,
    TaskManagerValidator,
    TaskManagerNodeExecution,
    TaskManagerRunRevisionRecord,
    TaskManagerRunSnapshot,
    TaskManagerRunStage,
    TaskManagerRunStore,
)
from jobslayer.task_manager.binding import (
    TaskManagerDependencyAttachment,
    TaskManagerExecutionBinding,
    TaskManagerExecutionTarget,
    TaskManagerExecutionTargetAssessment,
    TaskManagerExecutionTargetIssue,
    TaskManagerExecutionTargetRegistry,
    TaskManagerSourceDigest,
)


class ManagedTaskStage(str, Enum):
    PROPOSAL_PENDING = "proposal_pending"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    NEEDS_ATTENTION = "needs_attention"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class ManagedNodeState(str, Enum):
    PROPOSED = "proposed"
    PLANNED = "planned"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskManagerLogCategory(str, Enum):
    PLANNING = "planning"
    CONVERSATION = "conversation"
    DECISION = "decision"
    EXECUTION = "execution"
    FEEDBACK = "feedback"
    VERIFICATION = "verification"


class ManagedTaskSummary(DomainModel):
    schema_version: str = "1.0"
    task_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    title: str = Field(min_length=1, max_length=160)
    task_description: str = Field(min_length=1, max_length=12_000)
    revision: int = Field(ge=1)
    stage: ManagedTaskStage
    pending_proposal: bool
    node_count: int = Field(ge=0)
    backlog_count: int = Field(ge=0)
    blocker_count: int = Field(ge=0)
    is_archived: bool
    updated_at: datetime
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_summary(self) -> ManagedTaskSummary:
        if self.updated_at.tzinfo is None:
            raise ValueError("managed task update time needs a timezone")
        if self.is_archived != (self.stage is ManagedTaskStage.ARCHIVED):
            raise ValueError("managed task archive stage is inconsistent")
        return self


class ManagedNodeView(DomainModel):
    schema_version: str = "1.0"
    node: TaskPlanNode
    state: ManagedNodeState
    dependency_node_ids: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()


class TaskManagerBacklogItem(DomainModel):
    schema_version: str = "1.0"
    node_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=96)
    title: str = Field(min_length=1, max_length=200)
    state: ManagedNodeState
    reason: str = Field(min_length=1, max_length=1_000)
    dependency_node_ids: tuple[str, ...] = ()


class TaskManagerLogEntry(DomainModel):
    schema_version: str = "1.0"
    log_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=256)
    category: TaskManagerLogCategory
    event_type: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=12_000)
    actor_type: ActorType
    actor_id: str = Field(min_length=1, max_length=160)
    occurred_at: datetime
    revision: int = Field(ge=1)
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    node_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
        max_length=96,
    )

    @model_validator(mode="after")
    def validate_log_time(self) -> TaskManagerLogEntry:
        if self.occurred_at.tzinfo is None:
            raise ValueError("task-manager log time needs a timezone")
        return self


class ManagedTaskDetail(DomainModel):
    schema_version: str = "1.0"
    task: ManagedTaskSummary
    plan: TaskPlanSnapshot
    assessment: TaskPlanAssessment
    nodes: tuple[ManagedNodeView, ...]
    backlog: tuple[TaskManagerBacklogItem, ...]
    log: tuple[TaskManagerLogEntry, ...]
    execution_targets: tuple[TaskManagerExecutionTarget, ...] = ()
    execution_target: TaskManagerExecutionTarget | None = None
    execution_target_assessment: TaskManagerExecutionTargetAssessment | None = None
    execution_run: TaskManagerRunSnapshot | None = None
    run_assembly_available: bool = False
    execution_available: bool = False
    execution_blockers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_bindings(self) -> ManagedTaskDetail:
        if (
            self.task.task_id != self.plan.plan_id
            or self.task.revision != self.plan.revision
            or self.assessment.plan_id != self.plan.plan_id
            or self.assessment.revision != self.plan.revision
        ):
            raise ValueError("TaskManager detail sources are not revision-bound")
        if self.execution_available and self.execution_blockers:
            raise ValueError("available execution cannot retain blockers")
        if not self.execution_available and not self.execution_blockers:
            raise ValueError("unavailable execution needs an explicit blocker")
        if self.plan.execution_target_id is None:
            if self.execution_target is not None or self.execution_target_assessment is not None:
                raise ValueError("unselected plan cannot expose a resolved execution target")
        elif self.execution_target is not None:
            if self.execution_target.target_id != self.plan.execution_target_id:
                raise ValueError("resolved execution target does not match the plan")
            if (
                self.execution_target_assessment is None
                or self.execution_target_assessment.target_id
                != self.execution_target.target_id
                or self.execution_target_assessment.plan_id != self.plan.plan_id
                or self.execution_target_assessment.revision != self.plan.revision
            ):
                raise ValueError("execution-target assessment is not revision-bound")
        return self


__all__ = [
    "ManagedExecutionObservation",
    "ManagedCheckpointRequest",
    "ManagedCheckpointResult",
    "ManagedExecutionReference",
    "ManagedExecutionRequest",
    "ManagedExecutionStatus",
    "ManagedValidationCheckEvidence",
    "ManagedVerificationEvidence",
    "ManagedNodeState",
    "ManagedNodeView",
    "ManagedTaskDetail",
    "ManagedTaskStage",
    "ManagedTaskSummary",
    "TaskManagerBacklogItem",
    "TaskManagerLogCategory",
    "TaskManagerLogEntry",
    "TaskManagerExecutor",
    "TaskManagerSourceIntegrator",
    "TaskManagerValidator",
    "TaskManagerDependencyAttachment",
    "TaskManagerExecutionBinding",
    "TaskManagerExecutionTarget",
    "TaskManagerExecutionTargetAssessment",
    "TaskManagerExecutionTargetIssue",
    "TaskManagerExecutionTargetRegistry",
    "TaskManagerNodeExecution",
    "TaskManagerRunRevisionRecord",
    "TaskManagerRunSnapshot",
    "TaskManagerRunStage",
    "TaskManagerRunStore",
    "TaskManagerSourceDigest",
]
