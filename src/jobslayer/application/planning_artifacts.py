"""Read-only, integrity-verified views over planning-agent evidence."""

from __future__ import annotations

from pydantic import Field

from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.domain.models import ArtifactManifest, DomainModel


PLANNING_ARTIFACT_TYPES = frozenset(
    {
        "task_plan.agent.prompt",
        "task_plan.agent.raw_events",
        "task_plan.agent.stderr",
        "task_plan.agent.final_output",
    }
)


class PlanningArtifactQueryError(RuntimeError):
    """Raised when planning evidence cannot be verified or safely projected."""


class PlanningArtifactNotFoundError(PlanningArtifactQueryError):
    pass


class PlanningArtifactDescriptor(DomainModel):
    """Public metadata that deliberately omits the backing storage URI."""

    schema_version: str = "1.0"
    artifact_id: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(min_length=1, max_length=128)
    invocation_id: str | None = Field(default=None, max_length=160)
    artifact_type: str = Field(min_length=1, max_length=160)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    producer: str = Field(min_length=1, max_length=160)
    created_at: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class PlanningArtifactPreview(DomainModel):
    schema_version: str = "1.0"
    artifact: PlanningArtifactDescriptor
    content: str
    encoding: str = "utf-8"
    preview_size_bytes: int = Field(ge=0)
    truncated: bool
    content_verified: bool = True


class PlanningArtifactQuery:
    """Expose bounded text previews without exposing registry storage details."""

    def __init__(
        self,
        artifacts: ArtifactRegistry,
        *,
        max_preview_bytes: int = 1024 * 1024,
    ):
        if max_preview_bytes < 1_024 or max_preview_bytes > 4 * 1024 * 1024:
            raise ValueError(
                "planning artifact preview limit must be between 1 KiB and 4 MiB"
            )
        self.artifacts = artifacts
        self.max_preview_bytes = max_preview_bytes

    def list_for_plan(
        self, plan_id: str
    ) -> tuple[PlanningArtifactDescriptor, ...]:
        identifier = plan_id.strip()
        if not identifier:
            raise ValueError("planning artifact plan id must not be blank")
        try:
            manifests = self.artifacts.list_manifests(task_id=identifier)
            descriptors = tuple(
                self._descriptor(manifest)
                for manifest in sorted(
                    manifests,
                    key=lambda item: (item.created_at, item.artifact_id),
                )
                if manifest.artifact_type in PLANNING_ARTIFACT_TYPES
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise PlanningArtifactQueryError(
                "planning artifact manifests could not be verified"
            ) from exc
        return descriptors

    def preview(
        self,
        plan_id: str,
        artifact_id: str,
    ) -> PlanningArtifactPreview:
        descriptor = next(
            (
                item
                for item in self.list_for_plan(plan_id)
                if item.artifact_id == artifact_id
            ),
            None,
        )
        if descriptor is None:
            raise PlanningArtifactNotFoundError(
                "planning artifact does not exist for this plan"
            )
        try:
            manifest = self.artifacts.get(descriptor.artifact_id)
            content = self.artifacts.read(manifest)
        except (OSError, RuntimeError, ValueError) as exc:
            raise PlanningArtifactQueryError(
                "planning artifact content failed integrity verification"
            ) from exc
        preview = content[: self.max_preview_bytes]
        return PlanningArtifactPreview(
            artifact=descriptor,
            content=preview.decode("utf-8", errors="replace"),
            preview_size_bytes=len(preview),
            truncated=len(content) > len(preview),
        )

    @staticmethod
    def _descriptor(manifest: ArtifactManifest) -> PlanningArtifactDescriptor:
        return PlanningArtifactDescriptor(
            artifact_id=manifest.artifact_id,
            plan_id=manifest.task_id,
            invocation_id=manifest.run_id,
            artifact_type=manifest.artifact_type,
            sha256=manifest.sha256,
            size_bytes=manifest.size_bytes,
            producer=manifest.producer,
            created_at=manifest.created_at.isoformat(),
            metadata=dict(manifest.metadata),
        )


__all__ = [
    "PLANNING_ARTIFACT_TYPES",
    "PlanningArtifactDescriptor",
    "PlanningArtifactNotFoundError",
    "PlanningArtifactPreview",
    "PlanningArtifactQuery",
    "PlanningArtifactQueryError",
]
