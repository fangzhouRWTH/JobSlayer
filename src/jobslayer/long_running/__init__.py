"""Provider-neutral contracts for durable, resumable long-running work."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import Field, model_validator

from jobslayer.domain.models import DomainModel


class LongRunError(RuntimeError):
    pass


class LongRunConflictError(LongRunError):
    pass


class LongRunIntegrityError(LongRunError):
    pass


class LongRunStatus(str, Enum):
    ADMITTED = "admitted"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


TERMINAL_LONG_RUN_STATUSES = frozenset(
    {
        LongRunStatus.COMPLETED,
        LongRunStatus.FAILED,
        LongRunStatus.CANCELLED,
        LongRunStatus.LOST,
    }
)


class ProviderRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MISSING = "missing"


class BudgetEnforcement(str, Enum):
    HARD = "hard"
    SOFT = "soft"
    OBSERVE_ONLY = "observe_only"
    UNAVAILABLE = "unavailable"


class BillingMode(str, Enum):
    METERED = "metered"
    SUBSCRIPTION = "subscription"
    UNKNOWN = "unknown"


class LongRunBudgetDimension(str, Enum):
    TASK_ELAPSED_MS = "task_elapsed_ms"
    ATTEMPT_ELAPSED_MS = "attempt_elapsed_ms"
    MODEL_ACTIVE_MS = "model_active_ms"
    TOOL_ACTIVE_MS = "tool_active_ms"
    WAIT_MS = "wait_ms"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    COST_MICROUSD = "cost_microusd"
    TOOL_CALLS = "tool_calls"


class LongRunBudgetLimit(DomainModel):
    dimension: LongRunBudgetDimension
    enforcement: BudgetEnforcement
    maximum: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_limit(self) -> LongRunBudgetLimit:
        bounded = self.enforcement in {
            BudgetEnforcement.HARD,
            BudgetEnforcement.SOFT,
        }
        if bounded != (self.maximum is not None):
            raise ValueError(
                "hard/soft dimensions require a maximum and observational dimensions must omit it"
            )
        return self


class LongRunningExecutionPolicy(DomainModel):
    schema_version: str = "1.0"
    policy_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    billing_mode: BillingMode = BillingMode.UNKNOWN
    limits: tuple[LongRunBudgetLimit, ...] = Field(min_length=2)
    max_attempts: int = Field(default=1, ge=1, le=100)
    lease_seconds: int = Field(default=90, ge=2, le=3600)
    checkpoint_interval_ms: int = Field(default=20 * 60 * 1000, gt=0)
    progress_warning_after_ms: int = Field(default=20 * 60 * 1000, gt=0)

    @model_validator(mode="after")
    def validate_policy(self) -> LongRunningExecutionPolicy:
        by_dimension = {limit.dimension: limit for limit in self.limits}
        if len(by_dimension) != len(self.limits):
            raise ValueError("long-run budget dimensions must be unique")
        for required in (
            LongRunBudgetDimension.TASK_ELAPSED_MS,
            LongRunBudgetDimension.ATTEMPT_ELAPSED_MS,
        ):
            limit = by_dimension.get(required)
            if limit is None or limit.enforcement is not BudgetEnforcement.HARD:
                raise ValueError(
                    f"{required.value} must have a hard long-run limit"
                )
        task_maximum = by_dimension[
            LongRunBudgetDimension.TASK_ELAPSED_MS
        ].maximum
        attempt_maximum = by_dimension[
            LongRunBudgetDimension.ATTEMPT_ELAPSED_MS
        ].maximum
        assert task_maximum is not None and attempt_maximum is not None
        if attempt_maximum > task_maximum:
            raise ValueError("attempt elapsed limit cannot exceed task elapsed limit")
        cost = by_dimension.get(LongRunBudgetDimension.COST_MICROUSD)
        if (
            self.billing_mode is not BillingMode.METERED
            and cost is not None
            and cost.enforcement is BudgetEnforcement.HARD
        ):
            raise ValueError(
                "non-metered billing cannot use provider cost as a hard gate"
            )
        return self

    def limit_for(
        self, dimension: LongRunBudgetDimension
    ) -> LongRunBudgetLimit | None:
        return next(
            (limit for limit in self.limits if limit.dimension is dimension),
            None,
        )


class LongRunUsage(DomainModel):
    task_elapsed_ms: int = Field(default=0, ge=0)
    attempt_elapsed_ms: int = Field(default=0, ge=0)
    model_active_ms: int = Field(default=0, ge=0)
    tool_active_ms: int = Field(default=0, ge=0)
    wait_ms: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)

    def value_for(self, dimension: LongRunBudgetDimension) -> int:
        return int(getattr(self, dimension.value))


class ProviderRunReference(DomainModel):
    provider_adapter: str = Field(min_length=1)
    external_run_id: str = Field(min_length=1)
    provider_start_key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    start_evidence_artifact_id: str = Field(min_length=1)
    provider_session_id: str | None = Field(default=None, min_length=1)
    continuation_artifact_id: str | None = Field(default=None, min_length=1)
    started_at: datetime

    @model_validator(mode="after")
    def validate_started_at(self) -> ProviderRunReference:
        if self.started_at.tzinfo is None:
            raise ValueError("provider run start time must include a timezone")
        return self


class ProviderRunObservation(DomainModel):
    provider_adapter: str = Field(min_length=1)
    external_run_id: str = Field(min_length=1)
    status: ProviderRunStatus
    event_cursor: int = Field(ge=0)
    usage: LongRunUsage
    raw_event_artifact_ids: tuple[str, ...] = Field(min_length=1)
    observed_at: datetime

    @model_validator(mode="after")
    def validate_observed_at(self) -> ProviderRunObservation:
        if self.observed_at.tzinfo is None:
            raise ValueError("provider observation time must include a timezone")
        if len(self.raw_event_artifact_ids) != len(
            set(self.raw_event_artifact_ids)
        ):
            raise ValueError("provider raw event artifacts must be unique")
        return self


class ProviderStartRequest(DomainModel):
    """Persisted correlation identity used for idempotent start-or-locate."""

    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    task_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    provider_start_key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )


class ResumableRunHandle(DomainModel):
    schema_version: str = "1.0"
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    task_id: str = Field(min_length=1)
    policy: LongRunningExecutionPolicy
    status: LongRunStatus
    version: int = Field(ge=1)
    attempt_number: int = Field(default=0, ge=0)
    provider_start_key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    provider_run: ProviderRunReference | None = None
    worker_lease_id: str | None = Field(default=None, min_length=1)
    worker_lease_version: int | None = Field(default=None, ge=1)
    event_cursor: int = Field(default=0, ge=0)
    checkpoint_count: int = Field(default=0, ge=0)
    usage: LongRunUsage = Field(default_factory=LongRunUsage)
    attempt_usage: LongRunUsage = Field(default_factory=LongRunUsage)
    hard_limit_dimensions: tuple[LongRunBudgetDimension, ...] = ()
    soft_limit_dimensions: tuple[LongRunBudgetDimension, ...] = ()
    created_at: datetime
    updated_at: datetime
    last_progress_at: datetime

    @model_validator(mode="after")
    def validate_handle(self) -> ResumableRunHandle:
        for value in (self.created_at, self.updated_at, self.last_progress_at):
            if value.tzinfo is None:
                raise ValueError("long-run timestamps must include a timezone")
        if not (self.created_at <= self.last_progress_at <= self.updated_at):
            raise ValueError("long-run timestamps are not monotonic")
        if self.attempt_number > self.policy.max_attempts:
            raise ValueError("long-run attempt exceeds policy")
        lease_fields = (self.worker_lease_id, self.worker_lease_version)
        if (lease_fields[0] is None) != (lease_fields[1] is None):
            raise ValueError("worker lease id and version must be present together")
        if self.status in {LongRunStatus.RUNNING, LongRunStatus.CANCEL_REQUESTED}:
            if self.provider_run is None or self.worker_lease_id is None:
                raise ValueError("live long run requires provider and worker lease bindings")
        if self.status is LongRunStatus.ADMITTED and self.provider_run is not None:
            raise ValueError("admitted long run cannot already own a provider run")
        return self


class LongRunEventType(str, Enum):
    ADMITTED = "admitted"
    PROVIDER_BOUND = "provider_bound"
    OBSERVED = "observed"
    CHECKPOINTED = "checkpointed"
    PROGRESS_STALLED = "progress_stalled"
    LIMIT_WARNING = "limit_warning"
    LIMIT_EXCEEDED = "limit_exceeded"
    CANCEL_REQUESTED = "cancel_requested"
    RECOVERED = "recovered"
    RETRY_AUTHORIZED = "retry_authorized"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


class LongRunEvent(DomainModel):
    schema_version: str = "1.0"
    event_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    sequence: int = Field(ge=1)
    event_type: LongRunEventType
    handle: ResumableRunHandle
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    previous_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_binding(self) -> LongRunEvent:
        if self.created_at.tzinfo is None:
            raise ValueError("long-run event time must include a timezone")
        if self.handle.run_id != self.run_id or self.handle.version != self.sequence:
            raise ValueError("long-run event does not bind its handle version")
        return self


class ProgressCheckpoint(DomainModel):
    schema_version: str = "1.0"
    checkpoint_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    sequence: int = Field(ge=1)
    attempt_number: int = Field(ge=1)
    event_cursor: int = Field(ge=0)
    stage: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=4_000)
    referenced_artifact_ids: tuple[str, ...] = ()
    workspace_state_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    continuation_artifact_id: str | None = Field(default=None, min_length=1)
    checkpoint_artifact_id: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    usage: LongRunUsage
    created_at: datetime

    @model_validator(mode="after")
    def validate_checkpoint(self) -> ProgressCheckpoint:
        if self.created_at.tzinfo is None:
            raise ValueError("checkpoint time must include a timezone")
        if len(self.referenced_artifact_ids) != len(set(self.referenced_artifact_ids)):
            raise ValueError("checkpoint artifact references must be unique")
        return self


class LongRunObservationResult(DomainModel):
    handle: ResumableRunHandle
    cancel_required: bool = False
    progress_stalled: bool = False
    hard_exceeded: tuple[LongRunBudgetDimension, ...] = ()
    soft_exceeded: tuple[LongRunBudgetDimension, ...] = ()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def long_run_event_hash(event: LongRunEvent) -> str:
    payload = event.model_dump(mode="json", exclude={"record_hash"})
    return hashlib.sha256(_canonical(payload)).hexdigest()


def build_long_run_event(
    handle: ResumableRunHandle,
    event_type: LongRunEventType,
    *,
    previous_hash: str | None,
    details: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> LongRunEvent:
    draft = LongRunEvent(
        event_id=f"long-run-event-{uuid4().hex}",
        run_id=handle.run_id,
        sequence=handle.version,
        event_type=event_type,
        handle=handle,
        details=details or {},
        created_at=created_at or datetime.now(UTC),
        previous_hash=previous_hash,
        record_hash="0" * 64,
    )
    return draft.model_copy(update={"record_hash": long_run_event_hash(draft)})


class LongRunStore(Protocol):
    def create(self, event: LongRunEvent) -> ResumableRunHandle:
        """Create one admitted run and its first immutable event."""

    def append(
        self,
        event: LongRunEvent,
        *,
        expected_version: int,
        checkpoint: ProgressCheckpoint | None = None,
    ) -> ResumableRunHandle:
        """Atomically append an event, update the projection, and optionally checkpoint."""

    def get(self, run_id: str) -> ResumableRunHandle | None:
        """Return the integrity-verified latest projection."""

    def history(self, run_id: str) -> tuple[LongRunEvent, ...]:
        """Return and verify the append-only event chain."""

    def checkpoints(self, run_id: str) -> tuple[ProgressCheckpoint, ...]:
        """Return immutable checkpoints in sequence order."""

    def list_active(self) -> tuple[ResumableRunHandle, ...]:
        """Return admitted or live runs after integrity verification."""


class ResumableRunAdapter(Protocol):
    def start_or_locate(self, request: ProviderStartRequest) -> ProviderRunReference:
        """Idempotently start, or rediscover, the exact persisted attempt."""

    def observe(
        self, reference: ProviderRunReference, *, after_cursor: int
    ) -> ProviderRunObservation:
        """Read provider status and cumulative usage without changing JobSlayer state."""

    def request_cancel(self, reference: ProviderRunReference) -> None:
        """Signal cancellation only after JobSlayer has persisted the request."""


__all__ = [
    "BillingMode",
    "BudgetEnforcement",
    "LongRunBudgetDimension",
    "LongRunBudgetLimit",
    "LongRunConflictError",
    "LongRunError",
    "LongRunEvent",
    "LongRunEventType",
    "LongRunIntegrityError",
    "LongRunObservationResult",
    "LongRunStatus",
    "LongRunStore",
    "LongRunUsage",
    "LongRunningExecutionPolicy",
    "ProgressCheckpoint",
    "ProviderRunObservation",
    "ProviderRunReference",
    "ProviderRunStatus",
    "ProviderStartRequest",
    "ResumableRunAdapter",
    "ResumableRunHandle",
    "TERMINAL_LONG_RUN_STATUSES",
    "build_long_run_event",
    "long_run_event_hash",
]
