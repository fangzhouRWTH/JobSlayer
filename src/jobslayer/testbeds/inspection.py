from __future__ import annotations

from typing import Protocol

from jobslayer.domain.models import TestbedInspection, TestbedSpec


class TestbedInspectionError(RuntimeError):
    """Raised when local testbed facts cannot be observed reliably."""


class TestbedInspector(Protocol):
    def inspect(self, testbed: TestbedSpec) -> TestbedInspection:
        """Observe a testbed without changing its repository or workflow state."""
