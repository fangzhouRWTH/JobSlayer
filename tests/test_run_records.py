import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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

    def test_partial_generation_write_preserves_the_previous_ledger(self) -> None:
        first = self.ledger.append(
            task_id="task-1",
            stage=RunRecordStage.EXECUTION,
            payload={"state": "reviewing"},
        )
        previous_bytes = self.path.read_bytes()

        def partial_write(descriptor: int, content: bytes) -> None:
            os.write(descriptor, content[: max(1, len(content) // 2)])
            raise OSError("injected partial generation write")

        with (
            patch.object(LocalRunLedger, "_write_all", side_effect=partial_write),
            self.assertRaisesRegex(RunRecordError, "durably publish"),
        ):
            self.ledger.append(
                task_id="task-1",
                stage=RunRecordStage.IMPLEMENTATION_REVIEW,
                payload={"state": "merge_review"},
            )

        self.assertEqual(self.path.read_bytes(), previous_bytes)
        self.assertEqual(self.ledger.read_all(), (first,))
        self.assertEqual(
            tuple(self.path.parent.glob(f".{self.path.name}.*.tmp")),
            (),
        )

    def test_process_exit_around_atomic_replace_exposes_only_old_or_new_chain(self) -> None:
        first = self.ledger.append(
            task_id="task-1",
            stage=RunRecordStage.EXECUTION,
            payload={"state": "reviewing"},
        )
        crash_script = (
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            "from unittest.mock import patch\n"
            "from jobslayer.application.run_records import (\n"
            "    LocalRunLedger, RunRecordStage)\n"
            "ledger = LocalRunLedger(Path(sys.argv[1]), run_id='run-1')\n"
            "mode = sys.argv[2]\n"
            "original_replace = os.replace\n"
            "def crash_replace(source, destination):\n"
            "    if mode == 'after':\n"
            "        original_replace(source, destination)\n"
            "        os._exit(82)\n"
            "    os._exit(81)\n"
            "with patch('jobslayer.application.run_records.os.replace', "
            "side_effect=crash_replace):\n"
            "    ledger.append(\n"
            "        task_id='task-1',\n"
            "        stage=RunRecordStage.IMPLEMENTATION_REVIEW,\n"
            "        payload={'state': 'merge_review'})\n"
        )

        before = subprocess.run(
            (sys.executable, "-c", crash_script, str(self.path), "before"),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(before.returncode, 81, before.stderr)
        self.assertEqual(self.ledger.read_all(), (first,))

        after = subprocess.run(
            (sys.executable, "-c", crash_script, str(self.path), "after"),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(after.returncode, 82, after.stderr)
        records = self.ledger.read_all()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0], first)
        self.assertEqual(records[1].stage, RunRecordStage.IMPLEMENTATION_REVIEW)
        self.assertEqual(records[1].previous_hash, first.record_hash)


if __name__ == "__main__":
    unittest.main()
