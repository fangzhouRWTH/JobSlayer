from __future__ import annotations

import threading
from typing import Any

from jobslayer.domain.models import (
    DecisionCard,
    DecisionKind,
    HumanDecision,
    TaskState,
)
from jobslayer.supervision.decision import (
    DecisionError,
    create_human_decision,
    decision_card_hash,
)
from jobslayer.supervision.records import DecisionStore
from jobslayer.workflow.journal import JsonlAuditJournal


class ReviewSessionError(RuntimeError):
    """Raised when a local review session cannot preserve truthful bindings."""


class DecisionAlreadyRecordedError(ReviewSessionError):
    pass


class StaleDecisionCardError(ReviewSessionError):
    pass


_EXPECTED_CARD_STATES = {
    DecisionKind.PLAN_REVIEW: TaskState.PLAN_REVIEW,
    DecisionKind.MERGE_REVIEW: TaskState.MERGE_REVIEW,
}


class ReviewSession:
    """Application state exposed by both CLI and visual review adapters."""

    def __init__(
        self,
        *,
        card: DecisionCard,
        actor_id: str,
        decision_store: DecisionStore,
        journal: JsonlAuditJournal | None = None,
    ):
        if not actor_id.strip():
            raise ReviewSessionError("actor id must not be blank")
        self.card = card
        self.actor_id = actor_id
        self.decision_store = decision_store
        self.journal = journal
        self._lock = threading.Lock()
        existing = self.decision_store.load()
        if existing is not None:
            self._validate_existing_decision(existing)

    def snapshot(self) -> dict[str, Any]:
        existing = self.decision_store.load()
        if existing is not None:
            self._validate_existing_decision(existing)
        history = () if self.journal is None else tuple(
            self.journal.records_for(self.card.task_id)
        )
        state = history[-1].to_state if history else TaskState.DRAFT
        expected_state = _EXPECTED_CARD_STATES.get(self.card.decision_kind)
        state_matches = (
            None
            if self.journal is None or expected_state is None
            else state is expected_state
        )
        return {
            "schema_version": "1.0",
            "card": self.card.model_dump(mode="json"),
            "card_sha256": decision_card_hash(self.card),
            "actor": {
                "actor_id": self.actor_id,
                "authenticated": False,
                "notice": "本地 actor_id 只是身份声明，尚未经过认证。",
            },
            "workflow": {
                "journal_configured": self.journal is not None,
                "current_state": state.value if self.journal is not None else None,
                "expected_state": (
                    expected_state.value if expected_state is not None else None
                ),
                "card_state_matches": state_matches,
                "transitions": [
                    record.model_dump(mode="json") for record in history
                ],
            },
            "decision": (
                existing.model_dump(mode="json") if existing is not None else None
            ),
            "capabilities": {
                "decision_recording": existing is None and state_matches is not False,
                "decision_application": False,
                "git_merge": False,
                "deployment": False,
            },
        }

    def submit(
        self, *, selected_option_id: str, rationale: str
    ) -> HumanDecision:
        with self._lock:
            existing = self.decision_store.load()
            if existing is not None:
                self._validate_existing_decision(existing)
                raise DecisionAlreadyRecordedError(
                    "a decision has already been recorded for this session"
                )
            self._require_current_card_state()
            try:
                decision = create_human_decision(
                    self.card,
                    actor_id=self.actor_id,
                    selected_option_id=selected_option_id,
                    rationale=rationale,
                )
                self.decision_store.create(decision)
            except DecisionError:
                raise
            return decision

    def _require_current_card_state(self) -> None:
        expected_state = _EXPECTED_CARD_STATES.get(self.card.decision_kind)
        if self.journal is None or expected_state is None:
            return
        history = self.journal.records_for(self.card.task_id)
        state = history[-1].to_state if history else TaskState.DRAFT
        if state is not expected_state:
            raise StaleDecisionCardError(
                f"decision card expects {expected_state.value}, task is {state.value}"
            )

    def _validate_existing_decision(self, decision: HumanDecision) -> None:
        if (
            decision.card_id != self.card.card_id
            or decision.task_id != self.card.task_id
            or decision.card_sha256 != decision_card_hash(self.card)
            or decision.actor_id != self.actor_id
        ):
            raise ReviewSessionError(
                "existing decision record does not belong to this card and actor"
            )
        expected_evidence = tuple(
            evidence.evidence_id for evidence in self.card.evidence
        )
        available_options = {option.option_id for option in self.card.options}
        if decision.selected_option_id not in available_options:
            raise ReviewSessionError(
                "existing decision selected an option not present on this card"
            )
        if decision.evidence_ids != expected_evidence:
            raise ReviewSessionError(
                "existing decision record references different evidence"
            )
