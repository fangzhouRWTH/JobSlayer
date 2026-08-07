from __future__ import annotations

from typing import Protocol

from jobslayer.domain.models import HumanDecision


class DecisionStore(Protocol):
    """Persist a human decision without granting authority to apply it."""

    def load(self) -> HumanDecision | None:
        """Return the existing record, if any."""

    def create(self, decision: HumanDecision) -> None:
        """Create one record and refuse to replace an existing decision."""
