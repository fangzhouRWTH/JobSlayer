"""Application-owned task-plan discussion, editing, and finalization service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from jobslayer.domain.models import ActorType
from jobslayer.orchestration import (
    DiscussionRole,
    PlanningAgent,
    TaskPlanEdge,
    TaskPlanEdgeRelation,
    TaskPlanMessage,
    TaskPlanNode,
    TaskPlanNodeKind,
    TaskPlanRevisionRecord,
    TaskPlanSnapshot,
    TaskPlanStatus,
    TaskPlanStore,
)


class TaskOrchestrationError(RuntimeError):
    """Base error for deterministic task-plan application rules."""


class TaskPlanNotFoundError(TaskOrchestrationError):
    pass


class StaleTaskPlanRevisionError(TaskOrchestrationError):
    pass


class PendingTaskPlanProposalError(TaskOrchestrationError):
    pass


class TaskPlanProposalMismatchError(TaskOrchestrationError):
    pass


class TaskOrchestrationService:
    """Own plan revisions while treating agent output as untrusted proposals."""

    def __init__(
        self,
        store: TaskPlanStore,
        planning_agent: PlanningAgent,
        *,
        actor_id: str,
        clock: Callable[[], datetime] | None = None,
    ):
        if not actor_id.strip():
            raise ValueError("task-plan actor id must not be blank")
        self.store = store
        self.planning_agent = planning_agent
        self.actor_id = actor_id
        self.clock = clock or (lambda: datetime.now(UTC))

    def list_latest(self) -> tuple[TaskPlanRevisionRecord, ...]:
        records: list[TaskPlanRevisionRecord] = []
        for plan_id in self.store.list_plan_ids():
            history = self.store.history(plan_id)
            if history:
                records.append(history[-1])
        return tuple(records)

    def history(self, plan_id: str) -> tuple[TaskPlanRevisionRecord, ...]:
        history = self.store.history(plan_id)
        if not history:
            raise TaskPlanNotFoundError("task plan does not exist")
        return history

    def get(self, plan_id: str) -> TaskPlanRevisionRecord:
        return self.history(plan_id)[-1]

    def create(
        self,
        task_description: str,
        *,
        plan_id: str | None = None,
    ) -> TaskPlanRevisionRecord:
        description = task_description.strip()
        if not description:
            raise ValueError("task description must not be blank")
        identifier = plan_id or f"plan-{uuid4().hex}"
        if self.store.history(identifier):
            raise TaskOrchestrationError("task plan already exists")
        now = self._now()
        user_message = TaskPlanMessage(
            message_id=f"message-{uuid4().hex}",
            role=DiscussionRole.USER,
            content=description,
            created_at=now,
        )
        proposal = self.planning_agent.propose(
            plan_id=identifier,
            task_description=description,
            based_on_revision=0,
            nodes=(),
            edges=(),
            conversation=(user_message,),
            user_message=description,
            selected_node_id=None,
        )
        agent_message = TaskPlanMessage(
            message_id=f"message-{uuid4().hex}",
            role=DiscussionRole.AGENT,
            content=proposal.summary,
            created_at=now,
            agent_adapter=proposal.agent_adapter,
        )
        snapshot = TaskPlanSnapshot(
            plan_id=identifier,
            revision=1,
            task_description=description,
            nodes=(),
            edges=(),
            conversation=(user_message, agent_message),
            pending_proposal=proposal,
            created_at=now,
            updated_at=now,
        )
        return self.store.append(
            snapshot,
            actor_type=ActorType.HUMAN,
            actor_id=self.actor_id,
            operation="plan.created_with_agent_proposal",
        )

    def discuss(
        self,
        plan_id: str,
        content: str,
        *,
        expected_revision: int,
        selected_node_id: str | None = None,
    ) -> TaskPlanRevisionRecord:
        latest = self._expected(plan_id, expected_revision).snapshot
        message = content.strip()
        if not message:
            raise ValueError("planning discussion message must not be blank")
        effective_nodes = (
            latest.pending_proposal.nodes
            if latest.pending_proposal is not None
            else latest.nodes
        )
        effective_edges = (
            latest.pending_proposal.edges
            if latest.pending_proposal is not None
            else latest.edges
        )
        now = self._now()
        user_message = TaskPlanMessage(
            message_id=f"message-{uuid4().hex}",
            role=DiscussionRole.USER,
            content=message,
            created_at=now,
        )
        conversation = (*latest.conversation, user_message)
        proposal = self.planning_agent.propose(
            plan_id=latest.plan_id,
            task_description=latest.task_description,
            based_on_revision=latest.revision,
            nodes=effective_nodes,
            edges=effective_edges,
            conversation=conversation,
            user_message=message,
            selected_node_id=selected_node_id,
        )
        agent_message = TaskPlanMessage(
            message_id=f"message-{uuid4().hex}",
            role=DiscussionRole.AGENT,
            content=proposal.summary,
            created_at=now,
            agent_adapter=proposal.agent_adapter,
        )
        snapshot = self._next_snapshot(
            latest,
            conversation=(*conversation, agent_message),
            pending_proposal=proposal,
        )
        return self._append(snapshot, "discussion.proposal_recorded")

    def apply_proposal(
        self,
        plan_id: str,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> TaskPlanRevisionRecord:
        latest = self._expected(plan_id, expected_revision).snapshot
        proposal = latest.pending_proposal
        if proposal is None:
            raise TaskPlanProposalMismatchError("task plan has no pending proposal")
        if proposal.proposal_id != proposal_id:
            raise TaskPlanProposalMismatchError("task-plan proposal is stale or mismatched")
        snapshot = self._next_snapshot(
            latest,
            nodes=proposal.nodes,
            edges=proposal.edges,
            pending_proposal=None,
        )
        return self._append(snapshot, "agent_proposal.applied_by_user")

    def create_node(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        title: str,
        description: str = "",
        kind: TaskPlanNodeKind = TaskPlanNodeKind.TASK,
        executor_hint: str | None = None,
        source_node_id: str | None = None,
        relation: TaskPlanEdgeRelation = TaskPlanEdgeRelation.SEQUENCE,
        node_id: str | None = None,
    ) -> TaskPlanRevisionRecord:
        latest = self._editable(plan_id, expected_revision)
        identifier = node_id or f"node-{uuid4().hex[:12]}"
        if any(node.node_id == identifier for node in latest.nodes):
            raise TaskOrchestrationError("task-plan node already exists")
        if source_node_id is not None and not any(
            node.node_id == source_node_id for node in latest.nodes
        ):
            raise TaskOrchestrationError("source task-plan node does not exist")
        node = TaskPlanNode(
            node_id=identifier,
            title=title,
            description=description,
            kind=kind,
            executor_hint=executor_hint,
        )
        edges = list(latest.edges)
        if source_node_id is not None:
            edges.append(
                TaskPlanEdge(
                    edge_id=f"edge-{uuid4().hex[:16]}",
                    source_node_id=source_node_id,
                    target_node_id=node.node_id,
                    relation=relation,
                )
            )
        snapshot = self._next_snapshot(
            latest,
            nodes=(*latest.nodes, node),
            edges=tuple(edges),
        )
        return self._append(snapshot, f"node.created:{node.node_id}")

    def update_node(
        self,
        plan_id: str,
        node_id: str,
        *,
        expected_revision: int,
        title: str,
        description: str,
        kind: TaskPlanNodeKind,
        executor_hint: str | None,
    ) -> TaskPlanRevisionRecord:
        latest = self._editable(plan_id, expected_revision)
        current = next(
            (node for node in latest.nodes if node.node_id == node_id), None
        )
        if current is None:
            raise TaskPlanNotFoundError("task-plan node does not exist")
        replacement = TaskPlanNode(
            node_id=current.node_id,
            title=title,
            description=description,
            kind=kind,
            executor_hint=executor_hint,
            attributes=current.attributes,
        )
        snapshot = self._next_snapshot(
            latest,
            nodes=tuple(
                replacement if node.node_id == node_id else node
                for node in latest.nodes
            ),
        )
        return self._append(snapshot, f"node.updated:{node_id}")

    def delete_node(
        self,
        plan_id: str,
        node_id: str,
        *,
        expected_revision: int,
    ) -> TaskPlanRevisionRecord:
        latest = self._editable(plan_id, expected_revision)
        if not any(node.node_id == node_id for node in latest.nodes):
            raise TaskPlanNotFoundError("task-plan node does not exist")
        snapshot = self._next_snapshot(
            latest,
            nodes=tuple(node for node in latest.nodes if node.node_id != node_id),
            edges=tuple(
                edge
                for edge in latest.edges
                if node_id not in {edge.source_node_id, edge.target_node_id}
            ),
        )
        return self._append(snapshot, f"node.deleted:{node_id}")

    def split_node(
        self,
        plan_id: str,
        node_id: str,
        *,
        expected_revision: int,
        title: str,
        description: str,
        relation: TaskPlanEdgeRelation,
    ) -> TaskPlanRevisionRecord:
        if relation not in {
            TaskPlanEdgeRelation.BRANCH,
            TaskPlanEdgeRelation.SUBTASK,
        }:
            raise ValueError("node split relation must be branch or subtask")
        return self.create_node(
            plan_id,
            expected_revision=expected_revision,
            title=title,
            description=description,
            source_node_id=node_id,
            relation=relation,
        )

    def finalize(
        self,
        plan_id: str,
        *,
        expected_revision: int,
    ) -> TaskPlanRevisionRecord:
        latest = self._expected(plan_id, expected_revision).snapshot
        if latest.pending_proposal is not None:
            raise PendingTaskPlanProposalError(
                "apply or replace the pending proposal before finalization"
            )
        if not latest.nodes:
            raise TaskOrchestrationError("cannot finalize an empty task plan")
        now = self._now()
        next_revision = latest.revision + 1
        snapshot = TaskPlanSnapshot.model_validate(
            {
                **latest.model_dump(mode="json"),
                "revision": next_revision,
                "status": TaskPlanStatus.FINALIZED.value,
                "latest_finalized_revision": next_revision,
                "finalized_by": self.actor_id,
                "finalized_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        )
        return self._append(snapshot, "plan.finalized_by_user")

    def _editable(self, plan_id: str, expected_revision: int) -> TaskPlanSnapshot:
        latest = self._expected(plan_id, expected_revision).snapshot
        if latest.pending_proposal is not None:
            raise PendingTaskPlanProposalError(
                "apply or replace the pending proposal before direct node editing"
            )
        return latest

    def _expected(
        self, plan_id: str, expected_revision: int
    ) -> TaskPlanRevisionRecord:
        latest = self.get(plan_id)
        if latest.sequence != expected_revision:
            raise StaleTaskPlanRevisionError(
                f"expected task-plan revision {expected_revision}, current is {latest.sequence}"
            )
        return latest

    def _next_snapshot(
        self,
        latest: TaskPlanSnapshot,
        **updates: object,
    ) -> TaskPlanSnapshot:
        now = self._now()
        payload = latest.model_dump(mode="json")
        payload.update(updates)
        payload.update(
            {
                "revision": latest.revision + 1,
                "status": TaskPlanStatus.DRAFT.value,
                "finalized_by": None,
                "finalized_at": None,
                "updated_at": now.isoformat(),
            }
        )
        return TaskPlanSnapshot.model_validate(payload)

    def _append(
        self, snapshot: TaskPlanSnapshot, operation: str
    ) -> TaskPlanRevisionRecord:
        return self.store.append(
            snapshot,
            actor_type=ActorType.HUMAN,
            actor_id=self.actor_id,
            operation=operation,
        )

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("task orchestration clock must return a timezone")
        return now


__all__ = [
    "PendingTaskPlanProposalError",
    "StaleTaskPlanRevisionError",
    "TaskOrchestrationError",
    "TaskOrchestrationService",
    "TaskPlanNotFoundError",
    "TaskPlanProposalMismatchError",
]
