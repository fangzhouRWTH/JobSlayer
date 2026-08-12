from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from jobslayer.adapters.sqlite_budgets import SqliteBudgetStore
from jobslayer.adapters.sqlite_workers import SqliteWorkerLeaseStore
from jobslayer.agents.events import RunEventBuffer
from jobslayer.agents.executor import AgentRunStillRunningError
from jobslayer.application.governed_executor import (
    ExecutionGovernanceError,
    GovernedAgentExecutor,
)
from jobslayer.domain.models import (
    AgentCancellationResult,
    AgentInvocation,
    AgentRunHandle,
    AgentRunResult,
    AgentRunSpec,
    AgentRunStatus,
    WorkspaceManifest,
)
from jobslayer.governance import BudgetStatus, ContextComponent, ContextPackage, ExecutionBudget
from jobslayer.identity import AgentCredentialGrant
from jobslayer.workers import (
    NetworkPolicy,
    SandboxCapabilities,
    SandboxPolicy,
    WorkerLeaseStatus,
)


class _Broker:
    def __init__(self):
        self.revoked: list[str] = []

    def issue(self, **kwargs):  # pragma: no cover - issuance is outside this unit
        raise AssertionError("grant is supplied by the fixture")

    def revoke(self, grant_id: str) -> None:
        if grant_id not in self.revoked:
            self.revoked.append(grant_id)


class _Executor:
    def __init__(self, grant_id: str, *, capabilities: SandboxCapabilities):
        self.grant_id = grant_id
        self.capabilities = capabilities
        self.buffer = RunEventBuffer("run-1")
        self.handle: AgentRunHandle | None = None
        self.result: AgentRunResult | None = None
        self.start_count = 0
        self.cancel_count = 0

    def sandbox_capabilities(self) -> SandboxCapabilities:
        return self.capabilities

    def credential_grant_id(self) -> str:
        return self.grant_id

    def start(self, invocation, workspace):
        self.start_count += 1
        self.handle = AgentRunHandle(
            run_id=invocation.run_spec.run_id,
            external_id="worker-external-1",
            executor_type=invocation.run_spec.executor_type,
            workspace_id=workspace.workspace_id,
            started_at=datetime.now(UTC),
        )
        self.buffer.append("run.started")
        return self.handle

    def events(self, run_id: str, *, after_sequence: int = 0):
        return self.buffer.events(after_sequence=after_sequence)

    def cancel(self, run_id: str):
        self.cancel_count += 1
        if self.result is None:
            assert self.handle is not None
            self.buffer.append("run.cancelled")
            self.result = self._result(AgentRunStatus.CANCELLED, None)
        return AgentCancellationResult(
            run_id=run_id,
            cancellation_requested=True,
            already_terminal=False,
            status=AgentRunStatus.RUNNING,
        )

    def collect(self, run_id: str):
        if self.result is None:
            raise AgentRunStillRunningError(run_id)
        return self.result

    def complete(self, *, usage: dict[str, int]) -> None:
        assert self.handle is not None
        self.buffer.append(
            "agent.turn.completed",
            {"raw": {"type": "turn.completed", "usage": usage}},
        )
        self.buffer.append("run.completed")
        self.result = self._result(AgentRunStatus.COMPLETED, 0, usage=usage)

    def _result(
        self,
        status: AgentRunStatus,
        exit_code: int | None,
        *,
        usage: dict[str, int] | None = None,
    ) -> AgentRunResult:
        assert self.handle is not None
        return AgentRunResult(
            run_id=self.handle.run_id,
            external_id=self.handle.external_id,
            executor_type=self.handle.executor_type,
            workspace_id=self.handle.workspace_id,
            status=status,
            exit_code=exit_code,
            event_count=len(self.buffer.events()),
            usage=usage or {},
            raw_event_log_path="raw.jsonl",
            raw_event_log_sha256="a" * 64,
            stderr_log_path="stderr.log",
            stderr_log_sha256="b" * 64,
            started_at=self.handle.started_at,
            finished_at=self.handle.started_at + timedelta(milliseconds=20),
        )


class GovernedAgentExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.budgets = SqliteBudgetStore(self.root / "governance.sqlite3")
        self.leases = SqliteWorkerLeaseStore(self.root / "workers.sqlite3")
        self.budgets.migrate()
        self.leases.migrate()
        self.now = datetime.now(UTC)
        self.grant = AgentCredentialGrant(
            grant_id="grant-1",
            run_id="run-1",
            audience="fixture-agent",
            scopes=("execute",),
            issued_at=self.now - timedelta(seconds=1),
            valid_until=self.now + timedelta(minutes=5),
            broker_id="fixture-broker",
        )
        self.context = ContextPackage(
            package_id="context-1",
            task_id="task-1",
            run_id="run-1",
            components=(
                ContextComponent(
                    logical_name="task.json",
                    source_path="task.json",
                    artifact_id="artifact-context-1",
                    sha256="c" * 64,
                    size_bytes=12,
                    media_type="application/json",
                ),
            ),
            total_size_bytes=12,
            package_sha256="d" * 64,
        )
        self.budget = ExecutionBudget(
            budget_id="budget-1",
            task_id="task-1",
            run_id="run-1",
            maximum_input_tokens=100,
            maximum_output_tokens=50,
            maximum_cost_microusd=1_000_000,
            maximum_duration_ms=5_000,
            maximum_attempts=1,
            maximum_repairs=0,
        )
        self.policy = SandboxPolicy(
            policy_id="sandbox-v1",
            network=NetworkPolicy.DENY,
            cpu_seconds=5,
            memory_bytes=64 * 1024 * 1024,
            process_limit=16,
            timeout_seconds=5,
        )
        self.capabilities = SandboxCapabilities(
            adapter="fixture-sandbox",
            network_isolation=True,
            mount_isolation=True,
            cpu_limit=True,
            memory_limit=True,
            process_limit=True,
            process_tree_termination=True,
            wall_timeout=True,
        )
        self.invocation = AgentInvocation(
            run_spec=AgentRunSpec(
                run_id="run-1",
                task_id="task-1",
                executor_type="fixture-agent",
                model_profile="fixture",
                context_package_id="context-1",
                workspace_id="workspace-1",
                permission_profile="workspace_write",
                timeout_seconds=5,
                output_schema="none",
            ),
            prompt="perform the admitted fixture change",
        )
        self.workspace = WorkspaceManifest(
            workspace_id="workspace-1",
            task_id="task-1",
            repository_root=str(self.root),
            path=str(self.root),
            requested_base_commit="a" * 40,
            resolved_base_commit="a" * 40,
            branch_name="jobslayer/workspace-1",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def governor(self, delegate: _Executor, broker: _Broker, *, verify=True):
        return GovernedAgentExecutor(
            delegate,
            budget_store=self.budgets,
            worker_leases=self.leases,
            credential_broker=broker,
            credential_grant=self.grant,
            context_package=self.context,
            verify_context=lambda package: verify,
            budget=self.budget,
            sandbox_policy=self.policy,
            worker_id="worker-1",
            lease_seconds=30,
        )

    def test_reserves_before_launch_and_releases_after_terminal_collection(self) -> None:
        broker = _Broker()
        delegate = _Executor(self.grant.grant_id, capabilities=self.capabilities)
        governor = self.governor(delegate, broker)

        governor.start(self.invocation, self.workspace)
        evidence = governor.governance_evidence()
        self.assertEqual(evidence["budget"]["attempts_started"], 1)
        self.assertEqual(evidence["worker_lease"]["status"], "active")
        delegate.complete(
            usage={"input_tokens": 12, "output_tokens": 4, "cost_microusd": 25}
        )
        result = governor.collect("run-1")

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        evidence = governor.governance_evidence()
        self.assertEqual(evidence["budget"]["status"], BudgetStatus.RELEASED.value)
        self.assertEqual(evidence["budget"]["spent_input_tokens"], 12)
        self.assertEqual(
            evidence["worker_lease"]["status"], WorkerLeaseStatus.RELEASED.value
        )
        self.assertEqual(broker.revoked, ["grant-1"])

    def test_over_limit_usage_persists_cancellation_before_signaling_worker(self) -> None:
        tight_budget = self.budget.model_copy(
            update={"maximum_input_tokens": 10}
        )
        broker = _Broker()
        delegate = _Executor(self.grant.grant_id, capabilities=self.capabilities)
        governor = GovernedAgentExecutor(
            delegate,
            budget_store=self.budgets,
            worker_leases=self.leases,
            credential_broker=broker,
            credential_grant=self.grant,
            context_package=self.context,
            verify_context=lambda package: True,
            budget=tight_budget,
            sandbox_policy=self.policy,
            worker_id="worker-1",
        )
        governor.start(self.invocation, self.workspace)
        delegate.buffer.append(
            "agent.turn.completed",
            {"raw": {"usage": {"input_tokens": 11, "output_tokens": 0}}},
        )

        result = governor.collect("run-1")

        self.assertEqual(result.status, AgentRunStatus.CANCELLED)
        self.assertEqual(delegate.cancel_count, 1)
        evidence = governor.governance_evidence()
        self.assertEqual(evidence["budget"]["status"], BudgetStatus.EXHAUSTED.value)
        lease_events = self.leases.events(evidence["worker_lease"]["lease_id"])
        self.assertEqual(
            tuple(item.status for item in lease_events),
            (
                WorkerLeaseStatus.ACTIVE,
                WorkerLeaseStatus.CANCEL_REQUESTED,
                WorkerLeaseStatus.RELEASED,
            ),
        )

    def test_missing_context_or_isolation_rejects_before_any_launch_or_reservation(self) -> None:
        broker = _Broker()
        missing_network = self.capabilities.model_copy(
            update={"network_isolation": False}
        )
        for verify, capabilities in (
            (False, self.capabilities),
            (True, missing_network),
        ):
            with self.subTest(verify=verify):
                delegate = _Executor(self.grant.grant_id, capabilities=capabilities)
                governor = self.governor(delegate, broker, verify=verify)
                with self.assertRaises(ExecutionGovernanceError):
                    governor.start(self.invocation, self.workspace)
                self.assertEqual(delegate.start_count, 0)
        self.assertEqual(broker.revoked, [])


if __name__ == "__main__":
    unittest.main()
