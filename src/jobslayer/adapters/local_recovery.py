from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jobslayer.application.local_run import LocalRunCoordinator, LocalRunError
from jobslayer.application.run_records import LocalRunLedger, RunRecordError
from jobslayer.domain.models import DecisionCard, ReviewDisposition
from jobslayer.recovery import (
    RecoveryAssessment,
    RecoveryError,
    RecoveryStatus,
)


class LocalRunRecoveryManager:
    """Recover local derived projections without owning workflow state."""

    _RESTORE_DECISION_CARD = "restore_decision_card"

    def __init__(self, coordinator: LocalRunCoordinator):
        self.coordinator = coordinator

    def assess(self, run_directory: str | Path) -> RecoveryAssessment:
        try:
            directory = self._resolve_run_directory(run_directory)
        except (OSError, RecoveryError) as exc:
            return RecoveryAssessment(
                run_id=Path(run_directory).name,
                run_directory=str(run_directory),
                status=RecoveryStatus.INVALID_EVIDENCE,
                reason=str(exc),
            )

        ledger = LocalRunLedger(directory / "records.jsonl", run_id=directory.name)
        try:
            records = ledger.read_all()
        except RunRecordError as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"run ledger integrity failed: {exc}",
            )
        if not records:
            return self._assessment(
                directory,
                RecoveryStatus.MANUAL_INTERVENTION,
                "run directory exists without an authoritative execution record",
            )

        try:
            summary = self.coordinator.inspect(directory)
        except (LocalRunError, OSError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.MANUAL_INTERVENTION,
                f"run stores do not form a consistent reconstructable snapshot: {exc}",
                run_stage=records[-1].stage.value,
            )
        run_stage = str(summary["stage"])
        workflow_state = str(summary["state"])
        if not all(
            summary.get(key) is True
            for key in ("record_chain_valid", "audit_chain_valid", "artifacts_valid")
        ):
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "run summary reports invalid ledger, journal, or artifact evidence",
                run_stage=run_stage,
                workflow_state=workflow_state,
            )

        try:
            expected_card = self._expected_decision_card(records)
        except (KeyError, TypeError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"review evidence cannot reconstruct its decision card: {exc}",
                run_stage=run_stage,
                workflow_state=workflow_state,
            )
        card_path = directory / "decision-card.json"
        if expected_card is None:
            if card_path.exists() or card_path.is_symlink():
                return self._assessment(
                    directory,
                    RecoveryStatus.INVALID_EVIDENCE,
                    "decision-card projection exists without merge-review evidence",
                    run_stage=run_stage,
                    workflow_state=workflow_state,
                )
        elif card_path.is_symlink():
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "decision-card projection must not be a symbolic link",
                run_stage=run_stage,
                workflow_state=workflow_state,
            )
        elif not card_path.exists():
            return self._assessment(
                directory,
                RecoveryStatus.RECOVERABLE,
                "authoritative merge-review evidence exists but decision-card projection is missing",
                repair_action=self._RESTORE_DECISION_CARD,
                run_stage=run_stage,
                workflow_state=workflow_state,
            )
        else:
            try:
                projected = DecisionCard.model_validate_json(
                    card_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, ValueError) as exc:
                return self._assessment(
                    directory,
                    RecoveryStatus.INVALID_EVIDENCE,
                    f"decision-card projection is invalid: {exc}",
                    run_stage=run_stage,
                    workflow_state=workflow_state,
                )
            if projected != expected_card:
                return self._assessment(
                    directory,
                    RecoveryStatus.INVALID_EVIDENCE,
                    "decision-card projection does not match authoritative review evidence",
                    run_stage=run_stage,
                    workflow_state=workflow_state,
                )

        return self._assessment(
            directory,
            RecoveryStatus.CONSISTENT,
            "run ledger, workflow journal, artifacts, and derived projections agree",
            run_stage=run_stage,
            workflow_state=workflow_state,
        )

    def recover(self, run_directory: str | Path) -> RecoveryAssessment:
        assessment = self.assess(run_directory)
        if assessment.status is RecoveryStatus.CONSISTENT:
            return assessment
        if (
            assessment.status is not RecoveryStatus.RECOVERABLE
            or assessment.repair_action != self._RESTORE_DECISION_CARD
        ):
            raise RecoveryError(
                f"run cannot be repaired automatically: {assessment.reason}"
            )

        directory = self._resolve_run_directory(run_directory)
        try:
            records = LocalRunLedger(
                directory / "records.jsonl", run_id=directory.name
            ).read_all()
            expected_card = self._expected_decision_card(records)
        except (KeyError, RunRecordError, TypeError, ValueError) as exc:
            raise RecoveryError(
                "authoritative review evidence changed during recovery"
            ) from exc
        if expected_card is None:
            raise RecoveryError("merge-review evidence disappeared during recovery")
        encoded = (
            json.dumps(
                expected_card.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ).encode("utf-8")
            + b"\n"
        )
        card_path = directory / "decision-card.json"
        try:
            descriptor = os.open(
                card_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            concurrent = self.assess(directory)
            if concurrent.status is RecoveryStatus.CONSISTENT:
                return concurrent
            raise RecoveryError(
                "decision-card projection appeared concurrently but is not trustworthy"
            )
        try:
            try:
                self._write_all(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            try:
                card_path.unlink()
            except OSError as cleanup_exc:
                raise RecoveryError(
                    "projection recovery failed and its incomplete file could not be removed"
                ) from cleanup_exc
            raise RecoveryError(
                "projection recovery failed; the incomplete file was removed"
            ) from exc

        recovered = self.assess(directory)
        if recovered.status is not RecoveryStatus.CONSISTENT:
            raise RecoveryError(
                f"recovery did not produce a consistent run: {recovered.reason}"
            )
        return recovered

    def _resolve_run_directory(self, run_directory: str | Path) -> Path:
        directory = Path(run_directory)
        if not directory.is_absolute():
            directory = self.coordinator.repository_root / directory
        directory = directory.resolve(strict=True)
        expected_root = (self.coordinator.state_root / "runs").resolve(strict=False)
        if not directory.is_dir() or not directory.is_relative_to(expected_root):
            raise RecoveryError("run directory must be inside the configured state root")
        return directory

    @staticmethod
    def _expected_decision_card(records: tuple[Any, ...]) -> DecisionCard | None:
        if len(records) < 2:
            return None
        disposition = ReviewDisposition.model_validate(
            records[1].payload["disposition"]
        )
        package = disposition.merge_review_package
        return None if package is None else package.decision_card

    @staticmethod
    def _write_all(descriptor: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("could not persist the complete recovered projection")
            offset += written

    @staticmethod
    def _assessment(
        directory: Path,
        status: RecoveryStatus,
        reason: str,
        *,
        repair_action: str | None = None,
        run_stage: str | None = None,
        workflow_state: str | None = None,
    ) -> RecoveryAssessment:
        return RecoveryAssessment(
            run_id=directory.name,
            run_directory=str(directory),
            status=status,
            reason=reason,
            repair_action=repair_action,
            run_stage=run_stage,
            workflow_state=workflow_state,
        )
