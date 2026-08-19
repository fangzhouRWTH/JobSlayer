"""Application-owned task-plan discussion, editing, and finalization service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from jobslayer.domain.models import ActorType
from jobslayer.orchestration import (
    DiscussionRole,
    PlanningAgent,
    TaskPlanAssessment,
    TaskPlanEdge,
    TaskPlanEdgeRelation,
    TaskPlanMessage,
    TaskPlanNode,
    TaskPlanNodeKind,
    TaskPlanProposal,
    TaskPlanProposalDraft,
    TaskPlanRevisionRecord,
    TaskPlanSnapshot,
    TaskPlanStatus,
    TaskPlanStore,
    assess_task_plan,
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


class ArchivedTaskPlanError(TaskOrchestrationError):
    pass


class IncompleteTaskPlanError(TaskOrchestrationError):
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

    def assess(self, plan_id: str) -> TaskPlanAssessment:
        return assess_task_plan(self.get(plan_id).snapshot)

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
        draft = self.planning_agent.propose(
            plan_id=identifier,
            task_description=description,
            based_on_revision=0,
            nodes=(),
            edges=(),
            conversation=(user_message,),
            user_message=description,
            selected_node_id=None,
        )
        proposal = self._proposal_from_draft(draft, based_on_revision=0, created_at=now)
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
        self._require_active(latest)
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
        draft = self.planning_agent.propose(
            plan_id=latest.plan_id,
            task_description=latest.task_description,
            based_on_revision=latest.revision,
            nodes=effective_nodes,
            edges=effective_edges,
            conversation=conversation,
            user_message=message,
            selected_node_id=selected_node_id,
        )
        proposal = self._proposal_from_draft(
            draft,
            based_on_revision=latest.revision,
            created_at=now,
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

    def reject_proposal(
        self,
        plan_id: str,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> TaskPlanRevisionRecord:
        latest = self._expected(plan_id, expected_revision).snapshot
        self._require_active(latest)
        proposal = latest.pending_proposal
        if proposal is None or proposal.proposal_id != proposal_id:
            raise TaskPlanProposalMismatchError("task-plan proposal is stale or mismatched")
        snapshot = self._next_snapshot(latest, pending_proposal=None)
        return self._append(snapshot, "agent_proposal.rejected_by_user")

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
        acceptance_criteria: tuple[str, ...] = (),
        deliverables: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
        risks: tuple[str, ...] = (),
        verification_requirements: tuple[str, ...] = (),
        requires_human_decision: bool = False,
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
            acceptance_criteria=acceptance_criteria,
            deliverables=deliverables,
            constraints=constraints,
            risks=risks,
            verification_requirements=verification_requirements,
            requires_human_decision=requires_human_decision,
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
        acceptance_criteria: tuple[str, ...] | None = None,
        deliverables: tuple[str, ...] | None = None,
        constraints: tuple[str, ...] | None = None,
        risks: tuple[str, ...] | None = None,
        verification_requirements: tuple[str, ...] | None = None,
        requires_human_decision: bool | None = None,
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
            acceptance_criteria=(
                current.acceptance_criteria
                if acceptance_criteria is None
                else acceptance_criteria
            ),
            deliverables=(current.deliverables if deliverables is None else deliverables),
            constraints=(current.constraints if constraints is None else constraints),
            risks=(current.risks if risks is None else risks),
            verification_requirements=(
                current.verification_requirements
                if verification_requirements is None
                else verification_requirements
            ),
            requires_human_decision=(
                current.requires_human_decision
                if requires_human_decision is None
                else requires_human_decision
            ),
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

    def create_edge(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        source_node_id: str,
        target_node_id: str,
        relation: TaskPlanEdgeRelation,
        label: str | None = None,
        edge_id: str | None = None,
    ) -> TaskPlanRevisionRecord:
        latest = self._editable(plan_id, expected_revision)
        identifier = edge_id or f"edge-{uuid4().hex[:16]}"
        if any(edge.edge_id == identifier for edge in latest.edges):
            raise TaskOrchestrationError("task-plan edge already exists")
        edge = TaskPlanEdge(
            edge_id=identifier,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation=relation,
            label=label,
        )
        snapshot = self._next_snapshot(latest, edges=(*latest.edges, edge))
        return self._append(snapshot, f"edge.created:{edge.edge_id}")

    def update_edge(
        self,
        plan_id: str,
        edge_id: str,
        *,
        expected_revision: int,
        relation: TaskPlanEdgeRelation,
        label: str | None,
    ) -> TaskPlanRevisionRecord:
        latest = self._editable(plan_id, expected_revision)
        current = next(
            (edge for edge in latest.edges if edge.edge_id == edge_id), None
        )
        if current is None:
            raise TaskPlanNotFoundError("task-plan edge does not exist")
        replacement = current.model_copy(update={"relation": relation, "label": label})
        snapshot = self._next_snapshot(
            latest,
            edges=tuple(
                replacement if edge.edge_id == edge_id else edge
                for edge in latest.edges
            ),
        )
        return self._append(snapshot, f"edge.updated:{edge_id}")

    def delete_edge(
        self,
        plan_id: str,
        edge_id: str,
        *,
        expected_revision: int,
    ) -> TaskPlanRevisionRecord:
        latest = self._editable(plan_id, expected_revision)
        if not any(edge.edge_id == edge_id for edge in latest.edges):
            raise TaskPlanNotFoundError("task-plan edge does not exist")
        snapshot = self._next_snapshot(
            latest,
            edges=tuple(edge for edge in latest.edges if edge.edge_id != edge_id),
        )
        return self._append(snapshot, f"edge.deleted:{edge_id}")

    def derive_from_revision(
        self,
        plan_id: str,
        source_revision: int,
        *,
        expected_revision: int,
    ) -> TaskPlanRevisionRecord:
        latest = self._editable(plan_id, expected_revision)
        source = next(
            (
                record.snapshot
                for record in self.history(plan_id)
                if record.sequence == source_revision
            ),
            None,
        )
        if source is None:
            raise TaskPlanNotFoundError("source task-plan revision does not exist")
        system_message = TaskPlanMessage(
            message_id=f"message-{uuid4().hex}",
            role=DiscussionRole.SYSTEM,
            content=f"用户从 revision {source_revision} 派生了新的活动草案。",
            created_at=self._now(),
        )
        snapshot = self._next_snapshot(
            latest,
            task_description=source.task_description,
            nodes=source.nodes,
            edges=source.edges,
            conversation=(*latest.conversation, system_message),
            pending_proposal=None,
        )
        return self._append(snapshot, f"plan.derived_from_revision:{source_revision}")

    def set_archived(
        self,
        plan_id: str,
        *,
        archived: bool,
        expected_revision: int,
    ) -> TaskPlanRevisionRecord:
        latest = self._expected(plan_id, expected_revision).snapshot
        if latest.pending_proposal is not None:
            raise PendingTaskPlanProposalError(
                "apply or reject the pending proposal before changing archive state"
            )
        if latest.is_archived == archived:
            raise TaskOrchestrationError("task plan already has the requested archive state")
        now = self._now()
        snapshot = self._next_snapshot(
            latest,
            is_archived=archived,
            archived_by=self.actor_id if archived else None,
            archived_at=now.isoformat() if archived else None,
        )
        operation = "plan.archived_by_user" if archived else "plan.restored_by_user"
        return self._append(snapshot, operation)

    def finalize(
        self,
        plan_id: str,
        *,
        expected_revision: int,
    ) -> TaskPlanRevisionRecord:
        latest = self._expected(plan_id, expected_revision).snapshot
        self._require_active(latest)
        if latest.pending_proposal is not None:
            raise PendingTaskPlanProposalError(
                "apply or replace the pending proposal before finalization"
            )
        if not latest.nodes:
            raise TaskOrchestrationError("cannot finalize an empty task plan")
        assessment = assess_task_plan(latest)
        blockers = [
            issue.message
            for issue in assessment.issues
            if issue.severity.value == "blocker"
        ]
        if blockers:
            raise IncompleteTaskPlanError("; ".join(blockers))
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
        self._require_active(latest)
        if latest.pending_proposal is not None:
            raise PendingTaskPlanProposalError(
                "apply or replace the pending proposal before direct node editing"
            )
        return latest

    @staticmethod
    def _require_active(snapshot: TaskPlanSnapshot) -> None:
        if snapshot.is_archived:
            raise ArchivedTaskPlanError("archived task plan is read-only")

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

    def _proposal_from_draft(
        self,
        draft: TaskPlanProposalDraft,
        *,
        based_on_revision: int,
        created_at: datetime,
    ) -> TaskPlanProposal:
        return TaskPlanProposal(
            **draft.model_dump(mode="python"),
            proposal_id=f"proposal-{uuid4().hex}",
            based_on_revision=based_on_revision,
            agent_adapter=self.planning_agent.adapter_id,
            created_at=created_at,
        )


__all__ = [
    "ArchivedTaskPlanError",
    "IncompleteTaskPlanError",
    "PendingTaskPlanProposalError",
    "StaleTaskPlanRevisionError",
    "TaskOrchestrationError",
    "TaskOrchestrationService",
    "TaskPlanNotFoundError",
    "TaskPlanProposalMismatchError",
]
