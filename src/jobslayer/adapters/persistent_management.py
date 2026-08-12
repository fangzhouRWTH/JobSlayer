"""Read-only management projection over the transactional control-plane store."""

from __future__ import annotations

from collections import Counter
import json
import time
from typing import Any

from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.domain.models import TaskExecutionIntent, TaskExecutionOutcome, TaskState
from jobslayer.management import (
    InvalidRunSummary,
    ManagedRunSummary,
    ManagementQueryError,
    ManagementSnapshot,
)
from jobslayer.observability import NoopTelemetrySink, TelemetrySink
from jobslayer.persistence import ControlPlaneStore


class PersistentManagementQuery:
    """Build dashboard views exclusively from committed persistent truth."""

    source_kind = "transactional_control_plane"

    def __init__(
        self,
        store: ControlPlaneStore,
        artifacts: ArtifactRegistry,
        *,
        source_name: str,
        telemetry: TelemetrySink | None = None,
        maximum_runs: int = 1000,
    ):
        if maximum_runs < 1 or maximum_runs > 10_000:
            raise ValueError("maximum runs must be between 1 and 10000")
        self.store = store
        self.artifacts = artifacts
        self.source_name = source_name
        self.telemetry = telemetry or NoopTelemetrySink()
        self.maximum_runs = maximum_runs

    def snapshot(self) -> ManagementSnapshot:
        started = time.perf_counter()
        summaries: list[ManagedRunSummary] = []
        invalid: list[InvalidRunSummary] = []
        try:
            run_ids = self.store.list_run_ids(limit=self.maximum_runs)
        except (RuntimeError, ValueError) as exc:
            raise ManagementQueryError("persistent run index is unavailable") from exc
        for run_id in run_ids:
            try:
                summaries.append(self._summary(run_id))
            except (RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                invalid.append(InvalidRunSummary(run_id=run_id, reason=str(exc)))
        states = Counter(item.state for item in summaries)
        executors = Counter(item.executor_type for item in summaries)
        snapshot = ManagementSnapshot(
            state_root=self.source_name,
            runs=tuple(summaries),
            invalid_runs=tuple(invalid),
            state_counts=dict(sorted(states.items())),
            executor_counts=dict(sorted(executors.items())),
            total_input_tokens=sum(item.input_tokens for item in summaries),
            total_cached_input_tokens=sum(item.cached_input_tokens for item in summaries),
            total_output_tokens=sum(item.output_tokens for item in summaries),
            total_cost_microusd=sum(item.cost_microusd for item in summaries),
        )
        self.telemetry.record(
            "jobslayer.management.snapshot",
            {
                "jobslayer.runs.count": len(snapshot.runs),
                "jobslayer.runs.invalid_count": len(snapshot.invalid_runs),
                "jobslayer.query.duration_ms": int(
                    (time.perf_counter() - started) * 1000
                ),
                "jobslayer.query.source": "transactional_control_plane",
            },
        )
        return snapshot

    def run_detail(self, run_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        self._validate_run_id(run_id)
        try:
            summary = self._summary(run_id)
            workflow = self.store.task_history(summary.task_id)
            records = self.store.run_history(run_id)
            manifests = self.store.artifacts_for_run(run_id)
            events = self.store.events_for_run(run_id)
        except (RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ManagementQueryError(
                "persistent run detail is unavailable or invalid"
            ) from exc
        detail = {
            "schema_version": "1.0",
            "summary": summary.model_dump(mode="json"),
            "workflow": [item.model_dump(mode="json") for item in workflow],
            "run_records": [item.model_dump(mode="json") for item in records],
            "artifacts": [item.model_dump(mode="json") for item in manifests],
            "events": [item.model_dump(mode="json") for item in events],
        }
        self.telemetry.record(
            "jobslayer.management.run_detail",
            {
                "jobslayer.run.id": run_id,
                "jobslayer.workflow.event_count": len(workflow),
                "jobslayer.artifact.count": len(manifests),
                "jobslayer.query.duration_ms": int(
                    (time.perf_counter() - started) * 1000
                ),
                "jobslayer.query.source": "transactional_control_plane",
            },
        )
        return detail

    def _summary(self, run_id: str) -> ManagedRunSummary:
        self._validate_run_id(run_id)
        records = self.store.run_history(run_id)
        manifests = self.store.artifacts_for_run(run_id)
        if not records and not manifests:
            raise ManagementQueryError("persistent run does not exist")
        if not manifests or not all(self.artifacts.verify(item) for item in manifests):
            raise ManagementQueryError("one or more persisted artifacts failed integrity")

        task_id: str
        title: str
        executor_type: str
        executor_status: str
        usage: dict[str, Any]
        review_status = None
        decision_recorded = False
        decision_applied = False
        if records:
            execution = records[0]
            raw_intent = execution.payload.get("intent")
            raw_outcome = execution.payload.get("outcome")
            if not isinstance(raw_intent, dict) or not isinstance(raw_outcome, dict):
                raise ManagementQueryError(
                    "execution record lacks typed intent or outcome"
                )
            intent = TaskExecutionIntent.model_validate(raw_intent)
            outcome = TaskExecutionOutcome.model_validate(raw_outcome)
            task_id = intent.task.task_id
            title = intent.task.title
            executor_type = outcome.agent_run.executor_type
            executor_status = outcome.agent_run.status.value
            usage = outcome.agent_run.usage
            for record in records[1:]:
                if record.stage.value == "implementation_review":
                    report = record.payload.get("review_report") or record.payload.get("review")
                    if isinstance(report, dict):
                        review_status = report.get("status")
                elif record.stage.value == "decision_application":
                    decision_recorded = True
                    decision_applied = bool(record.payload.get("applied", True))
        else:
            intent_manifest = next(
                (
                    item
                    for item in manifests
                    if item.artifact_type == "task-execution-intent"
                ),
                None,
            )
            if intent_manifest is None:
                raise ManagementQueryError("intent-only run lacks its intent artifact")
            intent = TaskExecutionIntent.model_validate_json(
                self.artifacts.read(intent_manifest)
            )
            task_id = intent.task.task_id
            title = intent.task.title
            executor_type = intent.invocation.run_spec.executor_type
            executor_status = "intent_only"
            usage = {}
        workflow = self.store.task_history(task_id)
        state = workflow[-1].to_state.value if workflow else TaskState.DRAFT.value
        stage = records[-1].stage.value if records else "execution_intent"
        return ManagedRunSummary(
            run_id=run_id,
            task_id=task_id,
            title=title,
            state=state,
            stage=stage,
            executor_type=executor_type,
            executor_status=executor_status,
            input_tokens=self._usage(usage, "input_tokens"),
            cached_input_tokens=self._usage(usage, "cached_input_tokens"),
            output_tokens=self._usage(usage, "output_tokens"),
            cost_microusd=self._usage(usage, "cost_microusd"),
            review_status=review_status,
            decision_recorded=decision_recorded,
            decision_applied=decision_applied,
            artifacts_valid=True,
            workflow_valid=True,
            run_record_valid=True,
        )

    @staticmethod
    def _usage(usage: dict[str, Any], name: str) -> int:
        value = usage.get(name, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ManagementQueryError("executor usage is invalid")
        return value

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
            raise ManagementQueryError("run id is invalid")


__all__ = ["PersistentManagementQuery"]
