from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class RecoveryStatus(str, Enum):
    CONSISTENT = "consistent"
    RECOVERABLE = "recoverable"
    MANUAL_INTERVENTION = "manual_intervention"
    INVALID_EVIDENCE = "invalid_evidence"


@dataclass(frozen=True)
class RecoveryAssessment:
    run_id: str
    run_directory: str
    status: RecoveryStatus
    reason: str
    repair_action: str | None = None
    run_stage: str | None = None
    workflow_state: str | None = None

    @property
    def consistent(self) -> bool:
        return self.status is RecoveryStatus.CONSISTENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "run_directory": self.run_directory,
            "status": self.status.value,
            "consistent": self.consistent,
            "reason": self.reason,
            "repair_action": self.repair_action,
            "run_stage": self.run_stage,
            "workflow_state": self.workflow_state,
        }


class RecoveryError(RuntimeError):
    """Raised when recovery cannot proceed without weakening evidence rules."""


class RunRecoveryManager(Protocol):
    """Assess and repair only explicitly supported, evidence-backed gaps."""

    def assess(self, run_directory: str | Path) -> RecoveryAssessment:
        """Classify one persisted run without changing it."""

    def recover(self, run_directory: str | Path) -> RecoveryAssessment:
        """Apply one idempotent safe repair or refuse with RecoveryError."""


__all__ = [
    "RecoveryAssessment",
    "RecoveryError",
    "RecoveryStatus",
    "RunRecoveryManager",
]
