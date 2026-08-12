"""Read-only management projections over integrity-checked local runs."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import time
from typing import Any

from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.application.local_run import LocalRunCoordinator
from jobslayer.application.run_records import LocalRunLedger
from jobslayer.management import (
    InvalidRunSummary,
    ManagedRunSummary,
    ManagementQueryError,
    ManagementSnapshot,
)
from jobslayer.observability import NoopTelemetrySink, TelemetrySink
from jobslayer.workflow.journal import JsonlAuditJournal


class LocalManagementQuery:
    source_kind = "persisted_local_events"

    def __init__(
        self,
        coordinator: LocalRunCoordinator,
        *,
        telemetry: TelemetrySink | None = None,
    ):
        self.coordinator = coordinator
        self.runs_root = coordinator.state_root / "runs"
        self.telemetry = telemetry or NoopTelemetrySink()

    def snapshot(self) -> ManagementSnapshot:
        started = time.perf_counter()
        summaries: list[ManagedRunSummary] = []
        invalid: list[InvalidRunSummary] = []
        if self.runs_root.exists() and (
            not self.runs_root.is_dir() or self.runs_root.is_symlink()
        ):
            raise ManagementQueryError("runs root is not a safe directory")
        directories = (
            sorted(
                item
                for item in self.runs_root.iterdir()
                if item.is_dir() and not item.is_symlink()
            )
            if self.runs_root.is_dir()
            else []
        )
        for directory in directories:
            try:
                raw = self.coordinator.inspect(directory)
                summaries.append(self._summary(raw))
            except (OSError, RuntimeError, ValueError) as exc:
                invalid.append(
                    InvalidRunSummary(run_id=directory.name, reason=str(exc))
                )
        states = Counter(item.state for item in summaries)
        executors = Counter(item.executor_type for item in summaries)
        snapshot = ManagementSnapshot(
            state_root=str(self.coordinator.state_root),
            runs=tuple(summaries),
            invalid_runs=tuple(invalid),
            state_counts=dict(sorted(states.items())),
            executor_counts=dict(sorted(executors.items())),
            total_input_tokens=sum(item.input_tokens for item in summaries),
            total_cached_input_tokens=sum(
                item.cached_input_tokens for item in summaries
            ),
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
                "jobslayer.query.source": "persisted_local_events",
            },
        )
        return snapshot

    def run_detail(self, run_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        if (
            not run_id
            or run_id in {".", ".."}
            or "/" in run_id
            or "\\" in run_id
        ):
            raise ManagementQueryError("run id is invalid")
        run_directory = self.runs_root / run_id
        try:
            resolved = run_directory.resolve(strict=True)
            resolved.relative_to(self.runs_root.resolve(strict=False))
            summary = self.coordinator.inspect(resolved)
            workflow = JsonlAuditJournal(resolved / "workflow.jsonl").records_for(
                summary["task_id"]
            )
            records = LocalRunLedger(
                resolved / "records.jsonl", run_id=run_id
            ).read_all()
            artifacts = LocalArtifactRegistry(resolved / "artifacts").list_manifests()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ManagementQueryError("run detail is unavailable or invalid") from exc
        detail = {
            "schema_version": "1.0",
            "summary": summary,
            "workflow": [item.model_dump(mode="json") for item in workflow],
            "run_records": [
                {
                    "sequence": item.sequence,
                    "stage": item.stage.value,
                    "recorded_at": item.recorded_at.isoformat(),
                    "record_hash": item.record_hash,
                }
                for item in records
            ],
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "artifact_type": item.artifact_type,
                    "producer": item.producer,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in artifacts
            ],
        }
        self.telemetry.record(
            "jobslayer.management.run_detail",
            {
                "jobslayer.run.id": run_id,
                "jobslayer.workflow.event_count": len(workflow),
                "jobslayer.artifact.count": len(artifacts),
                "jobslayer.query.duration_ms": int(
                    (time.perf_counter() - started) * 1000
                ),
            },
        )
        return detail

    @staticmethod
    def _summary(raw: dict[str, Any]) -> ManagedRunSummary:
        usage = raw["executor"].get("usage") or {}
        review = raw.get("review") or {}
        decision = raw["decision"]
        return ManagedRunSummary(
            run_id=raw["run_id"],
            task_id=raw["task_id"],
            title=raw["title"],
            state=raw["state"],
            stage=raw["stage"],
            executor_type=raw["executor"]["type"],
            executor_status=raw["executor"]["status"],
            input_tokens=max(0, int(usage.get("input_tokens", 0))),
            cached_input_tokens=max(0, int(usage.get("cached_input_tokens", 0))),
            output_tokens=max(0, int(usage.get("output_tokens", 0))),
            cost_microusd=max(0, int(usage.get("cost_microusd", 0))),
            review_status=review.get("status"),
            decision_recorded=bool(decision["recorded"]),
            decision_applied=bool(decision["applied"]),
            artifacts_valid=bool(raw["artifacts_valid"]),
            workflow_valid=bool(raw["audit_chain_valid"]),
            run_record_valid=bool(raw["record_chain_valid"]),
        )


__all__ = ["LocalManagementQuery"]
