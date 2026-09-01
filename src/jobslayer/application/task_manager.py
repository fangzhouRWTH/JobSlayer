"""Focused TaskManager facade over planning and governed execution truth."""

from __future__ import annotations

from jobslayer.application.task_orchestration import (
    IncompleteTaskPlanError,
    StaleTaskPlanRevisionError,
    TaskOrchestrationService,
)
from jobslayer.application.task_manager_execution import TaskManagerExecutionService
from jobslayer.domain.models import ActorType, TaskState
from jobslayer.orchestration import (
    DiscussionRole,
    TaskPlanAssessment,
    TaskPlanMessage,
    TaskPlanRevisionRecord,
    TaskPlanStatus,
)
from jobslayer.task_manager import (
    ManagedNodeState,
    ManagedNodeView,
    ManagedTaskDetail,
    ManagedTaskStage,
    ManagedTaskSummary,
    TaskManagerBacklogItem,
    TaskManagerLogCategory,
    TaskManagerLogEntry,
    TaskManagerNodeExecution,
    TaskManagerRunRevisionRecord,
    TaskManagerRunSnapshot,
    TaskManagerRunStage,
)
from jobslayer.task_manager.binding import (
    TaskManagerExecutionTarget,
    TaskManagerExecutionTargetAssessment,
    assess_plan_for_target,
    describe_execution_target,
)


EXECUTION_NOT_CONNECTED = (
    "TaskManager execution adapter is not connected; the plan-bound run remains "
    "ready without dispatching an external agent."
)
RUN_ASSEMBLY_NOT_CONFIGURED = (
    "TaskManager run persistence is not configured; finalized plans cannot yet "
    "be assembled into governed node state."
)
PLAN_NOT_FINALIZED = "请先处理候选图、通过完整度检查并固化精确计划 revision。"
RUN_NOT_ASSEMBLED = "请先为当前 finalized revision 创建不可变绑定的执行运行。"
TARGET_NOT_SELECTED = "请先选择并固化当前任务的 BraveNewWorld 执行目标。"


class TaskManagerCapabilityUnavailableError(RuntimeError):
    """Raised when a command targets a deliberately unconfigured capability."""


class TaskManagerService:
    """Expose one task product surface without creating a second state owner."""

    def __init__(
        self,
        planning: TaskOrchestrationService,
        execution: TaskManagerExecutionService | None = None,
    ):
        self.planning = planning
        self.execution = execution

    def list_tasks(self) -> tuple[ManagedTaskSummary, ...]:
        summaries = tuple(
            self._summary(
                record,
                self.planning.assess(record.plan_id),
                run=self._run_for(record),
            )
            for record in self.planning.list_latest()
        )
        return tuple(
            sorted(
                summaries,
                key=lambda item: (
                    item.is_archived,
                    -item.updated_at.timestamp(),
                    item.task_id,
                ),
            )
        )

    def get(self, task_id: str) -> ManagedTaskDetail:
        record = self.planning.get(task_id)
        assessment = self.planning.assess(task_id)
        history = self.planning.history(task_id)
        return self._detail(
            record,
            assessment,
            history,
            run=self._run_for(record),
        )

    def create(
        self,
        task_description: str,
        *,
        task_id: str | None = None,
    ) -> ManagedTaskDetail:
        record = self.planning.create(task_description, plan_id=task_id)
        return self.get(record.plan_id)

    def discuss(
        self,
        task_id: str,
        content: str,
        *,
        expected_revision: int,
        selected_node_id: str | None = None,
    ) -> ManagedTaskDetail:
        self.planning.discuss(
            task_id,
            content,
            expected_revision=expected_revision,
            selected_node_id=selected_node_id,
        )
        return self.get(task_id)

    def list_execution_targets(self) -> tuple[TaskManagerExecutionTarget, ...]:
        return self.execution.list_targets() if self.execution is not None else ()

    def select_execution_target(
        self,
        task_id: str,
        target_id: str,
        *,
        expected_revision: int,
    ) -> ManagedTaskDetail:
        if self.execution is None:
            raise TaskManagerCapabilityUnavailableError(
                "TaskManager execution-target registry is not configured"
            )
        binding = self.execution.resolve_target(target_id)
        self.planning.set_execution_target(
            task_id,
            target_id,
            binding.source_bundle_sha256,
            expected_revision=expected_revision,
        )
        return self.get(task_id)

    def apply_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> ManagedTaskDetail:
        self.planning.apply_proposal(
            task_id,
            proposal_id,
            expected_revision=expected_revision,
        )
        return self.get(task_id)

    def reject_proposal(
        self,
        task_id: str,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> ManagedTaskDetail:
        self.planning.reject_proposal(
            task_id,
            proposal_id,
            expected_revision=expected_revision,
        )
        return self.get(task_id)

    def finalize(
        self,
        task_id: str,
        *,
        expected_revision: int,
    ) -> ManagedTaskDetail:
        if self.execution is not None:
            record = self.planning.get(task_id)
            if record.sequence != expected_revision:
                raise StaleTaskPlanRevisionError(
                    f"expected task-plan revision {expected_revision}, "
                    f"current is {record.sequence}"
                )
            target_id = record.snapshot.execution_target_id
            if target_id is None:
                raise IncompleteTaskPlanError(TARGET_NOT_SELECTED)
            assessment = assess_plan_for_target(
                record.snapshot,
                self.execution.resolve_target(target_id),
            )
            if not assessment.ready:
                raise IncompleteTaskPlanError(
                    "; ".join(
                        issue.message
                        for issue in assessment.issues
                        if issue.severity.value == "blocker"
                    )
                )
        self.planning.finalize(task_id, expected_revision=expected_revision)
        return self.get(task_id)

    def assemble_run(
        self,
        task_id: str,
        *,
        expected_revision: int,
        run_id: str | None = None,
    ) -> ManagedTaskDetail:
        if self.execution is None:
            raise TaskManagerCapabilityUnavailableError(
                RUN_ASSEMBLY_NOT_CONFIGURED
            )
        record = self.planning.get(task_id)
        self.execution.assemble(
            record,
            expected_plan_revision=expected_revision,
            run_id=run_id,
        )
        return self.get(task_id)

    def start_node(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        retry: bool = False,
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.start_node(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
            retry=retry,
        )
        return self.get(task_id)

    def run_validation_node(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.run_validation_node(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
        )
        return self.get(task_id)

    def confirm_scope_gate(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.confirm_scope_gate(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
            rationale=rationale,
        )
        return self.get(task_id)

    def approve_completion_gate(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.approve_completion_gate(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
            rationale=rationale,
        )
        return self.get(task_id)

    def observe_node(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.observe_node(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
        )
        return self.get(task_id)

    def verify_node(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.verify_node(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
        )
        return self.get(task_id)

    def accept_node_review(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.accept_node_review(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
            rationale=rationale,
        )
        return self.get(task_id)

    def review_source_node(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
        findings: tuple[str, ...] = (),
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.review_source_node(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
            rationale=rationale,
            findings=findings,
        )
        return self.get(task_id)

    def approve_source_checkpoint(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
        rationale: str,
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.approve_source_checkpoint(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
            rationale=rationale,
        )
        return self.get(task_id)

    def integrate_source_checkpoint(
        self,
        task_id: str,
        run_id: str,
        node_id: str,
        *,
        expected_run_revision: int,
    ) -> ManagedTaskDetail:
        execution = self._require_execution_run(task_id, run_id)
        execution.integrate_source_checkpoint(
            run_id,
            node_id,
            expected_run_revision=expected_run_revision,
        )
        return self.get(task_id)

    def _detail(
        self,
        record: TaskPlanRevisionRecord,
        assessment: TaskPlanAssessment,
        history: tuple[TaskPlanRevisionRecord, ...],
        *,
        run: TaskManagerRunRevisionRecord | None,
    ) -> ManagedTaskDetail:
        snapshot = record.snapshot
        target, target_assessment = self._target_context(record, run)
        nodes = self._nodes(record, assessment, run)
        backlog = tuple(
            TaskManagerBacklogItem(
                node_id=item.node.node_id,
                title=item.node.title,
                state=item.state,
                reason=self._backlog_reason(item, record, run),
                dependency_node_ids=item.dependency_node_ids,
            )
            for item in nodes
            if item.state is not ManagedNodeState.COMPLETED
        )
        blockers = self._execution_blockers(record, run)
        return ManagedTaskDetail(
            task=self._summary(
                record,
                assessment,
                run=run,
                backlog_count=len(backlog),
            ),
            plan=snapshot,
            assessment=assessment,
            nodes=nodes,
            backlog=backlog,
            log=self._log(
                history,
                self.execution.history(run.run_id) if run is not None else (),
            ),
            execution_targets=self.list_execution_targets(),
            execution_target=target,
            execution_target_assessment=target_assessment,
            execution_run=run.snapshot if run is not None else None,
            run_assembly_available=(
                self.execution is not None
                and run is None
                and snapshot.status is TaskPlanStatus.FINALIZED
                and not snapshot.is_archived
                and target_assessment is not None
                and target_assessment.ready
            ),
            execution_available=not blockers,
            execution_blockers=blockers,
        )

    def _summary(
        self,
        record: TaskPlanRevisionRecord,
        assessment: TaskPlanAssessment,
        *,
        run: TaskManagerRunRevisionRecord | None = None,
        backlog_count: int | None = None,
    ) -> ManagedTaskSummary:
        snapshot = record.snapshot
        effective_nodes = (
            snapshot.pending_proposal.nodes
            if snapshot.pending_proposal is not None
            else snapshot.nodes
        )
        projected_nodes = self._nodes(record, assessment, run)
        target_blockers = 0
        if self.execution is not None:
            if snapshot.execution_target_id is None:
                target_blockers = 1
            else:
                target_blockers = sum(
                    issue.severity.value == "blocker"
                    for issue in self.execution.assess_target(record).issues
                )
        return ManagedTaskSummary(
            task_id=record.plan_id,
            title=self._title(snapshot.task_description),
            task_description=snapshot.task_description,
            revision=record.sequence,
            stage=self._stage(record, run),
            pending_proposal=snapshot.pending_proposal is not None,
            node_count=len(effective_nodes),
            backlog_count=(
                sum(item.state is not ManagedNodeState.COMPLETED for item in projected_nodes)
                if backlog_count is None
                else backlog_count
            ),
            blocker_count=sum(
                issue.severity.value == "blocker" for issue in assessment.issues
            ) + target_blockers,
            is_archived=snapshot.is_archived,
            updated_at=(
                max(snapshot.updated_at, run.snapshot.updated_at)
                if run is not None
                else snapshot.updated_at
            ),
            record_hash=record.record_hash,
        )

    @staticmethod
    def _stage(
        record: TaskPlanRevisionRecord,
        run: TaskManagerRunRevisionRecord | None,
    ) -> ManagedTaskStage:
        snapshot = record.snapshot
        if snapshot.is_archived:
            return ManagedTaskStage.ARCHIVED
        if snapshot.pending_proposal is not None:
            return ManagedTaskStage.PROPOSAL_PENDING
        if run is not None:
            return {
                TaskManagerRunStage.READY: ManagedTaskStage.READY,
                TaskManagerRunStage.RUNNING: ManagedTaskStage.RUNNING,
                TaskManagerRunStage.NEEDS_ATTENTION: ManagedTaskStage.NEEDS_ATTENTION,
                TaskManagerRunStage.VERIFYING: ManagedTaskStage.VERIFYING,
                TaskManagerRunStage.COMPLETED: ManagedTaskStage.COMPLETED,
                TaskManagerRunStage.CANCELLED: ManagedTaskStage.CANCELLED,
            }[run.snapshot.stage]
        if snapshot.status is TaskPlanStatus.FINALIZED:
            return ManagedTaskStage.READY
        return ManagedTaskStage.PLANNING

    def _nodes(
        self,
        record: TaskPlanRevisionRecord,
        assessment: TaskPlanAssessment,
        run: TaskManagerRunRevisionRecord | None,
    ) -> tuple[ManagedNodeView, ...]:
        snapshot = record.snapshot
        issues = {
            node_id: tuple(
                issue.code for issue in assessment.issues if issue.node_id == node_id
            )
            for node_id in {
                issue.node_id for issue in assessment.issues if issue.node_id is not None
            }
        }
        if run is not None:
            return tuple(
                ManagedNodeView(
                    node=item.node,
                    state=self._run_node_state(item, run.snapshot),
                    dependency_node_ids=item.dependency_node_ids,
                    issue_codes=issues.get(item.node.node_id, ()),
                )
                for item in run.snapshot.nodes
            )
        effective_nodes = (
            snapshot.pending_proposal.nodes
            if snapshot.pending_proposal is not None
            else snapshot.nodes
        )
        effective_edges = (
            snapshot.pending_proposal.edges
            if snapshot.pending_proposal is not None
            else snapshot.edges
        )
        state = (
            ManagedNodeState.PROPOSED
            if snapshot.pending_proposal is not None
            else (
                ManagedNodeState.READY
                if snapshot.status is TaskPlanStatus.FINALIZED
                else ManagedNodeState.PLANNED
            )
        )
        return tuple(
            ManagedNodeView(
                node=node,
                state=state,
                dependency_node_ids=tuple(
                    edge.source_node_id
                    for edge in effective_edges
                    if edge.target_node_id == node.node_id
                ),
                issue_codes=issues.get(node.node_id, ()),
            )
            for node in effective_nodes
        )

    @staticmethod
    def _run_node_state(
        item: TaskManagerNodeExecution,
        run: TaskManagerRunSnapshot,
    ) -> ManagedNodeState:
        state = item.workflow_state
        if state in {TaskState.DRAFT, TaskState.PLANNED, TaskState.PLAN_REVIEW}:
            completed = {
                node.node.node_id
                for node in run.nodes
                if node.workflow_state
                in {
                    TaskState.COMPLETED,
                    TaskState.GATE_APPROVED,
                    TaskState.DELIVERABLE_ACCEPTED,
                }
            }
            return (
                ManagedNodeState.READY
                if set(item.dependency_node_ids) <= completed
                else ManagedNodeState.WAITING
            )
        return {
            TaskState.IMPLEMENTING: ManagedNodeState.RUNNING,
            TaskState.VERIFYING: ManagedNodeState.VERIFYING,
            TaskState.REPAIRING: ManagedNodeState.BLOCKED,
            TaskState.REVIEWING: ManagedNodeState.VERIFYING,
            TaskState.MERGE_REVIEW: ManagedNodeState.VERIFYING,
            TaskState.INTEGRATING: ManagedNodeState.VERIFYING,
            TaskState.BLOCKED: ManagedNodeState.BLOCKED,
            TaskState.FAILED: ManagedNodeState.FAILED,
            TaskState.COMPLETED: ManagedNodeState.COMPLETED,
            TaskState.CANCELLED: ManagedNodeState.CANCELLED,
            TaskState.GATE_APPROVED: ManagedNodeState.COMPLETED,
            TaskState.DELIVERABLE_ACCEPTED: ManagedNodeState.COMPLETED,
        }[state]

    def _backlog_reason(
        self,
        item: ManagedNodeView,
        record: TaskPlanRevisionRecord,
        run: TaskManagerRunRevisionRecord | None,
    ) -> str:
        snapshot = record.snapshot
        if snapshot.pending_proposal is not None:
            return "等待用户应用或拒绝 Agent 候选图。"
        if run is None and snapshot.status is TaskPlanStatus.FINALIZED:
            return RUN_NOT_ASSEMBLED
        if run is not None:
            run_node = next(
                node
                for node in run.snapshot.nodes
                if node.node.node_id == item.node.node_id
            )
            if item.state is ManagedNodeState.WAITING:
                return "等待所有依赖节点通过验证、审批和受治理完成。"
            if item.state is ManagedNodeState.READY:
                if run_node.node.kind.value == "human_gate":
                    return "等待具备权限的人类主体通过专用决定路径处理。"
                if run_node.node.kind.value == "validation":
                    return "等待确定性 verifier 运行目标验证规则并登记结构化证据。"
                return (
                    "节点已满足依赖，可以授权执行。"
                    if self.execution is not None and self.execution.adapter_available
                    else EXECUTION_NOT_CONNECTED
                )
            if item.state is ManagedNodeState.RUNNING:
                return (
                    run_node.latest_observation.summary
                    if run_node.latest_observation is not None
                    else "外部执行已授权；等待首个证据化反馈。"
                )
            if item.state is ManagedNodeState.VERIFYING:
                return "Agent 已终止成功；等待确定性验证和授权审批，尚未完成。"
            if item.state is ManagedNodeState.BLOCKED:
                return "节点已阻塞；需要诊断后由授权主体决定重试或取消。"
            if item.state is ManagedNodeState.FAILED:
                return "执行失败；保留证据并等待授权重试。"
            if item.state is ManagedNodeState.CANCELLED:
                return "节点已由受治理流程取消。"
        return "等待计划完整度检查通过并由用户固化任务流。"

    def _execution_blockers(
        self,
        record: TaskPlanRevisionRecord,
        run: TaskManagerRunRevisionRecord | None,
    ) -> tuple[str, ...]:
        if self.execution is None:
            return (RUN_ASSEMBLY_NOT_CONFIGURED,)
        target_id = record.snapshot.execution_target_id
        if target_id is None:
            return (TARGET_NOT_SELECTED,)
        binding = (
            run.snapshot.execution_binding
            if run is not None
            else self.execution.resolve_target(target_id)
        )
        assessment = assess_plan_for_target(record.snapshot, binding)
        target_blockers = tuple(
            issue.message
            for issue in assessment.issues
            if issue.severity.value == "blocker"
        )
        if target_blockers:
            return target_blockers
        if record.snapshot.status is not TaskPlanStatus.FINALIZED:
            return (PLAN_NOT_FINALIZED,)
        if run is None:
            return (RUN_NOT_ASSEMBLED,)
        if run.snapshot.stage in {
            TaskManagerRunStage.COMPLETED,
            TaskManagerRunStage.CANCELLED,
        }:
            return (f"执行运行已进入终态：{run.snapshot.stage.value}。",)
        workflow_states = {node.workflow_state for node in run.snapshot.nodes}
        if TaskState.REVIEWING in workflow_states:
            return ()
        if workflow_states & {TaskState.MERGE_REVIEW, TaskState.INTEGRATING}:
            if self.execution.source_integration_available:
                return ()
            return ("隔离运行分支源码检查点适配器尚未连接。",)
        ready_nodes = tuple(
            node
            for node in run.snapshot.nodes
            if node.workflow_state in {TaskState.PLANNED, TaskState.PLAN_REVIEW}
            and all(
                next(
                    item for item in run.snapshot.nodes
                    if item.node.node_id == dependency
                ).workflow_state
                in {
                    TaskState.COMPLETED,
                    TaskState.GATE_APPROVED,
                    TaskState.DELIVERABLE_ACCEPTED,
                }
                for dependency in node.dependency_node_ids
            )
        )
        if ready_nodes and all(
            node.node.kind.value == "human_gate" for node in ready_nodes
        ):
            return ()
        if ready_nodes and all(
            node.node.kind.value == "validation" for node in ready_nodes
        ):
            if not self.execution.validation_available:
                return ("确定性 validation 适配器尚未连接。",)
        elif not self.execution.adapter_available:
            return (EXECUTION_NOT_CONNECTED,)
        return ()

    def _target_context(
        self,
        record: TaskPlanRevisionRecord,
        run: TaskManagerRunRevisionRecord | None,
    ) -> tuple[
        TaskManagerExecutionTarget | None,
        TaskManagerExecutionTargetAssessment | None,
    ]:
        if self.execution is None or record.snapshot.execution_target_id is None:
            return None, None
        binding = (
            run.snapshot.execution_binding
            if run is not None
            else self.execution.resolve_target(record.snapshot.execution_target_id)
        )
        return (
            describe_execution_target(binding),
            assess_plan_for_target(record.snapshot, binding),
        )

    def _run_for(
        self,
        record: TaskPlanRevisionRecord,
    ) -> TaskManagerRunRevisionRecord | None:
        return (
            self.execution.for_plan_record(record)
            if self.execution is not None
            else None
        )

    def _require_execution_run(
        self,
        task_id: str,
        run_id: str,
    ) -> TaskManagerExecutionService:
        if self.execution is None:
            raise TaskManagerCapabilityUnavailableError(
                RUN_ASSEMBLY_NOT_CONFIGURED
            )
        plan = self.planning.get(task_id)
        run = self.execution.get(run_id)
        if (
            run.snapshot.plan_id != task_id
            or run.snapshot.plan_revision != plan.sequence
            or run.snapshot.plan_record_hash != plan.record_hash
        ):
            raise ValueError("TaskManager run is not bound to the current task revision")
        return self.execution

    @classmethod
    def _log(
        cls,
        history: tuple[TaskPlanRevisionRecord, ...],
        run_history: tuple[TaskManagerRunRevisionRecord, ...] = (),
    ) -> tuple[TaskManagerLogEntry, ...]:
        entries: list[TaskManagerLogEntry] = []
        seen_messages: set[str] = set()
        for record in history:
            entries.append(
                TaskManagerLogEntry(
                    log_id=f"log-{record.record_id}",
                    category=cls._category(record.operation),
                    event_type=record.operation,
                    summary=cls._operation_summary(record.operation),
                    actor_type=record.actor_type,
                    actor_id=record.actor_id,
                    occurred_at=record.occurred_at,
                    revision=record.sequence,
                    record_hash=record.record_hash,
                    node_id=cls._operation_node(record.operation),
                )
            )
            for message in record.snapshot.conversation:
                if message.message_id in seen_messages:
                    continue
                seen_messages.add(message.message_id)
                entries.append(cls._message_log(record, message))
        for record in run_history:
            entries.append(
                TaskManagerLogEntry(
                    log_id=f"log-{record.record_id}",
                    category=(
                        TaskManagerLogCategory.FEEDBACK
                        if record.operation.startswith("node.feedback_")
                        else TaskManagerLogCategory.EXECUTION
                    ),
                    event_type=f"execution.{record.operation}",
                    summary=cls._execution_summary(record.operation),
                    actor_type=record.actor_type,
                    actor_id=record.actor_id,
                    occurred_at=record.occurred_at,
                    revision=record.sequence,
                    record_hash=record.record_hash,
                    node_id=record.node_id,
                )
            )
        return tuple(
            sorted(
                entries,
                key=lambda item: (item.occurred_at, item.revision, item.log_id),
            )
        )

    @staticmethod
    def _message_log(
        record: TaskPlanRevisionRecord,
        message: TaskPlanMessage,
    ) -> TaskManagerLogEntry:
        if message.role is DiscussionRole.USER:
            actor_type = ActorType.HUMAN
            actor_id = record.actor_id
        elif message.role is DiscussionRole.AGENT:
            actor_type = ActorType.AGENT
            actor_id = message.agent_adapter or "planning-agent"
        else:
            actor_type = ActorType.SYSTEM
            actor_id = "task-manager"
        return TaskManagerLogEntry(
            log_id=f"log-{record.record_id}-{message.message_id}",
            category=TaskManagerLogCategory.CONVERSATION,
            event_type=f"conversation.{message.role.value}",
            summary=message.content,
            actor_type=actor_type,
            actor_id=actor_id,
            occurred_at=message.created_at,
            revision=record.sequence,
            record_hash=record.record_hash,
        )

    @staticmethod
    def _category(operation: str) -> TaskManagerLogCategory:
        if "proposal" in operation or operation.startswith("plan.created"):
            return TaskManagerLogCategory.DECISION
        if operation.startswith("discussion"):
            return TaskManagerLogCategory.CONVERSATION
        if operation.startswith("plan.finalized"):
            return TaskManagerLogCategory.DECISION
        return TaskManagerLogCategory.PLANNING

    @staticmethod
    def _operation_summary(operation: str) -> str:
        summaries = {
            "plan.created_with_agent_proposal": "任务已创建，Agent 候选图等待用户决定。",
            "discussion.proposal_recorded": "讨论已追加，新的 Agent 候选图等待用户决定。",
            "agent_proposal.applied_by_user": "用户已应用 Agent 候选图。",
            "agent_proposal.rejected_by_user": "用户已拒绝 Agent 候选图。",
            "plan.finalized_by_user": "用户已固化当前任务流。",
        }
        return summaries.get(operation, operation)

    @staticmethod
    def _execution_summary(operation: str) -> str:
        summaries = {
            "run.assembled_from_finalized_plan": "已从固化 revision 创建精确绑定的执行运行。",
            "node.dispatch_authorized": "授权节点进入实施，并在外部副作用前持久化幂等启动键。",
            "node.retry_authorized": "授权节点使用新的幂等启动键重试实施。",
            "node.provider_run_bound": "外部执行引用及其不可变证据已绑定到节点。",
            "node.validation_authorized": "已从 finalized target profile 授权确定性验证并先持久化稳定键。",
            "node.validation_run_bound": "本地验证运行及源控制命令证据已绑定到节点。",
            "node.feedback_running": "收到执行中的证据化反馈。",
            "node.feedback_succeeded": "外部执行成功；节点已进入确定性验证，不等同于完成。",
            "node.feedback_failed": "外部执行失败；节点已保留证据并进入失败状态。",
            "node.feedback_cancelled": "外部执行已取消；节点进入阻塞状态等待决定。",
            "node.verification_passed": "确定性验证通过；节点等待授权 reviewer 接受交付物。",
            "node.verification_failed": "确定性验证失败；节点进入修复路径。",
            "node.verified_deliverable_accepted": "Reviewer 已接受无源码差异的阶段性交付物。",
            "node.completion_gate_approved": "独立 Approver 已依据最终验证与接受证据批准完成门禁。",
        }
        return summaries.get(operation, operation)

    @staticmethod
    def _operation_node(operation: str) -> str | None:
        prefix = operation.split(":", 1)[0]
        if prefix in {"node.created", "node.updated", "node.deleted"}:
            return operation.split(":", 1)[1]
        return None

    @staticmethod
    def _title(description: str) -> str:
        compact = " ".join(description.split())
        return compact[:157] + "..." if len(compact) > 160 else compact


__all__ = [
    "EXECUTION_NOT_CONNECTED",
    "PLAN_NOT_FINALIZED",
    "RUN_ASSEMBLY_NOT_CONFIGURED",
    "RUN_NOT_ASSEMBLED",
    "TARGET_NOT_SELECTED",
    "TaskManagerCapabilityUnavailableError",
    "TaskManagerService",
]
