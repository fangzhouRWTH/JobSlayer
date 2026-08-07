from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobslayer.application.runbook import LocalRunbookLoader, RunbookError
from jobslayer.domain.models import (
    AgentInvocation,
    AgentRunSpec,
    CommandPolicy,
    CommandRule,
    RiskLevel,
    TaskSpec,
    TestbedBaseline,
    TestbedSpec,
    TestbedStatus,
    ValidationCheckSpec,
    ValidationProfile,
)


class LocalRunbookLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        for directory in ("testbeds", "tasks", "profiles", "patches", "runbooks"):
            (self.root / directory).mkdir()
        self.commit = "0123456789abcdef0123456789abcdef01234567"
        self.patch = b"fixture patch\n"
        (self.root / "patches" / "change.diff").write_bytes(self.patch)
        self._write_models()
        self.runbook_path = self.root / "runbooks" / "fixture.json"
        self.runbook_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "testbed_id": "fixture",
                    "testbed_path": "testbeds/fixture.json",
                    "task_path": "tasks/fixture.json",
                    "validation_profile_path": "profiles/fixture.json",
                    "invocation": self.invocation.model_dump(mode="json"),
                    "executor": {
                        "adapter": "scripted_patch",
                        "patch_path": "patches/change.diff",
                        "patch_sha256": hashlib.sha256(self.patch).hexdigest(),
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_models(self) -> None:
        testbed = TestbedSpec(
            testbed_id="fixture",
            display_name="Fixture",
            purpose="Runbook binding test",
            status=TestbedStatus.BOOTSTRAPPING,
            repository={
                "clone_url": "https://example.invalid/fixture.git",
                "default_branch": "main",
            },
            baseline=TestbedBaseline(
                commit=self.commit,
                tag="fixture-0",
                published=False,
                verification_command=("./verify",),
            ),
            architecture_areas=("simulation",),
            capability_targets=("test",),
        )
        task = TaskSpec(
            task_id="fixture-task",
            project_id="fixture",
            title="Fixture task",
            objective="Exercise runbook binding",
            repository="https://example.invalid/fixture.git",
            base_commit=self.commit,
            allowed_paths=("scenarios/",),
            required_capabilities=("test",),
            acceptance_criteria=("verification passes",),
            validation_profile="fixture-v1",
            risk=RiskLevel.LOW,
        )
        profile = ValidationProfile(
            profile_id="fixture-v1",
            command_policy=CommandPolicy(
                policy_id="fixture-policy",
                rules=(CommandRule(rule_id="verify", argv_prefix=("./verify",)),),
            ),
            checks=(
                ValidationCheckSpec(
                    check_id="verify",
                    title="Verify fixture",
                    argv=("./verify",),
                ),
            ),
        )
        self.invocation = AgentInvocation(
            run_spec=AgentRunSpec(
                run_id="fixture-run",
                task_id="fixture-task",
                executor_type="scripted_patch",
                model_profile="deterministic-replay-v1",
                context_package_id="context-fixture",
                workspace_id="fixture-workspace",
                permission_profile="workspace_write",
                timeout_seconds=5,
                output_schema="unified_diff",
            ),
            prompt="Replay the fixture patch.",
        )
        for path, model in (
            (self.root / "testbeds" / "fixture.json", testbed),
            (self.root / "tasks" / "fixture.json", task),
            (self.root / "profiles" / "fixture.json", profile),
        ):
            path.write_text(model.model_dump_json(indent=2), encoding="utf-8")

    def test_loads_and_binds_all_source_controlled_inputs(self) -> None:
        prepared = LocalRunbookLoader(self.root).load(self.runbook_path)

        self.assertEqual(prepared.task.task_id, "fixture-task")
        self.assertEqual(prepared.testbed.baseline.commit, self.commit)
        self.assertEqual(prepared.patch_bytes, self.patch)

    def test_loads_a_bound_codex_runbook_without_a_replay_patch(self) -> None:
        payload = json.loads(self.runbook_path.read_text(encoding="utf-8"))
        payload["executor"] = {"adapter": "codex_cli"}
        payload["invocation"]["run_spec"].update(
            {
                "executor_type": "codex_cli",
                "model_profile": "default",
                "output_schema": "none",
            }
        )
        self.runbook_path.write_text(json.dumps(payload), encoding="utf-8")

        prepared = LocalRunbookLoader(self.root).load(self.runbook_path)

        self.assertEqual(prepared.runbook.executor.adapter, "codex_cli")
        self.assertIsNone(prepared.patch_bytes)

    def test_rejects_codex_retry_policy_until_recovery_is_implemented(self) -> None:
        payload = json.loads(self.runbook_path.read_text(encoding="utf-8"))
        payload["executor"] = {"adapter": "codex_cli"}
        payload["invocation"]["run_spec"].update(
            {
                "executor_type": "codex_cli",
                "model_profile": "default",
                "output_schema": "none",
                "max_attempts": 2,
            }
        )
        self.runbook_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(RunbookError, "retry policy"):
            LocalRunbookLoader(self.root).load(self.runbook_path)

    def test_rejects_patch_content_that_drifted_after_review(self) -> None:
        (self.root / "patches" / "change.diff").write_text(
            "changed after hash\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(RunbookError, "sha256"):
            LocalRunbookLoader(self.root).load(self.runbook_path)

    def test_rejects_task_at_an_unregistered_baseline(self) -> None:
        task_path = self.root / "tasks" / "fixture.json"
        payload = json.loads(task_path.read_text(encoding="utf-8"))
        payload["base_commit"] = "fedcba9876543210fedcba9876543210fedcba98"
        task_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(RunbookError, "base_commit"):
            LocalRunbookLoader(self.root).load(self.runbook_path)


if __name__ == "__main__":
    unittest.main()
