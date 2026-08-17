"""Deterministic planning-agent fixture behind the provider-neutral proposal port."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from jobslayer.orchestration import (
    TaskPlanEdge,
    TaskPlanEdgeRelation,
    TaskPlanMessage,
    TaskPlanNode,
    TaskPlanNodeKind,
    TaskPlanProposal,
)


class LocalPlanningAgent:
    """Produce bounded graph proposals without owning or persisting plan state."""

    adapter_id = "local-planning-fixture-v1"

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
        del plan_id, conversation
        message = " ".join(user_message.split()).strip()
        if not message:
            raise ValueError("planning discussion message must not be blank")
        if not nodes:
            next_nodes, next_edges = self._initial_graph(task_description)
            summary = (
                "我先把目标拆成范围确认、方案设计、实施、确定性验证和人工定稿五个阶段。"
                "这些只是待应用提案，继续讨论会在同一草案拓扑上细化。"
            )
        else:
            next_nodes = list(nodes)
            next_edges = list(edges)
            selected = next(
                (node for node in next_nodes if node.node_id == selected_node_id),
                next_nodes[-1],
            )
            if selected_node_id and selected.node_id != selected_node_id:
                raise ValueError("selected task-plan node does not exist")
            if "删除" in message and selected_node_id:
                next_nodes = [node for node in next_nodes if node.node_id != selected.node_id]
                next_edges = [
                    edge
                    for edge in next_edges
                    if selected.node_id
                    not in {edge.source_node_id, edge.target_node_id}
                ]
                summary = f"建议从草案移除“{selected.title}”及其关联边；其他节点保持不变。"
            elif selected_node_id and any(
                keyword in message for keyword in ("修改", "调整", "改成", "改为")
            ):
                replacement = TaskPlanNode(
                    node_id=selected.node_id,
                    title=self._title(message),
                    description=message,
                    kind=selected.kind,
                    executor_hint=selected.executor_hint,
                    attributes=selected.attributes,
                )
                next_nodes = [
                    replacement if node.node_id == selected.node_id else node
                    for node in next_nodes
                ]
                summary = f"建议把节点“{selected.title}”更新为“{replacement.title}”。"
            else:
                relation = self._relation(message)
                node_id = self._next_node_id(tuple(next_nodes))
                addition = TaskPlanNode(
                    node_id=node_id,
                    title=self._title(message),
                    description=message,
                    kind=(
                        TaskPlanNodeKind.VALIDATION
                        if "验证" in message or "测试" in message
                        else TaskPlanNodeKind.TASK
                    ),
                    executor_hint="unassigned",
                    attributes={"proposal_source": self.adapter_id},
                )
                next_nodes.append(addition)
                next_edges.append(
                    TaskPlanEdge(
                        edge_id=self._next_edge_id(tuple(next_edges)),
                        source_node_id=selected.node_id,
                        target_node_id=addition.node_id,
                        relation=relation,
                    )
                )
                relation_label = {
                    TaskPlanEdgeRelation.BRANCH: "支线",
                    TaskPlanEdgeRelation.SUBTASK: "子任务",
                    TaskPlanEdgeRelation.DEPENDENCY: "依赖步骤",
                    TaskPlanEdgeRelation.SEQUENCE: "后续步骤",
                }[relation]
                summary = (
                    f"建议在“{selected.title}”之后增加{relation_label}“{addition.title}”。"
                    "提案尚未写入已应用计划。"
                )
        return TaskPlanProposal(
            proposal_id=f"proposal-{uuid4().hex}",
            based_on_revision=based_on_revision,
            summary=summary,
            agent_adapter=self.adapter_id,
            nodes=tuple(next_nodes),
            edges=tuple(next_edges),
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _initial_graph(
        task_description: str,
    ) -> tuple[tuple[TaskPlanNode, ...], tuple[TaskPlanEdge, ...]]:
        description = " ".join(task_description.split()).strip()
        phases = (
            ("scope", "确认目标与边界", TaskPlanNodeKind.MILESTONE, "human + planner"),
            ("design", "形成可执行方案", TaskPlanNodeKind.TASK, "planning agent"),
            ("implement", "分步实施任务", TaskPlanNodeKind.TASK, "unassigned"),
            ("verify", "执行确定性验证", TaskPlanNodeKind.VALIDATION, "verification engine"),
            ("finalize", "用户确认最终路径", TaskPlanNodeKind.HUMAN_GATE, "authorized planner"),
        )
        nodes = tuple(
            TaskPlanNode(
                node_id=node_id,
                title=title,
                description=(
                    f"围绕“{description}”完成{title}。"
                    if node_id != "scope"
                    else f"澄清“{description}”的范围、约束、验收条件与未决问题。"
                ),
                kind=kind,
                executor_hint=executor,
                attributes={"proposal_source": LocalPlanningAgent.adapter_id},
            )
            for node_id, title, kind, executor in phases
        )
        edges = tuple(
            TaskPlanEdge(
                edge_id=f"edge-{index}",
                source_node_id=phases[index - 1][0],
                target_node_id=phases[index][0],
                relation=TaskPlanEdgeRelation.SEQUENCE,
            )
            for index in range(1, len(phases))
        )
        return nodes, edges

    @staticmethod
    def _title(message: str) -> str:
        cleaned = message.strip("。.!！?？ ")
        return cleaned[:72] or "待细化任务"

    @staticmethod
    def _relation(message: str) -> TaskPlanEdgeRelation:
        if "支线" in message or "分支" in message or "并行" in message:
            return TaskPlanEdgeRelation.BRANCH
        if "子任务" in message or "拆分" in message or "拆成" in message:
            return TaskPlanEdgeRelation.SUBTASK
        if "依赖" in message or "前置" in message:
            return TaskPlanEdgeRelation.DEPENDENCY
        return TaskPlanEdgeRelation.SEQUENCE

    @staticmethod
    def _next_node_id(nodes: tuple[TaskPlanNode, ...]) -> str:
        known = {node.node_id for node in nodes}
        index = len(nodes) + 1
        while f"step-{index}" in known:
            index += 1
        return f"step-{index}"

    @staticmethod
    def _next_edge_id(edges: tuple[TaskPlanEdge, ...]) -> str:
        known = {edge.edge_id for edge in edges}
        index = len(edges) + 1
        while f"edge-{index}" in known:
            index += 1
        return f"edge-{index}"


__all__ = ["LocalPlanningAgent"]
