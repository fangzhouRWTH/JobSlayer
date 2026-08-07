from __future__ import annotations

from typing import Protocol

from jobslayer.domain.models import (
    AgentCancellationResult,
    AgentInvocation,
    AgentRunHandle,
    AgentRunResult,
    RunEvent,
    WorkspaceManifest,
)


class AgentExecutorError(RuntimeError):
    """Base error for provider-neutral executor lifecycle failures."""


class AgentRunNotFoundError(AgentExecutorError):
    pass


class AgentRunStillRunningError(AgentExecutorError):
    pass


class AgentExecutor(Protocol):
    """Provider-neutral non-blocking lifecycle for an external coding agent."""

    def start(
        self,
        invocation: AgentInvocation,
        workspace: WorkspaceManifest,
    ) -> AgentRunHandle:
        """Start a run and return immediately with a stable handle."""

    def events(
        self, run_id: str, *, after_sequence: int = 0
    ) -> tuple[RunEvent, ...]:
        """Return normalized events after the provided sequence number."""

    def cancel(self, run_id: str) -> AgentCancellationResult:
        """Request process-tree cancellation; the terminal result remains collectable."""

    def collect(self, run_id: str) -> AgentRunResult:
        """Collect a terminal structured result or reject if still running."""
