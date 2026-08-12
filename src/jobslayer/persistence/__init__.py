"""Provider-neutral transactional control-plane persistence contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import Field, model_validator

from jobslayer.application.run_records import RunRecord, RunRecordStage
from jobslayer.domain.models import ArtifactManifest, DomainModel, TransitionRecord
from jobslayer.workflow.journal import AuditJournal


class StateStoreError(RuntimeError):
    """Base error for transactional control-plane persistence."""


class StateConflictError(StateStoreError):
    """Raised when optimistic versions or unique identities conflict."""


class StateIntegrityError(StateStoreError):
    """Raised when persisted control-plane truth fails integrity checks."""


class OutboxEvent(DomainModel):
    """An event committed atomically with workflow/run/artifact metadata."""

    schema_version: str = "1.0"
    event_id: str = Field(
        default_factory=lambda: f"event-{uuid4().hex}",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    topic: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> OutboxEvent:
        if self.created_at.tzinfo is None:
            raise ValueError("outbox created_at must include a timezone")
        if self.published_at is not None:
            if self.published_at.tzinfo is None:
                raise ValueError("outbox published_at must include a timezone")
            if self.published_at < self.created_at:
                raise ValueError("outbox publication cannot precede creation")
        return self


class StateTransaction(Protocol):
    """One explicit transaction spanning owned control-plane metadata."""

    journal: AuditJournal

    def __enter__(self) -> StateTransaction:
        """Acquire the transaction and verify expected aggregate versions."""

    def __exit__(self, exc_type, exc, traceback) -> None:
        """Rollback unless commit completed successfully."""

    def run_records(self) -> tuple[RunRecord, ...]:
        """Return committed plus transaction-staged run records."""

    def append_run_record(
        self,
        *,
        stage: RunRecordStage,
        payload: dict[str, Any],
    ) -> RunRecord:
        """Stage the next run record."""

    def append_transition_record(self, record: TransitionRecord) -> None:
        """Stage an exact Kernel-produced transition after a long-running action."""

    def add_artifact(self, manifest: ArtifactManifest) -> None:
        """Stage immutable artifact metadata after object bytes are durable."""

    def enqueue(self, event: OutboxEvent) -> None:
        """Stage an outbox event in the same commit."""

    def commit(self) -> None:
        """Atomically commit all staged metadata exactly once."""


class ControlPlaneStore(Protocol):
    """Provider-neutral durable state, optimistic concurrency, and outbox port."""

    def migrate(self) -> None:
        """Apply and verify ordered schema migrations."""

    def transaction(
        self,
        *,
        task_id: str,
        run_id: str,
        expected_task_sequence: int,
        expected_run_sequence: int,
    ) -> StateTransaction:
        """Create an explicit transaction bound to expected aggregate versions."""

    def task_history(self, task_id: str) -> tuple[TransitionRecord, ...]:
        """Read and integrity-check one task history."""

    def run_history(self, run_id: str) -> tuple[RunRecord, ...]:
        """Read and integrity-check one run history."""

    def artifacts_for_run(self, run_id: str) -> tuple[ArtifactManifest, ...]:
        """Read immutable artifact metadata for one run."""

    def list_run_ids(self, *, limit: int = 1000) -> tuple[str, ...]:
        """List persisted run aggregates in deterministic identifier order."""

    def events_for_run(self, run_id: str) -> tuple[OutboxEvent, ...]:
        """Read all committed outbox events for one run, including published ones."""

    def pending_outbox(self, *, limit: int = 100) -> tuple[OutboxEvent, ...]:
        """Read unpublished events in stable commit order."""

    def mark_outbox_published(
        self,
        event_id: str,
        *,
        published_at: datetime | None = None,
    ) -> bool:
        """Idempotently mark an event delivered; return whether it existed."""


__all__ = [
    "ControlPlaneStore",
    "OutboxEvent",
    "StateConflictError",
    "StateIntegrityError",
    "StateStoreError",
    "StateTransaction",
]
