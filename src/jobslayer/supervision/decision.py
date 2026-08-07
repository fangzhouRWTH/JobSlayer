from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from jobslayer.domain.models import DecisionCard, HumanDecision


class DecisionError(ValueError):
    pass


def decision_card_hash(card: DecisionCard) -> str:
    canonical = json.dumps(
        card.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def render_decision_card(card: DecisionCard) -> str:
    risk = card.risk.value
    reversibility = "可逆" if card.reversible else "不可逆或难以恢复"
    lines = [
        f"[{card.decision_kind.value}] {card.title}",
        f"卡片: {card.card_id} | 任务: {card.task_id}",
        f"风险: {risk} | 可逆性: {reversibility}",
        "",
        f"需要决定: {card.decision_required}",
        f"为什么是现在: {card.why_now}",
        "",
        "证据:",
    ]
    for evidence in card.evidence:
        digest = f" | sha256={evidence.sha256}" if evidence.sha256 else ""
        lines.append(
            f"  - {evidence.evidence_id} [{evidence.evidence_type}]: "
            f"{evidence.summary}{digest}"
        )
    lines.extend(("", "选项:"))
    for option in card.options:
        marker = "（推荐/默认）" if option.recommended else ""
        lines.append(f"  - {option.option_id}: {option.label}{marker}")
        lines.append(f"    {option.description}")
        lines.append(f"    后果: {option.consequences}")
    if card.affected_artifact_ids:
        lines.extend(
            (
                "",
                "受影响制品: " + ", ".join(card.affected_artifact_ids),
            )
        )
    lines.extend(("", f"卡片哈希: {decision_card_hash(card)}"))
    return "\n".join(lines)


def create_human_decision(
    card: DecisionCard,
    *,
    actor_id: str,
    selected_option_id: str,
    rationale: str,
) -> HumanDecision:
    available_options = {option.option_id for option in card.options}
    if selected_option_id not in available_options:
        raise DecisionError(f"unknown decision option: {selected_option_id}")
    if not actor_id.strip():
        raise DecisionError("actor id must not be blank")
    if not rationale.strip():
        raise DecisionError("a human rationale is required")
    return HumanDecision(
        decision_id=f"decision-{uuid4().hex}",
        card_id=card.card_id,
        card_sha256=decision_card_hash(card),
        task_id=card.task_id,
        actor_id=actor_id,
        selected_option_id=selected_option_id,
        rationale=rationale,
        evidence_ids=tuple(evidence.evidence_id for evidence in card.evidence),
    )

