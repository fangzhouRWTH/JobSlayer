from __future__ import annotations

from typing import Protocol

from jobslayer.domain.models import (
    CommandPolicy,
    CommandRequest,
    CommandResult,
    WorkspaceManifest,
)


class CommandExecutionError(RuntimeError):
    """Base error for requests rejected before structured evidence exists."""


class CommandRunner(Protocol):
    """Run controller-approved commands inside a registered workspace."""

    def run(
        self,
        manifest: WorkspaceManifest,
        request: CommandRequest,
        policy: CommandPolicy,
    ) -> CommandResult:
        """Execute one request and return normalized output evidence."""
