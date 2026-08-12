"""Cross-cutting execution governance around a provider-neutral agent adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Callable

from jobslayer.agents.executor import AgentExecutor, AgentExecutorError
from jobslayer.domain.models import (
    AgentCancellationResult,
    AgentInvocation,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
    RunEvent,
    WorkspaceManifest,
)
from jobslayer.governance import (
    BudgetError,
    BudgetExceededError,
    BudgetSnapshot,
    BudgetStatus,
    BudgetStore,
    ContextPackage,
    ExecutionBudget,
)
from jobslayer.identity import AgentCredentialBroker, AgentCredentialGrant
from jobslayer.workers import (
    SandboxCapabilities,
    SandboxPolicy,
    WorkerLease,
    WorkerLeaseError,
    WorkerLeaseStatus,
    WorkerLeaseStore,
)


class ExecutionGovernanceError(AgentExecutorError):
    """Raised before or during execution when a control-plane gate fails."""


class GovernedAgentExecutor:
    """Enforce credentials, isolation, lease, context, and budget around an adapter.

    The wrapped executor remains responsible for the provider lifecycle. This
    decorator owns policy: it rejects launch before side effects when any gate
    is missing, persists a budget reservation and worker lease before launch,
    charges normalized usage incrementally, records cancellation before signaling
    the child, and revokes the credential only after terminal collection.
    """

    def __init__(
        self,
        delegate: AgentExecutor,
        *,
        budget_store: BudgetStore,
        worker_leases: WorkerLeaseStore,
        credential_broker: AgentCredentialBroker,
        credential_grant: AgentCredentialGrant,
        context_package: ContextPackage,
        verify_context: Callable[[ContextPackage], bool],
        budget: ExecutionBudget,
        sandbox_policy: SandboxPolicy,
        worker_id: str,
        lease_seconds: int = 30,
    ):
        if lease_seconds < 2 or lease_seconds > 3600:
            raise ValueError("worker lease duration must be between 2 and 3600 seconds")
        self.delegate = delegate
        self.budget_store = budget_store
        self.worker_leases = worker_leases
        self.credential_broker = credential_broker
        self.credential_grant = credential_grant
        self.context_package = context_package
        self.verify_context = verify_context
        self.budget = budget
        self.sandbox_policy = sandbox_policy
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._lock = Lock()
        self._budget_snapshot: BudgetSnapshot | None = None
        self._lease: WorkerLease | None = None
        self._last_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_microusd": 0,
        }
        self._budget_exceeded: BudgetExceededError | None = None
        self._credential_revoked = False
        self._terminal_result: AgentRunResult | None = None

    def start(
        self,
        invocation: AgentInvocation,
        workspace: WorkspaceManifest,
    ) -> AgentRunHandle:
        with self._lock:
            if self._budget_snapshot is not None or self._lease is not None:
                raise ExecutionGovernanceError("governed executor is single-use")
            self._validate_bindings(invocation)
            self._validate_preflight(invocation)
            try:
                snapshot = self.budget_store.reserve(self.budget)
                snapshot = self.budget_store.authorize_attempt(
                    snapshot.reservation_id,
                    expected_version=snapshot.version,
                )
                lease = self.worker_leases.acquire(
                    worker_id=self.worker_id,
                    run_id=invocation.run_spec.run_id,
                    lease_seconds=self.lease_seconds,
                )
            except (BudgetError, WorkerLeaseError) as exc:
                raise ExecutionGovernanceError(
                    "execution reservation or worker lease was rejected"
                ) from exc
            self._budget_snapshot = snapshot
            self._lease = lease

        try:
            return self.delegate.start(invocation, workspace)
        except Exception:
            self._close_without_worker()
            raise

    def events(
        self, run_id: str, *, after_sequence: int = 0
    ) -> tuple[RunEvent, ...]:
        events = self.delegate.events(run_id, after_sequence=after_sequence)
        self._observe(events)
        self._heartbeat_if_due()
        return events

    def cancel(self, run_id: str) -> AgentCancellationResult:
        result = self._request_cancel_before_signal()
        if result is not None:
            return result
        return self.delegate.cancel(run_id)

    def collect(self, run_id: str) -> AgentRunResult:
        if self._terminal_result is not None:
            return self._terminal_result
        # Polling collection is the controller's stable observation point. Read
        # all normalized events so an over-budget turn is cancelled immediately.
        self.events(run_id)
        result = self.delegate.collect(run_id)
        self._charge_absolute_usage(result.usage)
        duration_ms = max(
            0,
            int((result.finished_at - result.started_at).total_seconds() * 1000),
        )
        self._charge_duration(duration_ms)
        if self._budget_exceeded is not None and result.status is AgentRunStatus.COMPLETED:
            result = result.model_copy(
                update={
                    "status": AgentRunStatus.FAILED,
                    "error_summary": str(self._budget_exceeded),
                }
            )
        self._release_after_terminal()
        self._terminal_result = result
        return result

    def governance_evidence(self) -> dict[str, object]:
        """Return non-secret persisted evidence after admission or collection."""

        return {
            "credential_grant": self.credential_grant.model_dump(mode="json"),
            "context_package": self.context_package.model_dump(mode="json"),
            "budget": (
                None
                if self._budget_snapshot is None
                else self._budget_snapshot.model_dump(mode="json")
            ),
            "worker_lease": (
                None if self._lease is None else self._lease.model_dump(mode="json")
            ),
            "sandbox_capabilities": self._sandbox_capabilities().model_dump(
                mode="json"
            ),
        }

    def _validate_bindings(self, invocation: AgentInvocation) -> None:
        run_id = invocation.run_spec.run_id
        task_id = invocation.run_spec.task_id
        bindings = (
            self.budget.run_id == run_id,
            self.budget.task_id == task_id,
            self.context_package.run_id == run_id,
            self.context_package.task_id == task_id,
            self.context_package.package_id
            == invocation.run_spec.context_package_id,
            self.credential_grant.run_id == run_id,
            self.credential_grant.audience == invocation.run_spec.executor_type,
        )
        if not all(bindings):
            raise ExecutionGovernanceError(
                "governance inputs do not bind to the invocation"
            )

    def _validate_preflight(self, invocation: AgentInvocation) -> None:
        now = datetime.now(UTC)
        if not self.credential_grant.is_active(now):
            raise ExecutionGovernanceError("agent credential grant is not active")
        if "execute" not in self.credential_grant.scopes:
            raise ExecutionGovernanceError(
                "agent credential grant lacks the execute scope"
            )
        grant_method = getattr(self.delegate, "credential_grant_id", None)
        if grant_method is None or not callable(grant_method):
            raise ExecutionGovernanceError(
                "executor provides no credential binding attestation"
            )
        try:
            bound_grant_id = grant_method()
        except Exception as exc:
            raise ExecutionGovernanceError(
                "executor credential binding attestation is unavailable"
            ) from exc
        if bound_grant_id != self.credential_grant.grant_id:
            raise ExecutionGovernanceError(
                "executor is not bound to the admitted credential grant"
            )
        run_deadline = now + timedelta(seconds=invocation.run_spec.timeout_seconds)
        if self.credential_grant.valid_until < run_deadline:
            raise ExecutionGovernanceError(
                "agent credential expires before the governed run deadline"
            )
        if not self.verify_context(self.context_package):
            raise ExecutionGovernanceError("context package integrity check failed")
        capabilities = self._sandbox_capabilities()
        unmet = capabilities.unmet(self.sandbox_policy)
        if unmet:
            raise ExecutionGovernanceError(
                "executor sandbox lacks required controls: " + ", ".join(unmet)
            )

    def _sandbox_capabilities(self) -> SandboxCapabilities:
        method = getattr(self.delegate, "sandbox_capabilities", None)
        if method is None or not callable(method):
            raise ExecutionGovernanceError(
                "executor provides no enforcement-backed sandbox attestation"
            )
        try:
            capabilities = method()
        except Exception as exc:
            raise ExecutionGovernanceError(
                "executor sandbox attestation is unavailable"
            ) from exc
        if not isinstance(capabilities, SandboxCapabilities):
            raise ExecutionGovernanceError("executor sandbox attestation is invalid")
        return capabilities

    def _observe(self, events: tuple[RunEvent, ...]) -> None:
        for event in events:
            raw = event.payload.get("raw")
            usage = raw.get("usage") if isinstance(raw, dict) else None
            if isinstance(usage, dict):
                self._charge_absolute_usage(usage)

    def _charge_absolute_usage(self, usage: dict[str, object]) -> None:
        normalized = {}
        for name in self._last_usage:
            value = usage.get(name, self._last_usage[name])
            normalized[name] = (
                value if isinstance(value, int) and not isinstance(value, bool) else 0
            )
        if any(normalized[name] < self._last_usage[name] for name in normalized):
            self._budget_exceeded = BudgetExceededError(
                "executor usage counters moved backwards",
                self._required_budget_snapshot(),
            )
            self._request_cancel_before_signal()
            return
        deltas = {
            name: normalized[name] - self._last_usage[name] for name in normalized
        }
        if not any(deltas.values()):
            return
        snapshot = self._required_budget_snapshot()
        if snapshot.status is not BudgetStatus.ACTIVE:
            return
        try:
            updated = self.budget_store.charge(
                snapshot.reservation_id,
                expected_version=snapshot.version,
                input_tokens=deltas["input_tokens"],
                output_tokens=deltas["output_tokens"],
                cost_microusd=deltas["cost_microusd"],
            )
        except BudgetExceededError as exc:
            self._budget_snapshot = exc.snapshot
            self._last_usage = normalized
            self._budget_exceeded = exc
            self._request_cancel_before_signal()
        else:
            self._budget_snapshot = updated
            self._last_usage = normalized

    def _charge_duration(self, duration_ms: int) -> None:
        snapshot = self._required_budget_snapshot()
        if snapshot.status is not BudgetStatus.ACTIVE:
            return
        delta = max(0, duration_ms - snapshot.spent_duration_ms)
        if delta == 0:
            return
        try:
            self._budget_snapshot = self.budget_store.charge(
                snapshot.reservation_id,
                expected_version=snapshot.version,
                duration_ms=delta,
            )
        except BudgetExceededError as exc:
            self._budget_snapshot = exc.snapshot
            self._budget_exceeded = exc

    def _heartbeat_if_due(self) -> None:
        lease = self._lease
        if lease is None or lease.status is not WorkerLeaseStatus.ACTIVE:
            return
        now = datetime.now(UTC)
        interval = timedelta(seconds=max(1, self.lease_seconds // 3))
        if now - lease.last_heartbeat_at < interval:
            return
        try:
            self._lease = self.worker_leases.heartbeat(
                lease.lease_id,
                expected_version=lease.version,
                lease_seconds=self.lease_seconds,
                now=now,
            )
        except WorkerLeaseError as exc:
            self._request_cancel_before_signal()
            raise ExecutionGovernanceError("worker lease heartbeat failed") from exc

    def _request_cancel_before_signal(self) -> AgentCancellationResult | None:
        lease = self._lease
        if lease is None or lease.status is not WorkerLeaseStatus.ACTIVE:
            return None
        try:
            self._lease = self.worker_leases.request_cancel(
                lease.lease_id,
                expected_version=lease.version,
            )
        except WorkerLeaseError as exc:
            raise ExecutionGovernanceError(
                "could not persist cancellation before signaling the worker"
            ) from exc
        return self.delegate.cancel(lease.run_id)

    def _release_after_terminal(self) -> None:
        lease = self._lease
        if lease is not None and lease.status in {
            WorkerLeaseStatus.ACTIVE,
            WorkerLeaseStatus.CANCEL_REQUESTED,
        }:
            self._lease = self.worker_leases.release(
                lease.lease_id,
                expected_version=lease.version,
            )
        snapshot = self._budget_snapshot
        if snapshot is not None and snapshot.status is BudgetStatus.ACTIVE:
            self._budget_snapshot = self.budget_store.release(
                snapshot.reservation_id,
                expected_version=snapshot.version,
            )
        self._revoke_credential()

    def _close_without_worker(self) -> None:
        self._release_after_terminal()

    def _revoke_credential(self) -> None:
        if not self._credential_revoked:
            self.credential_broker.revoke(self.credential_grant.grant_id)
            self._credential_revoked = True

    def _required_budget_snapshot(self) -> BudgetSnapshot:
        if self._budget_snapshot is None:
            raise ExecutionGovernanceError("execution budget is not reserved")
        return self._budget_snapshot


__all__ = ["ExecutionGovernanceError", "GovernedAgentExecutor"]
