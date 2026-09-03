"""Provider-neutral instructions for governed human interaction points."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import ActorType, DomainModel
from jobslayer.orchestration import IDENTIFIER_PATTERN


class TaskManagerHumanActionKind(str, Enum):
    PROPOSAL_DECISION = "proposal_decision"
    PLAN_REFINEMENT = "plan_refinement"
    PLAN_FINALIZATION = "plan_finalization"
    RUN_ASSEMBLY = "run_assembly"
    SCOPE_CONFIRMATION = "scope_confirmation"
    VERIFIED_DELIVERABLE_REVIEW = "verified_deliverable_review"
    SOURCE_REVIEW = "source_review"
    SOURCE_CHECKPOINT_APPROVAL = "source_checkpoint_approval"
    COMPLETION_APPROVAL = "completion_approval"
    FAILURE_RECOVERY = "failure_recovery"
    BLOCKER_RESOLUTION = "blocker_resolution"


class TaskManagerHumanDecisionOption(DomainModel):
    schema_version: str = "1.0"
    decision_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=96)
    label: str = Field(min_length=1, max_length=120)
    effect: str = Field(min_length=1, max_length=1_000)
    command: str | None = Field(default=None, min_length=1, max_length=160)


class TaskManagerHumanInteractionKind(str, Enum):
    FEEDBACK = "feedback"
    ASSISTANT_REQUEST = "assistant_request"
    ASSISTANT_RESPONSE = "assistant_response"
    ASSISTANT_ERROR = "assistant_error"


class TaskManagerHumanInteraction(DomainModel):
    """One append-only message bound to the guidance revision it discusses."""

    schema_version: str = "1.0"
    interaction_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    guidance_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    kind: TaskManagerHumanInteractionKind
    node_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=96)
    actor_type: ActorType
    actor_id: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=12_000)
    decision_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
        max_length=96,
    )
    based_on_plan_revision: int = Field(ge=1)
    based_on_run_revision: int = Field(ge=1)
    evidence_artifact_ids: tuple[str, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def validate_interaction(self) -> TaskManagerHumanInteraction:
        if self.created_at.tzinfo is None:
            raise ValueError("human interaction time needs a timezone")
        if len(self.evidence_artifact_ids) != len(set(self.evidence_artifact_ids)):
            raise ValueError("human interaction evidence ids must be unique")
        if self.kind is TaskManagerHumanInteractionKind.FEEDBACK:
            if self.actor_type is not ActorType.HUMAN or self.decision_id is None:
                raise ValueError("human feedback needs a human actor and decision")
        elif self.decision_id is not None:
            raise ValueError("assistant interaction cannot claim a human decision")
        if self.kind is TaskManagerHumanInteractionKind.ASSISTANT_REQUEST:
            if self.actor_type is not ActorType.HUMAN:
                raise ValueError("assistant request needs a human actor")
        elif self.kind in {
            TaskManagerHumanInteractionKind.ASSISTANT_RESPONSE,
            TaskManagerHumanInteractionKind.ASSISTANT_ERROR,
        } and self.actor_type is not ActorType.AGENT:
            raise ValueError("assistant outcome needs an agent actor")
        return self


class TaskManagerHumanActionGuidance(DomainModel):
    """Revision-bound, explanatory projection; never an authorization itself."""

    schema_version: str = "1.0"
    guidance_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    kind: TaskManagerHumanActionKind
    node_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
        max_length=96,
    )
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2_000)
    permitted_actor_types: tuple[ActorType, ...] = Field(min_length=1)
    required_capability: str = Field(min_length=1, max_length=160)
    requirements: tuple[str, ...] = Field(min_length=1)
    steps: tuple[str, ...] = Field(min_length=1)
    decisions: tuple[TaskManagerHumanDecisionOption, ...] = Field(min_length=1)
    evidence_to_review: tuple[str, ...] = ()
    prohibited_actions: tuple[str, ...] = Field(min_length=1)
    expected_plan_revision: int = Field(ge=1)
    expected_run_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_guidance(self) -> TaskManagerHumanActionGuidance:
        if len(set(self.permitted_actor_types)) != len(self.permitted_actor_types):
            raise ValueError("human-action actor types must be unique")
        decision_ids = tuple(item.decision_id for item in self.decisions)
        if len(set(decision_ids)) != len(decision_ids):
            raise ValueError("human-action decision ids must be unique")
        if self.expected_run_revision is None and self.node_id is not None:
            raise ValueError("node-bound human guidance needs a run revision")
        return self


class TaskManagerHumanActionAssistantReply(DomainModel):
    schema_version: str = "1.0"
    adapter_id: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=12_000)
    evidence_artifact_ids: tuple[str, ...] = ()


class TaskManagerHumanActionAssistant(Protocol):
    adapter_id: str

    def assist(
        self,
        *,
        task_id: str,
        run_id: str,
        guidance: TaskManagerHumanActionGuidance,
        interactions: tuple[TaskManagerHumanInteraction, ...],
        user_message: str,
    ) -> TaskManagerHumanActionAssistantReply:
        """Explain evidence requirements or draft feedback without deciding."""


__all__ = [
    "TaskManagerHumanActionAssistant",
    "TaskManagerHumanActionAssistantReply",
    "TaskManagerHumanActionGuidance",
    "TaskManagerHumanActionKind",
    "TaskManagerHumanDecisionOption",
    "TaskManagerHumanInteraction",
    "TaskManagerHumanInteractionKind",
]
