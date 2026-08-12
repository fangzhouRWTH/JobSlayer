from __future__ import annotations

import unittest

from jobslayer.observability import OpenTelemetrySink


class _SpanContext:
    def __init__(self, tracer, name, attributes):
        self.tracer = tracer
        self.name = name
        self.attributes = attributes

    def __enter__(self):
        self.tracer.started.append((self.name, self.attributes))
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.tracer.ended += 1


class _Tracer:
    def __init__(self):
        self.started = []
        self.ended = 0

    def start_as_current_span(self, name, *, attributes):
        return _SpanContext(self, name, attributes)


class ObservabilityTests(unittest.TestCase):
    def test_otel_adapter_records_bounded_scalar_attributes(self) -> None:
        tracer = _Tracer()
        sink = OpenTelemetrySink(tracer=tracer)

        sink.record(
            "jobslayer.test",
            {
                "count": 2,
                "valid": True,
                "name": "fixture",
                "secret_like_nested_value": {"must": "not be exported"},
            },
        )

        self.assertEqual(
            tracer.started,
            [
                (
                    "jobslayer.test",
                    {"count": 2, "valid": True, "name": "fixture"},
                )
            ],
        )
        self.assertEqual(tracer.ended, 1)


if __name__ == "__main__":
    unittest.main()
