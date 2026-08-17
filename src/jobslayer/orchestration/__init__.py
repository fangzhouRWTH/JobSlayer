"""Provider-neutral contracts for collaborative task orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import ActorType, DomainModel


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class TaskPlanStatus(str, Enum):
    DRAFT = "draft"
    FINALIZED = "finalized"


class TaskPlanNodeKind(str, Enum):
    TASK = "task"
    MILESTONE = "milestone"
    VALIDATION = "validation"
    HUMAN_GATE = "human_gate"


class TaskPlanEdgeRelation(str, Enum):
    SEQUENCE = "sequence"
    DEPENDENCY = "dependency"
    BRANCH = "branch"
    SUBTASK = "subtask"


class DiscussionRole(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class TaskPlanNode(DomainModel):
    schema_version: str = "1.0"
    node_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=96)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=8_000)
    kind: TaskPlanNodeKind = TaskPlanNodeKind.TASK
    executor_hint: str | None = Field(default=None, max_length=160)
    attributes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_node(self) -> TaskPlanNode:
        if not self.title.strip():
            raise ValueError("task-plan node title must not be blank")
        if self.executor_hint is not None and not self.executor_hint.strip():
            raise ValueError("executor hint must be omitted or non-blank")
        if any(not key.strip() or not value.strip() for key, value in self.attributes.items()):
            raise ValueError("task-plan node attributes must be non-blank strings")
        return self


class TaskPlanEdge(DomainModel):
    schema_version: str = "1.0"
    edge_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    source_node_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=96)
    target_node_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=96)
    relation: TaskPlanEdgeRelation
    label: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_edge(self) -> TaskPlanEdge:
        if self.source_node_id == self.target_node_id:
            raise ValueError("task-plan edge cannot point to the same node")
        if self.label is not None and not self.label.strip():
            raise ValueError("task-plan edge label must be omitted or non-blank")
        return self


def validate_task_plan_graph(
    nodes: tuple[TaskPlanNode, ...],
    edges: tuple[TaskPlanEdge, ...],
) -> None:
    node_ids = [node.node_id for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("task-plan node ids must be unique")
    edge_ids = [edge.edge_id for edge in edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("task-plan edge ids must be unique")
    known = set(node_ids)
    for edge in edges:
        if edge.source_node_id not in known or edge.target_node_id not in known:
            raise ValueError("task-plan edge references an unknown node")

    successors: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        successors[edge.source_node_id].append(edge.target_node_id)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("task-plan graph must be acyclic")
        if node_id in visited:
            return
        visiting.add(node_id)
        for successor in successors[node_id]:
            visit(successor)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in node_ids:
        visit(node_id)


class TaskPlanMessage(DomainModel):
    schema_version: str = "1.0"
    message_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    role: DiscussionRole
    content: str = Field(min_length=1, max_length=12_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    agent_adapter: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_message(self) -> TaskPlanMessage:
        if not self.content.strip():
            raise ValueError("task-plan discussion message must not be blank")
        if self.created_at.tzinfo is None:
            raise ValueError("task-plan discussion timestamp needs a timezone")
        if self.role == DiscussionRole.AGENT and not self.agent_adapter:
            raise ValueError("agent discussion message needs an adapter id")
        if self.role != DiscussionRole.AGENT and self.agent_adapter is not None:
            raise ValueError("only agent messages may name an agent adapter")
        return self


class TaskPlanProposal(DomainModel):
    schema_version: str = "1.0"
    proposal_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    based_on_revision: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=4_000)
    agent_adapter: str = Field(min_length=1, max_length=160)
    nodes: tuple[TaskPlanNode, ...]
    edges: tuple[TaskPlanEdge, ...]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_proposal(self) -> TaskPlanProposal:
        if not self.summary.strip():
            raise ValueError("task-plan proposal summary must not be blank")
        if self.created_at.tzinfo is None:
            raise ValueError("task-plan proposal timestamp needs a timezone")
        validate_task_plan_graph(self.nodes, self.edges)
        return self


class TaskPlanSnapshot(DomainModel):
    schema_version: str = "1.0"
    plan_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    revision: int = Field(ge=1)
    task_description: str = Field(min_length=1, max_length=12_000)
    status: TaskPlanStatus = TaskPlanStatus.DRAFT
    nodes: tuple[TaskPlanNode, ...] = ()
    edges: tuple[TaskPlanEdge, ...] = ()
    conversation: tuple[TaskPlanMessage, ...] = ()
    pending_proposal: TaskPlanProposal | None = None
    latest_finalized_revision: int | None = Field(default=None, ge=1)
    finalized_by: str | None = Field(default=None, max_length=160)
    finalized_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_snapshot(self) -> TaskPlanSnapshot:
        if not self.task_description.strip():
            raise ValueError("task description must not be blank")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("task-plan timestamps need a timezone")
        if self.updated_at < self.created_at:
            raise ValueError("task-plan update time precedes creation")
        validate_task_plan_graph(self.nodes, self.edges)
        if self.latest_finalized_revision is not None and (
            self.latest_finalized_revision > self.revision
        ):
            raise ValueError("latest finalized revision cannot be in the future")
        if self.status == TaskPlanStatus.FINALIZED:
            if not self.nodes:
                raise ValueError("a finalized task plan needs at least one node")
            if self.pending_proposal is not None:
                raise ValueError("a task plan cannot finalize with a pending proposal")
            if (
                self.finalized_by is None
                or self.finalized_at is None
                or self.latest_finalized_revision != self.revision
            ):
                raise ValueError("finalized task plan lacks finalization evidence")
            if self.finalized_at.tzinfo is None:
                raise ValueError("task-plan finalization timestamp needs a timezone")
        elif self.finalized_by is not None or self.finalized_at is not None:
            raise ValueError("draft task plan cannot claim current finalization")
        return self


class TaskPlanRevisionRecord(DomainModel):
    schema_version: str = "1.0"
    record_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    plan_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    sequence: int = Field(ge=1)
    snapshot: TaskPlanSnapshot
    actor_type: ActorType
    actor_id: str = Field(min_length=1, max_length=160)
    operation: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    previous_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_record_binding(self) -> TaskPlanRevisionRecord:
        if self.plan_id != self.snapshot.plan_id or self.sequence != self.snapshot.revision:
            raise ValueError("task-plan revision record is not bound to its snapshot")
        if self.occurred_at.tzinfo is None:
            raise ValueError("task-plan revision timestamp needs a timezone")
        return self


class TaskPlanStore(Protocol):
    def list_plan_ids(self) -> tuple[str, ...]:
        """Return identifiers whose histories pass integrity validation."""

    def history(self, plan_id: str) -> tuple[TaskPlanRevisionRecord, ...]:
        """Return one complete, hash-verified revision history."""

    def append(
        self,
        snapshot: TaskPlanSnapshot,
        *,
        actor_type: ActorType,
        actor_id: str,
        operation: str,
    ) -> TaskPlanRevisionRecord:
        """Append exactly the next revision or reject stale/concurrent state."""


class PlanningAgent(Protocol):
    adapter_id: str

    def propose(
        self,
        *,
        plan_id: str,
        task_description: str,
        based_on_revision: int,
        nodes: tuple[TaskPlanNode, ...],
        edges: tuple[TaskPlanEdge, ...],
        conversation: tuple[TaskPlanMessage, ...],
        user_message: str,
        selected_node_id: str | None,
    ) -> TaskPlanProposal:
        """Return a provider-neutral proposal without changing stored plan state."""


__all__ = [
    "DiscussionRole",
    "PlanningAgent",
    "TaskPlanEdge",
    "TaskPlanEdgeRelation",
    "TaskPlanMessage",
    "TaskPlanNode",
    "TaskPlanNodeKind",
    "TaskPlanProposal",
    "TaskPlanRevisionRecord",
    "TaskPlanSnapshot",
    "TaskPlanStatus",
    "TaskPlanStore",
    "validate_task_plan_graph",
]
