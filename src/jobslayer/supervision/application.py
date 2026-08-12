from __future__ import annotations

from datetime import UTC, datetime

from jobslayer.domain.models import (
    ActorType,
    ApprovalAuthority,
    DecisionCard,
    DecisionKind,
    HumanDecision,
    TaskState,
    TransitionRecord,
    VerificationReport,
)
from jobslayer.supervision.decision import decision_card_hash
from jobslayer.workflow.kernel import WorkflowKernel


class DecisionApplicationError(RuntimeError):
    """Raised when a decision cannot authorize a workflow transition."""


_EXPECTED_STATES = {
    DecisionKind.PLAN_REVIEW: TaskState.PLAN_REVIEW,
    DecisionKind.MERGE_REVIEW: TaskState.MERGE_REVIEW,
}

_TRANSITIONS = {
    DecisionKind.PLAN_REVIEW: {
        "approve": TaskState.IMPLEMENTING,
        "request_changes": TaskState.PLANNED,
        "reject": TaskState.CANCELLED,
    },
    DecisionKind.MERGE_REVIEW: {
        "approve": TaskState.INTEGRATING,
        "request_changes": TaskState.REPAIRING,
        "reject": TaskState.CANCELLED,
    },
}


class DecisionApplicationService:
    """Validate an authorized human decision before invoking the workflow kernel."""

    def __init__(self, kernel: WorkflowKernel):
        self.kernel = kernel

    def apply(
        self,
        *,
        card: DecisionCard,
        decision: HumanDecision,
        authority: ApprovalAuthority,
        verification_report: VerificationReport | None = None,
        additional_evidence_ids: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> TransitionRecord:
        now = now or datetime.now(UTC)
        self._validate_bindings(card, decision)
        self._validate_authority(card, decision, authority, now)

        expected_state = _EXPECTED_STATES.get(card.decision_kind)
        if expected_state is None:
            raise DecisionApplicationError(
                f"decision kind is not yet applicable to task state: {card.decision_kind.value}"
            )
        current_state = self.kernel.current_state(card.task_id)
        if current_state is not expected_state:
            raise DecisionApplicationError(
                f"decision expects {expected_state.value}, task is {current_state.value}"
            )

        option_transitions = _TRANSITIONS[card.decision_kind]
        try:
            to_state = option_transitions[decision.selected_option_id]
        except KeyError as exc:
            raise DecisionApplicationError(
                "selected option has no approved workflow meaning for this decision kind"
            ) from exc

        if to_state is TaskState.INTEGRATING:
            if verification_report is None:
                raise DecisionApplicationError(
                    "merge approval requires the passing verification report"
                )
            if verification_report.report_id not in decision.evidence_ids:
                raise DecisionApplicationError(
                    "verification report was not part of the reviewed evidence"
                )

        evidence_ids = tuple(
            dict.fromkeys(
                (
                    decision.decision_id,
                    card.card_id,
                    authority.authorization_id,
                    *decision.evidence_ids,
                    *additional_evidence_ids,
                )
            )
        )
        return self.kernel.transition(
            task_id=card.task_id,
            to_state=to_state,
            actor_type=ActorType.HUMAN,
            actor_id=decision.actor_id,
            reason=f"human decision {decision.decision_id}: {decision.rationale}",
            verification_report=verification_report,
            evidence_ids=evidence_ids,
        )

    def validate_applied_transition(
        self,
        *,
        card: DecisionCard,
        decision: HumanDecision,
        authority: ApprovalAuthority,
        transition: TransitionRecord,
        verification_report: VerificationReport | None = None,
        required_evidence_ids: tuple[str, ...] = (),
    ) -> None:
        """Verify a persisted decision transition without applying it again."""

        self._validate_bindings(card, decision)
        self._validate_authority(
            card,
            decision,
            authority,
            transition.occurred_at,
        )
        expected_state = _EXPECTED_STATES.get(card.decision_kind)
        if expected_state is None:
            raise DecisionApplicationError(
                f"decision kind is not applicable: {card.decision_kind.value}"
            )
        try:
            expected_target = _TRANSITIONS[card.decision_kind][
                decision.selected_option_id
            ]
        except KeyError as exc:
            raise DecisionApplicationError(
                "selected option has no approved workflow meaning"
            ) from exc
        if expected_target is TaskState.INTEGRATING:
            if verification_report is None:
                raise DecisionApplicationError(
                    "merge approval requires the passing verification report"
                )
            if verification_report.report_id not in decision.evidence_ids:
                raise DecisionApplicationError(
                    "verification report was not part of the reviewed evidence"
                )
        expected_evidence = {
            decision.decision_id,
            card.card_id,
            authority.authorization_id,
            *decision.evidence_ids,
            *required_evidence_ids,
        }
        if verification_report is not None and expected_target is TaskState.INTEGRATING:
            expected_evidence.add(verification_report.report_id)
        if (
            transition.task_id != card.task_id
            or transition.from_state is not expected_state
            or transition.to_state is not expected_target
            or transition.actor_type is not ActorType.HUMAN
            or transition.actor_id != decision.actor_id
            or not expected_evidence.issubset(transition.evidence_ids)
        ):
            raise DecisionApplicationError(
                "persisted transition does not match the authorized decision"
            )

    @staticmethod
    def _validate_bindings(card: DecisionCard, decision: HumanDecision) -> None:
        if decision.card_id != card.card_id or decision.task_id != card.task_id:
            raise DecisionApplicationError("decision does not belong to this card and task")
        if decision.card_sha256 != decision_card_hash(card):
            raise DecisionApplicationError("decision card hash does not match")
        options = {option.option_id for option in card.options}
        if decision.selected_option_id not in options:
            raise DecisionApplicationError("decision selected an option not shown on the card")
        expected_evidence = tuple(item.evidence_id for item in card.evidence)
        if decision.evidence_ids != expected_evidence:
            raise DecisionApplicationError("decision evidence differs from the reviewed card")

    @staticmethod
    def _validate_authority(
        card: DecisionCard,
        decision: HumanDecision,
        authority: ApprovalAuthority,
        now: datetime,
    ) -> None:
        if now.tzinfo is None:
            raise DecisionApplicationError("decision application time must include a timezone")
        if authority.actor_id != decision.actor_id:
            raise DecisionApplicationError("authority belongs to a different actor")
        if card.decision_kind not in authority.allowed_decision_kinds:
            raise DecisionApplicationError("actor is not authorized for this decision kind")
        if now < authority.issued_at or now >= authority.valid_until:
            raise DecisionApplicationError("approval authority is not currently valid")
