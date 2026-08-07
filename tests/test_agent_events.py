import unittest

from jobslayer.agents.events import RunEventBuffer, RunEventIntegrityError


class RunEventBufferTests(unittest.TestCase):
    def test_assigns_sequences_hashes_and_supports_incremental_reads(self) -> None:
        events = RunEventBuffer("run-1")
        first = events.append("run.started", {"executor": "fake"})
        second = events.append("agent.message.completed", {"text": "done"})

        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(len(first.content_hash or ""), 64)
        self.assertEqual(events.events(after_sequence=1), (second,))
        events.verify()

    def test_rejects_events_after_a_terminal_event(self) -> None:
        events = RunEventBuffer("run-1")
        events.append("run.completed", {"status": "completed"})

        with self.assertRaises(RunEventIntegrityError):
            events.append("agent.message.completed", {"text": "too late"})

    def test_integrity_check_detects_a_modified_event(self) -> None:
        events = RunEventBuffer("run-1")
        original = events.append("run.started", {"executor": "fake"})
        events._events[0] = original.model_copy(  # intentional corruption probe
            update={"payload": {"executor": "rewritten"}}
        )

        with self.assertRaises(RunEventIntegrityError):
            events.verify()


if __name__ == "__main__":
    unittest.main()

