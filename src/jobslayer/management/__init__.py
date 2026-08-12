"""Provider-neutral read models for the Agent management control plane."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import Field

from jobslayer.domain.models import DomainModel


class ManagedRunSummary(DomainModel):
    schema_version: str = "1.0"
    run_id: str
    task_id: str
    title: str
    state: str
    stage: str
    executor_type: str
    executor_status: str
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    review_status: str | None
    decision_recorded: bool
    decision_applied: bool
    artifacts_valid: bool
    workflow_valid: bool
    run_record_valid: bool


class InvalidRunSummary(DomainModel):
    run_id: str
    reason: str


class ManagementSnapshot(DomainModel):
    schema_version: str = "1.0"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state_root: str
    runs: tuple[ManagedRunSummary, ...]
    invalid_runs: tuple[InvalidRunSummary, ...]
    state_counts: dict[str, int]
    executor_counts: dict[str, int]
    total_input_tokens: int = Field(ge=0)
    total_cached_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_cost_microusd: int = Field(ge=0)


class ManagementQuery(Protocol):
    def snapshot(self) -> ManagementSnapshot:
        """Return one integrity-checked persisted management snapshot."""

    def run_detail(self, run_id: str) -> dict[str, Any]:
        """Return one integrity-checked run plus ordered persisted events."""


class ManagementQueryError(RuntimeError):
    pass


__all__ = [
    "InvalidRunSummary",
    "ManagedRunSummary",
    "ManagementQuery",
    "ManagementQueryError",
    "ManagementSnapshot",
]
