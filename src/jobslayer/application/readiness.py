from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ReadinessInspectionError(RuntimeError):
    """Raised when the local readiness corpus cannot be enumerated safely."""


class RunInspector(Protocol):
    """Read-only run inspection port used by the Phase 0 readiness gate."""

    def inspect(self, run_directory: str | Path) -> dict[str, Any]:
        """Verify one persisted run and return its normalized summary."""


@dataclass(frozen=True)
class InvalidRun:
    run_id: str
    path: str
    error: str

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "path": self.path,
            "error": self.error,
        }


@dataclass(frozen=True)
class Phase0ReadinessReport:
    state_root: str
    required_reviewed_tasks: int
    discovered_runs: int
    valid_runs: int
    reviewed_runs: int
    reviewed_tasks: int
    completed_runs: int
    decision_applied_completed_runs: int
    negative_path_runs: int
    unique_tasks: int
    state_counts: dict[str, int]
    invalid_runs: tuple[InvalidRun, ...]
    unmet_criteria: tuple[str, ...]

    @property
    def automated_gate_passes(self) -> bool:
        return not self.unmet_criteria

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "gate_id": "phase0-run-corpus-v1",
            "automated_gate_passes": self.automated_gate_passes,
            "manual_confirmation_required": True,
            "state_root": self.state_root,
            "requirements": {
                "reviewed_tasks": self.required_reviewed_tasks,
                "decision_applied_completed_runs": 1,
                "negative_path_runs": 1,
                "invalid_runs": 0,
            },
            "counts": {
                "discovered_runs": self.discovered_runs,
                "valid_runs": self.valid_runs,
                "reviewed_runs": self.reviewed_runs,
                "reviewed_tasks": self.reviewed_tasks,
                "completed_runs": self.completed_runs,
                "decision_applied_completed_runs": (
                    self.decision_applied_completed_runs
                ),
                "negative_path_runs": self.negative_path_runs,
                "unique_tasks": self.unique_tasks,
            },
            "state_counts": dict(sorted(self.state_counts.items())),
            "invalid_runs": [item.to_dict() for item in self.invalid_runs],
            "unmet_criteria": list(self.unmet_criteria),
            "manual_requirements": [
                "append a real human/operator experience retrospective to the development log",
                "confirm the same run-corpus gate in both Windows and POSIX CI",
            ],
        }


class Phase0ReadinessEvaluator:
    """Measure Phase 0 run evidence without changing workflow or run state."""

    _NEGATIVE_STATES = {"cancelled", "failed", "repairing"}

    def __init__(
        self,
        inspector: RunInspector,
        *,
        state_root: str | Path,
        required_reviewed_tasks: int = 20,
    ):
        if required_reviewed_tasks < 1:
            raise ValueError("required reviewed tasks must be at least 1")
        self.inspector = inspector
        self.state_root = Path(state_root).resolve(strict=False)
        self.required_reviewed_tasks = required_reviewed_tasks

    def evaluate(self) -> Phase0ReadinessReport:
        run_root = self.state_root / "runs"
        try:
            entries = () if not run_root.exists() else tuple(sorted(run_root.iterdir()))
        except OSError as exc:
            raise ReadinessInspectionError(
                f"could not enumerate persisted runs: {run_root}: {exc}"
            ) from exc

        valid_summaries: list[dict[str, Any]] = []
        invalid_runs: list[InvalidRun] = []
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                invalid_runs.append(
                    InvalidRun(
                        run_id=entry.name,
                        path=str(entry),
                        error="run entry must be a real directory inside the state root",
                    )
                )
                continue
            try:
                summary = self.inspector.inspect(entry)
                self._validate_summary(summary, expected_run_id=entry.name)
            except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
                invalid_runs.append(
                    InvalidRun(
                        run_id=entry.name,
                        path=str(entry),
                        error=str(exc),
                    )
                )
                continue
            valid_summaries.append(summary)

        reviewed_summaries = [
            item for item in valid_summaries if item["review"] is not None
        ]
        reviewed_runs = len(reviewed_summaries)
        reviewed_tasks = len({item["task_id"] for item in reviewed_summaries})
        completed_runs = sum(item["state"] == "completed" for item in valid_summaries)
        decision_applied_completed_runs = sum(
            item["state"] == "completed" and item["decision"]["applied"] is True
            for item in valid_summaries
        )
        negative_path_runs = sum(
            item["state"] in self._NEGATIVE_STATES for item in valid_summaries
        )
        task_ids = {item["task_id"] for item in valid_summaries}
        state_counts = Counter(item["state"] for item in valid_summaries)

        unmet: list[str] = []
        if invalid_runs:
            unmet.append("all discovered run records must pass integrity inspection")
        if reviewed_tasks < self.required_reviewed_tasks:
            unmet.append(
                "unique reviewed task corpus is below the required threshold "
                f"({reviewed_tasks}/{self.required_reviewed_tasks})"
            )
        if decision_applied_completed_runs < 1:
            unmet.append(
                "at least one run must complete after an applied decision and integration"
            )
        if negative_path_runs < 1:
            unmet.append(
                "at least one verified run must preserve a failed, repairing, or cancelled path"
            )

        return Phase0ReadinessReport(
            state_root=str(self.state_root),
            required_reviewed_tasks=self.required_reviewed_tasks,
            discovered_runs=len(entries),
            valid_runs=len(valid_summaries),
            reviewed_runs=reviewed_runs,
            reviewed_tasks=reviewed_tasks,
            completed_runs=completed_runs,
            decision_applied_completed_runs=decision_applied_completed_runs,
            negative_path_runs=negative_path_runs,
            unique_tasks=len(task_ids),
            state_counts=dict(state_counts),
            invalid_runs=tuple(invalid_runs),
            unmet_criteria=tuple(unmet),
        )

    @staticmethod
    def _validate_summary(summary: dict[str, Any], *, expected_run_id: str) -> None:
        if summary.get("run_id") != expected_run_id:
            raise ValueError("inspected run id does not match its directory")
        if not isinstance(summary.get("task_id"), str) or not summary["task_id"]:
            raise ValueError("inspected run has no valid task id")
        if not isinstance(summary.get("state"), str) or not summary["state"]:
            raise ValueError("inspected run has no valid workflow state")
        for key in ("record_chain_valid", "audit_chain_valid", "artifacts_valid"):
            if summary.get(key) is not True:
                raise ValueError(f"inspected run failed integrity condition: {key}")
        if "review" not in summary:
            raise ValueError("inspected run summary is missing review state")
        decision = summary.get("decision")
        if not isinstance(decision, dict) or not isinstance(
            decision.get("applied"), bool
        ):
            raise ValueError("inspected run summary is missing decision application state")
