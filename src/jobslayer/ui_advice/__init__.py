"""Provider-neutral contracts for evidence-backed UI design advice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import ArtifactManifest, DomainModel
from jobslayer.ui_design import IDENTIFIER_PATTERN, SHA256_PATTERN


class UIAdviceMode(str, Enum):
    DESIGN_SYSTEM = "design_system"
    DOMAIN = "domain"
    STACK = "stack"


class UIAdviceRecommendationKind(str, Enum):
    PRODUCT_PATTERN = "product_pattern"
    VISUAL_STYLE = "visual_style"
    COLOR_SYSTEM = "color_system"
    TYPOGRAPHY = "typography"
    SPACING = "spacing"
    CONSTRAINT = "constraint"
    ANTI_PATTERN = "anti_pattern"
    UX_GUIDELINE = "ux_guideline"
    STACK_GUIDELINE = "stack_guideline"
    CHART = "chart"
    ICON = "icon"
    MOTION = "motion"
    OTHER = "other"


class UIAdviceRequest(DomainModel):
    """A bounded advisory query tied to one exact active SUID revision."""

    schema_version: str = "1.0"
    request_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    page_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    scheme_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    revision: int = Field(ge=1)
    descriptor_sha256: str = Field(pattern=SHA256_PATTERN)
    query: str = Field(min_length=2, max_length=1_000)
    mode: UIAdviceMode
    domain: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN, max_length=64)
    stack: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN, max_length=64)
    project_name: str | None = Field(default=None, max_length=160)
    max_results: int = Field(default=3, ge=1, le=20)
    variance: int | None = Field(default=None, ge=1, le=10)
    motion: int | None = Field(default=None, ge=1, le=10)
    density: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def validate_mode_options(self) -> UIAdviceRequest:
        if not self.query.strip():
            raise ValueError("UI advice query must not be blank")
        if self.project_name is not None and not self.project_name.strip():
            raise ValueError("UI advice project name must not be blank")
        dials = (self.variance, self.motion, self.density)
        if self.mode == UIAdviceMode.DESIGN_SYSTEM:
            if self.domain is not None or self.stack is not None:
                raise ValueError("design-system advice cannot select a domain or stack")
        elif self.mode == UIAdviceMode.DOMAIN:
            if self.domain is None or self.stack is not None:
                raise ValueError("domain advice needs exactly one domain")
            if self.project_name is not None or any(item is not None for item in dials):
                raise ValueError("domain advice cannot use project or design-dial options")
        elif self.mode == UIAdviceMode.STACK:
            if self.stack is None or self.domain is not None:
                raise ValueError("stack advice needs exactly one stack")
            if self.project_name is not None or any(item is not None for item in dials):
                raise ValueError("stack advice cannot use project or design-dial options")
        return self


class UIAdviceProviderIdentity(DomainModel):
    provider_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    provider_version: str = Field(min_length=1, max_length=64)
    source_repository: str = Field(min_length=1, max_length=512)
    source_ref: str = Field(min_length=1, max_length=128)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    license: str = Field(min_length=1, max_length=64)


class UIAdviceSourceField(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    value: str = Field(max_length=12_000)

    @model_validator(mode="after")
    def validate_field(self) -> UIAdviceSourceField:
        if not self.name.strip():
            raise ValueError("UI advice source field name must not be blank")
        return self


class UIAdviceRecommendation(DomainModel):
    recommendation_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    kind: UIAdviceRecommendationKind
    title: str = Field(min_length=1, max_length=400)
    guidance: str = Field(min_length=1, max_length=12_000)
    do: str | None = Field(default=None, max_length=12_000)
    avoid: str | None = Field(default=None, max_length=12_000)
    severity: str | None = Field(default=None, max_length=64)
    source_ref: str = Field(min_length=1, max_length=400)
    source_fields: tuple[UIAdviceSourceField, ...] = Field(default=(), max_length=96)

    @model_validator(mode="after")
    def validate_recommendation(self) -> UIAdviceRecommendation:
        if any(not item.strip() for item in (self.title, self.guidance, self.source_ref)):
            raise ValueError("UI advice recommendation text must not be blank")
        names = tuple(item.name for item in self.source_fields)
        if len(names) != len(set(names)):
            raise ValueError("UI advice source field names must be unique")
        return self


@dataclass(frozen=True)
class UIAdvisorResponse:
    provider: UIAdviceProviderIdentity
    recommendations: tuple[UIAdviceRecommendation, ...]
    raw_output: bytes


class UIAdvisor(Protocol):
    """Return advice only; never mutate SUID, source, or workflow state."""

    def advise(self, request: UIAdviceRequest) -> UIAdvisorResponse:
        """Run one bounded query and retain the exact provider output."""


class UIAdviceEvidence(DomainModel):
    """Normalized recommendations with immutable raw-provider provenance."""

    schema_version: str = "1.0"
    evidence_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    request: UIAdviceRequest
    provider: UIAdviceProviderIdentity
    recommendations: tuple[UIAdviceRecommendation, ...] = Field(max_length=64)
    raw_output_sha256: str = Field(pattern=SHA256_PATTERN)
    raw_output_artifact_id: str = Field(min_length=1, max_length=160)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_evidence(self) -> UIAdviceEvidence:
        if self.created_at.tzinfo is None:
            raise ValueError("UI advice evidence timestamp needs a timezone")
        return self


class UIAdviceCollection(DomainModel):
    evidence: UIAdviceEvidence
    raw_artifact: ArtifactManifest
    normalized_artifact: ArtifactManifest


__all__ = [
    "UIAdviceCollection",
    "UIAdviceEvidence",
    "UIAdviceMode",
    "UIAdviceProviderIdentity",
    "UIAdviceRecommendation",
    "UIAdviceRecommendationKind",
    "UIAdviceRequest",
    "UIAdviceSourceField",
    "UIAdvisor",
    "UIAdvisorResponse",
]
