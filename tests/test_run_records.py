import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobslayer.application.run_records import (
    LocalRunLedger,
    RunRecordError,
    RunRecordStage,
)


class LocalRunLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "records.jsonl"
        self.ledger = LocalRunLedger(self.path, run_id="run-1")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_appends_execution_and_review_with_a_valid_hash_chain(self) -> None:
        first = self.ledger.append(
            task_id="task-1",
            stage=RunRecordStage.EXECUTION,
            payload={"state": "reviewing"},
        )
        second = self.ledger.append(
            task_id="task-1",
            stage=RunRecordStage.IMPLEMENTATION_REVIEW,
            payload={"state": "merge_review"},
        )
        third = self.ledger.append(
            task_id="task-1",
            stage=RunRecordStage.DECISION_APPLICATION,
            payload={"state": "completed"},
        )
        fourth = self.ledger.append(
            task_id="task-1",
            stage=RunRecordStage.SOURCE_INTEGRATION,
            payload={"commit": "a" * 40},
        )
        fifth = self.ledger.append(
            task_id="task-1",
            stage=RunRecordStage.WORKSPACE_CLEANUP,
            payload={"removed": True},
        )

        records = self.ledger.read_all()
        self.assertEqual(records, (first, second, third, fourth, fifth))
        self.assertEqual(second.previous_hash, first.record_hash)
        self.assertEqual(third.previous_hash, second.record_hash)
        self.assertEqual(fourth.previous_hash, third.record_hash)
        self.assertEqual(fifth.previous_hash, fourth.record_hash)

    def test_detects_payload_tampering(self) -> None:
        self.ledger.append(
            task_id="task-1",
            stage=RunRecordStage.EXECUTION,
            payload={"state": "reviewing"},
        )
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["payload"]["state"] = "completed"
        self.path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(RunRecordError, "invalid run record"):
            self.ledger.read_all()

    def test_rejects_review_before_execution(self) -> None:
        with self.assertRaisesRegex(RunRecordError, "stage sequence"):
            self.ledger.append(
                task_id="task-1",
                stage=RunRecordStage.IMPLEMENTATION_REVIEW,
                payload={},
            )


if __name__ == "__main__":
    unittest.main()
