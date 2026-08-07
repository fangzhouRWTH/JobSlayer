import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jobslayer.domain.models import (
    ActorType,
    CheckResult,
    CheckStatus,
    SourceIntegrationResult,
    TaskState,
    VerificationReport,
)
from jobslayer.workflow.journal import AuditIntegrityError, JsonlAuditJournal
from jobslayer.workflow.kernel import (
    AuthorizationError,
    IllegalTransitionError,
    VerificationGateError,
    WorkflowKernel,
)


def report(task_id: str, *, passed: bool = True) -> VerificationReport:
    status = CheckStatus.PASSED if passed else CheckStatus.FAILED
    return VerificationReport(
        report_id=f"report-{task_id}-{'pass' if passed else 'fail'}",
        task_id=task_id,
        source_commit="0123456789abcdef",
        source_patch_sha256=hashlib.sha256(b"patch").hexdigest(),
        checks=(
            CheckResult(
                check_id="tests",
                status=status,
                summary=f"tests {status.value}",
                evidence_hash=hashlib.sha256(status.value.encode()).hexdigest(),
            ),
        ),
        required_checks_passed=passed,
    )


class WorkflowKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "audit.jsonl"
        self.journal = JsonlAuditJournal(self.path)
        self.kernel = WorkflowKernel(self.journal)
        self.task_id = "task-1"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def transition(
        self,
        state: TaskState,
        actor: ActorType = ActorType.SYSTEM,
        verification_report: VerificationReport | None = None,
    ) -> None:
        self.kernel.transition(
            task_id=self.task_id,
            to_state=state,
            actor_type=actor,
            actor_id=f"test-{actor.value}",
            reason=f"move to {state.value}",
            verification_report=verification_report,
        )

    def reach_verifying(self) -> None:
        self.transition(TaskState.PLANNED)
        self.transition(TaskState.IMPLEMENTING, ActorType.POLICY)
        self.transition(TaskState.VERIFYING, ActorType.AGENT)

    def reach_merge_review(self) -> VerificationReport:
        self.reach_verifying()
        passing = report(self.task_id)
        self.transition(TaskState.REVIEWING, verification_report=passing)
        self.transition(TaskState.MERGE_REVIEW, ActorType.AGENT)
        return passing

    def test_happy_path_requires_evidence_and_authorized_completion(self) -> None:
        passing = self.reach_merge_review()
        self.transition(TaskState.INTEGRATING, ActorType.HUMAN, passing)
        integration = SourceIntegrationResult(
            integration_id="integration-task-1",
            task_id=self.task_id,
            workspace_id="workspace-task-1",
            repository_root="/fixture",
            source_ref="jobslayer/workspace-task-1",
            target_ref="main",
            base_commit="0" * 40,
            commit="1" * 40,
            target_previous_commit="0" * 40,
            target_commit="1" * 40,
            source_patch_sha256=passing.source_patch_sha256,
            changed_paths=("src/value.py",),
            approved_by="test-human",
        )
        self.kernel.transition(
            task_id=self.task_id,
            to_state=TaskState.COMPLETED,
            actor_type=ActorType.HUMAN,
            actor_id="test-human",
            reason="verified patch was integrated",
            verification_report=passing,
            integration_result=integration,
        )

        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.COMPLETED)
        history = self.kernel.history(self.task_id)
        self.assertEqual(len(history), 7)
        self.assertIn(passing.report_id, history[-1].evidence_ids)
        self.assertIn(integration.integration_id, history[-1].evidence_ids)
        self.assertEqual(len(self.journal.read_all()), 7)

    def test_completion_rejects_missing_integration_evidence(self) -> None:
        passing = self.reach_merge_review()
        self.transition(TaskState.INTEGRATING, ActorType.HUMAN, passing)

        with self.assertRaises(VerificationGateError):
            self.transition(TaskState.COMPLETED, ActorType.HUMAN, passing)

        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.INTEGRATING)

    def test_illegal_transition_is_not_written(self) -> None:
        with self.assertRaises(IllegalTransitionError):
            self.transition(TaskState.COMPLETED, ActorType.HUMAN)
        self.assertEqual(self.journal.read_all(), [])

    def test_agent_cannot_enter_implementation_without_approval(self) -> None:
        self.transition(TaskState.PLANNED)
        with self.assertRaises(AuthorizationError):
            self.transition(TaskState.IMPLEMENTING, ActorType.AGENT)
        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.PLANNED)

    def test_agent_cannot_complete(self) -> None:
        passing = self.reach_merge_review()
        self.transition(TaskState.INTEGRATING, ActorType.HUMAN, passing)
        with self.assertRaises(AuthorizationError):
            self.transition(TaskState.COMPLETED, ActorType.AGENT, passing)
        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.INTEGRATING)

    def test_human_can_return_merge_review_to_repair(self) -> None:
        self.reach_merge_review()
        self.transition(TaskState.REPAIRING, ActorType.HUMAN)
        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.REPAIRING)

    def test_agent_cannot_return_merge_review_to_repair(self) -> None:
        self.reach_merge_review()
        with self.assertRaises(AuthorizationError):
            self.transition(TaskState.REPAIRING, ActorType.AGENT)
        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.MERGE_REVIEW)

    def test_passing_report_is_required_for_review(self) -> None:
        self.reach_verifying()
        with self.assertRaises(VerificationGateError):
            self.transition(TaskState.REVIEWING)
        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.VERIFYING)

    def test_failed_report_routes_to_repair(self) -> None:
        self.reach_verifying()
        failing = report(self.task_id, passed=False)
        self.transition(TaskState.REPAIRING, verification_report=failing)
        self.assertEqual(self.kernel.current_state(self.task_id), TaskState.REPAIRING)
        self.assertIn(failing.report_id, self.kernel.history(self.task_id)[-1].evidence_ids)

    def test_tampering_breaks_the_hash_chain(self) -> None:
        self.transition(TaskState.PLANNED)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        raw = json.loads(lines[0])
        raw["reason"] = "silently rewritten"
        self.path.write_text(json.dumps(raw) + "\n", encoding="utf-8")

        with self.assertRaises(AuditIntegrityError):
            self.journal.read_all()


if __name__ == "__main__":
    unittest.main()
