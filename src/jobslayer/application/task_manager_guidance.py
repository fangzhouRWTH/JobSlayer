"""Deterministic human-action guidance projected from plan and run truth."""

from __future__ import annotations

import hashlib

from jobslayer.domain.models import ActorType, TaskState
from jobslayer.orchestration import TaskPlanAssessment, TaskPlanRevisionRecord, TaskPlanStatus
from jobslayer.task_manager.execution import (
    TaskManagerNodeExecution,
    TaskManagerRunRevisionRecord,
    TaskManagerRunStage,
)
from jobslayer.task_manager.guidance import (
    TaskManagerHumanActionGuidance,
    TaskManagerHumanActionKind,
    TaskManagerHumanDecisionOption,
)


_SATISFIED_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.GATE_APPROVED, TaskState.DELIVERABLE_ACCEPTED}
)


def project_human_actions(
    plan: TaskPlanRevisionRecord,
    assessment: TaskPlanAssessment,
    run: TaskManagerRunRevisionRecord | None,
) -> tuple[TaskManagerHumanActionGuidance, ...]:
    """Return only current, revision-bound interaction instructions."""

    snapshot = plan.snapshot
    if snapshot.is_archived:
        return ()
    if run is not None and run.snapshot.stage in {
        TaskManagerRunStage.COMPLETED,
        TaskManagerRunStage.CANCELLED,
    }:
        return ()
    if snapshot.pending_proposal is not None:
        return (_proposal_guidance(plan),)
    if run is None:
        if snapshot.status is TaskPlanStatus.FINALIZED:
            return (_run_assembly_guidance(plan, assessment),)
        if assessment.ready_to_finalize:
            return (_plan_finalization_guidance(plan),)
        return (_plan_refinement_guidance(plan, assessment),)
    actions: list[TaskManagerHumanActionGuidance] = []
    for node in run.snapshot.nodes:
        if node.workflow_state is TaskState.REVIEWING:
            actions.append(_review_guidance(plan, run, node))
        elif node.workflow_state is TaskState.MERGE_REVIEW:
            actions.append(_checkpoint_guidance(plan, run, node))
        elif node.workflow_state in {TaskState.FAILED, TaskState.REPAIRING}:
            actions.append(_failure_guidance(plan, run, node))
        elif node.workflow_state is TaskState.BLOCKED:
            actions.append(_blocker_guidance(plan, run, node))

    if actions:
        return tuple(actions)

    for node in run.snapshot.nodes:
        if (
            node.workflow_state in {TaskState.PLANNED, TaskState.PLAN_REVIEW}
            and all(
                _node(run, dependency).workflow_state in _SATISFIED_STATES
                for dependency in node.dependency_node_ids
            )
            and node.node.kind.value == "human_gate"
        ):
            actions.append(
                _completion_guidance(plan, run, node)
                if node.dependency_node_ids
                else _scope_guidance(plan, run, node)
            )
    return tuple(actions)


def _option(
    decision_id: str,
    label: str,
    effect: str,
    command: str | None = None,
) -> TaskManagerHumanDecisionOption:
    return TaskManagerHumanDecisionOption(
        decision_id=decision_id,
        label=label,
        effect=effect,
        command=command,
    )


def _guidance_id(
    plan_id: str,
    plan_revision: int,
    kind: TaskManagerHumanActionKind,
    *,
    run_id: str | None = None,
    run_revision: int | None = None,
    node_id: str | None = None,
) -> str:
    raw = ":".join(
        (
            plan_id,
            str(plan_revision),
            run_id or "plan",
            str(run_revision or 0),
            node_id or "plan",
            kind.value,
        )
    ).encode("utf-8")
    return "tmguide-" + hashlib.sha256(raw).hexdigest()[:32]


def _base(
    plan: TaskPlanRevisionRecord,
    kind: TaskManagerHumanActionKind,
    *,
    run: TaskManagerRunRevisionRecord | None = None,
    node_id: str | None = None,
    **values: object,
) -> TaskManagerHumanActionGuidance:
    return TaskManagerHumanActionGuidance(
        guidance_id=_guidance_id(
            plan.plan_id,
            plan.sequence,
            kind,
            run_id=run.run_id if run is not None else None,
            run_revision=run.sequence if run is not None else None,
            node_id=node_id,
        ),
        kind=kind,
        node_id=node_id,
        expected_plan_revision=plan.sequence,
        expected_run_revision=run.sequence if run is not None else None,
        **values,
    )


def _proposal_guidance(plan: TaskPlanRevisionRecord) -> TaskManagerHumanActionGuidance:
    proposal = plan.snapshot.pending_proposal
    assert proposal is not None
    return _base(
        plan,
        TaskManagerHumanActionKind.PROPOSAL_DECISION,
        title="决定 Agent 候选任务图",
        summary="候选 DAG 只是提案；必须由人类核对目标、路径和验收要求后应用、退回调整或拒绝。",
        permitted_actor_types=(ActorType.HUMAN,),
        required_capability="manage_task_plan",
        requirements=(
            f"核对当前 plan revision {plan.sequence} 与 proposal {proposal.proposal_id}。",
            "逐节点检查目标、依赖、交付物、约束、风险和验证要求。",
            "确认候选图没有删除仍有效的用户意图或扩大执行授权。",
        ),
        steps=(
            "在任务图中依次选择新增或变化的节点，阅读右侧完整详情。",
            "检查每条依赖边是否形成可执行且无循环的顺序。",
            "如需调整，在 Agent 对话中写明节点、期望变化和验收标准，等待新的 proposal revision。",
            "确认内容准确后选择“应用候选图”；不接受则选择“拒绝”。",
            "应用后刷新并确认候选标记消失、plan revision 增长；此时计划仍未自动固化或执行。",
        ),
        decisions=(
            _option("request_changes", "继续调整", "记录新一轮讨论并生成新的候选图。", "discuss"),
            _option("apply_proposal", "应用候选图", "产生新的已应用 planning revision。", "apply_proposal"),
            _option("reject_proposal", "拒绝候选图", "保留当前已应用任务图。", "reject_proposal"),
        ),
        evidence_to_review=(
            proposal.proposal_id,
            *proposal.evidence_artifact_ids,
        ),
        prohibited_actions=(
            "不得把 Agent proposal 当成已固化任务流。",
            "不得因应用候选图而自动授权执行、修改源码或完成任务。",
        ),
    )


def _plan_refinement_guidance(
    plan: TaskPlanRevisionRecord,
    assessment: TaskPlanAssessment,
) -> TaskManagerHumanActionGuidance:
    blockers = tuple(
        f"{item.code}: {item.message}" for item in assessment.issues
        if item.severity.value == "blocker"
    ) or ("计划完整度尚未达到固化要求。",)
    return _base(
        plan,
        TaskManagerHumanActionKind.PLAN_REFINEMENT,
        title="补齐任务计划后再固化",
        summary="当前计划存在阻塞项，需要用户与 Agent 明确缺失信息。",
        permitted_actor_types=(ActorType.HUMAN,),
        required_capability="manage_task_plan",
        requirements=blockers,
        steps=(
            "阅读阻塞项并定位对应节点或执行目标。",
            "通过 Agent 对话补充目标、依赖、验收标准、风险或验证路径。",
            "审阅并应用新的候选 DAG。",
            "刷新完整度检查，直到所有 blocker 消失后再固化。",
        ),
        decisions=(
            _option("discuss", "补充或调整计划", "形成新的 revision-bound proposal。", "discuss"),
            _option("wait", "暂不处理", "保持当前 planning 状态，不启动执行。"),
        ),
        evidence_to_review=tuple(item.code for item in assessment.issues),
        prohibited_actions=("不得绕过 blocker 强制固化或装配执行 run。",),
    )


def _plan_finalization_guidance(plan: TaskPlanRevisionRecord) -> TaskManagerHumanActionGuidance:
    return _base(
        plan,
        TaskManagerHumanActionKind.PLAN_FINALIZATION,
        title="固化最终任务路径",
        summary="已应用任务图满足结构要求；需要人类确认它可以成为后续执行的不可变输入。",
        permitted_actor_types=(ActorType.HUMAN,),
        required_capability="manage_task_plan",
        requirements=(
            "没有待处理 proposal 或完整度 blocker。",
            "执行目标、验收标准、验证节点和人工门均已明确。",
            f"决定必须绑定 plan revision {plan.sequence}。",
        ),
        steps=(
            "从根节点到最终门沿边检查一次完整路径和支线。",
            "核对每个执行节点的交付物、禁止项和验证要求。",
            "确认目标仓库与执行边界后执行 finalize。",
            "刷新并确认计划状态为 finalized、record hash 已更新。",
        ),
        decisions=(
            _option("finalize", "固化任务流", "当前 revision 成为 run assembly 输入。", "finalize"),
            _option("revise", "继续调整", "返回讨论并生成新的候选图。", "discuss"),
        ),
        evidence_to_review=(plan.record_hash,),
        prohibited_actions=("不得在 finalize 后静默改写该 revision 或其 record hash。",),
    )


def _run_assembly_guidance(
    plan: TaskPlanRevisionRecord,
    assessment: TaskPlanAssessment,
) -> TaskManagerHumanActionGuidance:
    target = plan.snapshot.execution_target_id or "未选择"
    blockers = tuple(
        item.message for item in assessment.issues if item.severity.value == "blocker"
    )
    return _base(
        plan,
        TaskManagerHumanActionKind.RUN_ASSEMBLY,
        title="装配并授权执行 Run",
        summary="finalized 计划尚未绑定执行 run；装配只捕获输入，不会调用外部 Agent。",
        permitted_actor_types=(ActorType.HUMAN,),
        required_capability="manage_task_plan",
        requirements=(
            f"执行目标为 {target}，并通过 source/dependency preflight。",
            "确认 source bundle、基线 commit、预算和权限配置。",
            *(blockers or ("当前 target assessment 没有 blocker。",)),
        ),
        steps=(
            "打开执行目标详情并核对仓库、基线、允许/禁止路径和 dependency attachments。",
            "检查模型、effort、超时、attempt/repair 与 token/cost 预算语义。",
            "创建唯一 run id 并执行 assemble；该操作不得启动 provider。",
            "刷新后确认 run 绑定相同 plan revision/hash 与 source bundle hash。",
            "另行使用 executor 权限启动 coordinator 的第一个受治理动作。",
        ),
        decisions=(
            _option("assemble_run", "装配 Run", "创建 append-only run revision 1。", "assemble_run"),
            _option("revise_plan", "返回规划", "派生或讨论新的计划 revision。", "discuss"),
        ),
        evidence_to_review=(plan.record_hash, target),
        prohibited_actions=(
            "不得把 run assembly 表述为 Agent 已启动。",
            "不得在 target preflight 失败时继续执行。",
        ),
    )


def _review_evidence(node: TaskManagerNodeExecution) -> tuple[str, ...]:
    values: list[str] = []
    if node.verification_report is not None:
        values.append(node.verification_report.report_id)
        if node.verification_report.source_patch_sha256 is not None:
            values.append("patch:" + node.verification_report.source_patch_sha256)
    for value in (
        node.verification_artifact_id,
        node.review_artifact_id,
        node.source_review_artifact_id,
        node.source_approval_artifact_id,
        node.integration_artifact_id,
    ):
        if value is not None:
            values.append(value)
    if node.verification_evidence is not None:
        values.extend(node.verification_evidence.evidence_artifact_ids)
    return tuple(dict.fromkeys(values))


def _review_guidance(
    plan: TaskPlanRevisionRecord,
    run: TaskManagerRunRevisionRecord,
    node: TaskManagerNodeExecution,
) -> TaskManagerHumanActionGuidance:
    evidence = node.verification_evidence
    source_changed = bool(
        evidence is not None
        and (evidence.source_patch_sha256 or evidence.workspace.changed_paths)
    )
    if source_changed:
        patch = evidence.source_patch_sha256 if evidence is not None else None
        return _base(
            plan,
            TaskManagerHumanActionKind.SOURCE_REVIEW,
            run=run,
            node_id=node.node.node_id,
            title=f"技术审查源码节点：{node.node.title}",
            summary="passing report 只证明检查结果；独立 Reviewer 仍需审阅精确补丁并记录结论。",
            permitted_actor_types=(ActorType.HUMAN, ActorType.AGENT),
            required_capability="review_implementation",
            requirements=(
                f"绑定 run revision {run.sequence} 和 patch {patch or '未提供'}。",
                "Reviewer 必须独立于后续 human checkpoint Approver。",
                "逐项核对 changed paths、验收标准、验证结果和未解决风险。",
            ),
            steps=(
                "打开 verification report，确认 required checks 全部通过且无未解决回归。",
                "检查完整 patch 和 changed-path allowlist，不只阅读 Agent 总结。",
                "对照节点 acceptance criteria、constraints 与 deliverables 逐项复核。",
                "记录具体 findings；接受则执行 review-source，发现问题则明确要求修改并返回 repair。",
                "刷新并确认 accepted review 绑定同一 patch SHA，节点进入 merge_review。",
            ),
            decisions=(
                _option("accept_source_review", "技术审查通过", "进入独立 checkpoint 决策。", "review_source"),
                _option("request_changes", "要求修改", "保留 findings 并回到受治理 repair。", "request_changes"),
            ),
            evidence_to_review=_review_evidence(node),
            prohibited_actions=(
                "不得只凭 Agent 自述接受源码。",
                "Reviewer 不得批准或集成自己审查的 patch。",
            ),
        )
    return _base(
        plan,
        TaskManagerHumanActionKind.VERIFIED_DELIVERABLE_REVIEW,
        run=run,
        node_id=node.node.node_id,
        title=f"接受验证交付物：{node.node.title}",
        summary="该节点没有源码变化；授权 human 或命名 policy 需要核对完整确定性证据后接受交付物。",
        permitted_actor_types=(ActorType.HUMAN, ActorType.POLICY),
        required_capability="review_implementation",
        requirements=(
            "verification report 的 required checks 全部 passed。",
            "workspace clean、changed paths 为空且没有 source patch。",
            "接受者能够解释所接受的 deliverables 与 acceptance criteria。",
        ),
        steps=(
            "打开 report 与每个 required command 的原始 stdout/stderr、退出码和哈希。",
            "核对 dependency attachments、workspace 与 source commit 没有漂移。",
            "逐项核对节点 deliverables 和 acceptance criteria。",
            "满足要求后执行 accept-review；否则保留原因并要求重新验证或修复。",
            "刷新并确认节点进入 deliverable_accepted，后继依赖才可解锁。",
        ),
        decisions=(
            _option("accept_deliverable", "接受验证交付物", "节点成为 deliverable_accepted。", "accept_review"),
            _option("reject_deliverable", "暂不接受", "保持 reviewing 并要求补证或修复。"),
        ),
        evidence_to_review=_review_evidence(node),
        prohibited_actions=(
            "Agent 不得接受 artifact-only deliverable。",
            "不得把 passing report 自动等同于最终任务完成。",
        ),
    )


def _checkpoint_guidance(
    plan: TaskPlanRevisionRecord,
    run: TaskManagerRunRevisionRecord,
    node: TaskManagerNodeExecution,
) -> TaskManagerHumanActionGuidance:
    patch = (
        node.verification_report.source_patch_sha256
        if node.verification_report is not None
        else None
    )
    return _base(
        plan,
        TaskManagerHumanActionKind.SOURCE_CHECKPOINT_APPROVAL,
        run=run,
        node_id=node.node.node_id,
        title=f"批准精确源码检查点：{node.node.title}",
        summary="独立 human Approver 必须决定是否允许把已审查 patch 写入隔离 run branch。",
        permitted_actor_types=(ActorType.HUMAN,),
        required_capability="apply_decision",
        requirements=(
            f"批准对象必须是 patch {patch or '未提供'}，run revision {run.sequence}。",
            "存在 accepted source review，且 Approver 与 Reviewer 身份不同。",
            "批准范围明确排除 main merge、push、deploy 与最终完成，除非另有授权。",
        ),
        steps=(
            "复核 source review findings、verification report、完整 patch 和 changed paths。",
            "确认目标基线未漂移、工作树仍与被审查 patch 完全一致。",
            "明确批准范围和禁止项，使用独立 approver session 执行 approve-checkpoint。",
            "刷新并确认批准 artifact 绑定相同 patch、review id 与 integration key。",
            "再由具备 integration opt-in 的 coordinator 写入隔离 branch；核对生成 commit，不合并主干。",
        ),
        decisions=(
            _option("approve_checkpoint", "批准隔离检查点", "节点进入 integrating，允许精确 patch checkpoint。", "approve_checkpoint"),
            _option("request_changes", "拒绝或要求修改", "不产生 Git 副作用，返回修复路径。", "request_changes"),
        ),
        evidence_to_review=_review_evidence(node),
        prohibited_actions=(
            "不得批准与 review hash 不同的 patch。",
            "不得由同一 Reviewer 自审自批。",
            "不得借 checkpoint 执行 main merge、push 或部署。",
        ),
    )


def _scope_guidance(
    plan: TaskPlanRevisionRecord,
    run: TaskManagerRunRevisionRecord,
    node: TaskManagerNodeExecution,
) -> TaskManagerHumanActionGuidance:
    return _base(
        plan,
        TaskManagerHumanActionKind.SCOPE_CONFIRMATION,
        run=run,
        node_id=node.node.node_id,
        title=f"确认任务根范围：{node.node.title}",
        summary="根人工门需要用户确认目标、边界和验收口径，之后才能解锁执行节点。",
        permitted_actor_types=(ActorType.HUMAN,),
        required_capability="manage_task_plan",
        requirements=(
            f"确认 run 仍绑定 finalized plan revision {plan.sequence}。",
            "目标仓库、允许/禁止路径、预算和验证路径均可接受。",
        ),
        steps=(
            "阅读任务目标、根节点描述和全部 acceptance criteria。",
            "沿 DAG 检查后继执行、验证与最终人工门没有缺失。",
            "核对 execution target、source bundle 与权限边界。",
            "接受则执行 confirm-scope；否则回到规划创建新 revision。",
            "刷新并确认根门 gate_approved，后继节点才变为 ready。",
        ),
        decisions=(
            _option("confirm_scope", "确认范围", "根门通过并解锁后继节点。", "confirm_scope"),
            _option("revise_scope", "调整范围", "保持门禁并返回任务讨论。", "discuss"),
        ),
        evidence_to_review=(plan.record_hash,),
        prohibited_actions=("不得由 Agent 或 coordinator 代替用户确认根范围。",),
    )


def _completion_guidance(
    plan: TaskPlanRevisionRecord,
    run: TaskManagerRunRevisionRecord,
    node: TaskManagerNodeExecution,
) -> TaskManagerHumanActionGuidance:
    dependencies = tuple(_node(run, item) for item in node.dependency_node_ids)
    evidence = tuple(
        dict.fromkeys(
            value
            for dependency in dependencies
            for value in _review_evidence(dependency)
        )
    )
    return _base(
        plan,
        TaskManagerHumanActionKind.COMPLETION_APPROVAL,
        run=run,
        node_id=node.node.node_id,
        title=f"最终确认任务闭环：{node.node.title}",
        summary="所有直接依赖已满足；只有授权且独立的人类可以基于完整证据决定是否完成本次 run。",
        permitted_actor_types=(ActorType.HUMAN,),
        required_capability="apply_decision",
        requirements=(
            f"决定绑定 plan R{plan.sequence}、run R{run.sequence} 与当前哈希链。",
            "所有直接依赖均为 completed、deliverable_accepted 或 gate_approved。",
            "最终 Approver 不得是直接依赖的最后 Reviewer。",
            "明确区分 run 完成与 main merge、push、deploy、release 等外部动作。",
        ),
        steps=(
            "在任务图中选择本节点，确认所有前置节点均为受治理满足状态。",
            "逐一打开直接依赖的 verification report、review/approval 和 integration artifacts。",
            "核对 required checks、原始命令结果、运行 marker、未解决风险与工作树状态。",
            "确认交付物实际存在于绑定目标，并记录本次完成决定不包含的外部动作。",
            "认可则使用独立 human approver 执行 approve-completion；不认可则保持门禁并明确缺证或修改要求。",
            "刷新并验证 gate_approved、run stage=completed、coordinator=completed 和审计哈希链完整。",
        ),
        decisions=(
            _option("approve_completion", "批准完成并归档 Run", "最终门 gate_approved，run 进入 completed。", "approve_completion"),
            _option("withhold_completion", "暂不批准", "保持 plan_review，记录缺失证据或修改要求。"),
        ),
        evidence_to_review=evidence,
        prohibited_actions=(
            "Agent、policy、coordinator 或 UI 不得代替人类完成决定。",
            "不得把 run 完成自动扩大为 main merge、push、deploy 或 release。",
        ),
    )


def _failure_guidance(
    plan: TaskPlanRevisionRecord,
    run: TaskManagerRunRevisionRecord,
    node: TaskManagerNodeExecution,
) -> TaskManagerHumanActionGuidance:
    return _base(
        plan,
        TaskManagerHumanActionKind.FAILURE_RECOVERY,
        run=run,
        node_id=node.node.node_id,
        title=f"处理失败节点：{node.node.title}",
        summary="coordinator 已停止自动推进；操作者必须先诊断证据，再决定受限重试、修复或终止。",
        permitted_actor_types=(ActorType.HUMAN,),
        required_capability="execute_task",
        requirements=(
            f"从 run revision {run.sequence} 和当前 provider evidence 开始诊断。",
            "检查 attempt/repair/timeout/token/cost 预算仍允许下一动作。",
            "失败原因和拟修复内容必须进入审计记录。",
        ),
        steps=(
            "打开 latest observation、stderr/stdout 和 verification report，定位第一个确定性失败。",
            "区分源码缺陷、依赖/环境问题、权限问题与 provider 暂态故障。",
            "确认是否需要新计划 revision、受治理 repair，或在完全相同输入上重试。",
            "只有预算和策略允许时执行 retry；随后通过 coordinator 重新 observe/verify。",
            "若无法安全继续，保持停顿并通过授权取消路径终止，不删除原始证据。",
        ),
        decisions=(
            _option("retry", "授权重试", "创建下一受限 attempt 并保留原失败证据。", "retry"),
            _option("repair", "进入修复", "先形成可审查修复输入，再重新执行。", "repair"),
            _option("stop", "保持停止", "不产生新副作用，等待进一步决定。"),
        ),
        evidence_to_review=_review_evidence(node),
        prohibited_actions=(
            "不得无限重试或清除失败证据。",
            "不得手工修改状态绕过 WorkflowKernel、预算或验证。",
        ),
    )


def _blocker_guidance(
    plan: TaskPlanRevisionRecord,
    run: TaskManagerRunRevisionRecord,
    node: TaskManagerNodeExecution,
) -> TaskManagerHumanActionGuidance:
    return _base(
        plan,
        TaskManagerHumanActionKind.BLOCKER_RESOLUTION,
        run=run,
        node_id=node.node.node_id,
        title=f"解除阻塞节点：{node.node.title}",
        summary="节点缺少继续执行所需条件；必须补齐依赖、权限或证据后再推进。",
        permitted_actor_types=(ActorType.HUMAN,),
        required_capability="execute_task",
        requirements=(
            "识别阻塞来源并保留可复核事实。",
            "解除条件不得改变 finalized 输入或扩大权限；如需改变，应派生新计划/run。",
        ),
        steps=(
            "检查节点 transition history、依赖状态和最新反馈。",
            "验证所需 attachment、工具链、显示环境、身份和 adapter capability。",
            "补齐可恢复的外部条件并重新运行 preflight。",
            "确认 run revision 未漂移后由授权操作者继续；仍不满足则保持阻塞。",
        ),
        decisions=(
            _option("resume", "条件满足后继续", "从同一权威 run revision 恢复。", "advance"),
            _option("replan", "派生新计划", "保留当前 run，另行处理输入变化。", "derive_revision"),
            _option("wait", "继续等待", "不产生新的执行副作用。"),
        ),
        evidence_to_review=_review_evidence(node),
        prohibited_actions=("不得通过直接改 journal 或 task state 假装解除阻塞。",),
    )


def _node(run: TaskManagerRunRevisionRecord, node_id: str) -> TaskManagerNodeExecution:
    return next(item for item in run.snapshot.nodes if item.node.node_id == node_id)


__all__ = ["project_human_actions"]
