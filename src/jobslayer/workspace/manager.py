from __future__ import annotations

from typing import Protocol

from jobslayer.domain.models import (
    TaskSpec,
    WorkspaceInspection,
    WorkspaceManifest,
    WorkspacePatch,
    WorkspaceRemovalInspection,
    WorkspaceSpec,
)


class WorkspaceOperationError(RuntimeError):
    """Base error for provider-neutral workspace lifecycle failures."""


class WorkspaceManager(Protocol):
    """Provider-neutral lifecycle for a task-scoped writable workspace."""

    def create(self, spec: WorkspaceSpec) -> WorkspaceManifest:
        """Create a workspace from an immutable base revision."""

    def inspect(self, manifest: WorkspaceManifest) -> WorkspaceInspection:
        """Inspect the current revision and all changes relative to its base."""

    def collect_patch(
        self, manifest: WorkspaceManifest, task: TaskSpec
    ) -> WorkspacePatch:
        """Enforce task path policy and return a content-addressed patch."""

    def remove(self, manifest: WorkspaceManifest) -> None:
        """Remove a clean registered worktree while preserving its branch."""

    def inspect_removal(
        self,
        manifest: WorkspaceManifest,
        *,
        expected_commit: str,
    ) -> WorkspaceRemovalInspection:
        """Attest path/registration absence and the preserved source revision."""
