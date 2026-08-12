from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit
from urllib.request import url2pathname

from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.sqlite_budgets import SqliteBudgetStore
from jobslayer.application.context_packages import ContextPackageBuilder
from jobslayer.application.budget_policy import execution_budget_from_contracts
from jobslayer.domain.models import AgentRunSpec, RiskLevel, TaskSpec
from jobslayer.governance import (
    BudgetError,
    BudgetExceededError,
    BudgetStatus,
    ContextPackageError,
    ExecutionBudget,
)


class BudgetGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = SqliteBudgetStore(self.root / "budgets.sqlite3")
        self.store.migrate()
        self.now = datetime(2026, 8, 12, 3, 0, tzinfo=UTC)
        self.budget = ExecutionBudget(
            budget_id="budget-1",
            task_id="task-1",
            run_id="run-1",
            maximum_input_tokens=100,
            maximum_output_tokens=50,
            maximum_cost_microusd=1_000_000,
            maximum_duration_ms=10_000,
            maximum_attempts=2,
            maximum_repairs=1,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_reserves_before_attempt_and_persists_incremental_usage(self) -> None:
        reserved = self.store.reserve(self.budget, now=self.now)
        attempt = self.store.authorize_attempt(
            reserved.reservation_id,
            expected_version=1,
            now=self.now,
        )
        charged = self.store.charge(
            reserved.reservation_id,
            expected_version=2,
            input_tokens=40,
            output_tokens=10,
            cost_microusd=250_000,
            duration_ms=500,
            now=self.now + timedelta(seconds=1),
        )
        released = self.store.release(
            reserved.reservation_id,
            expected_version=3,
            now=self.now + timedelta(seconds=2),
        )

        self.assertEqual(attempt.attempts_started, 1)
        self.assertEqual(charged.spent_input_tokens, 40)
        self.assertEqual(released.status, BudgetStatus.RELEASED)
        self.assertEqual(
            tuple(item.version for item in self.store.events(reserved.reservation_id)),
            (1, 2, 3, 4),
        )

    def test_overage_is_persisted_exhausted_and_requires_cancellation(self) -> None:
        reserved = self.store.reserve(self.budget, now=self.now)
        attempt = self.store.authorize_attempt(
            reserved.reservation_id,
            expected_version=1,
            now=self.now,
        )
        with self.assertRaisesRegex(BudgetExceededError, "input tokens") as caught:
            self.store.charge(
                reserved.reservation_id,
                expected_version=attempt.version,
                input_tokens=101,
                now=self.now + timedelta(seconds=1),
            )

        self.assertEqual(caught.exception.snapshot.status, BudgetStatus.EXHAUSTED)
        reopened = SqliteBudgetStore(self.root / "budgets.sqlite3")
        persisted = reopened.get(reserved.reservation_id)
        self.assertEqual(persisted, caught.exception.snapshot)
        with self.assertRaisesRegex(BudgetError, "not active"):
            reopened.charge(
                reserved.reservation_id,
                expected_version=persisted.version,
                input_tokens=1,
            )

    def test_attempt_and_repair_limits_are_deterministic(self) -> None:
        reserved = self.store.reserve(self.budget, now=self.now)
        first = self.store.authorize_attempt(
            reserved.reservation_id,
            expected_version=1,
            now=self.now,
        )
        repair = self.store.authorize_attempt(
            reserved.reservation_id,
            expected_version=first.version,
            repair=True,
            now=self.now + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(BudgetExceededError, "attempt budget") as caught:
            self.store.authorize_attempt(
                reserved.reservation_id,
                expected_version=repair.version,
                repair=True,
                now=self.now + timedelta(seconds=2),
            )
        self.assertEqual(caught.exception.snapshot.status, BudgetStatus.EXHAUSTED)

    def test_derives_exact_budget_from_source_controlled_task_and_run_contracts(self) -> None:
        task = TaskSpec(
            task_id="task-contract",
            project_id="fixture",
            title="Budget contract",
            objective="Derive deterministic limits",
            repository="https://example.invalid/repository.git",
            base_commit="a" * 40,
            allowed_paths=("src/",),
            required_capabilities=("edit",),
            acceptance_criteria=("tests pass",),
            validation_profile="fixture-v1",
            risk=RiskLevel.LOW,
            max_cost_usd=1.2345678,
        )
        spec = AgentRunSpec(
            run_id="run-contract",
            task_id=task.task_id,
            executor_type="fixture",
            model_profile="fixture",
            context_package_id="context-contract",
            workspace_id="workspace-contract",
            permission_profile="workspace_write",
            timeout_seconds=12,
            max_attempts=2,
            max_repairs=1,
            maximum_input_tokens=1000,
            maximum_output_tokens=200,
            maximum_context_bytes=65536,
            output_schema="none",
        )

        budget = execution_budget_from_contracts(task, spec)

        self.assertEqual(budget.maximum_cost_microusd, 1_234_567)
        self.assertEqual(budget.maximum_duration_ms, 12_000)
        self.assertEqual(budget.maximum_repairs, 1)


class ContextPackageTests(unittest.TestCase):
    def test_builds_sorted_content_addressed_context_and_detects_tampering(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("alpha", encoding="utf-8")
            (root / "b.json").write_text('{"b": 2}', encoding="utf-8")
            artifacts = LocalArtifactRegistry(root / "artifacts")
            builder = ContextPackageBuilder(root, artifacts)
            package = builder.build(
                task_id="task-context",
                run_id="run-context",
                sources={"spec/b": "b.json", "spec/a": "a.txt"},
                maximum_size_bytes=100,
                package_id="context-fixed-v1",
            )

            self.assertEqual(
                tuple(item.logical_name for item in package.components),
                ("spec/a", "spec/b"),
            )
            self.assertTrue(builder.verify(package))
            self.assertEqual(package.package_id, "context-fixed-v1")
            manifest = artifacts.get(package.components[0].artifact_id)
            artifact_path = Path(url2pathname(urlsplit(manifest.uri).path))
            if os.name == "nt" and str(artifact_path).startswith("\\"):
                artifact_path = Path(str(artifact_path)[1:])
            artifact_path.chmod(0o600)
            artifact_path.write_bytes(b"tampered")
            self.assertFalse(builder.verify(package))

    def test_rejects_oversized_or_escaping_context_before_registration(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "large.txt").write_bytes(b"x" * 20)
            outside_path = Path(outside) / "outside.txt"
            outside_path.write_text("outside", encoding="utf-8")
            artifacts = LocalArtifactRegistry(root / "artifacts")
            builder = ContextPackageBuilder(root, artifacts)
            with self.assertRaisesRegex(ContextPackageError, "byte budget"):
                builder.build(
                    task_id="task-context",
                    run_id="run-context",
                    sources={"large": "large.txt"},
                    maximum_size_bytes=10,
                )
            with self.assertRaisesRegex(ContextPackageError, "escapes"):
                builder.build(
                    task_id="task-context",
                    run_id="run-context",
                    sources={"outside": outside_path},
                    maximum_size_bytes=100,
                )
            self.assertEqual(artifacts.list_manifests(), ())


if __name__ == "__main__":
    unittest.main()
