"""Provider-neutral executor comparison and regression evidence models."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field, model_validator

from jobslayer.domain.models import DomainModel


class ExecutorEvaluationSample(DomainModel):
    schema_version: str = "1.0"
    run_id: str
    task_id: str
    executor_type: str
    task_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_state: str
    verification_passed: bool
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_microusd: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    human_interventions: int = Field(ge=0)


class ExecutorAggregate(DomainModel):
    executor_type: str
    runs: int = Field(gt=0)
    verified_successes: int = Field(ge=0)
    total_input_tokens: int = Field(ge=0)
    total_cached_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_cost_microusd: int = Field(ge=0)
    total_duration_ms: int = Field(ge=0)
    total_human_interventions: int = Field(ge=0)


class ExecutorComparisonReport(DomainModel):
    schema_version: str = "1.0"
    comparison_id: str
    task_id: str
    task_contract_sha256: str
    validation_contract_sha256: str
    samples: tuple[ExecutorEvaluationSample, ...]
    aggregates: tuple[ExecutorAggregate, ...]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_comparison(self) -> ExecutorComparisonReport:
        if len({item.executor_type for item in self.samples}) < 2:
            raise ValueError("comparison requires at least two executor types")
        if any(
            item.task_id != self.task_id
            or item.task_contract_sha256 != self.task_contract_sha256
            or item.validation_contract_sha256 != self.validation_contract_sha256
            for item in self.samples
        ):
            raise ValueError("comparison samples do not share task/validation contracts")
        return self


class ExecutorComparisonError(RuntimeError):
    pass


__all__ = [
    "ExecutorAggregate",
    "ExecutorComparisonError",
    "ExecutorComparisonReport",
    "ExecutorEvaluationSample",
]
