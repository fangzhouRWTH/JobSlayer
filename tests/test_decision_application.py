import hashlib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobslayer.domain.models import (
    ActorType,
    ApprovalAuthority,
    CheckResult,
    CheckStatus,
    DecisionCard,
    DecisionKind,
    DecisionOption,
    EvidenceSummary,
    RiskLevel,
    TaskState,
    VerificationReport,
)
from jobslayer.supervision.application import (
    DecisionApplicationError,
    DecisionApplicationService,
)
from jobslayer.supervision.decision import create_human_decision
from jobslayer.workflow.journal import JsonlAuditJournal
from jobslayer.workflow.kernel import WorkflowKernel


class DecisionApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        journal = JsonlAuditJournal(
            Path(self.temporary_directory.name) / "audit.jsonl"
        )
        self.kernel = WorkflowKernel(journal)
        self.service = DecisionApplicationService(self.kernel)
        self.task_id = "task-decision"
        self.now = datetime(2026, 8, 7, 12, tzinfo=UTC)
        self.report = VerificationReport(
            report_id="verification-decision-1",
            task_id=self.task_id,
            source_commit="0123456789abcdef",
            checks=(
                CheckResult(
                    check_id="tests",
                    status=CheckStatus.PASSED,
                    summary="all required checks passed",
                    evidence_hash=hashlib.sha256(b"passed").hexdigest(),
                ),
            ),
            required_checks_passed=True,
        )
        self._reach_merge_review()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _transition(
        self,
        state: TaskState,
        actor: ActorType,
        report: VerificationReport | None = None,
    ) -> None:
        self.kernel.transition(
            task_id=self.task_id,
            to_state=state,
            actor_type=actor,
            actor_id=f"test-{actor.value}",
            reason=f"move to {state.value}",
            verification_report=report,
        )

    def _reach_merge_review(self) -> None:
        self._transition(TaskState.PLANNED, ActorType.SYSTEM)
        self._transition(TaskState.IMPLEMENTING, ActorType.POLICY)
        self._transition(TaskState.VERIFYING, ActorType.AGENT)
        self._transition(TaskState.REVIEWING, ActorType.SYSTEM, self.report)
        self._transition(TaskState.MERGE_REVIEW, ActorType.AGENT)

    def card(self) -> DecisionCard:
        return DecisionCard(
            card_id="card-decision-1",
            task_id=self.task_id,
            decision_kind=DecisionKind.MERGE_REVIEW,
            title="Review patch",
            decision_required="Approve, revise, or reject the patch",
            why_now="Verification and independent review are complete",
            risk=RiskLevel.MEDIUM,
            reversible=True,
            evidence=(
                EvidenceSummary(
                    evidence_id=self.report.report_id,
                    evidence_type="verification_report",
                    summary="required checks passed",
                ),
            ),
            options=(
                DecisionOption(
                    option_id="approve",
                    label="Approve",
                    description="Accept the merge proposal",
                    consequences="The controller may complete the task",
                    recommended=True,
                ),
                DecisionOption(
                    option_id="request_changes",
                    label="Request changes",
                    description="Return the task for repair",
                    consequences="A new verification round is required",
                ),
                DecisionOption(
                    option_id="reject",
                    label="Reject",
                    description="Cancel this task",
                    consequences="The patch is not accepted",
                ),
            ),
            default_option_id="approve",
        )

    def authority(self, *, expired: bool = False) -> ApprovalAuthority:
        if expired:
            issued_at = self.now - timedelta(hours=2)
            valid_until = self.now - timedelta(hours=1)
        else:
            issued_at = self.now - timedelta(minutes=5)
            valid_until = self.now + timedelta(minutes=5)
        return ApprovalAuthority(
            authorization_id="authority-merge-review",
            actor_id="human-reviewer",
            allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
            issued_at=issued_at,
            valid_until=valid_until,
        )

    def decision(self, card: DecisionCard, option: str):
        return create_human_decision(
            card,
            actor_id="human-reviewer",
            selected_option_id=option,
            rationale=f"selected {option} after reviewing evidence",
        )

    def test_authorized_approval_enters_integration_through_the_kernel(self) -> None:
        card = self.card()

        record = self.service.apply(
            card=card,
            decision=self.decision(card, "approve"),
            authority=self.authority(),
            verification_report=self.report,
            now=self.now,
        )

        self.assertEqual(record.to_state, TaskState.INTEGRATING)
        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.INTEGRATING)
        self.assertIn("authority-merge-review", record.evidence_ids)
        self.assertIn(self.report.report_id, record.evidence_ids)

    def test_request_changes_returns_the_task_to_repair(self) -> None:
        card = self.card()

        self.service.apply(
            card=card,
            decision=self.decision(card, "request_changes"),
            authority=self.authority(),
            now=self.now,
        )

        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.REPAIRING)

    def test_rejects_a_decision_if_the_card_changed_after_review(self) -> None:
        original = self.card()
        decision = self.decision(original, "approve")
        changed = original.model_copy(update={"title": "Changed after review"})

        with self.assertRaises(DecisionApplicationError):
            self.service.apply(
                card=changed,
                decision=decision,
                authority=self.authority(),
                verification_report=self.report,
                now=self.now,
            )

        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.MERGE_REVIEW)

    def test_rejects_expired_authority_without_changing_state(self) -> None:
        card = self.card()

        with self.assertRaises(DecisionApplicationError):
            self.service.apply(
                card=card,
                decision=self.decision(card, "approve"),
                authority=self.authority(expired=True),
                verification_report=self.report,
                now=self.now,
            )

        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.MERGE_REVIEW)

    def test_merge_approval_requires_the_reviewed_verification_report(self) -> None:
        card = self.card()

        with self.assertRaises(DecisionApplicationError):
            self.service.apply(
                card=card,
                decision=self.decision(card, "approve"),
                authority=self.authority(),
                now=self.now,
            )

        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.MERGE_REVIEW)


if __name__ == "__main__":
    unittest.main()
