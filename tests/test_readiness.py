from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobslayer.application.readiness import Phase0ReadinessEvaluator


class _MappingInspector:
    def __init__(self, summaries: dict[str, dict], failures: set[str] | None = None):
        self.summaries = summaries
        self.failures = failures or set()

    def inspect(self, run_directory: str | Path) -> dict:
        run_id = Path(run_directory).name
        if run_id in self.failures:
            raise RuntimeError("persisted run failed hash-chain verification")
        return self.summaries[run_id]


def _summary(run_id: str, *, state: str, applied: bool, reviewed: bool = True) -> dict:
    return {
        "run_id": run_id,
        "task_id": f"task-{run_id}",
        "state": state,
        "record_chain_valid": True,
        "audit_chain_valid": True,
        "artifacts_valid": True,
        "review": {"status": "accepted"} if reviewed else None,
        "decision": {"applied": applied},
    }


class Phase0ReadinessEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.state_root = Path(self.temporary_directory.name)
        (self.state_root / "runs").mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_accepts_a_complete_reviewed_corpus_with_negative_evidence(self) -> None:
        summaries = {}
        for index in range(20):
            run_id = f"run-{index:02d}"
            (self.state_root / "runs" / run_id).mkdir()
            summaries[run_id] = _summary(
                run_id,
                state=(
                    "completed"
                    if index == 0
                    else "repairing"
                    if index == 1
                    else "merge_review"
                ),
                applied=index == 0,
            )

        report = Phase0ReadinessEvaluator(
            _MappingInspector(summaries),
            state_root=self.state_root,
        ).evaluate()

        self.assertTrue(report.automated_gate_passes)
        self.assertEqual(report.reviewed_runs, 20)
        self.assertEqual(report.reviewed_tasks, 20)
        self.assertEqual(report.decision_applied_completed_runs, 1)
        self.assertEqual(report.negative_path_runs, 1)
        self.assertTrue(report.to_dict()["manual_confirmation_required"])

    def test_rejects_an_empty_run_corpus_with_actionable_reasons(self) -> None:
        report = Phase0ReadinessEvaluator(
            _MappingInspector({}),
            state_root=self.state_root,
        ).evaluate()

        self.assertFalse(report.automated_gate_passes)
        self.assertEqual(report.discovered_runs, 0)
        self.assertIn("(0/20)", " ".join(report.unmet_criteria))
        self.assertIn("applied decision", " ".join(report.unmet_criteria))
        self.assertIn("failed, repairing, or cancelled", " ".join(report.unmet_criteria))

    def test_repeated_runs_of_one_task_do_not_satisfy_the_task_corpus(self) -> None:
        summaries = {}
        for index in range(20):
            run_id = f"run-{index:02d}"
            (self.state_root / "runs" / run_id).mkdir()
            summaries[run_id] = _summary(
                run_id,
                state=("completed" if index == 0 else "repairing"),
                applied=index == 0,
            )
            summaries[run_id]["task_id"] = "one-repeated-task"

        report = Phase0ReadinessEvaluator(
            _MappingInspector(summaries),
            state_root=self.state_root,
        ).evaluate()

        self.assertFalse(report.automated_gate_passes)
        self.assertEqual(report.reviewed_runs, 20)
        self.assertEqual(report.reviewed_tasks, 1)
        self.assertIn("(1/20)", " ".join(report.unmet_criteria))

    def test_one_corrupt_run_blocks_an_otherwise_complete_corpus(self) -> None:
        summaries = {}
        for index in range(20):
            run_id = f"run-{index:02d}"
            (self.state_root / "runs" / run_id).mkdir()
            summaries[run_id] = _summary(
                run_id,
                state=("completed" if index == 0 else "repairing"),
                applied=index == 0,
            )

        report = Phase0ReadinessEvaluator(
            _MappingInspector(summaries, failures={"run-19"}),
            state_root=self.state_root,
            required_reviewed_tasks=19,
        ).evaluate()

        self.assertFalse(report.automated_gate_passes)
        self.assertEqual(report.valid_runs, 19)
        self.assertEqual(len(report.invalid_runs), 1)
        self.assertIn("hash-chain", report.invalid_runs[0].error)

    def test_rejects_a_non_positive_review_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 1"):
            Phase0ReadinessEvaluator(
                _MappingInspector({}),
                state_root=self.state_root,
                required_reviewed_tasks=0,
            )


if __name__ == "__main__":
    unittest.main()
