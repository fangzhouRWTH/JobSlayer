"""Provider-neutral contracts for the task-independent Quick Agent surface."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import DomainModel


class QuickAgentMode(str, Enum):
    DISCUSS = "discuss"
    EXECUTE = "execute"


class QuickAgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class QuickAgentEvent(DomainModel):
    schema_version: str = "1.0"
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=80)
    role: str = Field(pattern=r"^(user|agent|tool|system)$")
    content: str = Field(max_length=24_000)
    created_at: datetime
    mode: QuickAgentMode | None = None
    status: str | None = Field(default=None, max_length=80)


class QuickAgentRateLimitWindow(DomainModel):
    schema_version: str = "1.0"
    used_percent: int = Field(ge=0, le=100)
    remaining_percent: int = Field(ge=0, le=100)
    window_duration_minutes: int | None = Field(default=None, ge=1)
    resets_at: datetime | None = None

    @model_validator(mode="after")
    def validate_percentages(self) -> QuickAgentRateLimitWindow:
        if self.used_percent + self.remaining_percent != 100:
            raise ValueError("rate-limit percentages must total 100")
        return self


class QuickAgentRateLimitBucket(DomainModel):
    schema_version: str = "1.0"
    limit_id: str = Field(min_length=1, max_length=120)
    limit_name: str | None = Field(default=None, max_length=160)
    plan_type: str | None = Field(default=None, max_length=80)
    primary: QuickAgentRateLimitWindow | None = None
    secondary: QuickAgentRateLimitWindow | None = None
    rate_limit_reached_type: str | None = Field(default=None, max_length=120)


class QuickAgentCapacitySnapshot(DomainModel):
    schema_version: str = "1.0"
    available: bool
    source: str = Field(min_length=1)
    observed_at: datetime
    refresh_after_seconds: int = Field(ge=5, le=600)
    buckets: tuple[QuickAgentRateLimitBucket, ...] = ()
    reset_credit_count: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_availability(self) -> QuickAgentCapacitySnapshot:
        if self.available and not self.buckets:
            raise ValueError("available capacity must contain at least one bucket")
        if self.available and self.error is not None:
            raise ValueError("available capacity cannot contain an error")
        return self


class QuickAgentReasoningEffortOption(DomainModel):
    schema_version: str = "1.0"
    effort: str = Field(min_length=1, max_length=40)
    description: str = Field(max_length=500)


class QuickAgentServiceTierOption(DomainModel):
    schema_version: str = "1.0"
    tier_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=500)


class QuickAgentModelOption(DomainModel):
    schema_version: str = "1.0"
    model_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(max_length=1_000)
    default_reasoning_effort: str = Field(min_length=1, max_length=40)
    reasoning_efforts: tuple[QuickAgentReasoningEffortOption, ...]
    input_modalities: tuple[str, ...] = ()
    supports_personality: bool = False
    multi_agent_version: str | None = Field(default=None, max_length=40)
    service_tiers: tuple[QuickAgentServiceTierOption, ...] = ()
    default_service_tier: str | None = Field(default=None, max_length=80)
    is_default: bool = False
    upgrade_model: str | None = Field(default=None, max_length=120)
    retirement_at: datetime | None = None

    @model_validator(mode="after")
    def validate_default_effort(self) -> QuickAgentModelOption:
        supported = {item.effort for item in self.reasoning_efforts}
        if not supported:
            raise ValueError("model must advertise at least one reasoning effort")
        if self.default_reasoning_effort not in supported:
            raise ValueError("default reasoning effort must be supported")
        if (
            self.default_service_tier is not None
            and self.default_service_tier
            not in {item.tier_id for item in self.service_tiers}
        ):
            raise ValueError("default service tier must be advertised")
        return self


class QuickAgentModelCatalogSnapshot(DomainModel):
    schema_version: str = "1.0"
    available: bool
    source: str = Field(min_length=1)
    observed_at: datetime
    refresh_after_seconds: int = Field(ge=30, le=3_600)
    models: tuple[QuickAgentModelOption, ...] = ()
    error: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_availability(self) -> QuickAgentModelCatalogSnapshot:
        if self.available and not self.models:
            raise ValueError("available model catalog must contain at least one model")
        if self.available and self.error is not None:
            raise ValueError("available model catalog cannot contain an error")
        return self


class QuickAgentSessionSnapshot(DomainModel):
    schema_version: str = "1.0"
    adapter_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    thread_id: str | None = None
    active_turn_id: str | None = None
    state: QuickAgentState
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    service_tier: str | None = Field(default=None, max_length=80)
    workspace_root: str = Field(min_length=1)
    maximum_turn_seconds: int = Field(ge=1)
    events: tuple[QuickAgentEvent, ...] = ()
    usage: dict[str, int] = Field(default_factory=dict)
    last_error: str | None = Field(default=None, max_length=2_000)
    updated_at: datetime


class QuickAgent(Protocol):
    adapter_id: str

    def capacity(self, *, force_refresh: bool = False) -> QuickAgentCapacitySnapshot:
        """Read provider-reported capacity without estimating missing values."""

    def models(self, *, force_refresh: bool = False) -> QuickAgentModelCatalogSnapshot:
        """Read the provider-advertised selectable model capabilities."""

    def snapshot(self) -> QuickAgentSessionSnapshot:
        """Return the current task-independent conversation projection."""

    def start_turn(
        self,
        content: str,
        *,
        mode: QuickAgentMode,
        model: str | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
    ) -> QuickAgentSessionSnapshot:
        """Start one streamed turn under an explicit permission mode."""

    def cancel(self) -> QuickAgentSessionSnapshot:
        """Interrupt the active turn if one exists."""

    def new_session(self) -> QuickAgentSessionSnapshot:
        """Detach from the current thread without deleting provider history."""

    def close(self) -> None:
        """Stop owned local processes idempotently."""


class QuickAgentError(RuntimeError):
    pass


class QuickAgentBusyError(QuickAgentError):
    pass


class QuickAgentUnavailableError(QuickAgentError):
    pass


__all__ = [
    "QuickAgent",
    "QuickAgentBusyError",
    "QuickAgentCapacitySnapshot",
    "QuickAgentError",
    "QuickAgentEvent",
    "QuickAgentMode",
    "QuickAgentModelCatalogSnapshot",
    "QuickAgentModelOption",
    "QuickAgentRateLimitBucket",
    "QuickAgentRateLimitWindow",
    "QuickAgentReasoningEffortOption",
    "QuickAgentServiceTierOption",
    "QuickAgentSessionSnapshot",
    "QuickAgentState",
    "QuickAgentUnavailableError",
]
