"""Provider-neutral worker lease and sandbox contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import DomainModel


class WorkerLeaseStatus(str, Enum):
    ACTIVE = "active"
    CANCEL_REQUESTED = "cancel_requested"
    RELEASED = "released"
    EXPIRED = "expired"


class WorkerLease(DomainModel):
    schema_version: str = "1.0"
    lease_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    worker_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    status: WorkerLeaseStatus
    version: int = Field(ge=1)
    acquired_at: datetime
    last_heartbeat_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_times(self) -> WorkerLease:
        for value in (self.acquired_at, self.last_heartbeat_at, self.expires_at):
            if value.tzinfo is None:
                raise ValueError("worker lease timestamps must include a timezone")
        if self.last_heartbeat_at < self.acquired_at:
            raise ValueError("heartbeat cannot precede lease acquisition")
        if self.expires_at <= self.last_heartbeat_at:
            raise ValueError("worker lease must expire after its heartbeat")
        return self

    def is_live(self, now: datetime | None = None) -> bool:
        when = now or datetime.now(UTC)
        return (
            self.status
            in {WorkerLeaseStatus.ACTIVE, WorkerLeaseStatus.CANCEL_REQUESTED}
            and when < self.expires_at
        )


class WorkerLeaseStore(Protocol):
    def acquire(
        self,
        *,
        worker_id: str,
        run_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        """Acquire the only live lease for one run."""

    def heartbeat(
        self,
        lease_id: str,
        *,
        expected_version: int,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        """Renew a live lease under optimistic concurrency."""

    def request_cancel(
        self,
        lease_id: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        """Persist cancellation before signaling the worker."""

    def release(
        self,
        lease_id: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> WorkerLease:
        """Mark a lease terminal after the worker has stopped."""

    def recover_orphans(self, *, now: datetime | None = None) -> tuple[WorkerLease, ...]:
        """Expire all elapsed live leases after restart."""


class NetworkPolicy(str, Enum):
    DENY = "deny"
    ALLOW = "allow"


class SandboxPolicy(DomainModel):
    schema_version: str = "1.0"
    policy_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    network: NetworkPolicy = NetworkPolicy.DENY
    read_only_root: bool = True
    cpu_seconds: int = Field(gt=0)
    memory_bytes: int = Field(ge=16 * 1024 * 1024)
    process_limit: int = Field(gt=0, le=4096)
    timeout_seconds: float = Field(gt=0)


class SandboxCapabilities(DomainModel):
    schema_version: str = "1.0"
    adapter: str = Field(min_length=1)
    network_isolation: bool
    mount_isolation: bool
    cpu_limit: bool
    memory_limit: bool
    process_limit: bool
    process_tree_termination: bool
    wall_timeout: bool

    def unmet(self, policy: SandboxPolicy) -> tuple[str, ...]:
        requirements = {
            "network_isolation": policy.network is NetworkPolicy.DENY,
            "mount_isolation": policy.read_only_root,
            "cpu_limit": True,
            "memory_limit": True,
            "process_limit": True,
            "process_tree_termination": True,
            "wall_timeout": True,
        }
        return tuple(
            name
            for name, required in requirements.items()
            if required and not getattr(self, name)
        )


class SandboxRequest(DomainModel):
    schema_version: str = "1.0"
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    workspace: Path
    argv: tuple[str, ...] = Field(min_length=1)
    policy: SandboxPolicy

    @model_validator(mode="after")
    def validate_argv(self) -> SandboxRequest:
        if any(not value or "\x00" in value for value in self.argv):
            raise ValueError("sandbox argv contains an invalid argument")
        return self


class SandboxLaunchPlan(DomainModel):
    """A fully materialized process launch that enforces one sandbox request."""

    schema_version: str = "1.0"
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    adapter: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: Path
    environment: dict[str, str]
    capabilities: SandboxCapabilities

    @model_validator(mode="after")
    def validate_launch(self) -> SandboxLaunchPlan:
        if any(not value or "\x00" in value for value in self.argv):
            raise ValueError("sandbox launch argv contains an invalid argument")
        if any(
            not name or "\x00" in name or "=" in name or "\x00" in value
            for name, value in self.environment.items()
        ):
            raise ValueError("sandbox launch environment is invalid")
        return self


class SandboxResult(DomainModel):
    schema_version: str = "1.0"
    request_id: str
    run_id: str
    adapter: str
    exit_code: int | None
    timed_out: bool
    started_at: datetime
    finished_at: datetime
    stdout: str
    stderr: str
    capabilities: SandboxCapabilities


class SandboxExecutor(Protocol):
    def capabilities(self) -> SandboxCapabilities:
        """Report enforceable isolation, never inferred requested capability."""

    def execute(self, request: SandboxRequest) -> SandboxResult:
        """Fail closed if the adapter cannot enforce every policy requirement."""


class SandboxLauncher(Protocol):
    def capabilities(self) -> SandboxCapabilities:
        """Report the controls enforced by prepared launches."""

    def prepare(self, request: SandboxRequest) -> SandboxLaunchPlan:
        """Build a launch plan or fail closed before the child process exists."""


class WorkerLeaseError(RuntimeError):
    pass


class SandboxUnavailableError(RuntimeError):
    pass


__all__ = [
    "NetworkPolicy",
    "SandboxCapabilities",
    "SandboxExecutor",
    "SandboxLaunchPlan",
    "SandboxLauncher",
    "SandboxPolicy",
    "SandboxRequest",
    "SandboxResult",
    "SandboxUnavailableError",
    "WorkerLease",
    "WorkerLeaseError",
    "WorkerLeaseStatus",
    "WorkerLeaseStore",
]
