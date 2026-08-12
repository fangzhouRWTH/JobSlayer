"""Provider-neutral telemetry boundary with an optional OpenTelemetry adapter."""

from __future__ import annotations

from typing import Any, Protocol


class TelemetrySink(Protocol):
    def record(self, name: str, attributes: dict[str, Any]) -> None:
        """Record one completed control-plane operation without owning its result."""


class NoopTelemetrySink:
    def record(self, name: str, attributes: dict[str, Any]) -> None:
        return


class OpenTelemetrySink:
    """Use the configured global OTel provider; API-only installs remain no-op."""

    def __init__(self, tracer=None):
        if tracer is None:
            try:
                from opentelemetry import trace
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "OpenTelemetry support requires the optional 'observability' dependency"
                ) from exc
            tracer = trace.get_tracer("jobslayer.control-plane", "0.1.0")
        self.tracer = tracer

    def record(self, name: str, attributes: dict[str, Any]) -> None:
        normalized = {
            key: value
            for key, value in attributes.items()
            if isinstance(value, (bool, str, int, float))
        }
        with self.tracer.start_as_current_span(name, attributes=normalized):
            pass


__all__ = ["NoopTelemetrySink", "OpenTelemetrySink", "TelemetrySink"]
