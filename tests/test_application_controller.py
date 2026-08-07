import hashlib
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.local_command import GovernedLocalCommandRunner
from jobslayer.application.controller import (
    ExecutionAuthorizationError,
    TaskExecutionController,
)
from jobslayer.domain.models import (
    ActorType,
    AgentCancellationResult,
    AgentInvocation,
    AgentRunHandle,
    AgentRunResult,
    AgentRunSpec,
    AgentRunStatus,
    ApprovalAuthority,
    CommandPolicy,
    CommandRule,
    DecisionKind,
    ReviewDispositionStatus,
    ReviewReport,
    ReviewStatus,
    RiskLevel,
    TaskExecutionAuthorization,
    TaskExecutionStatus,
    TaskSpec,
    TaskState,
    ValidationCheckSpec,
    ValidationProfile,
    WorkspaceManifest,
)
from jobslayer.supervision.application import DecisionApplicationService
from jobslayer.supervision.decision import create_human_decision
from jobslayer.verification.engine import VerificationEngine
from jobslayer.workflow.journal import JsonlAuditJournal
from jobslayer.workflow.kernel import WorkflowKernel


class _EditingAgentExecutor:
    def __init__(
        self,
        log_root: Path,
        *,
        value: str = "changed\n",
        status: AgentRunStatus = AgentRunStatus.COMPLETED,
    ):
        self.log_root = log_root
        self.value = value
        self.status = status
        self.results: dict[str, AgentRunResult] = {}

    def start(
        self, invocation: AgentInvocation, workspace: WorkspaceManifest
    ) -> AgentRunHandle:
        spec = invocation.run_spec
        started_at = datetime.now(UTC)
        external_id = f"fake-{spec.run_id}"
        run_directory = self.log_root / spec.run_id
        run_directory.mkdir(parents=True)
        raw_log = run_directory / "events.jsonl"
        stderr_log = run_directory / "stderr.log"
        raw_log.write_text('{"type":"turn.completed"}\n', encoding="utf-8")
        stderr_log.write_text("", encoding="utf-8")
        if self.status is AgentRunStatus.COMPLETED:
            (Path(workspace.path) / "src" / "value.txt").write_text(
                self.value, encoding="utf-8"
            )
        finished_at = datetime.now(UTC)
        self.results[spec.run_id] = AgentRunResult(
            run_id=spec.run_id,
            external_id=external_id,
            executor_type=spec.executor_type,
            workspace_id=workspace.workspace_id,
            status=self.status,
            exit_code=0 if self.status is AgentRunStatus.COMPLETED else 1,
            event_count=1,
            final_message="fixture terminal result",
            raw_event_log_path=str(raw_log),
            raw_event_log_sha256=hashlib.sha256(raw_log.read_bytes()).hexdigest(),
            stderr_log_path=str(stderr_log),
            stderr_log_sha256=hashlib.sha256(stderr_log.read_bytes()).hexdigest(),
            started_at=started_at,
            finished_at=finished_at,
            error_summary=(
                None
                if self.status is AgentRunStatus.COMPLETED
                else "fixture agent failed"
            ),
        )
        return AgentRunHandle(
            run_id=spec.run_id,
            external_id=external_id,
            executor_type=spec.executor_type,
            workspace_id=workspace.workspace_id,
            started_at=started_at,
        )

    def events(self, run_id: str, *, after_sequence: int = 0):
        return ()

    def cancel(self, run_id: str) -> AgentCancellationResult:
        return AgentCancellationResult(
            run_id=run_id,
            cancellation_requested=False,
            already_terminal=True,
            status=self.results[run_id].status,
        )

    def collect(self, run_id: str) -> AgentRunResult:
        return self.results[run_id]


class TaskExecutionControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "source"
        self.repository.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "JobSlayer Test")
        self._git("config", "user.email", "jobslayer@example.invalid")
        (self.repository / "src").mkdir()
        (self.repository / "src" / "value.txt").write_text(
            "baseline\n", encoding="utf-8"
        )
        (self.repository / "verify.py").write_text(
            "from pathlib import Path\n"
            "value = Path('src/value.txt').read_text()\n"
            "if value != 'changed\\n':\n"
            "    raise SystemExit(9)\n"
            "print('verified')\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.base_commit = self._git("rev-parse", "HEAD").strip()
        self.now = datetime(2026, 8, 7, 12, tzinfo=UTC)
        self.manager = GitWorktreeManager(
            self.repository, self.root / "worktrees"
        )
        self.registry = LocalArtifactRegistry(self.root / "artifacts")
        self.kernel = WorkflowKernel(JsonlAuditJournal(self.root / "audit.jsonl"))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def task(self, *, risk: RiskLevel = RiskLevel.LOW) -> TaskSpec:
        return TaskSpec(
            task_id="task-controller",
            project_id="fixture",
            title="Close the governed loop",
            objective="Produce and validate one deterministic change",
            repository=str(self.repository),
            base_commit=self.base_commit,
            allowed_paths=("src/",),
            required_capabilities=("file_change",),
            acceptance_criteria=("fixture verification passes",),
            validation_profile="fixture-v1",
            risk=risk,
        )

    def invocation(self) -> AgentInvocation:
        return AgentInvocation(
            run_spec=AgentRunSpec(
                run_id="run-controller",
                task_id="task-controller",
                executor_type="fake",
                model_profile="fixture",
                context_package_id="context-fixture",
                workspace_id="workspace-controller",
                permission_profile="workspace_write",
                timeout_seconds=2,
                output_schema="none",
            ),
            prompt="Change the fixture value exactly as requested.",
        )

    def profile(self) -> ValidationProfile:
        argv = (sys.executable, "verify.py")
        return ValidationProfile(
            profile_id="fixture-v1",
            command_policy=CommandPolicy(
                policy_id="fixture-policy",
                rules=(
                    CommandRule(
                        rule_id="verify",
                        argv_prefix=argv,
                        max_timeout_seconds=2,
                    ),
                ),
                max_timeout_seconds=2,
            ),
            checks=(
                ValidationCheckSpec(
                    check_id="unit",
                    title="Fixture verification",
                    argv=argv,
                    timeout_seconds=1,
                ),
            ),
        )

    def authorization(
        self, *, maximum_risk: RiskLevel = RiskLevel.LOW
    ) -> TaskExecutionAuthorization:
        return TaskExecutionAuthorization(
            authorization_id="execution-auth-1",
            task_id="task-controller",
            actor_type=ActorType.POLICY,
            actor_id="test-policy",
            maximum_risk=maximum_risk,
            issued_at=self.now - timedelta(minutes=5),
            valid_until=self.now + timedelta(minutes=5),
        )

    def controller(self, executor: _EditingAgentExecutor) -> TaskExecutionController:
        runner = GovernedLocalCommandRunner(self.manager)
        return TaskExecutionController(
            kernel=self.kernel,
            workspace_manager=self.manager,
            agent_executor=executor,
            verification_engine=VerificationEngine(runner, self.registry),
            artifact_registry=self.registry,
            poll_interval_seconds=0.001,
            collection_grace_seconds=0.1,
        )

    def execute(self, executor: _EditingAgentExecutor):
        return self.controller(executor).execute_implementation(
            task=self.task(),
            invocation=self.invocation(),
            validation_profile=self.profile(),
            authorization=self.authorization(),
            now=self.now,
        )

    def test_fake_agent_to_authorized_integration_is_fully_audited(self) -> None:
        controller = self.controller(_EditingAgentExecutor(self.root / "agent-logs"))
        task = self.task()
        outcome = controller.execute_implementation(
            task=task,
            invocation=self.invocation(),
            validation_profile=self.profile(),
            authorization=self.authorization(),
            now=self.now,
        )

        self.assertEqual(outcome.status, TaskExecutionStatus.AWAITING_REVIEW)
        self.assertEqual(self.kernel.current_state(task.task_id), TaskState.REVIEWING)
        self.assertTrue(outcome.verification_report.passes_gate)
        self.assertTrue(self.registry.verify(outcome.patch_artifact))
        review = ReviewReport(
            review_id="review-1",
            task_id=task.task_id,
            reviewer_actor_type=ActorType.AGENT,
            reviewer_id="review-agent",
            patch_sha256=outcome.patch.sha256,
            status=ReviewStatus.ACCEPTED,
            summary="Patch is scoped and matches the task acceptance criteria.",
            evidence_ids=(outcome.verification_report.report_id,),
        )
        disposition = controller.prepare_merge_review(
            task=task,
            outcome=outcome,
            review_report=review,
        )

        self.assertEqual(
            disposition.status, ReviewDispositionStatus.AWAITING_MERGE_DECISION
        )
        package = disposition.merge_review_package
        self.assertIsNotNone(package)
        decision = create_human_decision(
            package.decision_card,
            actor_id="human-reviewer",
            selected_option_id="approve",
            rationale="Evidence is complete and the fixture result is correct.",
        )
        authority = ApprovalAuthority(
            authorization_id="merge-auth-1",
            actor_id="human-reviewer",
            allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
            issued_at=self.now - timedelta(minutes=5),
            valid_until=self.now + timedelta(minutes=5),
        )
        DecisionApplicationService(self.kernel).apply(
            card=package.decision_card,
            decision=decision,
            authority=authority,
            verification_report=package.verification_report,
            now=self.now,
        )

        self.assertEqual(self.kernel.current_state(task.task_id), TaskState.INTEGRATING)
        self.assertTrue(self.kernel.journal.read_all())

    def test_failing_validation_routes_to_repair(self) -> None:
        outcome = self.execute(
            _EditingAgentExecutor(self.root / "agent-logs", value="incorrect\n")
        )

        self.assertEqual(outcome.status, TaskExecutionStatus.REPAIR_REQUIRED)
        self.assertEqual(self.kernel.current_state(outcome.task_id), TaskState.REPAIRING)
        self.assertFalse(outcome.verification_report.passes_gate)

    def test_failed_agent_run_routes_to_failed_with_evidence(self) -> None:
        outcome = self.execute(
            _EditingAgentExecutor(
                self.root / "agent-logs", status=AgentRunStatus.FAILED
            )
        )

        self.assertEqual(outcome.status, TaskExecutionStatus.FAILED)
        self.assertEqual(self.kernel.current_state(outcome.task_id), TaskState.FAILED)
        self.assertIsNotNone(outcome.failure_artifact)
        self.assertTrue(self.registry.verify(outcome.failure_artifact))

    def test_review_changes_requested_routes_to_repair(self) -> None:
        controller = self.controller(_EditingAgentExecutor(self.root / "agent-logs"))
        task = self.task()
        outcome = controller.execute_implementation(
            task=task,
            invocation=self.invocation(),
            validation_profile=self.profile(),
            authorization=self.authorization(),
            now=self.now,
        )
        review = ReviewReport(
            review_id="review-changes",
            task_id=task.task_id,
            reviewer_actor_type=ActorType.HUMAN,
            reviewer_id="human-reviewer",
            patch_sha256=outcome.patch.sha256,
            status=ReviewStatus.CHANGES_REQUESTED,
            summary="The explanation needs one more deterministic assertion.",
            findings=("Add an assertion",),
        )

        disposition = controller.prepare_merge_review(
            task=task,
            outcome=outcome,
            review_report=review,
        )

        self.assertEqual(disposition.status, ReviewDispositionStatus.REPAIR_REQUIRED)
        self.assertEqual(self.kernel.current_state(task.task_id), TaskState.REPAIRING)
        self.assertIsNone(disposition.merge_review_package)

    def test_risk_above_authority_is_rejected_without_state_change(self) -> None:
        controller = self.controller(_EditingAgentExecutor(self.root / "agent-logs"))

        with self.assertRaises(ExecutionAuthorizationError):
            controller.execute_implementation(
                task=self.task(risk=RiskLevel.HIGH),
                invocation=self.invocation(),
                validation_profile=self.profile(),
                authorization=self.authorization(maximum_risk=RiskLevel.LOW),
                now=self.now,
            )

        self.assertEqual(self.kernel.current_state("task-controller"), TaskState.DRAFT)
        self.assertEqual(tuple((self.root / "worktrees").iterdir()), ())


if __name__ == "__main__":
    unittest.main()
