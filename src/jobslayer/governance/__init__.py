"""Budget, context-package, and bounded-attempt governance contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import DomainModel


class BudgetStatus(str, Enum):
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    RELEASED = "released"


class ExecutionBudget(DomainModel):
    schema_version: str = "1.0"
    budget_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    maximum_input_tokens: int = Field(ge=0)
    maximum_output_tokens: int = Field(ge=0)
    maximum_cost_microusd: int = Field(ge=0)
    maximum_duration_ms: int = Field(gt=0)
    maximum_attempts: int = Field(gt=0, le=100)
    maximum_repairs: int = Field(ge=0, le=99)

    @model_validator(mode="after")
    def validate_attempts(self) -> ExecutionBudget:
        if self.maximum_repairs >= self.maximum_attempts:
            raise ValueError("repair limit must be smaller than attempt limit")
        return self


class BudgetSnapshot(DomainModel):
    schema_version: str = "1.0"
    reservation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    budget: ExecutionBudget
    version: int = Field(ge=1)
    status: BudgetStatus
    spent_input_tokens: int = Field(ge=0)
    spent_output_tokens: int = Field(ge=0)
    spent_cost_microusd: int = Field(ge=0)
    spent_duration_ms: int = Field(ge=0)
    attempts_started: int = Field(ge=0)
    repairs_started: int = Field(ge=0)
    reserved_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_snapshot(self) -> BudgetSnapshot:
        if self.reserved_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("budget timestamps must include a timezone")
        if self.updated_at < self.reserved_at:
            raise ValueError("budget update cannot precede reservation")
        if self.repairs_started > self.attempts_started:
            raise ValueError("repairs cannot exceed started attempts")
        return self


class BudgetStore(Protocol):
    def reserve(
        self,
        budget: ExecutionBudget,
        *,
        now: datetime | None = None,
    ) -> BudgetSnapshot:
        """Persist the full run budget before any executor side effect."""

    def authorize_attempt(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        repair: bool = False,
        now: datetime | None = None,
    ) -> BudgetSnapshot:
        """Consume one deterministic attempt slot before execution."""

    def charge(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_microusd: int = 0,
        duration_ms: int = 0,
        now: datetime | None = None,
    ) -> BudgetSnapshot:
        """Persist observed usage; overage is persisted and then rejected."""

    def release(
        self,
        reservation_id: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> BudgetSnapshot:
        """Close the reservation after terminal executor collection."""


class ContextComponent(DomainModel):
    schema_version: str = "1.0"
    logical_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    source_path: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)


class ContextPackage(DomainModel):
    schema_version: str = "1.0"
    package_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    components: tuple[ContextComponent, ...] = Field(min_length=1)
    total_size_bytes: int = Field(ge=0)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_components(self) -> ContextPackage:
        names = tuple(item.logical_name for item in self.components)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("context components must have sorted unique logical names")
        if self.total_size_bytes != sum(item.size_bytes for item in self.components):
            raise ValueError("context package total size does not match components")
        if self.created_at.tzinfo is None:
            raise ValueError("context package timestamp must include a timezone")
        return self


class BudgetError(RuntimeError):
    pass


class BudgetExceededError(BudgetError):
    def __init__(self, message: str, snapshot: BudgetSnapshot):
        super().__init__(message)
        self.snapshot = snapshot


class ContextPackageError(RuntimeError):
    pass


__all__ = [
    "BudgetError",
    "BudgetExceededError",
    "BudgetSnapshot",
    "BudgetStatus",
    "BudgetStore",
    "ContextComponent",
    "ContextPackage",
    "ContextPackageError",
    "ExecutionBudget",
]
