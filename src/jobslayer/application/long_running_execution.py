"""Durable control-plane service for resumable long-running Agent work."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.long_running import (
    BudgetEnforcement,
    LongRunBudgetDimension,
    LongRunError,
    LongRunEventType,
    LongRunObservationResult,
    LongRunStatus,
    LongRunStore,
    LongRunUsage,
    LongRunningExecutionPolicy,
    ProgressCheckpoint,
    ProviderRunObservation,
    ProviderRunReference,
    ProviderRunStatus,
    ProviderStartRequest,
    ResumableRunHandle,
    build_long_run_event,
)
from jobslayer.workers import (
    WorkerLeaseError,
    WorkerLeaseStatus,
    WorkerLeaseStore,
)


class LongRunningExecutionError(LongRunError):
    pass


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class LongRunningExecutionService:
    """Own lifecycle truth while provider adapters only report observations."""

    producer = "long-running-execution-service"

    def __init__(
        self,
        store: LongRunStore,
        worker_leases: WorkerLeaseStore,
        artifacts: ArtifactRegistry,
    ):
        self.store = store
        self.worker_leases = worker_leases
        self.artifacts = artifacts

    def admit(
        self,
        *,
        run_id: str,
        task_id: str,
        policy: LongRunningExecutionPolicy,
        now: datetime | None = None,
    ) -> ResumableRunHandle:
        when = self._when(now)
        handle = ResumableRunHandle(
            run_id=run_id,
            task_id=task_id,
            policy=policy,
            status=LongRunStatus.ADMITTED,
            version=1,
            attempt_number=1,
            provider_start_key=f"provider-start-{uuid4().hex}",
            created_at=when,
            updated_at=when,
            last_progress_at=when,
        )
        event = build_long_run_event(
            handle,
            LongRunEventType.ADMITTED,
            previous_hash=None,
            details={"attempt_number": 1, "policy_id": policy.policy_id},
            created_at=when,
        )
        return self.store.create(event)

    def start_request(self, run_id: str) -> ProviderStartRequest:
        """Return the persisted identity an adapter must use before side effects."""

        current = self._required(run_id)
        if current.status is not LongRunStatus.ADMITTED:
            raise LongRunningExecutionError(
                "only an admitted run has a provider start request"
            )
        return ProviderStartRequest(
            run_id=current.run_id,
            task_id=current.task_id,
            attempt_number=current.attempt_number,
            provider_start_key=current.provider_start_key,
        )

    def bind_provider(
        self,
        run_id: str,
        *,
        reference: ProviderRunReference,
        worker_id: str,
        now: datetime | None = None,
    ) -> ResumableRunHandle:
        current = self._required(run_id)
        when = self._when(now)
        if current.status is not LongRunStatus.ADMITTED:
            raise LongRunningExecutionError("only an admitted run can bind a provider")
        self._require_monotonic_time(current, when)
        if reference.started_at < current.created_at or reference.started_at > when:
            raise LongRunningExecutionError("provider start time is outside admission")
        if reference.provider_start_key != current.provider_start_key:
            raise LongRunningExecutionError("provider start identity mismatch")
        self._validate_artifact_ids(
            current,
            (reference.start_evidence_artifact_id,),
            error_prefix="provider start evidence",
        )
        if current.attempt_number > current.policy.max_attempts:
            raise LongRunningExecutionError("long-run attempt budget is exhausted")
        self._require_not_past_task_deadline(current, when)
        try:
            lease = self.worker_leases.acquire(
                worker_id=worker_id,
                run_id=run_id,
                lease_seconds=current.policy.lease_seconds,
                now=when,
            )
        except WorkerLeaseError as exc:
            raise LongRunningExecutionError("worker lease rejected provider binding") from exc
        handle = current.model_copy(
            update={
                "status": LongRunStatus.RUNNING,
                "version": current.version + 1,
                "provider_run": reference,
                "worker_lease_id": lease.lease_id,
                "worker_lease_version": lease.version,
                "event_cursor": 0,
                "attempt_usage": LongRunUsage(),
                "updated_at": when,
                "last_progress_at": when,
            }
        )
        try:
            return self._append(
                current,
                handle,
                LongRunEventType.PROVIDER_BOUND,
                details={
                    "provider_adapter": reference.provider_adapter,
                    "external_run_id": reference.external_run_id,
                    "provider_start_key": reference.provider_start_key,
                    "start_evidence_artifact_id": (
                        reference.start_evidence_artifact_id
                    ),
                    "worker_id": worker_id,
                    "attempt_number": handle.attempt_number,
                },
                created_at=when,
            )
        except Exception:
            try:
                self.worker_leases.release(
                    lease.lease_id,
                    expected_version=lease.version,
                    now=when,
                )
            except WorkerLeaseError:
                pass
            raise

    def observe(
        self,
        run_id: str,
        observation: ProviderRunObservation,
        *,
        made_progress: bool = False,
        stage: str | None = None,
    ) -> LongRunObservationResult:
        current = self._required(run_id)
        if current.status not in {
            LongRunStatus.RUNNING,
            LongRunStatus.CANCEL_REQUESTED,
        }:
            raise LongRunningExecutionError("only a live run accepts observations")
        if observation.status not in {
            ProviderRunStatus.QUEUED,
            ProviderRunStatus.RUNNING,
        }:
            raise LongRunningExecutionError(
                "terminal provider observations must use finish"
            )
        usage, attempt_usage = self._merge_usage(current, observation)
        hard, soft = self._limits(current, usage, attempt_usage)
        when = observation.observed_at
        stalled = (
            not made_progress
            and int((when - current.last_progress_at).total_seconds() * 1000)
            >= current.policy.progress_warning_after_ms
        )
        status = current.status
        lease_version = current.worker_lease_version
        if hard and current.status is LongRunStatus.RUNNING:
            lease = self._request_lease_cancel(current, when)
            status = LongRunStatus.CANCEL_REQUESTED
            lease_version = lease.version
        elif current.status is LongRunStatus.RUNNING:
            lease = self._heartbeat(current, when)
            lease_version = lease.version
        event_type = LongRunEventType.OBSERVED
        if hard:
            event_type = LongRunEventType.LIMIT_EXCEEDED
        elif soft:
            event_type = LongRunEventType.LIMIT_WARNING
        elif stalled:
            event_type = LongRunEventType.PROGRESS_STALLED
        handle = current.model_copy(
            update={
                "status": status,
                "version": current.version + 1,
                "worker_lease_version": lease_version,
                "event_cursor": observation.event_cursor,
                "usage": usage,
                "attempt_usage": attempt_usage,
                "hard_limit_dimensions": self._merged_dimensions(
                    current.hard_limit_dimensions, hard
                ),
                "soft_limit_dimensions": self._merged_dimensions(
                    current.soft_limit_dimensions, soft
                ),
                "updated_at": when,
                "last_progress_at": when if made_progress else current.last_progress_at,
            }
        )
        persisted = self._append(
            current,
            handle,
            event_type,
            details={
                "provider_status": observation.status.value,
                "event_cursor": observation.event_cursor,
                "raw_event_artifact_ids": list(
                    observation.raw_event_artifact_ids
                ),
                "made_progress": made_progress,
                "stage": stage,
                "hard_exceeded": [item.value for item in hard],
                "soft_exceeded": [item.value for item in soft],
                "progress_stalled": stalled,
            },
            created_at=when,
        )
        return LongRunObservationResult(
            handle=persisted,
            cancel_required=bool(hard),
            progress_stalled=stalled,
            hard_exceeded=hard,
            soft_exceeded=soft,
        )

    def checkpoint(
        self,
        run_id: str,
        *,
        stage: str,
        summary: str,
        referenced_artifact_ids: tuple[str, ...] = (),
        workspace_state_sha256: str | None = None,
        continuation_artifact_id: str | None = None,
        now: datetime | None = None,
    ) -> ProgressCheckpoint:
        current = self._required(run_id)
        when = self._when(now)
        if current.status is not LongRunStatus.RUNNING:
            raise LongRunningExecutionError("only a running long run can checkpoint")
        self._require_monotonic_time(current, when)
        self._validate_artifact_references(
            current,
            referenced_artifact_ids,
            continuation_artifact_id,
        )
        sequence = current.checkpoint_count + 1
        payload = {
            "schema_version": "1.0",
            "run_id": run_id,
            "task_id": current.task_id,
            "checkpoint_sequence": sequence,
            "attempt_number": current.attempt_number,
            "event_cursor": current.event_cursor,
            "stage": stage,
            "summary": summary,
            "referenced_artifact_ids": list(referenced_artifact_ids),
            "workspace_state_sha256": workspace_state_sha256,
            "continuation_artifact_id": continuation_artifact_id,
            "usage": current.usage.model_dump(mode="json"),
            "created_at": when.isoformat(),
        }
        manifest = self.artifacts.register_bytes(
            task_id=current.task_id,
            run_id=run_id,
            artifact_type="long_run.progress_checkpoint",
            producer=self.producer,
            content=_canonical(payload),
            metadata={
                "checkpoint_sequence": sequence,
                "attempt_number": current.attempt_number,
                "event_cursor": current.event_cursor,
            },
        )
        checkpoint = ProgressCheckpoint(
            checkpoint_id=f"checkpoint-{uuid4().hex}",
            run_id=run_id,
            sequence=sequence,
            attempt_number=current.attempt_number,
            event_cursor=current.event_cursor,
            stage=stage,
            summary=summary,
            referenced_artifact_ids=referenced_artifact_ids,
            workspace_state_sha256=workspace_state_sha256,
            continuation_artifact_id=continuation_artifact_id,
            checkpoint_artifact_id=manifest.artifact_id,
            checkpoint_sha256=manifest.sha256,
            usage=current.usage,
            created_at=when,
        )
        lease = self._heartbeat(current, when)
        handle = current.model_copy(
            update={
                "version": current.version + 1,
                "checkpoint_count": sequence,
                "worker_lease_version": lease.version,
                "updated_at": when,
                "last_progress_at": when,
            }
        )
        self._append(
            current,
            handle,
            LongRunEventType.CHECKPOINTED,
            details={
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_artifact_id": manifest.artifact_id,
                "stage": stage,
            },
            created_at=when,
            checkpoint=checkpoint,
        )
        return checkpoint

    def request_cancel(
        self, run_id: str, *, reason: str, now: datetime | None = None
    ) -> ResumableRunHandle:
        current = self._required(run_id)
        when = self._when(now)
        if current.status is not LongRunStatus.RUNNING:
            raise LongRunningExecutionError("only a running long run accepts cancellation")
        self._require_monotonic_time(current, when)
        lease = self._request_lease_cancel(current, when)
        handle = current.model_copy(
            update={
                "status": LongRunStatus.CANCEL_REQUESTED,
                "version": current.version + 1,
                "worker_lease_version": lease.version,
                "updated_at": when,
            }
        )
        return self._append(
            current,
            handle,
            LongRunEventType.CANCEL_REQUESTED,
            details={"reason": reason},
            created_at=when,
        )

    def finish(
        self,
        run_id: str,
        observation: ProviderRunObservation,
    ) -> ResumableRunHandle:
        current = self._required(run_id)
        if current.status not in {
            LongRunStatus.RUNNING,
            LongRunStatus.CANCEL_REQUESTED,
        }:
            raise LongRunningExecutionError("only a live run can become terminal")
        if observation.status in {
            ProviderRunStatus.QUEUED,
            ProviderRunStatus.RUNNING,
        }:
            raise LongRunningExecutionError("finish requires a terminal provider status")
        usage, attempt_usage = self._merge_usage(current, observation)
        hard, soft = self._limits(current, usage, attempt_usage)
        status, event_type = self._terminal_status(observation.status, bool(hard))
        lease_version = self._release_lease(current, observation.observed_at)
        handle = current.model_copy(
            update={
                "status": status,
                "version": current.version + 1,
                "worker_lease_version": lease_version,
                "event_cursor": observation.event_cursor,
                "usage": usage,
                "attempt_usage": attempt_usage,
                "hard_limit_dimensions": self._merged_dimensions(
                    current.hard_limit_dimensions, hard
                ),
                "soft_limit_dimensions": self._merged_dimensions(
                    current.soft_limit_dimensions, soft
                ),
                "updated_at": observation.observed_at,
            }
        )
        return self._append(
            current,
            handle,
            event_type,
            details={
                "provider_status": observation.status.value,
                "raw_event_artifact_ids": list(
                    observation.raw_event_artifact_ids
                ),
                "hard_exceeded": [item.value for item in hard],
                "soft_exceeded": [item.value for item in soft],
            },
            created_at=observation.observed_at,
        )

    def recover(
        self,
        run_id: str,
        observation: ProviderRunObservation,
        *,
        worker_id: str,
    ) -> ResumableRunHandle:
        current = self._required(run_id)
        when = observation.observed_at
        if current.status is not LongRunStatus.RUNNING:
            raise LongRunningExecutionError("only a running orphan can be recovered")
        self._validate_observation_identity(current, observation)
        self._validate_artifact_ids(
            current,
            observation.raw_event_artifact_ids,
            error_prefix="provider observation evidence",
        )
        if observation.status not in {
            ProviderRunStatus.QUEUED,
            ProviderRunStatus.RUNNING,
        }:
            raise LongRunningExecutionError(
                "terminal recovery evidence must be reconciled with finish"
            )
        assert current.worker_lease_id is not None
        lease = self.worker_leases.get(current.worker_lease_id)
        if lease is None:
            raise LongRunningExecutionError("recorded worker lease is missing")
        if lease.is_live(when):
            raise LongRunningExecutionError("live worker lease prevents recovery takeover")
        self.worker_leases.recover_orphans(now=when)
        lease = self.worker_leases.get(current.worker_lease_id)
        if lease is None or lease.status is not WorkerLeaseStatus.EXPIRED:
            raise LongRunningExecutionError("orphan worker lease did not expire")
        usage, attempt_usage = self._merge_usage(current, observation)
        hard, _soft = self._limits(current, usage, attempt_usage)
        if hard:
            raise LongRunningExecutionError("over-budget orphan cannot be resumed")
        try:
            replacement = self.worker_leases.acquire(
                worker_id=worker_id,
                run_id=run_id,
                lease_seconds=current.policy.lease_seconds,
                now=when,
            )
        except WorkerLeaseError as exc:
            raise LongRunningExecutionError("replacement worker lease was rejected") from exc
        made_progress = observation.event_cursor > current.event_cursor
        handle = current.model_copy(
            update={
                "version": current.version + 1,
                "worker_lease_id": replacement.lease_id,
                "worker_lease_version": replacement.version,
                "event_cursor": observation.event_cursor,
                "usage": usage,
                "attempt_usage": attempt_usage,
                "updated_at": when,
                "last_progress_at": when if made_progress else current.last_progress_at,
            }
        )
        return self._append(
            current,
            handle,
            LongRunEventType.RECOVERED,
            details={
                "expired_lease_id": lease.lease_id,
                "replacement_lease_id": replacement.lease_id,
                "worker_id": worker_id,
                "attempt_number": current.attempt_number,
            },
            created_at=when,
        )

    def authorize_retry(
        self, run_id: str, *, reason: str, now: datetime | None = None
    ) -> ResumableRunHandle:
        current = self._required(run_id)
        when = self._when(now)
        if current.status not in {LongRunStatus.FAILED, LongRunStatus.LOST}:
            raise LongRunningExecutionError("only failed or lost work can retry")
        self._require_monotonic_time(current, when)
        if current.attempt_number >= current.policy.max_attempts:
            raise LongRunningExecutionError("long-run attempt budget is exhausted")
        self._require_not_past_task_deadline(current, when)
        handle = current.model_copy(
            update={
                "status": LongRunStatus.ADMITTED,
                "version": current.version + 1,
                "attempt_number": current.attempt_number + 1,
                "provider_start_key": f"provider-start-{uuid4().hex}",
                "provider_run": None,
                "worker_lease_id": None,
                "worker_lease_version": None,
                "event_cursor": 0,
                "attempt_usage": LongRunUsage(),
                "updated_at": when,
                "last_progress_at": when,
            }
        )
        return self._append(
            current,
            handle,
            LongRunEventType.RETRY_AUTHORIZED,
            details={"reason": reason, "attempt_number": handle.attempt_number},
            created_at=when,
        )

    def _required(self, run_id: str) -> ResumableRunHandle:
        handle = self.store.get(run_id)
        if handle is None:
            raise LongRunningExecutionError("long run does not exist")
        return handle

    def _append(
        self,
        current: ResumableRunHandle,
        updated: ResumableRunHandle,
        event_type: LongRunEventType,
        *,
        details: dict[str, Any],
        created_at: datetime,
        checkpoint: ProgressCheckpoint | None = None,
    ) -> ResumableRunHandle:
        history = self.store.history(current.run_id)
        if not history or history[-1].handle != current:
            raise LongRunningExecutionError("long-run projection changed before append")
        event = build_long_run_event(
            updated,
            event_type,
            previous_hash=history[-1].record_hash,
            details=details,
            created_at=created_at,
        )
        return self.store.append(
            event,
            expected_version=current.version,
            checkpoint=checkpoint,
        )

    def _merge_usage(
        self,
        current: ResumableRunHandle,
        observation: ProviderRunObservation,
    ) -> tuple[LongRunUsage, LongRunUsage]:
        self._validate_observation_identity(current, observation)
        self._validate_artifact_ids(
            current,
            observation.raw_event_artifact_ids,
            error_prefix="provider observation evidence",
        )
        if observation.observed_at < current.updated_at:
            raise LongRunningExecutionError("provider observation moved backwards in time")
        if observation.event_cursor < current.event_cursor:
            raise LongRunningExecutionError("provider event cursor moved backwards")
        prior_attempt = current.attempt_usage
        for name in LongRunUsage.model_fields:
            if name in {
                LongRunBudgetDimension.TASK_ELAPSED_MS.value,
                LongRunBudgetDimension.ATTEMPT_ELAPSED_MS.value,
            }:
                continue
            if getattr(observation.usage, name) < getattr(prior_attempt, name):
                raise LongRunningExecutionError(
                    f"provider usage counter moved backwards: {name}"
                )
        elapsed = max(
            current.usage.task_elapsed_ms,
            int((observation.observed_at - current.created_at).total_seconds() * 1000),
        )
        cumulative = current.usage.model_dump()
        attempt = observation.usage.model_dump()
        for name in LongRunUsage.model_fields:
            if name in {
                LongRunBudgetDimension.TASK_ELAPSED_MS.value,
                LongRunBudgetDimension.ATTEMPT_ELAPSED_MS.value,
            }:
                continue
            delta = attempt[name] - getattr(prior_attempt, name)
            cumulative[name] += delta
        assert current.provider_run is not None
        attempt_elapsed = max(
            current.attempt_usage.attempt_elapsed_ms,
            int(
                (
                    observation.observed_at - current.provider_run.started_at
                ).total_seconds()
                * 1000
            ),
        )
        cumulative[LongRunBudgetDimension.ATTEMPT_ELAPSED_MS.value] += (
            attempt_elapsed - current.attempt_usage.attempt_elapsed_ms
        )
        cumulative[LongRunBudgetDimension.TASK_ELAPSED_MS.value] = elapsed
        attempt[LongRunBudgetDimension.TASK_ELAPSED_MS.value] = elapsed
        attempt[LongRunBudgetDimension.ATTEMPT_ELAPSED_MS.value] = attempt_elapsed
        return LongRunUsage(**cumulative), LongRunUsage(**attempt)

    @staticmethod
    def _limits(
        current: ResumableRunHandle,
        usage: LongRunUsage,
        attempt_usage: LongRunUsage,
    ) -> tuple[
        tuple[LongRunBudgetDimension, ...],
        tuple[LongRunBudgetDimension, ...],
    ]:
        hard: list[LongRunBudgetDimension] = []
        soft: list[LongRunBudgetDimension] = []
        for limit in current.policy.limits:
            if limit.maximum is None:
                continue
            source = (
                attempt_usage
                if limit.dimension is LongRunBudgetDimension.ATTEMPT_ELAPSED_MS
                else usage
            )
            if source.value_for(limit.dimension) <= limit.maximum:
                continue
            if limit.enforcement is BudgetEnforcement.HARD:
                hard.append(limit.dimension)
            elif limit.enforcement is BudgetEnforcement.SOFT:
                soft.append(limit.dimension)
        return tuple(hard), tuple(soft)

    @staticmethod
    def _merged_dimensions(
        existing: tuple[LongRunBudgetDimension, ...],
        new: tuple[LongRunBudgetDimension, ...],
    ) -> tuple[LongRunBudgetDimension, ...]:
        return tuple(sorted(set(existing).union(new), key=lambda item: item.value))

    def _heartbeat(self, current: ResumableRunHandle, when: datetime):
        if current.worker_lease_id is None or current.worker_lease_version is None:
            raise LongRunningExecutionError("live run has no worker lease")
        try:
            return self.worker_leases.heartbeat(
                current.worker_lease_id,
                expected_version=current.worker_lease_version,
                lease_seconds=current.policy.lease_seconds,
                now=when,
            )
        except WorkerLeaseError as exc:
            raise LongRunningExecutionError("worker lease heartbeat failed") from exc

    def _request_lease_cancel(self, current: ResumableRunHandle, when: datetime):
        if current.worker_lease_id is None or current.worker_lease_version is None:
            raise LongRunningExecutionError("live run has no worker lease")
        try:
            return self.worker_leases.request_cancel(
                current.worker_lease_id,
                expected_version=current.worker_lease_version,
                now=when,
            )
        except WorkerLeaseError as exc:
            raise LongRunningExecutionError("worker lease cancellation failed") from exc

    def _release_lease(self, current: ResumableRunHandle, when: datetime) -> int:
        if current.worker_lease_id is None or current.worker_lease_version is None:
            raise LongRunningExecutionError("live run has no worker lease")
        try:
            lease = self.worker_leases.release(
                current.worker_lease_id,
                expected_version=current.worker_lease_version,
                now=when,
            )
        except WorkerLeaseError as exc:
            raise LongRunningExecutionError("worker lease release failed") from exc
        return lease.version

    def _validate_observation_identity(
        self,
        current: ResumableRunHandle,
        observation: ProviderRunObservation,
    ) -> None:
        reference = current.provider_run
        if reference is None:
            raise LongRunningExecutionError("long run has no provider binding")
        if (
            observation.provider_adapter != reference.provider_adapter
            or observation.external_run_id != reference.external_run_id
        ):
            raise LongRunningExecutionError("provider observation identity mismatch")

    def _validate_artifact_references(
        self,
        current: ResumableRunHandle,
        referenced_artifact_ids: tuple[str, ...],
        continuation_artifact_id: str | None,
    ) -> None:
        if len(referenced_artifact_ids) != len(set(referenced_artifact_ids)):
            raise LongRunningExecutionError("checkpoint artifact references are duplicated")
        artifact_ids = referenced_artifact_ids + (
            (continuation_artifact_id,) if continuation_artifact_id else ()
        )
        self._validate_artifact_ids(
            current,
            artifact_ids,
            error_prefix="checkpoint artifact",
        )

    def _validate_artifact_ids(
        self,
        current: ResumableRunHandle,
        artifact_ids: tuple[str, ...],
        *,
        error_prefix: str,
    ) -> None:
        for artifact_id in artifact_ids:
            try:
                manifest = self.artifacts.get(artifact_id)
            except Exception as exc:
                raise LongRunningExecutionError(
                    f"{error_prefix} references an unknown artifact"
                ) from exc
            if manifest.task_id != current.task_id or manifest.run_id != current.run_id:
                raise LongRunningExecutionError(
                    f"{error_prefix} does not belong to the long run"
                )
            if not self.artifacts.verify(manifest):
                raise LongRunningExecutionError(
                    f"{error_prefix} integrity check failed"
                )

    @staticmethod
    def _terminal_status(
        provider_status: ProviderRunStatus, hard_exceeded: bool
    ) -> tuple[LongRunStatus, LongRunEventType]:
        if provider_status is ProviderRunStatus.MISSING:
            return LongRunStatus.LOST, LongRunEventType.LOST
        if provider_status is ProviderRunStatus.CANCELLED:
            return LongRunStatus.CANCELLED, LongRunEventType.CANCELLED
        if provider_status is ProviderRunStatus.COMPLETED and not hard_exceeded:
            return LongRunStatus.COMPLETED, LongRunEventType.COMPLETED
        return LongRunStatus.FAILED, LongRunEventType.FAILED

    @staticmethod
    def _when(value: datetime | None) -> datetime:
        when = value or datetime.now(UTC)
        if when.tzinfo is None:
            raise LongRunningExecutionError("long-run time must include a timezone")
        return when

    @staticmethod
    def _require_not_past_task_deadline(
        current: ResumableRunHandle, when: datetime
    ) -> None:
        limit = current.policy.limit_for(LongRunBudgetDimension.TASK_ELAPSED_MS)
        assert limit is not None and limit.maximum is not None
        elapsed = int((when - current.created_at).total_seconds() * 1000)
        if elapsed > limit.maximum:
            raise LongRunningExecutionError("long-run task elapsed limit has passed")

    @staticmethod
    def _require_monotonic_time(
        current: ResumableRunHandle, when: datetime
    ) -> None:
        if when < current.updated_at:
            raise LongRunningExecutionError("long-run time moved backwards")


__all__ = ["LongRunningExecutionError", "LongRunningExecutionService"]
