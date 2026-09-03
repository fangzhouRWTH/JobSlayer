"""Governed TaskManager run assembly and evidence-backed executor feedback."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import PurePosixPath
import threading
from uuid import uuid4

from pydantic import ValidationError

from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.domain.models import (
    ActorType,
    CheckResult,
    CheckStatus,
    CommandStatus,
    ReviewReport,
    ReviewStatus,
    TaskState,
    VerificationReport,
)
from jobslayer.orchestration import (
    TaskPlanNodeKind,
    TaskPlanRevisionRecord,
    TaskPlanStatus,
)
from jobslayer.persistence.transactional_journal import TransactionalAuditJournal
from jobslayer.task_manager.execution import (
    ManagedExecutionObservation,
    ManagedExecutionReference,
    ManagedExecutionRequest,
    ManagedExecutionStatus,
    ManagedCheckpointRequest,
    ManagedCheckpointResult,
    ManagedVerificationEvidence,
    TaskManagerExecutor,
    TaskManagerNodeExecution,
    TaskManagerRunRevisionRecord,
    TaskManagerRunSnapshot,
    TaskManagerRunStore,
    TaskManagerSourceIntegrator,
    TaskManagerValidator,
    derive_run_stage,
)
from jobslayer.task_manager.binding import (
    TaskManagerExecutionBinding,
    TaskManagerExecutionTarget,
    TaskManagerExecutionTargetAssessment,
    TaskManagerExecutionTargetRegistry,
    assess_plan_for_target,
    describe_execution_target,
)
from jobslayer.task_manager.guidance import (
    TaskManagerHumanActionAssistant,
    TaskManagerHumanActionGuidance,
    TaskManagerHumanInteraction,
    TaskManagerHumanInteractionKind,
)
from jobslayer.workflow.kernel import WorkflowKernel


class TaskManagerExecutionError(RuntimeError):
    """Base class for rejected run assembly or execution commands."""


class TaskManagerRunNotFoundError(TaskManagerExecutionError):
    pass


class TaskManagerRunAlreadyExistsError(TaskManagerExecutionError):
    pass


class TaskManagerPlanNotFinalizedError(TaskManagerExecutionError):
    pass


class StaleTaskManagerRunRevisionError(TaskManagerExecutionError):
    pass


class TaskManagerExecutionNodeNotFoundError(TaskManagerExecutionError):
    pass


class TaskManagerExecutionNodeNotReadyError(TaskManagerExecutionError):
    pass


class TaskManagerExecutionAdapterUnavailableError(TaskManagerExecutionError):
    pass


class TaskManagerExecutionEvidenceError(TaskManagerExecutionError):
    pass


class TaskManagerExecutionProviderError(TaskManagerExecutionError):
    pass


class TaskManagerExecutionTargetUnavailableError(TaskManagerExecutionError):
    pass


class TaskManagerExecutionTargetMismatchError(TaskManagerExecutionError):
    pass


class TaskManagerExecutionService:
    """Bind finalized plans to Kernel-owned node state and adapter evidence."""

    def __init__(
        self,
        store: TaskManagerRunStore,
        artifacts: ArtifactRegistry,
        *,
        actor_id: str,
        executor: TaskManagerExecutor | None = None,
        validator: TaskManagerValidator | None = None,
        source_integrator: TaskManagerSourceIntegrator | None = None,
        targets: TaskManagerExecutionTargetRegistry | None = None,
        human_action_assistant: TaskManagerHumanActionAssistant | None = None,
    ):
        if not actor_id.strip():
            raise ValueError("TaskManager execution actor id must not be blank")
        self.store = store
        self.artifacts = artifacts
        self.actor_id = actor_id
        self.executor = executor
        self.validator = validator
        self.source_integrator = source_integrator
        self.targets = targets
        self.human_action_assistant = human_action_assistant
        self._command_lock = threading.Lock()

    @property
    def adapter_available(self) -> bool:
        return self.executor is not None

    @property
    def adapter_id(self) -> str | None:
        return self.executor.adapter_id if self.executor is not None else None

    @property
    def validation_available(self) -> bool:
        return self.validator is not None

    @property
    def validator_id(self) -> str | None:
        return self.validator.adapter_id if self.validator is not None else None

    @property
    def source_integration_available(self) -> bool:
        return self.source_integrator is not None

    @property
    def source_integrator_id(self) -> str | None:
        return (
            self.source_integrator.adapter_id
            if self.source_integrator is not None
            else None
        )

    @property
    def human_action_assistant_available(self) -> bool:
        return self.human_action_assistant is not None

    @property
    def human_action_assistant_id(self) -> str | None:
        return (
            self.human_action_assistant.adapter_id
            if self.human_action_assistant is not None
            else None
        )

    def list_targets(self) -> tuple[TaskManagerExecutionTarget, ...]:
        if self.targets is None:
            return ()
        try:
            bindings = self.targets.list_targets()
            return tuple(
                describe_execution_target(binding) for binding in bindings
            )
        except (LookupError, OSError, RuntimeError, ValueError) as exc:
            raise TaskManagerExecutionTargetUnavailableError(
                "TaskManager execution targets are unavailable"
            ) from exc

    def resolve_target(self, target_id: str) -> TaskManagerExecutionBinding:
        if self.targets is None:
            raise TaskManagerExecutionTargetUnavailableError(
                "TaskManager execution-target registry is not configured"
            )
        try:
            return self.targets.get(target_id)
        except (LookupError, OSError, RuntimeError, ValueError) as exc:
            raise TaskManagerExecutionTargetUnavailableError(
                f"TaskManager execution target is unavailable: {target_id}"
            ) from exc

    def assess_target(
        self,
        plan: TaskPlanRevisionRecord,
    ) -> TaskManagerExecutionTargetAssessment:
        target_id = plan.snapshot.execution_target_id
        if target_id is None:
            raise TaskManagerExecutionTargetUnavailableError(
                "task plan has no selected execution target"
            )
        existing_run = self.for_plan_record(plan)
        binding = (
            existing_run.snapshot.execution_binding
            if existing_run is not None
            else self.resolve_target(target_id)
        )
        return assess_plan_for_target(plan.snapshot, binding)

    def list_latest(self) -> tuple[TaskManagerRunRevisionRecord, ...]:
        records = []
        for run_id in self.store.list_run_ids():
            history = self.store.history(run_id)
            if history:
                records.append(history[-1])
        return tuple(sorted(records, key=lambda item: item.occurred_at))

    def history(self, run_id: str) -> tuple[TaskManagerRunRevisionRecord, ...]:
        history = self.store.history(run_id)
        if not history:
            raise TaskManagerRunNotFoundError("TaskManager execution run does not exist")
        return history

    def get(self, run_id: str) -> TaskManagerRunRevisionRecord:
        return self.history(run_id)[-1]

    def for_plan_record(
        self,
        plan: TaskPlanRevisionRecord,
    ) -> TaskManagerRunRevisionRecord | None:
        matches = tuple(
            item
            for item in self.list_latest()
            if (
                item.snapshot.plan_id == plan.plan_id
                and item.snapshot.plan_revision == plan.sequence
                and item.snapshot.plan_record_hash == plan.record_hash
            )
        )
        if len(matches) > 1:
            raise TaskManagerExecutionError(
                "finalized plan revision is bound to multiple TaskManager runs"
            )
        return matches[0] if matches else None

    def assemble(
        self,
        plan: TaskPlanRevisionRecord,
        *,
        expected_plan_revision: int,
        run_id: str | None = None,
    ) -> TaskManagerRunRevisionRecord:
        with self._command_lock:
            if plan.sequence != expected_plan_revision:
                raise TaskManagerPlanNotFinalizedError(
                    "task-plan revision changed before run assembly"
                )
            snapshot = plan.snapshot
            if (
                snapshot.status is not TaskPlanStatus.FINALIZED
                or snapshot.latest_finalized_revision != plan.sequence
                or snapshot.is_archived
            ):
                raise TaskManagerPlanNotFinalizedError(
                    "TaskManager run assembly requires an active finalized plan revision"
                )
            if self.for_plan_record(plan) is not None:
                raise TaskManagerRunAlreadyExistsError(
                    "finalized plan revision already has a TaskManager execution run"
                )
            identifier = run_id or f"tmrun-{uuid4().hex}"
            if self.store.history(identifier):
                raise TaskManagerRunAlreadyExistsError(
                    "TaskManager execution run id already exists"
                )
            target_id = snapshot.execution_target_id
            if target_id is None:
                raise TaskManagerExecutionTargetUnavailableError(
                    "run assembly requires a selected execution target"
                )
            binding = self.resolve_target(target_id)
            assessment = assess_plan_for_target(snapshot, binding)
            if not assessment.ready:
                messages = "; ".join(
                    issue.message
                    for issue in assessment.issues
                    if issue.severity.value == "blocker"
                )
                raise TaskManagerExecutionTargetMismatchError(messages)
            dependencies = {
                node.node_id: tuple(
                    edge.source_node_id
                    for edge in snapshot.edges
                    if edge.target_node_id == node.node_id
                )
                for node in snapshot.nodes
            }
            nodes = []
            for node in snapshot.nodes:
                workflow_task_id = self._workflow_task_id(plan, node.node_id)
                journal = TransactionalAuditJournal(workflow_task_id, ())
                WorkflowKernel(journal).transition(
                    task_id=workflow_task_id,
                    to_state=TaskState.PLANNED,
                    actor_type=ActorType.SYSTEM,
                    actor_id="task-manager-run-assembler",
                    reason=(
                        "bound node to finalized TaskManager plan revision "
                        f"{plan.plan_id}@{plan.sequence}"
                    ),
                    evidence_ids=(plan.record_hash,),
                )
                workflow_state = TaskState.PLANNED
                if node.kind is TaskPlanNodeKind.HUMAN_GATE:
                    WorkflowKernel(journal).transition(
                        task_id=workflow_task_id,
                        to_state=TaskState.PLAN_REVIEW,
                        actor_type=ActorType.SYSTEM,
                        actor_id="task-manager-run-assembler",
                        reason="human-gate node is awaiting an authorized decision",
                        evidence_ids=(plan.record_hash,),
                    )
                    workflow_state = TaskState.PLAN_REVIEW
                nodes.append(
                    TaskManagerNodeExecution(
                        node=node,
                        workflow_task_id=workflow_task_id,
                        dependency_node_ids=dependencies[node.node_id],
                        workflow_state=workflow_state,
                        transition_history=journal.staged,
                    )
                )
            now = datetime.now(UTC)
            run_snapshot = TaskManagerRunSnapshot(
                run_id=identifier,
                revision=1,
                plan_id=plan.plan_id,
                plan_revision=plan.sequence,
                plan_record_hash=plan.record_hash,
                execution_binding=binding,
                stage=derive_run_stage(tuple(nodes)),
                nodes=tuple(nodes),
                created_by=self.actor_id,
                created_at=now,
                updated_at=now,
            )
            return self.store.append(
                run_snapshot,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                operation="run.assembled_from_finalized_plan",
            )

    def confirm_scope_gate(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
    ) -> TaskManagerRunRevisionRecord:
        """Bind one root human gate to an explicit finalized-scope confirmation."""

        reason = " ".join(rationale.split()).strip()
        if not reason:
            raise TaskManagerExecutionNodeNotReadyError(
                "scope-gate confirmation requires a non-blank rationale"
            )
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            if (
                node.node.kind is not TaskPlanNodeKind.HUMAN_GATE
                or node.dependency_node_ids
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "only a dependency-free human gate can bind plan finalization"
                )
            if node.workflow_state is TaskState.GATE_APPROVED:
                return latest
            if node.workflow_state not in {
                TaskState.PLANNED,
                TaskState.PLAN_REVIEW,
            }:
                raise TaskManagerExecutionNodeNotReadyError(
                    "scope gate is not awaiting a finalized-plan decision"
                )
            occurred_at = datetime.now(UTC)
            payload = {
                "schema_version": "1.0",
                "decision": "approved",
                "decision_kind": "finalized_scope_confirmation",
                "run_id": run_id,
                "plan_id": latest.snapshot.plan_id,
                "plan_revision": latest.snapshot.plan_revision,
                "plan_record_hash": latest.snapshot.plan_record_hash,
                "node_id": node_id,
                "workflow_task_id": node.workflow_task_id,
                "actor_id": self.actor_id,
                "rationale": reason,
                "occurred_at": occurred_at.isoformat(),
            }
            evidence = self.artifacts.register_bytes(
                task_id=node.workflow_task_id,
                run_id=run_id,
                artifact_type="task-manager.scope-gate-decision",
                producer="task-manager-execution-service",
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                metadata={
                    "node_id": node_id,
                    "plan_record_hash": latest.snapshot.plan_record_hash,
                    "decision": "approved",
                },
            )
            journal = TransactionalAuditJournal(
                node.workflow_task_id,
                node.transition_history,
            )
            if node.workflow_state is TaskState.PLANNED:
                WorkflowKernel(journal).transition(
                    task_id=node.workflow_task_id,
                    to_state=TaskState.PLAN_REVIEW,
                    actor_type=ActorType.SYSTEM,
                    actor_id="task-manager-scope-gate",
                    reason="scope gate entered explicit plan review",
                    evidence_ids=(latest.snapshot.plan_record_hash,),
                )
            WorkflowKernel(journal).transition(
                task_id=node.workflow_task_id,
                to_state=TaskState.GATE_APPROVED,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                reason=reason,
                evidence_ids=(
                    latest.snapshot.plan_record_hash,
                    evidence.artifact_id,
                ),
            )
            history = (*node.transition_history, *journal.staged)
            changed = self._replace_node(
                latest.snapshot,
                node_id,
                workflow_state=TaskState.GATE_APPROVED,
                transition_history=history,
            )
            return self.store.append(
                changed,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                operation="node.scope_gate_confirmed_from_finalized_plan",
                node_id=node_id,
            )

    def approve_completion_gate(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
    ) -> TaskManagerRunRevisionRecord:
        """Approve a dependent final gate from verified terminal evidence."""

        reason = " ".join(rationale.split()).strip()
        if not reason:
            raise TaskManagerExecutionNodeNotReadyError(
                "completion approval requires a non-blank rationale"
            )
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            if node.workflow_state is TaskState.GATE_APPROVED:
                return latest
            if (
                node.node.kind is not TaskPlanNodeKind.HUMAN_GATE
                or not node.dependency_node_ids
                or node.workflow_state
                not in {TaskState.PLANNED, TaskState.PLAN_REVIEW}
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "only a dependent human gate awaiting review can approve completion"
                )
            dependencies = tuple(
                self._node(latest.snapshot, dependency_id)
                for dependency_id in node.dependency_node_ids
            )
            if any(
                node_id in item.dependency_node_ids
                for item in latest.snapshot.nodes
                if item.node.node_id != node_id
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "completion approval is available only for a terminal human gate"
                )
            if any(not self._dependency_satisfied(item) for item in dependencies):
                raise TaskManagerExecutionNodeNotReadyError(
                    "completion gate dependencies have not passed governed completion"
                )
            if any(
                not self._dependency_satisfied(item)
                for item in latest.snapshot.nodes
                if item.node.node_id != node_id
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "completion gate requires every other run node to be terminal"
                )
            if any(
                item.verification_report is None
                or not item.verification_report.passes_gate
                or item.verification_artifact_id is None
                or (
                    item.review_artifact_id is None
                    and item.integration_artifact_id is None
                )
                for item in dependencies
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "completion gate requires passing verification and authorized "
                    "acceptance evidence from every direct dependency"
                )
            dependent_reviewer_ids = {
                item.transition_history[-1].actor_id for item in dependencies
            }
            if self.actor_id in dependent_reviewer_ids:
                raise TaskManagerExecutionNodeNotReadyError(
                    "completion approval must be independent from direct dependency review"
                )
            occurred_at = datetime.now(UTC)
            dependency_evidence = tuple(
                {
                    "node_id": item.node.node_id,
                    "workflow_task_id": item.workflow_task_id,
                    "workflow_state": item.workflow_state.value,
                    "verification_report_id": item.verification_report.report_id,
                    "verification_artifact_id": item.verification_artifact_id,
                    "acceptance_artifact_id": (
                        item.review_artifact_id or item.integration_artifact_id
                    ),
                }
                for item in dependencies
            )
            payload = {
                "schema_version": "1.0",
                "decision": "approved",
                "decision_kind": "final_completion_approval",
                "run_id": run_id,
                "plan_id": latest.snapshot.plan_id,
                "plan_revision": latest.snapshot.plan_revision,
                "plan_record_hash": latest.snapshot.plan_record_hash,
                "node_id": node_id,
                "workflow_task_id": node.workflow_task_id,
                "actor_id": self.actor_id,
                "rationale": reason,
                "dependencies": dependency_evidence,
                "occurred_at": occurred_at.isoformat(),
            }
            decision_artifact = self.artifacts.register_bytes(
                task_id=node.workflow_task_id,
                run_id=run_id,
                artifact_type="task-manager.completion-gate-decision",
                producer="task-manager-execution-service",
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                metadata={
                    "node_id": node_id,
                    "plan_record_hash": latest.snapshot.plan_record_hash,
                    "decision": "approved",
                },
            )
            cited_evidence = tuple(
                dict.fromkeys(
                    (
                        latest.snapshot.plan_record_hash,
                        decision_artifact.artifact_id,
                        *(
                            evidence_id
                            for item in dependencies
                            for evidence_id in (
                                item.verification_artifact_id,
                                item.review_artifact_id or item.integration_artifact_id,
                            )
                            if evidence_id is not None
                        ),
                    )
                )
            )
            journal = TransactionalAuditJournal(
                node.workflow_task_id,
                node.transition_history,
            )
            if node.workflow_state is TaskState.PLANNED:
                WorkflowKernel(journal).transition(
                    task_id=node.workflow_task_id,
                    to_state=TaskState.PLAN_REVIEW,
                    actor_type=ActorType.SYSTEM,
                    actor_id="task-manager-completion-gate",
                    reason="legacy final gate entered explicit completion review",
                    evidence_ids=(latest.snapshot.plan_record_hash,),
                )
            WorkflowKernel(journal).transition(
                task_id=node.workflow_task_id,
                to_state=TaskState.GATE_APPROVED,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                reason=reason,
                evidence_ids=cited_evidence,
            )
            changed = self._replace_node(
                latest.snapshot,
                node_id,
                workflow_state=TaskState.GATE_APPROVED,
                transition_history=(*node.transition_history, *journal.staged),
            )
            return self.store.append(
                changed,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                operation="node.completion_gate_approved",
                node_id=node_id,
            )

    def start_node(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        retry: bool = False,
    ) -> TaskManagerRunRevisionRecord:
        if self.executor is None:
            raise TaskManagerExecutionAdapterUnavailableError(
                "TaskManager execution adapter is not configured"
            )
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            if (
                latest.snapshot.execution_binding.executor_adapter
                != self.executor.adapter_id
            ):
                raise TaskManagerExecutionAdapterUnavailableError(
                    "configured executor does not match the finalized target binding"
                )
            if node.node.kind is TaskPlanNodeKind.HUMAN_GATE:
                raise TaskManagerExecutionNodeNotReadyError(
                    "human-gate nodes require an authorized human decision, not an executor"
                )
            if node.node.kind is TaskPlanNodeKind.VALIDATION:
                raise TaskManagerExecutionNodeNotReadyError(
                    "validation nodes require the deterministic verifier, not an executor"
                )
            if node.workflow_state is TaskState.IMPLEMENTING:
                if node.provider_start_key is None or node.provider_reference is not None:
                    raise TaskManagerExecutionNodeNotReadyError(
                        "implementing node is not awaiting provider start recovery"
                    )
                authorized = latest
                start_key = node.provider_start_key
            else:
                expected_states = (
                    {TaskState.FAILED, TaskState.BLOCKED}
                    if retry
                    else {TaskState.PLANNED}
                )
                if node.workflow_state not in expected_states:
                    raise TaskManagerExecutionNodeNotReadyError(
                        "node workflow state does not permit this execution attempt"
                    )
                if any(
                    not self._dependency_satisfied(
                        self._node(latest.snapshot, dependency)
                    )
                    for dependency in node.dependency_node_ids
                ):
                    raise TaskManagerExecutionNodeNotReadyError(
                        "node dependencies have not passed governed completion"
                    )
                attempt = sum(
                    record.to_state is TaskState.IMPLEMENTING
                    for record in node.transition_history
                ) + 1
                start_key = self._provider_start_key(run_id, node_id, attempt)
                journal = TransactionalAuditJournal(
                    node.workflow_task_id,
                    node.transition_history,
                )
                WorkflowKernel(journal).transition(
                    task_id=node.workflow_task_id,
                    to_state=TaskState.IMPLEMENTING,
                    actor_type=ActorType.HUMAN,
                    actor_id=self.actor_id,
                    reason=(
                        "authorized TaskManager executor attempt "
                        f"{attempt} for finalized plan node"
                    ),
                    evidence_ids=(latest.snapshot.plan_record_hash,),
                )
                changed = self._replace_node(
                    latest.snapshot,
                    node_id,
                    workflow_state=TaskState.IMPLEMENTING,
                    transition_history=(*node.transition_history, *journal.staged),
                    provider_start_key=start_key,
                    provider_reference=None,
                    latest_observation=None,
                    verification_evidence=None,
                    verification_report=None,
                    verification_artifact_id=None,
                    review_artifact_id=None,
                )
                authorized = self.store.append(
                    changed,
                    actor_type=ActorType.HUMAN,
                    actor_id=self.actor_id,
                    operation=(
                        "node.retry_authorized" if retry else "node.dispatch_authorized"
                    ),
                    node_id=node_id,
                )
            authorized_node = self._node(authorized.snapshot, node_id)
            request = ManagedExecutionRequest(
                provider_start_key=start_key,
                run_id=run_id,
                plan_id=authorized.snapshot.plan_id,
                plan_revision=authorized.snapshot.plan_revision,
                plan_record_hash=authorized.snapshot.plan_record_hash,
                workflow_task_id=authorized_node.workflow_task_id,
                execution_binding=authorized.snapshot.execution_binding,
                node=authorized_node.node,
                dependency_node_ids=authorized_node.dependency_node_ids,
                prompt=self._prompt(authorized.snapshot, authorized_node),
            )
            try:
                raw_reference = self.executor.start_or_locate(request)
            except (OSError, RuntimeError) as exc:
                raise TaskManagerExecutionProviderError(
                    "executor failed to start or locate the authorized provider run"
                ) from exc
            try:
                reference = ManagedExecutionReference.model_validate(raw_reference)
            except (TypeError, ValueError, ValidationError) as exc:
                raise TaskManagerExecutionEvidenceError(
                    "executor returned an invalid provider reference"
                ) from exc
            if (
                reference.provider_start_key != start_key
                or reference.adapter_id != self.executor.adapter_id
            ):
                raise TaskManagerExecutionEvidenceError(
                    "executor reference does not match the authorized start request"
                )
            self._verify_evidence(
                reference.evidence_artifact_ids,
                task_id=authorized_node.workflow_task_id,
                run_id=run_id,
            )
            bound = self._replace_node(
                authorized.snapshot,
                node_id,
                provider_reference=reference,
            )
            return self.store.append(
                bound,
                actor_type=ActorType.SYSTEM,
                actor_id=self.executor.adapter_id,
                operation="node.provider_run_bound",
                node_id=node_id,
            )

    def run_validation_node(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
    ) -> TaskManagerRunRevisionRecord:
        """Authorize and idempotently run one source-controlled validation profile."""

        if self.validator is None:
            raise TaskManagerExecutionAdapterUnavailableError(
                "TaskManager deterministic validation adapter is not configured"
            )
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            if node.node.kind is not TaskPlanNodeKind.VALIDATION:
                raise TaskManagerExecutionNodeNotReadyError(
                    "deterministic validation accepts validation nodes only"
                )
            if node.workflow_state is TaskState.IMPLEMENTING:
                if node.provider_start_key is None or node.provider_reference is not None:
                    raise TaskManagerExecutionNodeNotReadyError(
                        "validation node is not awaiting start recovery"
                    )
                authorized = latest
                start_key = node.provider_start_key
            else:
                if node.workflow_state is not TaskState.PLANNED:
                    raise TaskManagerExecutionNodeNotReadyError(
                        "validation node state does not permit a new validation attempt"
                    )
                if any(
                    not self._dependency_satisfied(
                        self._node(latest.snapshot, dependency)
                    )
                    for dependency in node.dependency_node_ids
                ):
                    raise TaskManagerExecutionNodeNotReadyError(
                        "validation node dependencies have not passed governed completion"
                    )
                attempt = sum(
                    record.to_state is TaskState.IMPLEMENTING
                    for record in node.transition_history
                ) + 1
                start_key = self._validation_start_key(run_id, node_id, attempt)
                journal = TransactionalAuditJournal(
                    node.workflow_task_id,
                    node.transition_history,
                )
                WorkflowKernel(journal).transition(
                    task_id=node.workflow_task_id,
                    to_state=TaskState.IMPLEMENTING,
                    actor_type=ActorType.HUMAN,
                    actor_id=self.actor_id,
                    reason=(
                        "authorized deterministic validation attempt "
                        f"{attempt} from the finalized target profile"
                    ),
                    evidence_ids=(latest.snapshot.plan_record_hash,),
                )
                changed = self._replace_node(
                    latest.snapshot,
                    node_id,
                    workflow_state=TaskState.IMPLEMENTING,
                    transition_history=(*node.transition_history, *journal.staged),
                    provider_start_key=start_key,
                    provider_reference=None,
                    latest_observation=None,
                    verification_evidence=None,
                    verification_report=None,
                    verification_artifact_id=None,
                    review_artifact_id=None,
                )
                authorized = self.store.append(
                    changed,
                    actor_type=ActorType.HUMAN,
                    actor_id=self.actor_id,
                    operation="node.validation_authorized",
                    node_id=node_id,
                )
            authorized_node = self._node(authorized.snapshot, node_id)
            request = ManagedExecutionRequest(
                provider_start_key=start_key,
                run_id=run_id,
                plan_id=authorized.snapshot.plan_id,
                plan_revision=authorized.snapshot.plan_revision,
                plan_record_hash=authorized.snapshot.plan_record_hash,
                workflow_task_id=authorized_node.workflow_task_id,
                execution_binding=authorized.snapshot.execution_binding,
                node=authorized_node.node,
                dependency_node_ids=authorized_node.dependency_node_ids,
                prompt=self._prompt(authorized.snapshot, authorized_node),
            )
            try:
                raw_reference = self.validator.start_or_locate(request)
            except (OSError, RuntimeError) as exc:
                raise TaskManagerExecutionProviderError(
                    "deterministic validator failed to run or locate the authorized checks"
                ) from exc
            try:
                reference = ManagedExecutionReference.model_validate(raw_reference)
            except (TypeError, ValueError, ValidationError) as exc:
                raise TaskManagerExecutionEvidenceError(
                    "deterministic validator returned an invalid run reference"
                ) from exc
            if (
                reference.provider_start_key != start_key
                or reference.adapter_id != self.validator.adapter_id
            ):
                raise TaskManagerExecutionEvidenceError(
                    "validation reference does not match the authorized request"
                )
            self._verify_evidence(
                reference.evidence_artifact_ids,
                task_id=authorized_node.workflow_task_id,
                run_id=run_id,
            )
            bound = self._replace_node(
                authorized.snapshot,
                node_id,
                provider_reference=reference,
            )
            return self.store.append(
                bound,
                actor_type=ActorType.SYSTEM,
                actor_id=self.validator.adapter_id,
                operation="node.validation_run_bound",
                node_id=node_id,
            )

    def observe_node(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
    ) -> TaskManagerRunRevisionRecord:
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            reference = node.provider_reference
            adapter = self._adapter_for_reference(reference)
            if (
                node.workflow_state is not TaskState.IMPLEMENTING
                or reference is None
                or adapter is None
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "node has no observable live provider execution"
                )
            try:
                raw_observation = adapter.observe(
                    reference,
                    after_cursor=(
                        node.latest_observation.cursor
                        if node.latest_observation is not None
                        else None
                    ),
                )
            except (OSError, RuntimeError) as exc:
                raise TaskManagerExecutionProviderError(
                    "executor failed to observe the bound provider run"
                ) from exc
            try:
                observation = ManagedExecutionObservation.model_validate(
                    raw_observation
                )
            except (TypeError, ValueError, ValidationError) as exc:
                raise TaskManagerExecutionEvidenceError(
                    "executor returned an invalid execution observation"
                ) from exc
            if observation.provider_run_id != reference.provider_run_id:
                raise TaskManagerExecutionEvidenceError(
                    "execution observation belongs to another provider run"
                )
            if node.latest_observation is not None:
                if observation.cursor == node.latest_observation.cursor:
                    if observation != node.latest_observation:
                        raise TaskManagerExecutionEvidenceError(
                            "execution cursor was reused with different evidence"
                        )
                    return latest
                if observation.observed_at < node.latest_observation.observed_at:
                    raise TaskManagerExecutionEvidenceError(
                        "execution observations moved backwards in time"
                    )
            self._verify_evidence(
                observation.evidence_artifact_ids,
                task_id=node.workflow_task_id,
                run_id=run_id,
            )
            state = node.workflow_state
            history = node.transition_history
            if observation.status is not ManagedExecutionStatus.RUNNING:
                target = {
                    ManagedExecutionStatus.SUCCEEDED: TaskState.VERIFYING,
                    ManagedExecutionStatus.FAILED: TaskState.FAILED,
                    ManagedExecutionStatus.CANCELLED: TaskState.BLOCKED,
                }[observation.status]
                journal = TransactionalAuditJournal(
                    node.workflow_task_id,
                    node.transition_history,
                )
                WorkflowKernel(journal).transition(
                    task_id=node.workflow_task_id,
                    to_state=target,
                    actor_type=ActorType.SYSTEM,
                    actor_id=adapter.adapter_id,
                    reason=(
                        "provider execution succeeded and awaits deterministic verification"
                        if target is TaskState.VERIFYING
                        else f"provider execution ended as {observation.status.value}"
                    ),
                    evidence_ids=observation.evidence_artifact_ids,
                )
                state = target
                history = (*node.transition_history, *journal.staged)
            changed = self._replace_node(
                latest.snapshot,
                node_id,
                workflow_state=state,
                transition_history=history,
                latest_observation=observation,
            )
            return self.store.append(
                changed,
                actor_type=ActorType.SYSTEM,
                actor_id=adapter.adapter_id,
                operation=f"node.feedback_{observation.status.value}",
                node_id=node_id,
            )

    def verify_node(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
    ) -> TaskManagerRunRevisionRecord:
        """Compile adapter facts into a Kernel-enforced verification report."""

        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            adapter = self._adapter_for_reference(node.provider_reference)
            if (
                node.workflow_state is not TaskState.VERIFYING
                or node.provider_reference is None
                or node.latest_observation is None
                or node.latest_observation.status is not ManagedExecutionStatus.SUCCEEDED
                or adapter is None
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "node is not awaiting deterministic verification of a successful run"
                )
            try:
                raw_evidence = adapter.collect_verification_evidence(
                    node.provider_reference
                )
            except (OSError, RuntimeError) as exc:
                raise TaskManagerExecutionProviderError(
                    "executor failed to collect structured verification evidence"
                ) from exc
            try:
                evidence = ManagedVerificationEvidence.model_validate(raw_evidence)
            except (TypeError, ValueError, ValidationError) as exc:
                raise TaskManagerExecutionEvidenceError(
                    "executor returned invalid verification evidence"
                ) from exc
            if evidence.provider_run_id != node.provider_reference.provider_run_id:
                raise TaskManagerExecutionEvidenceError(
                    "verification evidence belongs to another provider run"
                )
            self._verify_evidence(
                evidence.evidence_artifact_ids,
                task_id=node.workflow_task_id,
                run_id=run_id,
            )

            binding = latest.snapshot.execution_binding
            workspace_bound = (
                evidence.workspace.task_id == binding.task.task_id
                and evidence.source_commit.lower()
                == evidence.workspace.head_commit.lower()
            )
            violations = tuple(
                path
                for path in evidence.workspace.changed_paths
                if not self._path_is_allowed(
                    path,
                    allowed_paths=binding.task.allowed_paths,
                    forbidden_paths=binding.task.forbidden_paths,
                )
            )
            validation_results: tuple[CheckResult, ...] = ()
            if node.node.kind is TaskPlanNodeKind.VALIDATION:
                if self.validator is None or adapter.adapter_id != self.validator.adapter_id:
                    raise TaskManagerExecutionEvidenceError(
                        "validation node evidence did not come from the configured validator"
                    )
                expected_checks = binding.validation_profile.checks
                observed_checks = evidence.validation_checks
                expected_environment = binding.command_environment()
                if len(observed_checks) != len(expected_checks):
                    raise TaskManagerExecutionEvidenceError(
                        "validation evidence does not cover the finalized validation profile"
                    )
                compiled: list[CheckResult] = []
                for expected_check, observed_check in zip(
                    expected_checks,
                    observed_checks,
                    strict=True,
                ):
                    result = observed_check.result
                    if (
                        observed_check.check_id != expected_check.check_id
                        or observed_check.required != expected_check.required
                        or result.argv
                        not in {
                            expected_check.argv,
                            *expected_check.platform_argv.values(),
                        }
                        or result.cwd != expected_check.cwd
                        or result.environment != expected_environment
                        or result.workspace_id != evidence.workspace.workspace_id
                        or result.task_id != binding.task.task_id
                        or result.policy_id
                        != binding.validation_profile.command_policy.policy_id
                    ):
                        raise TaskManagerExecutionEvidenceError(
                            "validation command evidence drifted from the finalized profile"
                        )
                    status = {
                        CommandStatus.PASSED: CheckStatus.PASSED,
                        CommandStatus.FAILED: CheckStatus.FAILED,
                        CommandStatus.TIMED_OUT: CheckStatus.ERROR,
                    }[result.status]
                    summary = {
                        CheckStatus.PASSED: f"{expected_check.title}: passed",
                        CheckStatus.FAILED: (
                            f"{expected_check.title}: failed with exit code "
                            f"{result.exit_code}"
                        ),
                        CheckStatus.ERROR: f"{expected_check.title}: timed out",
                    }[status]
                    compiled.append(
                        CheckResult(
                            check_id=expected_check.check_id,
                            status=status,
                            required=expected_check.required,
                            command=result.argv,
                            artifact_ids=(observed_check.evidence_artifact_id,),
                            summary=summary,
                            evidence_hash=self._fact_hash(
                                result.model_dump(mode="json")
                            ),
                        )
                    )
                validation_results = tuple(compiled)
                if evidence.dependency_attachments != binding.dependency_attachments:
                    raise TaskManagerExecutionEvidenceError(
                        "validation dependency evidence drifted from the run binding"
                    )
                if any(not item.ready for item in evidence.dependency_attachments):
                    raise TaskManagerExecutionEvidenceError(
                        "validation dependency evidence contains an unready attachment"
                    )
                if (
                    evidence.source_patch_sha256 is not None
                    or evidence.workspace.changed_paths
                    or not evidence.workspace.working_tree_clean
                ):
                    raise TaskManagerExecutionEvidenceError(
                        "deterministic validation must not change the bound workspace"
                    )
            elif evidence.validation_checks:
                raise TaskManagerExecutionEvidenceError(
                    "executor verification evidence cannot claim validation commands"
                )
            check_facts = (
                (
                    (
                        "validation-run-terminal"
                        if node.node.kind is TaskPlanNodeKind.VALIDATION
                        else "provider-terminal-success"
                    ),
                    True,
                    (
                        "validation runner terminated and its observation is evidence-bound"
                        if node.node.kind is TaskPlanNodeKind.VALIDATION
                        else "provider terminal status is succeeded and observation is evidence-bound"
                    ),
                    {"provider_run_id": evidence.provider_run_id},
                ),
                (
                    "workspace-binding",
                    workspace_bound,
                    (
                        "workspace inspection matches the source-bound execution target"
                        if workspace_bound
                        else "workspace inspection does not match the source-bound target"
                    ),
                    evidence.workspace.model_dump(mode="json"),
                ),
                (
                    "changed-path-policy",
                    not violations,
                    (
                        "all changed paths are within the target policy"
                        if not violations
                        else "changed paths violate target policy: " + ", ".join(violations)
                    ),
                    {
                        "changed_paths": list(evidence.workspace.changed_paths),
                        "violations": list(violations),
                        "allowed_paths": list(binding.task.allowed_paths),
                        "forbidden_paths": list(binding.task.forbidden_paths),
                    },
                ),
                (
                    "immutable-evidence-integrity",
                    True,
                    "all verification artifacts are task/run-bound and hash-verified",
                    {"artifact_ids": list(evidence.evidence_artifact_ids)},
                ),
                *(
                    (
                        (
                            "dependency-attachment-binding",
                            True,
                            (
                                "external dependencies match the immutable run binding "
                                "and remained unchanged through validation"
                            ),
                            {
                                "attachments": [
                                    item.model_dump(mode="json")
                                    for item in evidence.dependency_attachments
                                ]
                            },
                        ),
                    )
                    if node.node.kind is TaskPlanNodeKind.VALIDATION
                    and binding.dependency_attachments
                    else ()
                ),
            )
            fact_checks = tuple(
                CheckResult(
                    check_id=check_id,
                    status=CheckStatus.PASSED if passed else CheckStatus.FAILED,
                    required=True,
                    artifact_ids=evidence.evidence_artifact_ids,
                    summary=summary,
                    evidence_hash=self._fact_hash(facts),
                )
                for check_id, passed, summary, facts in check_facts
            )
            checks = (*validation_results, *fact_checks)
            report = VerificationReport(
                report_id=f"tmverify-{uuid4().hex}",
                task_id=node.workflow_task_id,
                source_commit=evidence.source_commit,
                source_patch_sha256=evidence.source_patch_sha256,
                checks=checks,
                required_checks_passed=all(
                    not check.required or check.status is CheckStatus.PASSED
                    for check in checks
                ),
            )
            report_artifact = self.artifacts.register_bytes(
                task_id=node.workflow_task_id,
                run_id=run_id,
                artifact_type="task-manager-verification-report",
                producer="task-manager-deterministic-verifier",
                content=json.dumps(
                    report.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                metadata={
                    "node_id": node_id,
                    "report_id": report.report_id,
                    "passes_gate": report.passes_gate,
                },
            )
            target = (
                TaskState.REVIEWING if report.passes_gate else TaskState.REPAIRING
            )
            journal = TransactionalAuditJournal(
                node.workflow_task_id,
                node.transition_history,
            )
            WorkflowKernel(journal).transition(
                task_id=node.workflow_task_id,
                to_state=target,
                actor_type=ActorType.SYSTEM,
                actor_id="task-manager-deterministic-verifier",
                reason=(
                    "deterministic verification passed; awaiting an authorized reviewer"
                    if target is TaskState.REVIEWING
                    else "deterministic verification failed; repair is required"
                ),
                verification_report=report,
                evidence_ids=(
                    *evidence.evidence_artifact_ids,
                    report_artifact.artifact_id,
                ),
            )
            changed = self._replace_node(
                latest.snapshot,
                node_id,
                workflow_state=target,
                transition_history=(*node.transition_history, *journal.staged),
                verification_evidence=evidence,
                verification_report=report,
                verification_artifact_id=report_artifact.artifact_id,
            )
            return self.store.append(
                changed,
                actor_type=ActorType.SYSTEM,
                actor_id="task-manager-deterministic-verifier",
                operation=(
                    "node.verification_passed"
                    if report.passes_gate
                    else "node.verification_failed"
                ),
                node_id=node_id,
            )

    def accept_node_review(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
        reviewer_actor_type: ActorType = ActorType.HUMAN,
    ) -> TaskManagerRunRevisionRecord:
        """Accept a verified artifact-only node without claiming source integration."""

        if reviewer_actor_type not in {ActorType.HUMAN, ActorType.POLICY}:
            raise TaskManagerExecutionNodeNotReadyError(
                "artifact-only acceptance requires a human or policy reviewer"
            )
        reason = " ".join(rationale.split()).strip()
        if not reason:
            raise TaskManagerExecutionNodeNotReadyError(
                "node review acceptance requires a non-blank rationale"
            )
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            if node.workflow_state is TaskState.DELIVERABLE_ACCEPTED:
                return latest
            if (
                node.node.kind
                not in {
                    TaskPlanNodeKind.TASK,
                    TaskPlanNodeKind.MILESTONE,
                    TaskPlanNodeKind.VALIDATION,
                }
                or node.workflow_state is not TaskState.REVIEWING
                or node.verification_report is None
                or node.verification_evidence is None
                or node.verification_artifact_id is None
                or not node.verification_report.passes_gate
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "node is not awaiting review of a passing verification report"
                )
            evidence = node.verification_evidence
            if (
                evidence.source_patch_sha256 is not None
                or evidence.workspace.changed_paths
                or not evidence.workspace.working_tree_clean
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "source-changing work requires review and source integration; "
                    "it cannot use artifact-only acceptance"
                )
            occurred_at = datetime.now(UTC)
            payload = {
                "schema_version": "1.0",
                "decision": "accepted",
                "decision_kind": "verified_artifact_deliverable_review",
                "run_id": run_id,
                "plan_id": latest.snapshot.plan_id,
                "plan_revision": latest.snapshot.plan_revision,
                "plan_record_hash": latest.snapshot.plan_record_hash,
                "node_id": node_id,
                "workflow_task_id": node.workflow_task_id,
                "verification_report_id": node.verification_report.report_id,
                "verification_artifact_id": node.verification_artifact_id,
                "reviewer_actor_type": reviewer_actor_type.value,
                "reviewer_id": self.actor_id,
                "rationale": reason,
                "accepted_deliverables": list(node.node.deliverables),
                "accepted_criteria": list(node.node.acceptance_criteria),
                "occurred_at": occurred_at.isoformat(),
            }
            review_artifact = self.artifacts.register_bytes(
                task_id=node.workflow_task_id,
                run_id=run_id,
                artifact_type="task-manager-deliverable-review",
                producer=self.actor_id,
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                metadata={
                    "node_id": node_id,
                    "decision": "accepted",
                    "verification_report_id": node.verification_report.report_id,
                },
            )
            journal = TransactionalAuditJournal(
                node.workflow_task_id,
                node.transition_history,
            )
            WorkflowKernel(journal).transition(
                task_id=node.workflow_task_id,
                to_state=TaskState.DELIVERABLE_ACCEPTED,
                actor_type=reviewer_actor_type,
                actor_id=self.actor_id,
                reason=reason,
                verification_report=node.verification_report,
                evidence_ids=(
                    node.verification_artifact_id,
                    review_artifact.artifact_id,
                    *evidence.evidence_artifact_ids,
                ),
            )
            changed = self._replace_node(
                latest.snapshot,
                node_id,
                workflow_state=TaskState.DELIVERABLE_ACCEPTED,
                transition_history=(*node.transition_history, *journal.staged),
                review_artifact_id=review_artifact.artifact_id,
            )
            return self.store.append(
                changed,
                actor_type=reviewer_actor_type,
                actor_id=self.actor_id,
                operation="node.verified_deliverable_accepted",
                node_id=node_id,
            )

    def review_source_node(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
        findings: tuple[str, ...] = (),
        reviewer_actor_type: ActorType = ActorType.HUMAN,
    ) -> TaskManagerRunRevisionRecord:
        """Accept one verified source patch for an independent merge decision."""

        if reviewer_actor_type not in {ActorType.HUMAN, ActorType.AGENT}:
            raise TaskManagerExecutionNodeNotReadyError(
                "source review requires a human or agent reviewer"
            )
        reason = " ".join(rationale.split()).strip()
        normalized_findings = tuple(
            item for item in (" ".join(value.split()).strip() for value in findings) if item
        )
        if not reason:
            raise TaskManagerExecutionNodeNotReadyError(
                "source review requires a non-blank rationale"
            )
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            if (
                node.node.kind
                not in {TaskPlanNodeKind.TASK, TaskPlanNodeKind.MILESTONE}
                or node.workflow_state is not TaskState.REVIEWING
                or node.verification_report is None
                or node.verification_evidence is None
                or node.verification_artifact_id is None
                or not node.verification_report.passes_gate
                or node.verification_report.source_patch_sha256 is None
                or not node.verification_evidence.workspace.changed_paths
                or node.verification_evidence.workspace.working_tree_clean
                or node.source_review is not None
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "node is not awaiting review of one passing source patch"
                )
            evidence = node.verification_evidence
            report = node.verification_report
            review = ReviewReport(
                review_id=f"tmreview-{uuid4().hex}",
                task_id=node.workflow_task_id,
                reviewer_actor_type=reviewer_actor_type,
                reviewer_id=self.actor_id,
                patch_sha256=report.source_patch_sha256,
                status=ReviewStatus.ACCEPTED,
                summary=reason,
                findings=normalized_findings,
                evidence_ids=tuple(
                    dict.fromkeys(
                        (
                            report.report_id,
                            node.verification_artifact_id,
                            *evidence.evidence_artifact_ids,
                        )
                    )
                ),
            )
            artifact = self.artifacts.register_bytes(
                task_id=node.workflow_task_id,
                run_id=run_id,
                artifact_type="task-manager-source-review",
                producer=self.actor_id,
                content=json.dumps(
                    review.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                metadata={
                    "node_id": node_id,
                    "review_id": review.review_id,
                    "patch_sha256": review.patch_sha256,
                    "decision": review.status.value,
                },
            )
            journal = TransactionalAuditJournal(
                node.workflow_task_id,
                node.transition_history,
            )
            WorkflowKernel(journal).transition(
                task_id=node.workflow_task_id,
                to_state=TaskState.MERGE_REVIEW,
                actor_type=reviewer_actor_type,
                actor_id=self.actor_id,
                reason=reason,
                verification_report=report,
                evidence_ids=(
                    artifact.artifact_id,
                    node.verification_artifact_id,
                    *evidence.evidence_artifact_ids,
                ),
            )
            changed = self._replace_node(
                latest.snapshot,
                node_id,
                workflow_state=TaskState.MERGE_REVIEW,
                transition_history=(*node.transition_history, *journal.staged),
                source_review=review,
                source_review_artifact_id=artifact.artifact_id,
            )
            return self.store.append(
                changed,
                actor_type=reviewer_actor_type,
                actor_id=self.actor_id,
                operation="node.source_review_accepted",
                node_id=node_id,
            )

    def approve_source_checkpoint(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
    ) -> TaskManagerRunRevisionRecord:
        """Authorize a reviewed patch for the existing isolated run branch."""

        reason = " ".join(rationale.split()).strip()
        if not reason:
            raise TaskManagerExecutionNodeNotReadyError(
                "source checkpoint approval requires a non-blank rationale"
            )
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            if (
                node.workflow_state is not TaskState.MERGE_REVIEW
                or node.verification_report is None
                or node.verification_evidence is None
                or node.verification_artifact_id is None
                or node.source_review is None
                or node.source_review_artifact_id is None
                or not node.verification_report.passes_gate
                or node.verification_report.source_patch_sha256 is None
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "node is not awaiting approval of a reviewed source patch"
                )
            if node.source_review.reviewer_id == self.actor_id:
                raise TaskManagerExecutionNodeNotReadyError(
                    "source checkpoint approver must be independent from the reviewer"
                )
            integration_key = self._integration_key(
                run_id,
                node_id,
                node.source_review.review_id,
                node.verification_report.source_patch_sha256,
            )
            occurred_at = datetime.now(UTC)
            payload = {
                "schema_version": "1.0",
                "decision": "approved",
                "decision_kind": "isolated_run_branch_source_checkpoint",
                "run_id": run_id,
                "plan_id": latest.snapshot.plan_id,
                "plan_revision": latest.snapshot.plan_revision,
                "plan_record_hash": latest.snapshot.plan_record_hash,
                "node_id": node_id,
                "workflow_task_id": node.workflow_task_id,
                "verification_report_id": node.verification_report.report_id,
                "source_review_id": node.source_review.review_id,
                "source_patch_sha256": node.verification_report.source_patch_sha256,
                "changed_paths": list(
                    node.verification_evidence.workspace.changed_paths
                ),
                "target_ref": node.verification_evidence.workspace.branch_name,
                "integration_key": integration_key,
                "approver_id": self.actor_id,
                "rationale": reason,
                "scope": "isolated run branch only; no main merge, push, or deploy",
                "occurred_at": occurred_at.isoformat(),
            }
            artifact = self.artifacts.register_bytes(
                task_id=node.workflow_task_id,
                run_id=run_id,
                artifact_type="task-manager-source-checkpoint-approval",
                producer=self.actor_id,
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                metadata={
                    "node_id": node_id,
                    "integration_key": integration_key,
                    "source_review_id": node.source_review.review_id,
                    "patch_sha256": node.verification_report.source_patch_sha256,
                    "target_ref": node.verification_evidence.workspace.branch_name,
                },
            )
            journal = TransactionalAuditJournal(
                node.workflow_task_id,
                node.transition_history,
            )
            WorkflowKernel(journal).transition(
                task_id=node.workflow_task_id,
                to_state=TaskState.INTEGRATING,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                reason=reason,
                verification_report=node.verification_report,
                evidence_ids=(
                    node.source_review_artifact_id,
                    artifact.artifact_id,
                    node.verification_artifact_id,
                    *node.verification_evidence.evidence_artifact_ids,
                ),
            )
            changed = self._replace_node(
                latest.snapshot,
                node_id,
                workflow_state=TaskState.INTEGRATING,
                transition_history=(*node.transition_history, *journal.staged),
                source_approval_artifact_id=artifact.artifact_id,
                source_approved_by=self.actor_id,
                integration_key=integration_key,
            )
            return self.store.append(
                changed,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                operation="node.source_checkpoint_approved",
                node_id=node_id,
            )

    def integrate_source_checkpoint(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
    ) -> TaskManagerRunRevisionRecord:
        """Checkpoint the exact approved patch and complete its Kernel task."""

        if self.source_integrator is None:
            raise TaskManagerExecutionAdapterUnavailableError(
                "TaskManager source checkpoint integrator is not configured"
            )
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            if node.workflow_state is TaskState.COMPLETED:
                return latest
            if (
                node.workflow_state is not TaskState.INTEGRATING
                or node.provider_reference is None
                or node.verification_report is None
                or node.verification_evidence is None
                or node.verification_artifact_id is None
                or node.source_review is None
                or node.source_review_artifact_id is None
                or node.source_approval_artifact_id is None
                or node.source_approved_by is None
                or node.integration_key is None
            ):
                raise TaskManagerExecutionNodeNotReadyError(
                    "node has no complete approved source checkpoint intent"
                )
            request = ManagedCheckpointRequest(
                integration_key=node.integration_key,
                run_id=run_id,
                workflow_task_id=node.workflow_task_id,
                node_id=node_id,
                provider_reference=node.provider_reference,
                execution_binding=latest.snapshot.execution_binding,
                verification_report=node.verification_report,
                verification_evidence=node.verification_evidence,
                source_review=node.source_review,
                approved_by=node.source_approved_by,
            )
            try:
                raw_result = self.source_integrator.integrate_checkpoint(request)
                result = ManagedCheckpointResult.model_validate(raw_result)
            except (OSError, RuntimeError, TypeError, ValueError, ValidationError) as exc:
                raise TaskManagerExecutionProviderError(
                    "source checkpoint integrator could not attest the approved patch"
                ) from exc
            integration = result.integration_result
            if (
                result.integration_key != node.integration_key
                or integration.task_id != node.workflow_task_id
                or integration.approved_by != node.source_approved_by
                or integration.base_commit.lower()
                != node.verification_report.source_commit.lower()
                or integration.source_patch_sha256
                != node.verification_report.source_patch_sha256
                or integration.changed_paths
                != node.verification_evidence.workspace.changed_paths
                or integration.target_ref
                != node.verification_evidence.workspace.branch_name
            ):
                raise TaskManagerExecutionEvidenceError(
                    "source checkpoint result does not match approved review evidence"
                )
            self._verify_evidence(
                result.evidence_artifact_ids,
                task_id=node.workflow_task_id,
                run_id=run_id,
            )
            journal = TransactionalAuditJournal(
                node.workflow_task_id,
                node.transition_history,
            )
            WorkflowKernel(journal).transition(
                task_id=node.workflow_task_id,
                to_state=TaskState.COMPLETED,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                reason=(
                    "approved patch was checkpointed exactly on the isolated run branch"
                ),
                verification_report=node.verification_report,
                integration_result=integration,
                evidence_ids=(
                    node.source_review_artifact_id,
                    node.source_approval_artifact_id,
                    *result.evidence_artifact_ids,
                ),
            )
            changed = self._replace_node(
                latest.snapshot,
                node_id,
                workflow_state=TaskState.COMPLETED,
                transition_history=(*node.transition_history, *journal.staged),
                integration_result=integration,
                integration_artifact_id=result.evidence_artifact_ids[0],
            )
            return self.store.append(
                changed,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                operation="node.source_checkpoint_integrated",
                node_id=node_id,
            )

    def record_human_feedback(
        self,
        run_id: str,
        node_id: str,
        *,
        guidance: TaskManagerHumanActionGuidance,
        decision_id: str,
        content: str,
        expected_run_revision: int,
    ) -> TaskManagerRunRevisionRecord:
        """Append explicit non-transitioning feedback to the governed run journal."""

        message = self._human_message(content)
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            self._validate_human_guidance(latest, node, guidance)
            decision = next(
                (item for item in guidance.decisions if item.decision_id == decision_id),
                None,
            )
            if decision is None:
                raise TaskManagerExecutionNodeNotReadyError(
                    "human feedback decision is not offered by the current guidance"
                )
            now = datetime.now(UTC)
            interaction_id = f"tminteraction-{uuid4().hex}"
            artifact = self.artifacts.register_bytes(
                task_id=node.workflow_task_id,
                run_id=run_id,
                artifact_type="task-manager-human-feedback",
                producer=self.actor_id,
                content=json.dumps(
                    {
                        "schema_version": "1.0",
                        "interaction_id": interaction_id,
                        "guidance_id": guidance.guidance_id,
                        "decision_id": decision.decision_id,
                        "decision_label": decision.label,
                        "content": message,
                        "actor_type": ActorType.HUMAN.value,
                        "actor_id": self.actor_id,
                        "based_on_plan_revision": guidance.expected_plan_revision,
                        "based_on_run_revision": guidance.expected_run_revision,
                        "created_at": now.isoformat(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                metadata={
                    "guidance_id": guidance.guidance_id,
                    "node_id": node_id,
                    "decision_id": decision.decision_id,
                    "based_on_plan_revision": guidance.expected_plan_revision,
                    "based_on_run_revision": guidance.expected_run_revision,
                },
            )
            interaction = TaskManagerHumanInteraction(
                interaction_id=interaction_id,
                guidance_id=guidance.guidance_id,
                kind=TaskManagerHumanInteractionKind.FEEDBACK,
                node_id=node_id,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                content=message,
                decision_id=decision.decision_id,
                based_on_plan_revision=guidance.expected_plan_revision,
                based_on_run_revision=expected_run_revision,
                evidence_artifact_ids=(artifact.artifact_id,),
                created_at=now,
            )
            changed = self._replace_node(
                latest.snapshot,
                node_id,
                human_interactions=(*node.human_interactions, interaction),
            )
            return self.store.append(
                changed,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                operation=f"node.human_feedback_recorded:{decision.decision_id}",
                node_id=node_id,
            )

    def request_human_action_assistance(
        self,
        run_id: str,
        node_id: str,
        *,
        guidance: TaskManagerHumanActionGuidance,
        content: str,
        expected_run_revision: int,
    ) -> TaskManagerRunRevisionRecord:
        """Record an intent and one read-only assistant outcome without deciding."""

        assistant = self.human_action_assistant
        if assistant is None:
            raise TaskManagerExecutionAdapterUnavailableError(
                "TaskManager human-action assistant is not configured"
            )
        message = self._human_message(content)
        with self._command_lock:
            latest = self._expected(run_id, expected_run_revision)
            node = self._node(latest.snapshot, node_id)
            self._validate_human_guidance(latest, node, guidance)
            now = datetime.now(UTC)
            request_id = f"tminteraction-{uuid4().hex}"
            request_artifact = self.artifacts.register_bytes(
                task_id=node.workflow_task_id,
                run_id=run_id,
                artifact_type="task-manager-human-assistant-request",
                producer=self.actor_id,
                content=json.dumps(
                    {
                        "schema_version": "1.0",
                        "interaction_id": request_id,
                        "guidance": guidance.model_dump(mode="json"),
                        "content": message,
                        "actor_id": self.actor_id,
                        "created_at": now.isoformat(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                metadata={
                    "guidance_id": guidance.guidance_id,
                    "node_id": node_id,
                    "based_on_plan_revision": guidance.expected_plan_revision,
                    "based_on_run_revision": guidance.expected_run_revision,
                },
            )
            request = TaskManagerHumanInteraction(
                interaction_id=request_id,
                guidance_id=guidance.guidance_id,
                kind=TaskManagerHumanInteractionKind.ASSISTANT_REQUEST,
                node_id=node_id,
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                content=message,
                based_on_plan_revision=guidance.expected_plan_revision,
                based_on_run_revision=expected_run_revision,
                evidence_artifact_ids=(request_artifact.artifact_id,),
                created_at=now,
            )
            requested = self.store.append(
                self._replace_node(
                    latest.snapshot,
                    node_id,
                    human_interactions=(*node.human_interactions, request),
                ),
                actor_type=ActorType.HUMAN,
                actor_id=self.actor_id,
                operation="node.human_assistance_requested",
                node_id=node_id,
            )
            requested_node = self._node(requested.snapshot, node_id)
            try:
                reply = assistant.assist(
                    task_id=node.workflow_task_id,
                    run_id=run_id,
                    guidance=guidance,
                    interactions=requested_node.human_interactions,
                    user_message=message,
                )
                if reply.evidence_artifact_ids:
                    self._verify_evidence(
                        reply.evidence_artifact_ids,
                        task_id=node.workflow_task_id,
                        run_id=run_id,
                    )
                kind = TaskManagerHumanInteractionKind.ASSISTANT_RESPONSE
                actor_id = reply.adapter_id
                response = reply.content
                evidence_ids = reply.evidence_artifact_ids
                operation = "node.human_assistance_responded"
            except (OSError, RuntimeError, TypeError, ValueError, ValidationError) as exc:
                kind = TaskManagerHumanInteractionKind.ASSISTANT_ERROR
                actor_id = assistant.adapter_id
                response = f"Agent 辅助失败：{str(exc)[:900]}"
                failure = self.artifacts.register_bytes(
                    task_id=node.workflow_task_id,
                    run_id=run_id,
                    artifact_type="task-manager-human-assistant-error",
                    producer=assistant.adapter_id,
                    content=response.encode("utf-8"),
                    metadata={
                        "guidance_id": guidance.guidance_id,
                        "node_id": node_id,
                        "request_interaction_id": request_id,
                    },
                )
                evidence_ids = (failure.artifact_id,)
                operation = "node.human_assistance_failed"
            outcome = TaskManagerHumanInteraction(
                interaction_id=f"tminteraction-{uuid4().hex}",
                guidance_id=guidance.guidance_id,
                kind=kind,
                node_id=node_id,
                actor_type=ActorType.AGENT,
                actor_id=actor_id,
                content=response,
                based_on_plan_revision=guidance.expected_plan_revision,
                based_on_run_revision=expected_run_revision,
                evidence_artifact_ids=evidence_ids,
                created_at=datetime.now(UTC),
            )
            changed = self._replace_node(
                requested.snapshot,
                node_id,
                human_interactions=(*requested_node.human_interactions, outcome),
            )
            return self.store.append(
                changed,
                actor_type=ActorType.AGENT,
                actor_id=actor_id,
                operation=operation,
                node_id=node_id,
            )

    @staticmethod
    def _human_message(content: str) -> str:
        message = content.strip()
        if not message or len(message) > 12_000 or "\x00" in message:
            raise ValueError("human interaction content must be 1-12000 characters without NUL bytes")
        return message

    @staticmethod
    def _validate_human_guidance(
        run: TaskManagerRunRevisionRecord,
        node: TaskManagerNodeExecution,
        guidance: TaskManagerHumanActionGuidance,
    ) -> None:
        if (
            guidance.node_id != node.node.node_id
            or guidance.expected_plan_revision != run.snapshot.plan_revision
            or guidance.expected_run_revision != run.sequence
        ):
            raise TaskManagerExecutionNodeNotReadyError(
                "human interaction guidance is stale or belongs to another node"
            )

    def _expected(
        self,
        run_id: str,
        expected_revision: int,
    ) -> TaskManagerRunRevisionRecord:
        latest = self.get(run_id)
        if latest.sequence != expected_revision:
            raise StaleTaskManagerRunRevisionError(
                f"expected TaskManager run revision {expected_revision}, "
                f"latest is {latest.sequence}"
            )
        return latest

    @staticmethod
    def _node(
        snapshot: TaskManagerRunSnapshot,
        node_id: str,
    ) -> TaskManagerNodeExecution:
        for node in snapshot.nodes:
            if node.node.node_id == node_id:
                return node
        raise TaskManagerExecutionNodeNotFoundError(
            "TaskManager execution node does not exist"
        )

    @staticmethod
    def _dependency_satisfied(node: TaskManagerNodeExecution) -> bool:
        return node.workflow_state in {
            TaskState.COMPLETED,
            TaskState.GATE_APPROVED,
            TaskState.DELIVERABLE_ACCEPTED,
        }

    def _adapter_for_reference(
        self,
        reference: ManagedExecutionReference | None,
    ) -> TaskManagerExecutor | TaskManagerValidator | None:
        if reference is None:
            return None
        if self.executor is not None and reference.adapter_id == self.executor.adapter_id:
            return self.executor
        if self.validator is not None and reference.adapter_id == self.validator.adapter_id:
            return self.validator
        return None

    @staticmethod
    def _path_is_allowed(
        path: str,
        *,
        allowed_paths: tuple[str, ...],
        forbidden_paths: tuple[str, ...],
    ) -> bool:
        candidate = PurePosixPath(path)

        def inside(root: str) -> bool:
            if root in {".", "./"}:
                return True
            boundary = PurePosixPath(root.rstrip("/"))
            return candidate == boundary or boundary in candidate.parents

        return not any(inside(root) for root in forbidden_paths) and any(
            inside(root) for root in allowed_paths
        )

    @staticmethod
    def _fact_hash(payload: object) -> str:
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _workflow_task_id(plan: TaskPlanRevisionRecord, node_id: str) -> str:
        digest = hashlib.sha256(
            f"{plan.plan_id}:{plan.sequence}:{plan.record_hash}:{node_id}".encode()
        ).hexdigest()[:32]
        return f"tmnode-{digest}"

    @staticmethod
    def _provider_start_key(run_id: str, node_id: str, attempt: int) -> str:
        digest = hashlib.sha256(
            f"{run_id}:{node_id}:{attempt}".encode()
        ).hexdigest()[:32]
        return f"tmstart-{digest}"

    @staticmethod
    def _validation_start_key(run_id: str, node_id: str, attempt: int) -> str:
        digest = hashlib.sha256(
            f"validation:{run_id}:{node_id}:{attempt}".encode()
        ).hexdigest()[:32]
        return f"tmvalidate-{digest}"

    @staticmethod
    def _integration_key(
        run_id: str,
        node_id: str,
        review_id: str,
        patch_sha256: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{run_id}:{node_id}:{review_id}:{patch_sha256}".encode()
        ).hexdigest()[:32]
        return f"tmintegrate-{digest}"

    @staticmethod
    def _prompt(
        snapshot: TaskManagerRunSnapshot,
        node: TaskManagerNodeExecution,
    ) -> str:
        plan_node = node.node
        lines = [
            f"Execute finalized TaskManager plan {snapshot.plan_id} "
            f"revision {snapshot.plan_revision}, node {plan_node.node_id}.",
            f"Title: {plan_node.title}",
            f"Description: {plan_node.description or '(none)'}",
            "Acceptance criteria:",
            *(f"- {item}" for item in plan_node.acceptance_criteria),
            "Deliverables:",
            *(f"- {item}" for item in plan_node.deliverables),
            "Constraints:",
            *(f"- {item}" for item in plan_node.constraints),
            "Verification requirements:",
            *(f"- {item}" for item in plan_node.verification_requirements),
            "Report evidence and progress, but do not claim workflow completion.",
        ]
        return "\n".join(lines)

    def _verify_evidence(
        self,
        artifact_ids: tuple[str, ...],
        *,
        task_id: str,
        run_id: str,
    ) -> None:
        try:
            manifests = tuple(self.artifacts.get(item) for item in artifact_ids)
            valid = all(
                manifest.task_id == task_id
                and manifest.run_id == run_id
                and self.artifacts.verify(manifest)
                for manifest in manifests
            )
        except (OSError, RuntimeError) as exc:
            raise TaskManagerExecutionEvidenceError(
                "executor evidence artifact is unavailable"
            ) from exc
        if not valid:
            raise TaskManagerExecutionEvidenceError(
                "executor evidence is not task/run-bound and hash-verified"
            )

    @staticmethod
    def _replace_node(
        snapshot: TaskManagerRunSnapshot,
        node_id: str,
        **updates: object,
    ) -> TaskManagerRunSnapshot:
        nodes = []
        for item in snapshot.nodes:
            if item.node.node_id == node_id:
                payload = item.model_dump(mode="json")
                payload.update(updates)
                item = TaskManagerNodeExecution.model_validate(payload)
            nodes.append(item)
        payload = snapshot.model_dump(mode="json")
        payload.update(
            {
                "revision": snapshot.revision + 1,
                "nodes": [item.model_dump(mode="json") for item in nodes],
                "stage": derive_run_stage(tuple(nodes)).value,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        return TaskManagerRunSnapshot.model_validate(payload)


__all__ = [
    "StaleTaskManagerRunRevisionError",
    "TaskManagerExecutionAdapterUnavailableError",
    "TaskManagerExecutionError",
    "TaskManagerExecutionEvidenceError",
    "TaskManagerExecutionNodeNotFoundError",
    "TaskManagerExecutionNodeNotReadyError",
    "TaskManagerExecutionProviderError",
    "TaskManagerExecutionService",
    "TaskManagerExecutionTargetMismatchError",
    "TaskManagerExecutionTargetUnavailableError",
    "TaskManagerPlanNotFinalizedError",
    "TaskManagerRunAlreadyExistsError",
    "TaskManagerRunNotFoundError",
]
