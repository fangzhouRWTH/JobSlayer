"""Restart-safe, one-action-at-a-time coordination for finalized TaskManager runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib

from jobslayer.application.task_manager_execution import (
    StaleTaskManagerRunRevisionError,
    TaskManagerExecutionService,
)
from jobslayer.domain.models import ActorType, TaskState
from jobslayer.orchestration import TaskPlanNodeKind
from jobslayer.task_manager.coordinator import (
    SIDE_EFFECTING_COORDINATOR_ACTIONS,
    TaskManagerCoordinatorAction,
    TaskManagerCoordinatorIntent,
    TaskManagerCoordinatorSnapshot,
    TaskManagerCoordinatorStage,
    TaskManagerCoordinatorStore,
    TaskManagerCoordinatorTickResult,
)
from jobslayer.task_manager.execution import (
    TaskManagerNodeExecution,
    TaskManagerRunRevisionRecord,
    TaskManagerRunSnapshot,
    TaskManagerRunStage,
)
from jobslayer.workers import WorkerLease, WorkerLeaseError, WorkerLeaseStore


class TaskManagerCoordinatorError(RuntimeError):
    """Base class for serial coordinator failures."""


class TaskManagerCoordinatorBusyError(TaskManagerCoordinatorError):
    """Another live coordinator tick owns this run."""


@dataclass(frozen=True)
class _Decision:
    stage: TaskManagerCoordinatorStage
    action: TaskManagerCoordinatorAction
    node_id: str | None
    reason: str


_SATISFIED_STATES = frozenset(
    {
        TaskState.COMPLETED,
        TaskState.GATE_APPROVED,
        TaskState.DELIVERABLE_ACCEPTED,
    }
)
_ACTIVE_STATES = frozenset(
    {
        TaskState.IMPLEMENTING,
        TaskState.VERIFYING,
        TaskState.REPAIRING,
        TaskState.REVIEWING,
        TaskState.MERGE_REVIEW,
        TaskState.INTEGRATING,
        TaskState.BLOCKED,
        TaskState.FAILED,
    }
)


class TaskManagerSerialCoordinator:
    """Persist intent/cursor and invoke at most one existing command per tick.

    The execution service and ``WorkflowKernel`` remain the only owners of task
    state.  A coordinator crash is reconciled from the newer append-only run
    revision; provider start and integration commands retain their own stable
    idempotency keys.
    """

    def __init__(
        self,
        execution: TaskManagerExecutionService,
        store: TaskManagerCoordinatorStore,
        leases: WorkerLeaseStore,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ):
        if not worker_id or any(
            not (character.isalnum() or character in "._-")
            for character in worker_id
        ):
            raise ValueError("coordinator worker id is invalid")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("coordinator lease must be between 1 and 3600 seconds")
        self.execution = execution
        self.store = store
        self.leases = leases
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def snapshot(self, run_id: str) -> TaskManagerCoordinatorSnapshot | None:
        history = self.store.history(run_id)
        return history[-1].snapshot if history else None

    def tick(
        self,
        run_id: str,
        *,
        expected_run_revision: int,
    ) -> TaskManagerCoordinatorTickResult:
        lease: WorkerLease | None = None
        try:
            self.leases.recover_orphans()
            lease = self.leases.acquire(
                worker_id=self.worker_id,
                run_id=run_id,
                lease_seconds=self.lease_seconds,
            )
        except WorkerLeaseError as exc:
            raise TaskManagerCoordinatorBusyError(
                "TaskManager run already has a live coordinator tick"
            ) from exc

        assert lease is not None
        try:
            result = self._tick_with_lease(
                run_id,
                expected_run_revision=expected_run_revision,
            )
        except Exception:
            try:
                self.leases.release(
                    lease.lease_id,
                    expected_version=lease.version,
                )
            except WorkerLeaseError:
                pass
            raise
        try:
            self.leases.release(
                lease.lease_id,
                expected_version=lease.version,
            )
        except WorkerLeaseError as exc:
            raise TaskManagerCoordinatorError(
                "coordinator action finished without a durable lease release"
            ) from exc
        return result

    def _tick_with_lease(
        self,
        run_id: str,
        *,
        expected_run_revision: int,
    ) -> TaskManagerCoordinatorTickResult:
        run = self.execution.get(run_id)
        if run.sequence != expected_run_revision:
            raise StaleTaskManagerRunRevisionError(
                f"expected TaskManager run revision {expected_run_revision}, "
                f"current revision is {run.sequence}"
            )
        cursor = self._ensure_cursor(run)
        if cursor.run_revision > run.sequence:
            raise TaskManagerCoordinatorError(
                "coordinator cursor is ahead of TaskManager run truth"
            )

        if cursor.pending_intent is not None and run.sequence != cursor.run_revision:
            decision = self._decide(run.snapshot)
            if (
                decision.action is cursor.pending_intent.action
                and decision.node_id == cursor.pending_intent.node_id
            ):
                updated_run = self._execute(cursor.pending_intent, run)
                next_decision = self._decide(updated_run.snapshot)
                resumed = self._append_projection(
                    cursor,
                    updated_run,
                    next_decision,
                    operation=(
                        "coordinator.intent_resumed_from_partial_run:"
                        f"{cursor.pending_intent.action.value}"
                    ),
                    last_completed_intent_id=cursor.pending_intent.intent_id,
                )
                return TaskManagerCoordinatorTickResult(
                    coordinator=resumed,
                    run=updated_run.snapshot,
                    performed_action=cursor.pending_intent.action,
                    side_effect_performed=True,
                    recovered_intent=True,
                )
            reconciled = self._append_projection(
                cursor,
                run,
                decision,
                operation="coordinator.intent_reconciled_from_run",
                last_completed_intent_id=cursor.pending_intent.intent_id,
            )
            return TaskManagerCoordinatorTickResult(
                coordinator=reconciled,
                run=run.snapshot,
                performed_action=cursor.pending_intent.action,
                recovered_intent=True,
            )

        if cursor.pending_intent is not None:
            intent = cursor.pending_intent
            updated_run = self._execute(intent, run)
            decision = self._decide(updated_run.snapshot)
            completed = self._append_projection(
                cursor,
                updated_run,
                decision,
                operation=f"coordinator.action_completed:{intent.action.value}",
                last_completed_intent_id=intent.intent_id,
            )
            return TaskManagerCoordinatorTickResult(
                coordinator=completed,
                run=updated_run.snapshot,
                performed_action=intent.action,
                side_effect_performed=True,
                recovered_intent=True,
            )

        decision = self._decide(run.snapshot)
        if decision.action not in SIDE_EFFECTING_COORDINATOR_ACTIONS:
            projected = self._project_if_changed(cursor, run, decision)
            return TaskManagerCoordinatorTickResult(
                coordinator=projected,
                run=run.snapshot,
                performed_action=decision.action,
            )

        assert decision.node_id is not None
        intent = TaskManagerCoordinatorIntent(
            intent_id=self._intent_id(run, decision),
            run_id=run_id,
            node_id=decision.node_id,
            action=decision.action,
            expected_run_revision=run.sequence,
            created_at=datetime.now(UTC),
        )
        advancing = cursor.model_copy(
            update={
                "revision": cursor.revision + 1,
                "run_revision": run.sequence,
                "stage": TaskManagerCoordinatorStage.ADVANCING,
                "cursor_node_id": decision.node_id,
                "next_action": decision.action,
                "pending_intent": intent,
                "reason": decision.reason,
                "updated_at": datetime.now(UTC),
            }
        )
        intent_record = self.store.append(
            advancing,
            actor_type=ActorType.SYSTEM,
            actor_id=self.worker_id,
            operation=f"coordinator.intent_recorded:{decision.action.value}",
        )
        updated_run = self._execute(intent, run)
        next_decision = self._decide(updated_run.snapshot)
        completed = self._append_projection(
            intent_record.snapshot,
            updated_run,
            next_decision,
            operation=f"coordinator.action_completed:{decision.action.value}",
            last_completed_intent_id=intent.intent_id,
        )
        return TaskManagerCoordinatorTickResult(
            coordinator=completed,
            run=updated_run.snapshot,
            performed_action=decision.action,
            side_effect_performed=True,
        )

    def _ensure_cursor(
        self,
        run: TaskManagerRunRevisionRecord,
    ) -> TaskManagerCoordinatorSnapshot:
        history = self.store.history(run.run_id)
        if history:
            return history[-1].snapshot
        decision = self._decide(run.snapshot)
        now = datetime.now(UTC)
        snapshot = TaskManagerCoordinatorSnapshot(
            run_id=run.run_id,
            revision=1,
            run_revision=run.sequence,
            stage=decision.stage,
            cursor_node_id=decision.node_id,
            next_action=decision.action,
            reason=decision.reason,
            created_at=now,
            updated_at=now,
        )
        return self.store.append(
            snapshot,
            actor_type=ActorType.SYSTEM,
            actor_id=self.worker_id,
            operation="coordinator.cursor_initialized",
        ).snapshot

    def _project_if_changed(
        self,
        cursor: TaskManagerCoordinatorSnapshot,
        run: TaskManagerRunRevisionRecord,
        decision: _Decision,
    ) -> TaskManagerCoordinatorSnapshot:
        if (
            cursor.run_revision == run.sequence
            and cursor.stage is decision.stage
            and cursor.cursor_node_id == decision.node_id
            and cursor.next_action is decision.action
            and cursor.reason == decision.reason
        ):
            return cursor
        return self._append_projection(
            cursor,
            run,
            decision,
            operation=f"coordinator.projected:{decision.action.value}",
        )

    def _append_projection(
        self,
        cursor: TaskManagerCoordinatorSnapshot,
        run: TaskManagerRunRevisionRecord,
        decision: _Decision,
        *,
        operation: str,
        last_completed_intent_id: str | None = None,
    ) -> TaskManagerCoordinatorSnapshot:
        snapshot = cursor.model_copy(
            update={
                "revision": cursor.revision + 1,
                "run_revision": run.sequence,
                "stage": decision.stage,
                "cursor_node_id": decision.node_id,
                "next_action": decision.action,
                "pending_intent": None,
                "last_completed_intent_id": (
                    last_completed_intent_id or cursor.last_completed_intent_id
                ),
                "reason": decision.reason,
                "updated_at": datetime.now(UTC),
            }
        )
        return self.store.append(
            snapshot,
            actor_type=ActorType.SYSTEM,
            actor_id=self.worker_id,
            operation=operation,
        ).snapshot

    def _execute(
        self,
        intent: TaskManagerCoordinatorIntent,
        run: TaskManagerRunRevisionRecord,
    ) -> TaskManagerRunRevisionRecord:
        expected = run.sequence
        if intent.action is TaskManagerCoordinatorAction.START_NODE:
            return self.execution.start_node(
                run.run_id,
                intent.node_id,
                expected_run_revision=expected,
            )
        if intent.action is TaskManagerCoordinatorAction.RUN_VALIDATION:
            return self.execution.run_validation_node(
                run.run_id,
                intent.node_id,
                expected_run_revision=expected,
            )
        if intent.action is TaskManagerCoordinatorAction.OBSERVE_NODE:
            return self.execution.observe_node(
                run.run_id,
                intent.node_id,
                expected_run_revision=expected,
            )
        if intent.action is TaskManagerCoordinatorAction.VERIFY_NODE:
            return self.execution.verify_node(
                run.run_id,
                intent.node_id,
                expected_run_revision=expected,
            )
        if intent.action is TaskManagerCoordinatorAction.INTEGRATE_CHECKPOINT:
            return self.execution.integrate_source_checkpoint(
                run.run_id,
                intent.node_id,
                expected_run_revision=expected,
            )
        raise TaskManagerCoordinatorError(
            f"coordinator cannot execute action {intent.action.value}"
        )

    @staticmethod
    def _intent_id(
        run: TaskManagerRunRevisionRecord,
        decision: _Decision,
    ) -> str:
        payload = (
            f"{run.run_id}:{run.sequence}:{decision.node_id}:{decision.action.value}"
        ).encode("utf-8")
        return "tmcoord-" + hashlib.sha256(payload).hexdigest()[:32]

    @classmethod
    def _decide(cls, run: TaskManagerRunSnapshot) -> _Decision:
        if run.stage is TaskManagerRunStage.COMPLETED:
            return _Decision(
                TaskManagerCoordinatorStage.COMPLETED,
                TaskManagerCoordinatorAction.COMPLETE,
                None,
                "all finalized DAG nodes passed governed completion",
            )
        if run.stage is TaskManagerRunStage.CANCELLED:
            return _Decision(
                TaskManagerCoordinatorStage.NEEDS_ATTENTION,
                TaskManagerCoordinatorAction.NEEDS_ATTENTION,
                None,
                "run is cancelled; coordinator will not start further work",
            )

        active = tuple(
            node for node in run.nodes if node.workflow_state in _ACTIVE_STATES
        )
        if len(active) > 1:
            identifiers = ", ".join(node.node.node_id for node in active)
            return _Decision(
                TaskManagerCoordinatorStage.NEEDS_ATTENTION,
                TaskManagerCoordinatorAction.NEEDS_ATTENTION,
                None,
                "serial invariant found more than one active node: " + identifiers,
            )
        if active:
            return cls._active_decision(active[0])

        ready = tuple(
            node
            for node in run.nodes
            if node.workflow_state in {TaskState.PLANNED, TaskState.PLAN_REVIEW}
            and all(
                cls._node(run, dependency).workflow_state in _SATISFIED_STATES
                for dependency in node.dependency_node_ids
            )
        )
        if ready:
            node = ready[0]
            if node.node.kind is TaskPlanNodeKind.HUMAN_GATE:
                return _Decision(
                    TaskManagerCoordinatorStage.WAITING_HUMAN,
                    TaskManagerCoordinatorAction.WAIT_HUMAN,
                    node.node.node_id,
                    "next-ready node is a human gate and requires an authorized decision",
                )
            if node.node.kind is TaskPlanNodeKind.VALIDATION:
                return _Decision(
                    TaskManagerCoordinatorStage.READY,
                    TaskManagerCoordinatorAction.RUN_VALIDATION,
                    node.node.node_id,
                    "next-ready validation node will run its finalized profile",
                )
            return _Decision(
                TaskManagerCoordinatorStage.READY,
                TaskManagerCoordinatorAction.START_NODE,
                node.node.node_id,
                "stable DAG order selected the only next node for execution",
            )

        return _Decision(
            TaskManagerCoordinatorStage.NEEDS_ATTENTION,
            TaskManagerCoordinatorAction.NEEDS_ATTENTION,
            None,
            "no node is active or dependency-ready; the run needs operator inspection",
        )

    @staticmethod
    def _active_decision(node: TaskManagerNodeExecution) -> _Decision:
        node_id = node.node.node_id
        if node.workflow_state is TaskState.IMPLEMENTING:
            if node.provider_reference is None:
                return _Decision(
                    TaskManagerCoordinatorStage.READY,
                    (
                        TaskManagerCoordinatorAction.RUN_VALIDATION
                        if node.node.kind is TaskPlanNodeKind.VALIDATION
                        else TaskManagerCoordinatorAction.START_NODE
                    ),
                    node_id,
                    "recover the persisted provider start intent without duplicating it",
                )
            return _Decision(
                TaskManagerCoordinatorStage.READY,
                TaskManagerCoordinatorAction.OBSERVE_NODE,
                node_id,
                "observe the one bound provider run and append normalized feedback",
            )
        if node.workflow_state is TaskState.VERIFYING:
            return _Decision(
                TaskManagerCoordinatorStage.READY,
                TaskManagerCoordinatorAction.VERIFY_NODE,
                node_id,
                "compile provider facts into a deterministic verification report",
            )
        if node.workflow_state is TaskState.INTEGRATING:
            return _Decision(
                TaskManagerCoordinatorStage.READY,
                TaskManagerCoordinatorAction.INTEGRATE_CHECKPOINT,
                node_id,
                "integrate the exact independently reviewed and approved checkpoint",
            )
        if node.workflow_state in {TaskState.REVIEWING, TaskState.MERGE_REVIEW}:
            return _Decision(
                TaskManagerCoordinatorStage.WAITING_REVIEW,
                TaskManagerCoordinatorAction.WAIT_REVIEW,
                node_id,
                "passing evidence is waiting for an authorized independent review",
            )
        return _Decision(
            TaskManagerCoordinatorStage.NEEDS_ATTENTION,
            TaskManagerCoordinatorAction.NEEDS_ATTENTION,
            node_id,
            f"node is {node.workflow_state.value}; explicit repair, retry, or cancellation is required",
        )

    @staticmethod
    def _node(run: TaskManagerRunSnapshot, node_id: str) -> TaskManagerNodeExecution:
        return next(node for node in run.nodes if node.node.node_id == node_id)


__all__ = [
    "TaskManagerCoordinatorBusyError",
    "TaskManagerCoordinatorError",
    "TaskManagerSerialCoordinator",
]
