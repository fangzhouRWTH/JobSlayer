"""Provider-neutral state for the restart-safe serial TaskManager coordinator."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import ActorType, DomainModel
from jobslayer.orchestration import IDENTIFIER_PATTERN
from jobslayer.task_manager.execution import TaskManagerRunSnapshot


class TaskManagerCoordinatorStage(str, Enum):
    READY = "ready"
    ADVANCING = "advancing"
    WAITING_HUMAN = "waiting_human"
    WAITING_REVIEW = "waiting_review"
    NEEDS_ATTENTION = "needs_attention"
    COMPLETED = "completed"


class TaskManagerCoordinatorAction(str, Enum):
    START_NODE = "start_node"
    RUN_VALIDATION = "run_validation"
    OBSERVE_NODE = "observe_node"
    VERIFY_NODE = "verify_node"
    INTEGRATE_CHECKPOINT = "integrate_checkpoint"
    WAIT_HUMAN = "wait_human"
    WAIT_REVIEW = "wait_review"
    NEEDS_ATTENTION = "needs_attention"
    COMPLETE = "complete"


SIDE_EFFECTING_COORDINATOR_ACTIONS = frozenset(
    {
        TaskManagerCoordinatorAction.START_NODE,
        TaskManagerCoordinatorAction.RUN_VALIDATION,
        TaskManagerCoordinatorAction.OBSERVE_NODE,
        TaskManagerCoordinatorAction.VERIFY_NODE,
        TaskManagerCoordinatorAction.INTEGRATE_CHECKPOINT,
    }
)


class TaskManagerCoordinatorIntent(DomainModel):
    schema_version: str = "1.0"
    intent_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    run_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    node_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=96)
    action: TaskManagerCoordinatorAction
    expected_run_revision: int = Field(ge=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_intent(self) -> TaskManagerCoordinatorIntent:
        if self.action not in SIDE_EFFECTING_COORDINATOR_ACTIONS:
            raise ValueError("coordinator intent must describe one side effect")
        if self.created_at.tzinfo is None:
            raise ValueError("coordinator intent time needs a timezone")
        return self


class TaskManagerCoordinatorSnapshot(DomainModel):
    schema_version: str = "1.0"
    run_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    revision: int = Field(ge=1)
    run_revision: int = Field(ge=1)
    stage: TaskManagerCoordinatorStage
    cursor_node_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
        max_length=96,
    )
    next_action: TaskManagerCoordinatorAction
    pending_intent: TaskManagerCoordinatorIntent | None = None
    last_completed_intent_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
        max_length=160,
    )
    reason: str = Field(min_length=1, max_length=2_000)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_snapshot(self) -> TaskManagerCoordinatorSnapshot:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("coordinator timestamps need a timezone")
        if self.updated_at < self.created_at:
            raise ValueError("coordinator update precedes creation")
        if self.pending_intent is not None:
            if (
                self.stage is not TaskManagerCoordinatorStage.ADVANCING
                or self.next_action is not self.pending_intent.action
                or self.cursor_node_id != self.pending_intent.node_id
                or self.pending_intent.run_id != self.run_id
                or self.pending_intent.expected_run_revision != self.run_revision
            ):
                raise ValueError("pending coordinator intent is not cursor-bound")
        elif self.stage is TaskManagerCoordinatorStage.ADVANCING:
            raise ValueError("advancing coordinator needs a persisted intent")
        if self.next_action in SIDE_EFFECTING_COORDINATOR_ACTIONS and self.cursor_node_id is None:
            raise ValueError("side-effecting coordinator action needs a node cursor")
        if self.next_action is TaskManagerCoordinatorAction.COMPLETE:
            if self.stage is not TaskManagerCoordinatorStage.COMPLETED:
                raise ValueError("complete action requires completed coordinator stage")
        return self


class TaskManagerCoordinatorRevisionRecord(DomainModel):
    schema_version: str = "1.0"
    record_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    run_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    sequence: int = Field(ge=1)
    snapshot: TaskManagerCoordinatorSnapshot
    actor_type: ActorType
    actor_id: str = Field(min_length=1, max_length=160)
    operation: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    previous_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_record(self) -> TaskManagerCoordinatorRevisionRecord:
        if self.run_id != self.snapshot.run_id or self.sequence != self.snapshot.revision:
            raise ValueError("coordinator record is not snapshot-bound")
        if self.occurred_at.tzinfo is None:
            raise ValueError("coordinator record time needs a timezone")
        return self


class TaskManagerCoordinatorTickResult(DomainModel):
    schema_version: str = "1.0"
    coordinator: TaskManagerCoordinatorSnapshot
    run: TaskManagerRunSnapshot
    performed_action: TaskManagerCoordinatorAction | None = None
    side_effect_performed: bool = False
    recovered_intent: bool = False


class TaskManagerCoordinatorStore(Protocol):
    def history(
        self,
        run_id: str,
    ) -> tuple[TaskManagerCoordinatorRevisionRecord, ...]:
        """Return one hash-verified append-only coordinator history."""

    def append(
        self,
        snapshot: TaskManagerCoordinatorSnapshot,
        *,
        actor_type: ActorType,
        actor_id: str,
        operation: str,
    ) -> TaskManagerCoordinatorRevisionRecord:
        """Append the exact next coordinator revision."""


__all__ = [
    "SIDE_EFFECTING_COORDINATOR_ACTIONS",
    "TaskManagerCoordinatorAction",
    "TaskManagerCoordinatorIntent",
    "TaskManagerCoordinatorRevisionRecord",
    "TaskManagerCoordinatorSnapshot",
    "TaskManagerCoordinatorStage",
    "TaskManagerCoordinatorStore",
    "TaskManagerCoordinatorTickResult",
]
