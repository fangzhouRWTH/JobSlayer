"""Deterministic offline assistant for TaskManager human-action handoffs."""

from __future__ import annotations

from jobslayer.task_manager.guidance import (
    TaskManagerHumanActionAssistantReply,
    TaskManagerHumanActionGuidance,
    TaskManagerHumanInteraction,
)


class LocalHumanActionAssistant:
    """Explain the structured guidance without making or applying a decision."""

    adapter_id = "local-human-action-assistant-v1"

    def assist(
        self,
        *,
        task_id: str,
        run_id: str,
        guidance: TaskManagerHumanActionGuidance,
        interactions: tuple[TaskManagerHumanInteraction, ...],
        user_message: str,
    ) -> TaskManagerHumanActionAssistantReply:
        del task_id, run_id, interactions
        message = " ".join(user_message.split()).strip()
        if not message:
            raise ValueError("human-action assistant message must not be blank")
        evidence = "、".join(guidance.evidence_to_review) or "当前指导未列出独立 evidence ID"
        decisions = "；".join(
            f"{item.label}：{item.effect}" for item in guidance.decisions
        )
        return TaskManagerHumanActionAssistantReply(
            adapter_id=self.adapter_id,
            content=(
                f"我只能协助理解和起草意见，不能替你作出或提交“{guidance.title}”的决定。\n\n"
                f"你提出：{message}\n\n"
                f"请先按顺序完成 {len(guidance.steps)} 个步骤，并核对这些证据：{evidence}。\n"
                f"当前允许的决定是：{decisions}\n"
                "如需暂不批准，请在反馈中明确写出缺失证据、期望修改和再次验收条件；"
                "如需批准，仍必须由授权人类在正式确认区勾选证据并点击结构化按钮。"
            ),
        )


__all__ = ["LocalHumanActionAssistant"]
