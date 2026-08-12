from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.local_command import GovernedLocalCommandRunner
from jobslayer.adapters.sqlite_state import SqliteControlPlaneStore
from jobslayer.adapters.sqlite_budgets import SqliteBudgetStore
from jobslayer.adapters.sqlite_workers import SqliteWorkerLeaseStore
from jobslayer.adapters.persistent_management import PersistentManagementQuery
from jobslayer.adapters.local_identity import LocalIdentityProvider
from jobslayer.adapters.local_git_integration import LocalGitIntegrator
from jobslayer.agents.executor import AgentExecutorError
from jobslayer.application.controller import TaskExecutionError
from jobslayer.application.transactional_execution import TransactionalExecutionCoordinator
from jobslayer.application.governed_executor import GovernedAgentExecutor
from jobslayer.domain.models import (
    ActorType,
    AgentCancellationResult,
    AgentInvocation,
    AgentRunHandle,
    AgentRunResult,
    AgentRunSpec,
    AgentRunStatus,
    CommandPolicy,
    CommandRule,
    RiskLevel,
    ReviewReport,
    ReviewStatus,
    TaskExecutionAuthorization,
    TaskSpec,
    TaskState,
    ValidationCheckSpec,
    ValidationProfile,
    WorkspaceManifest,
)
from jobslayer.governance import ContextComponent, ContextPackage, ExecutionBudget
from jobslayer.identity import (
    AgentCredentialGrant,
    AuthorizationAction,
    AuthorizationDeniedError,
    AuthorizationVerdict,
)
from jobslayer.verification.engine import VerificationEngine
from jobslayer.workers import NetworkPolicy, SandboxCapabilities, SandboxPolicy
from jobslayer.supervision.decision import create_human_decision
from jobslayer.domain.models import DecisionKind


class _CredentialBroker:
    def __init__(self):
        self.revoked: list[str] = []

    def issue(self, **kwargs):
        raise AssertionError("fixture supplies its grant")

    def revoke(self, grant_id: str) -> None:
        if grant_id not in self.revoked:
            self.revoked.append(grant_id)


class _EditingExecutor:
    def __init__(
        self,
        log_root: Path,
        *,
        fail_start: bool = False,
        credential_grant_id: str | None = None,
        sandbox_capabilities: SandboxCapabilities | None = None,
    ):
        self.log_root = log_root
        self.fail_start = fail_start
        self.results: dict[str, AgentRunResult] = {}
        self._credential_grant_id = credential_grant_id
        self._sandbox_capabilities = sandbox_capabilities

    def credential_grant_id(self):
        if self._credential_grant_id is None:
            raise AgentExecutorError("no credential binding")
        return self._credential_grant_id

    def sandbox_capabilities(self):
        if self._sandbox_capabilities is None:
            raise AgentExecutorError("no sandbox attestation")
        return self._sandbox_capabilities

    def start(self, invocation: AgentInvocation, workspace: WorkspaceManifest):
        if self.fail_start:
            raise AgentExecutorError("fixture worker unavailable")
        spec = invocation.run_spec
        started_at = datetime.now(UTC)
        run_root = self.log_root / spec.run_id
        run_root.mkdir(parents=True)
        raw = run_root / "events.jsonl"
        stderr = run_root / "stderr.log"
        raw.write_text('{"type":"turn.completed"}\n', encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        (Path(workspace.path) / "src" / "value.txt").write_text(
            "changed\n", encoding="utf-8"
        )
        handle = AgentRunHandle(
            run_id=spec.run_id,
            external_id="fixture-worker-1",
            executor_type=spec.executor_type,
            workspace_id=workspace.workspace_id,
            started_at=started_at,
        )
        self.results[spec.run_id] = AgentRunResult(
            run_id=spec.run_id,
            external_id=handle.external_id,
            executor_type=spec.executor_type,
            workspace_id=workspace.workspace_id,
            status=AgentRunStatus.COMPLETED,
            exit_code=0,
            event_count=1,
            usage={"input_tokens": 7, "output_tokens": 2},
            raw_event_log_path=str(raw),
            raw_event_log_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
            stderr_log_path=str(stderr),
            stderr_log_sha256=hashlib.sha256(stderr.read_bytes()).hexdigest(),
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
        return handle

    def events(self, run_id: str, *, after_sequence: int = 0):
        return ()

    def cancel(self, run_id: str):
        return AgentCancellationResult(
            run_id=run_id,
            cancellation_requested=False,
            already_terminal=True,
            status=self.results[run_id].status,
        )

    def collect(self, run_id: str):
        return self.results[run_id]


class TransactionalExecutionCoordinatorTests(unittest.TestCase):
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
            "if Path('src/value.txt').read_text() != 'changed\\n':\n"
            "    raise SystemExit(9)\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.commit = self._git("rev-parse", "HEAD")
        self.manager = GitWorktreeManager(self.repository, self.root / "worktrees")
        self.artifacts = LocalArtifactRegistry(self.root / "artifacts")
        self.store_path = self.root / "control-plane.sqlite3"
        self.store = SqliteControlPlaneStore(self.store_path)
        self.store.migrate()
        self.now = datetime.now(UTC)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ("git", "-C", str(self.repository), *arguments),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def inputs(self):
        task = TaskSpec(
            task_id="transactional-task",
            project_id="fixture",
            title="Transactional execution",
            objective="Commit execution truth atomically",
            repository=str(self.repository),
            base_commit=self.commit,
            allowed_paths=("src/",),
            required_capabilities=("file_change",),
            acceptance_criteria=("verification passes",),
            validation_profile="fixture-v1",
            risk=RiskLevel.LOW,
        )
        invocation = AgentInvocation(
            run_spec=AgentRunSpec(
                run_id="transactional-run",
                task_id=task.task_id,
                executor_type="fixture",
                model_profile="fixture",
                context_package_id="context-1",
                workspace_id="transactional-workspace",
                permission_profile="workspace_write",
                timeout_seconds=3,
                output_schema="none",
            ),
            prompt="make the admitted deterministic edit",
        )
        argv = (sys.executable, "verify.py")
        profile = ValidationProfile(
            profile_id="fixture-v1",
            command_policy=CommandPolicy(
                policy_id="fixture-policy",
                rules=(CommandRule(rule_id="verify", argv_prefix=argv),),
                max_timeout_seconds=3,
            ),
            checks=(
                ValidationCheckSpec(
                    check_id="verify",
                    title="Verify fixture",
                    argv=argv,
                    timeout_seconds=2,
                ),
            ),
        )
        authorization = TaskExecutionAuthorization(
            authorization_id="execution-authorization-1",
            task_id=task.task_id,
            actor_type=ActorType.POLICY,
            actor_id="fixture-policy",
            maximum_risk=RiskLevel.LOW,
            issued_at=self.now - timedelta(minutes=1),
            valid_until=self.now + timedelta(minutes=5),
        )
        return task, invocation, profile, authorization

    def coordinator(self, executor):
        runner = GovernedLocalCommandRunner(self.manager)
        return TransactionalExecutionCoordinator(
            store=self.store,
            workspace_manager=self.manager,
            agent_executor=executor,
            verification_engine=VerificationEngine(runner, self.artifacts),
            artifact_registry=self.artifacts,
            source_integrator=LocalGitIntegrator(self.manager),
            poll_interval_seconds=0.001,
            collection_grace_seconds=0.1,
        )

    def test_commits_workflow_run_artifacts_and_outbox_as_one_outcome(self) -> None:
        task, invocation, profile, authorization = self.inputs()
        result = self.coordinator(_EditingExecutor(self.root / "logs")).execute(
            task=task,
            invocation=invocation,
            validation_profile=profile,
            authorization=authorization,
            governance_evidence={"policy_id": "fixture-governance-v1"},
            now=self.now,
        )

        reopened = SqliteControlPlaneStore(self.store_path)
        self.assertEqual(result.outcome.state, TaskState.REVIEWING)
        self.assertEqual(
            reopened.task_history(task.task_id)[-1].to_state, TaskState.REVIEWING
        )
        self.assertEqual(reopened.run_history(invocation.run_spec.run_id), (result.run_record,))
        persisted_ids = {
            item.artifact_id
            for item in reopened.artifacts_for_run(invocation.run_spec.run_id)
        }
        local_ids = {
            item.artifact_id
            for item in self.artifacts.list_manifests(run_id=invocation.run_spec.run_id)
        }
        self.assertEqual(persisted_ids, local_ids)
        self.assertEqual(
            tuple(event.topic for event in reopened.pending_outbox()),
            ("execution.intent.recorded", "execution.outcome.committed"),
        )
        query = PersistentManagementQuery(
            reopened,
            self.artifacts,
            source_name="sqlite://control-plane",
        )
        snapshot = query.snapshot()
        self.assertEqual(len(snapshot.runs), 1)
        self.assertEqual(snapshot.runs[0].state, "reviewing")
        self.assertEqual(snapshot.total_input_tokens, 7)
        detail = query.run_detail(invocation.run_spec.run_id)
        self.assertEqual(len(detail["workflow"]), result.committed_task_sequence)
        self.assertEqual(len(detail["events"]), 2)

    def test_worker_start_failure_leaves_only_durable_intent_and_never_outcome(self) -> None:
        task, invocation, profile, authorization = self.inputs()
        with self.assertRaises(TaskExecutionError):
            self.coordinator(
                _EditingExecutor(self.root / "logs", fail_start=True)
            ).execute(
                task=task,
                invocation=invocation,
                validation_profile=profile,
                authorization=authorization,
                governance_evidence={"policy_id": "fixture-governance-v1"},
                now=self.now,
            )

        self.assertEqual(self.store.task_history(task.task_id), ())
        self.assertEqual(self.store.run_history(invocation.run_spec.run_id), ())
        persisted = self.store.artifacts_for_run(invocation.run_spec.run_id)
        self.assertEqual(
            tuple(item.artifact_type for item in persisted),
            ("task-execution-intent",),
        )
        self.assertEqual(
            tuple(event.topic for event in self.store.pending_outbox()),
            ("execution.intent.recorded",),
        )

    def test_composes_governance_gates_with_transactional_execution_truth(self) -> None:
        task, invocation, profile, authorization = self.inputs()
        grant = AgentCredentialGrant(
            grant_id="grant-transactional-run",
            run_id=invocation.run_spec.run_id,
            audience=invocation.run_spec.executor_type,
            scopes=("execute",),
            issued_at=self.now - timedelta(seconds=1),
            valid_until=self.now + timedelta(minutes=5),
            broker_id="fixture-broker",
        )
        context = ContextPackage(
            package_id=invocation.run_spec.context_package_id,
            task_id=task.task_id,
            run_id=invocation.run_spec.run_id,
            components=(
                ContextComponent(
                    logical_name="task.json",
                    source_path="task.json",
                    artifact_id="context-artifact-1",
                    sha256="c" * 64,
                    size_bytes=4,
                    media_type="application/json",
                ),
            ),
            total_size_bytes=4,
            package_sha256="d" * 64,
        )
        capabilities = SandboxCapabilities(
            adapter="fixture-sandbox",
            network_isolation=True,
            mount_isolation=True,
            cpu_limit=True,
            memory_limit=True,
            process_limit=True,
            process_tree_termination=True,
            wall_timeout=True,
        )
        delegate = _EditingExecutor(
            self.root / "governed-logs",
            credential_grant_id=grant.grant_id,
            sandbox_capabilities=capabilities,
        )
        budget_store = SqliteBudgetStore(self.root / "budgets.sqlite3")
        lease_store = SqliteWorkerLeaseStore(self.root / "workers.sqlite3")
        budget_store.migrate()
        lease_store.migrate()
        broker = _CredentialBroker()
        governor = GovernedAgentExecutor(
            delegate,
            budget_store=budget_store,
            worker_leases=lease_store,
            credential_broker=broker,
            credential_grant=grant,
            context_package=context,
            verify_context=lambda package: package is context,
            budget=ExecutionBudget(
                budget_id="budget-transactional-run",
                task_id=task.task_id,
                run_id=invocation.run_spec.run_id,
                maximum_input_tokens=100,
                maximum_output_tokens=50,
                maximum_cost_microusd=1_000_000,
                maximum_duration_ms=5_000,
                maximum_attempts=1,
                maximum_repairs=0,
            ),
            sandbox_policy=SandboxPolicy(
                policy_id="fixture-sandbox-v1",
                network=NetworkPolicy.DENY,
                cpu_seconds=3,
                memory_bytes=64 * 1024 * 1024,
                process_limit=16,
                timeout_seconds=3,
            ),
            worker_id="fixture-worker",
        )

        result = self.coordinator(governor).execute(
            task=task,
            invocation=invocation,
            validation_profile=profile,
            authorization=authorization,
            governance_evidence=governor.governance_evidence,
            now=self.now,
        )

        governance = result.run_record.payload["governance"]
        self.assertEqual(governance["credential_grant"]["grant_id"], grant.grant_id)
        self.assertEqual(governance["budget"]["status"], "released")
        self.assertEqual(governance["worker_lease"]["status"], "released")
        self.assertEqual(governance["sandbox_capabilities"]["adapter"], "fixture-sandbox")
        self.assertEqual(broker.revoked, [grant.grant_id])

    def test_review_and_signed_human_decision_commit_as_atomic_stages(self) -> None:
        task, invocation, profile, authorization = self.inputs()
        coordinator = self.coordinator(_EditingExecutor(self.root / "stage-logs"))
        execution = coordinator.execute(
            task=task,
            invocation=invocation,
            validation_profile=profile,
            authorization=authorization,
            governance_evidence={"policy_id": "fixture-governance-v1"},
            now=self.now,
        )
        review = ReviewReport(
            review_id="review-transactional-1",
            task_id=task.task_id,
            reviewer_actor_type=ActorType.AGENT,
            reviewer_id="independent-reviewer",
            patch_sha256=execution.outcome.patch.sha256,
            status=ReviewStatus.ACCEPTED,
            summary="The bounded deterministic patch satisfies the contract.",
            evidence_ids=(execution.outcome.verification_report.report_id,),
        )

        disposition, review_record = coordinator.prepare_review(
            run_id=invocation.run_spec.run_id,
            review_report=review,
        )
        package = disposition.merge_review_package
        self.assertIsNotNone(package)
        identity = LocalIdentityProvider(self.root / "approval-key.json")
        identity.create_key()
        session = identity.issue(
            subject_id="human-approver",
            display_name="Human Approver",
            roles=("approver",),
            lifetime=timedelta(minutes=10),
            now=self.now,
        )
        authority = identity.issue_approval_authority(
            session,
            allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
            lifetime=timedelta(minutes=5),
            now=self.now,
        )
        decision = create_human_decision(
            package.decision_card,
            actor_id="human-approver",
            selected_option_id="approve",
            rationale="Verification and independent review evidence pass.",
        )

        transition, decision_record = coordinator.apply_decision(
            run_id=invocation.run_spec.run_id,
            decision=decision,
            authority=authority,
            authority_verifier=(
                lambda supplied, when: identity.verify_approval_authority(
                    supplied, now=when
                )
            ),
            now=self.now + timedelta(seconds=1),
        )

        self.assertEqual(review_record.sequence, 2)
        self.assertEqual(decision_record.sequence, 3)
        self.assertEqual(transition.to_state, TaskState.INTEGRATING)
        self.assertEqual(
            self.store.task_history(task.task_id)[-1].to_state,
            TaskState.INTEGRATING,
        )
        query = PersistentManagementQuery(
            self.store,
            self.artifacts,
            source_name="sqlite://control-plane",
        )
        summary = query.snapshot().runs[0]
        self.assertEqual(summary.review_status, "accepted")
        self.assertTrue(summary.decision_recorded)
        self.assertTrue(summary.decision_applied)
        self.assertEqual(
            len(self.store.events_for_run(invocation.run_spec.run_id)), 4
        )

        denied_integration = AuthorizationVerdict(
            permitted=False,
            policy_id="fixture-rbac-v1",
            subject_id="human-approver",
            action=AuthorizationAction.INTEGRATE_SOURCE,
            reason="fixture denial proves no external side effect",
            evidence_ids=(session.principal.session_id,),
            decided_at=self.now + timedelta(seconds=2),
        )
        head_before_denial = self._git("rev-parse", "HEAD")
        with self.assertRaises(AuthorizationDeniedError):
            coordinator.integrate(
                run_id=invocation.run_spec.run_id,
                target_ref="main",
                authorization=denied_integration,
            )
        self.assertEqual(self._git("rev-parse", "HEAD"), head_before_denial)
        self.assertEqual(len(self.store.run_history(invocation.run_spec.run_id)), 3)

        integration = AuthorizationVerdict(
            permitted=True,
            policy_id="fixture-rbac-v1",
            subject_id="human-approver",
            action=AuthorizationAction.INTEGRATE_SOURCE,
            reason="signed approver session permits local integration",
            evidence_ids=(session.principal.session_id,),
            decided_at=self.now + timedelta(seconds=2),
        )
        integration_result, completed, integration_record = coordinator.integrate(
            run_id=invocation.run_spec.run_id,
            target_ref="main",
            authorization=integration,
        )
        cleanup = AuthorizationVerdict(
            permitted=True,
            policy_id="fixture-rbac-v1",
            subject_id="human-approver",
            action=AuthorizationAction.CLEANUP_WORKSPACE,
            reason="signed approver session permits exact workspace cleanup",
            evidence_ids=(session.principal.session_id,),
            decided_at=self.now + timedelta(seconds=3),
        )
        removal, cleanup_record = coordinator.cleanup(
            run_id=invocation.run_spec.run_id,
            authorization=cleanup,
        )

        self.assertEqual(completed.to_state, TaskState.COMPLETED)
        self.assertEqual(integration_record.sequence, 4)
        self.assertEqual(cleanup_record.sequence, 5)
        self.assertTrue(removal.safely_removed)
        self.assertEqual(self._git("rev-parse", "HEAD"), integration_result.commit)
        self.assertEqual(len(self.store.run_history(invocation.run_spec.run_id)), 5)
        self.assertEqual(len(self.store.events_for_run(invocation.run_spec.run_id)), 6)
        self.assertEqual(query.snapshot().runs[0].state, "completed")


if __name__ == "__main__":
    unittest.main()
