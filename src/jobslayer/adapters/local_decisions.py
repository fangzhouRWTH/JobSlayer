from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from jobslayer.domain.models import HumanDecision


class DecisionStoreError(RuntimeError):
    """Base error for local human-decision persistence."""


class DecisionRecordExistsError(DecisionStoreError):
    pass


class DecisionRecordInvalidError(DecisionStoreError):
    pass


class LocalDecisionStore:
    """Single-record JSON store with create-only write semantics."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> HumanDecision | None:
        if not self.path.exists():
            return None
        try:
            return HumanDecision.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            raise DecisionRecordInvalidError(
                f"existing decision record is invalid: {self.path}"
            ) from exc

    def create(self, decision: HumanDecision) -> None:
        serialized = (decision.model_dump_json(indent=2) + "\n").encode("utf-8")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise DecisionRecordExistsError(
                f"refusing to overwrite an existing decision record: {self.path}"
            ) from exc
        except OSError as exc:
            raise DecisionStoreError(
                f"could not write decision record: {self.path}"
            ) from exc
