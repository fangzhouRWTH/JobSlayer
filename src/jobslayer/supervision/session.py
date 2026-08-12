from __future__ import annotations

import threading
from datetime import UTC, datetime
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
from jobslayer.workflow.journal import AuditJournal
from jobslayer.identity import (
    AuthenticatedPrincipal,
    AuthorizationAction,
    AuthorizationRequest,
    Authorizer,
)


class ReviewSessionError(RuntimeError):
    """Raised when a local review session cannot preserve truthful bindings."""


class DecisionAlreadyRecordedError(ReviewSessionError):
    pass


class StaleDecisionCardError(ReviewSessionError):
    pass


class ReviewAuthorizationError(ReviewSessionError):
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
        actor_id: str | None = None,
        principal: AuthenticatedPrincipal | None = None,
        authorizer: Authorizer | None = None,
        decision_store: DecisionStore,
        journal: AuditJournal | None = None,
    ):
        if (actor_id is None) == (principal is None):
            raise ReviewSessionError(
                "review session requires exactly one declared actor or authenticated principal"
            )
        if actor_id is not None and not actor_id.strip():
            raise ReviewSessionError("actor id must not be blank")
        if principal is not None and authorizer is None:
            raise ReviewSessionError(
                "authenticated review session requires an authorizer"
            )
        self.card = card
        self.principal = principal
        self.authorizer = authorizer
        self.actor_id = (
            principal.subject_id if principal is not None else str(actor_id)
        )
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
        authorization = self._decision_authorization()
        return {
            "schema_version": "1.0",
            "card": self.card.model_dump(mode="json"),
            "card_sha256": decision_card_hash(self.card),
            "actor": self._actor_snapshot(),
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
                "decision_recording": existing is None
                and state_matches is not False
                and (authorization is None or authorization.permitted),
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
            authorization = self._decision_authorization()
            if authorization is not None and not authorization.permitted:
                raise ReviewAuthorizationError(authorization.reason)
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

    def _actor_snapshot(self) -> dict[str, Any]:
        if self.principal is None:
            return {
                "actor_id": self.actor_id,
                "authenticated": False,
                "notice": "本地 actor_id 只是身份声明，尚未经过认证。",
            }
        return {
            "actor_id": self.principal.subject_id,
            "display_name": self.principal.display_name,
            "authenticated": True,
            "authentication_method": self.principal.authentication_method.value,
            "session_id": self.principal.session_id,
            "roles": list(self.principal.roles),
            "valid_until": self.principal.valid_until.isoformat(),
            "notice": "身份已由短期签名会话验证；具体操作仍需 RBAC 授权。",
        }

    def _decision_authorization(self):
        if self.principal is None:
            return None
        assert self.authorizer is not None
        return self.authorizer.authorize(
            AuthorizationRequest(
                principal=self.principal,
                action=AuthorizationAction.RECORD_DECISION,
                task_id=self.card.task_id,
            ),
            now=datetime.now(UTC),
        )

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
