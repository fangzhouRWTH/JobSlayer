import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobslayer.application.phase0_corpus import (
    Phase0CorpusBuilder,
    Phase0CorpusDefinition,
    Phase0CorpusError,
)


class Phase0CorpusBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.definition_path = self.root / "definition.json"
        self.definition_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "corpus_id": "test-corpus-v1",
                    "required_reviewed_tasks": 3,
                    "cases": [
                        {"case_id": "case-00", "action": "approve_complete"},
                        {"case_id": "case-01", "action": "request_changes"},
                        {"case_id": "case-02", "action": "reject"},
                        {"case_id": "case-03", "action": "verification_failure"},
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_builds_an_integrity_verified_cross_path_corpus(self) -> None:
        output_root = self.root / "output"

        report = Phase0CorpusBuilder(
            self.definition_path,
            output_root,
        ).build()

        readiness = report["readiness"]
        self.assertTrue(readiness["automated_gate_passes"])
        self.assertTrue(readiness["manual_confirmation_required"])
        self.assertEqual(readiness["counts"]["reviewed_tasks"], 3)
        self.assertEqual(readiness["counts"]["valid_runs"], 4)
        self.assertEqual(readiness["counts"]["completed_runs"], 1)
        self.assertGreaterEqual(readiness["counts"]["negative_path_runs"], 3)
        self.assertFalse(report["human_confirmation_claimed"])
        self.assertEqual(report["evidence_class"], "deterministic_fixture")
        self.assertTrue((output_root / "corpus-report.json").is_file())
        self.assertTrue((output_root / "control" / ".git").is_dir())
        self.assertTrue((output_root / "testbed" / ".git").is_dir())

    def test_refuses_to_reuse_an_existing_output_directory(self) -> None:
        output_root = self.root / "existing"
        output_root.mkdir()

        with self.assertRaisesRegex(Phase0CorpusError, "refusing to reuse"):
            Phase0CorpusBuilder(self.definition_path, output_root).build()

    def test_definition_requires_all_foundational_paths(self) -> None:
        payload = json.loads(self.definition_path.read_text(encoding="utf-8"))
        payload["cases"][-1]["action"] = "await_decision"
        payload["required_reviewed_tasks"] = 4

        with self.assertRaisesRegex(ValueError, "failure"):
            Phase0CorpusDefinition.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
