from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainModel(BaseModel):
    """Strict and immutable base for records crossing system boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskState(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    PLAN_REVIEW = "plan_review"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    REVIEWING = "reviewing"
    MERGE_REVIEW = "merge_review"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ActorType(str, Enum):
    AGENT = "agent"
    HUMAN = "human"
    POLICY = "policy"
    SYSTEM = "system"


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


def _validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value.strip() or path.is_absolute() or ".." in path.parts:
        raise ValueError("paths must be non-empty repository-relative POSIX paths")
    if value.startswith("./"):
        raise ValueError("paths must be normalized and must not start with './'")
    return value


class TaskSpec(DomainModel):
    schema_version: str = "1.0"
    task_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    base_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    allowed_paths: tuple[str, ...] = Field(min_length=1)
    forbidden_paths: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    validation_profile: str = Field(min_length=1)
    risk: RiskLevel
    deadline: datetime | None = None
    max_cost_usd: float | None = Field(default=None, ge=0)

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def validate_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_validate_relative_path(path) for path in paths)
        if len(normalized) != len(set(normalized)):
            raise ValueError("paths must not contain duplicates")
        return normalized

    @field_validator("required_capabilities", "acceptance_criteria")
    @classmethod
    def validate_non_empty_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("list items must not be blank")
        return values


class AgentRunSpec(DomainModel):
    schema_version: str = "1.0"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    executor_type: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
    context_package_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    permission_profile: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    max_attempts: int = Field(default=1, ge=1)
    output_schema: str = Field(min_length=1)


class RunEvent(DomainModel):
    schema_version: str = "1.0"
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ArtifactManifest(DomainModel):
    schema_version: str = "1.0"
    artifact_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str | None = None
    artifact_type: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    producer: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CheckResult(DomainModel):
    check_id: str = Field(min_length=1)
    status: CheckStatus
    required: bool = True
    command: tuple[str, ...] | None = None
    artifact_ids: tuple[str, ...] = ()
    summary: str = Field(min_length=1)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerificationReport(DomainModel):
    schema_version: str = "1.0"
    report_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    checks: tuple[CheckResult, ...] = Field(min_length=1)
    required_checks_passed: bool
    regressions_detected: bool = False
    unresolved_risks: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_required_check_claim(self) -> VerificationReport:
        if not any(check.required for check in self.checks):
            raise ValueError("a verification report must contain a required check")
        calculated = all(
            check.status is CheckStatus.PASSED
            for check in self.checks
            if check.required
        )
        if self.required_checks_passed != calculated:
            raise ValueError(
                "required_checks_passed must match the statuses of all required checks"
            )
        return self

    @property
    def passes_gate(self) -> bool:
        return (
            self.required_checks_passed
            and not self.regressions_detected
            and not self.unresolved_risks
        )


class TransitionRecord(DomainModel):
    schema_version: str = "1.0"
    task_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    from_state: TaskState
    to_state: TaskState
    actor_type: ActorType
    actor_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    previous_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
