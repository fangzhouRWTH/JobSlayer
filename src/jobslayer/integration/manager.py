from __future__ import annotations

from typing import Protocol

from jobslayer.domain.models import (
    SourceIntegrationResult,
    TaskSpec,
    WorkspaceManifest,
    WorkspacePatch,
)


class SourceIntegrationError(RuntimeError):
    """Base error for a rejected or failed source integration operation."""


class SourceIntegrator(Protocol):
    """Integrate one approved, content-addressed patch into a target ref."""

    def integrate(
        self,
        *,
        task: TaskSpec,
        workspace: WorkspaceManifest,
        reviewed_patch: WorkspacePatch,
        target_ref: str,
        approved_by: str,
        commit_message: str,
    ) -> SourceIntegrationResult:
        """Return evidence only after the target ref contains the reviewed patch."""
