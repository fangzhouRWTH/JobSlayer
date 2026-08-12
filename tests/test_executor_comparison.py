from __future__ import annotations

from datetime import UTC, datetime
import unittest

from jobslayer.application.executor_comparison import ExecutorComparisonEvaluator
from jobslayer.evaluation import ExecutorComparisonError, ExecutorEvaluationSample


class ExecutorComparisonTests(unittest.TestCase):
    @staticmethod
    def sample(executor: str, *, task_hash: str = "a" * 64):
        return ExecutorEvaluationSample(
            run_id=f"run-{executor}",
            task_id="task-shared",
            executor_type=executor,
            task_contract_sha256=task_hash,
            validation_contract_sha256="b" * 64,
            terminal_state="reviewing",
            verification_passed=True,
            input_tokens=10 if executor == "codex_cli" else 0,
            cached_input_tokens=2 if executor == "codex_cli" else 0,
            output_tokens=4 if executor == "codex_cli" else 0,
            cost_microusd=100 if executor == "codex_cli" else 0,
            duration_ms=50 if executor == "codex_cli" else 10,
            human_interventions=1,
        )

    def test_compares_two_executors_under_identical_contract_hashes(self) -> None:
        report = ExecutorComparisonEvaluator().evaluate(
            (self.sample("scripted_patch"), self.sample("codex_cli")),
            now=datetime(2026, 8, 12, tzinfo=UTC),
        )

        self.assertEqual(
            tuple(item.executor_type for item in report.aggregates),
            ("codex_cli", "scripted_patch"),
        )
        self.assertEqual(report.aggregates[0].total_input_tokens, 10)
        self.assertEqual(report.aggregates[1].total_input_tokens, 0)
        self.assertEqual(report.aggregates[0].verified_successes, 1)

    def test_rejects_single_executor_or_contract_drift(self) -> None:
        evaluator = ExecutorComparisonEvaluator()
        with self.assertRaisesRegex(ExecutorComparisonError, "two executor"):
            evaluator.evaluate((self.sample("codex_cli"), self.sample("codex_cli")))
        with self.assertRaisesRegex(ExecutorComparisonError, "exact task"):
            evaluator.evaluate(
                (
                    self.sample("codex_cli"),
                    self.sample("scripted_patch", task_hash="c" * 64),
                )
            )


if __name__ == "__main__":
    unittest.main()
