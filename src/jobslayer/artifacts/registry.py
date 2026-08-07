from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from jobslayer.domain.models import ArtifactManifest


class ArtifactRegistry(Protocol):
    """Register immutable evidence without exposing a storage implementation."""

    def register_bytes(
        self,
        *,
        task_id: str,
        artifact_type: str,
        producer: str,
        content: bytes,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactManifest:
        """Persist bytes and return their immutable manifest."""

    def register_file(
        self,
        path: str | Path,
        *,
        task_id: str,
        artifact_type: str,
        producer: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactManifest:
        """Copy an existing file into immutable storage and return its manifest."""

    def read(self, manifest: ArtifactManifest) -> bytes:
        """Read an artifact only after verifying its manifest binding."""

    def get(self, artifact_id: str) -> ArtifactManifest:
        """Load a persisted manifest by its stable registry identifier."""

    def verify(self, manifest: ArtifactManifest) -> bool:
        """Return whether stored bytes still match the manifest."""
