from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.local_dependency_attachments import (
    directory_sha256,
    resolve_local_dependency_attachment,
)
from jobslayer.adapters.task_manager_validation import (
    LocalTaskManagerValidationRunner,
    TaskManagerValidationError,
)
from jobslayer.domain.models import (
    AgentInvocation,
    CommandPolicy,
    CommandRule,
    CommandStatus,
    TestbedInspection,
    ValidationCheckSpec,
    ValidationProfile,
    WorkspaceSpec,
)
from jobslayer.application.runbook import LocalDependencyAttachmentConfig
from jobslayer.orchestration import TaskPlanNode, TaskPlanNodeKind
from jobslayer.task_manager import ManagedExecutionRequest, ManagedExecutionStatus
from tests.task_manager_fixtures import fixture_execution_binding


class LocalTaskManagerValidationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "source"
        self.repository.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "JobSlayer Test")
        self._git("config", "user.email", "jobslayer@example.invalid")
        (self.repository / "verify.py").write_text(
            "import os\n"
            "print('deterministic validation passed')\n"
            "if value := os.getenv('FIXTURE_DEPENDENCY_ROOT'):\n"
            "    print(value)\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.base_commit = self._git("rev-parse", "HEAD").strip()

        self.state_root = self.root / "state"
        self.artifacts = LocalArtifactRegistry(self.root / "artifacts")
        self.binding = self._binding()
        manager = GitWorktreeManager(
            self.repository,
            self.state_root / "workspaces",
        )
        self.workspace = manager.create(
            WorkspaceSpec(
                workspace_id="validation-workspace",
                task_id=self.binding.task.task_id,
                base_commit=self.base_commit,
            )
        )
        run_directory = self.state_root / "runs" / hashlib.sha256(
            b"tmrun-validation-adapter"
        ).hexdigest()
        run_directory.mkdir(parents=True)
        (run_directory / "workspace.json").write_text(
            json.dumps(self.workspace.model_dump(mode="json")),
            encoding="utf-8",
        )
        self.request = ManagedExecutionRequest(
            provider_start_key="tmvalidate-adapter-fixture",
            run_id="tmrun-validation-adapter",
            plan_id="plan-validation-adapter",
            plan_revision=3,
            plan_record_hash="c" * 64,
            workflow_task_id="tmnode-validation-adapter",
            execution_binding=self.binding,
            node=TaskPlanNode(
                node_id="verify",
                title="Run deterministic validation",
                kind=TaskPlanNodeKind.VALIDATION,
                acceptance_criteria=("The finalized check passes",),
                deliverables=("Structured command evidence",),
            ),
            dependency_node_ids=("implementation",),
            prompt="Run only the finalized validation profile.",
        )
        self.runner = LocalTaskManagerValidationRunner(
            self.state_root,
            self.artifacts,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def _binding(self):
        original = fixture_execution_binding()
        argv = (sys.executable, "verify.py")
        profile = ValidationProfile(
            profile_id="fixture-local-validation-v1",
            command_policy=CommandPolicy(
                policy_id="fixture-local-validation-policy-v1",
                rules=(
                    CommandRule(
                        rule_id="fixture-check",
                        argv_prefix=argv,
                    ),
                ),
            ),
            checks=(
                ValidationCheckSpec(
                    check_id="fixture-check",
                    title="fixture deterministic check",
                    argv=argv,
                ),
            ),
        )
        task = original.task.model_copy(
            update={
                "repository": str(self.repository),
                "base_commit": self.base_commit,
                "validation_profile": profile.profile_id,
            }
        )
        run_spec = original.invocation.run_spec.model_copy(
            update={"task_id": task.task_id}
        )
        invocation = AgentInvocation(
            run_spec=run_spec,
            prompt=original.invocation.prompt,
        )
        return original.model_copy(
            update={
                "task": task,
                "validation_profile": profile,
                "invocation": invocation,
                "testbed_inspection": TestbedInspection(
                    testbed_id=task.project_id,
                    checkout_path=str(self.repository),
                    baseline_commit=self.base_commit,
                    head_commit=self.base_commit,
                    tag="fixture-v1",
                    tag_commit=self.base_commit,
                    origin_url=str(self.repository),
                    working_tree_clean=True,
                    head_matches_baseline=True,
                    tag_matches_baseline=True,
                    origin_registered=True,
                    baseline_published=False,
                ),
                "resolved_at": datetime.now(UTC),
            }
        )

    def test_runs_finalized_profile_once_and_returns_raw_evidence(self) -> None:
        reference = self.runner.start_or_locate(self.request)
        state = self.runner._state_directory(self.request.provider_start_key)
        terminal_before = (state / "terminal.json").read_bytes()

        repeated = self.runner.start_or_locate(self.request)
        observation = self.runner.observe(reference, after_cursor=None)
        repeated_observation = self.runner.observe(
            reference,
            after_cursor=observation.cursor,
        )
        evidence = self.runner.collect_verification_evidence(reference)

        self.assertEqual(repeated, reference)
        self.assertEqual((state / "terminal.json").read_bytes(), terminal_before)
        self.assertEqual(observation, repeated_observation)
        self.assertEqual(observation.status, ManagedExecutionStatus.SUCCEEDED)
        self.assertEqual(len(evidence.validation_checks), 1)
        check = evidence.validation_checks[0]
        self.assertEqual(check.check_id, "fixture-check")
        self.assertEqual(check.result.status, CommandStatus.PASSED)
        self.assertEqual(
            check.result.stdout,
            "deterministic validation passed\n",
        )
        self.assertTrue(evidence.workspace.working_tree_clean)
        self.assertEqual(evidence.workspace.changed_paths, ())
        self.assertIsNone(evidence.source_patch_sha256)
        for artifact_id in (
            *reference.evidence_artifact_ids,
            *observation.evidence_artifact_ids,
            *evidence.evidence_artifact_ids,
        ):
            self.assertTrue(self.artifacts.verify(self.artifacts.get(artifact_id)))

    def test_rejects_same_key_with_different_request(self) -> None:
        self.runner.start_or_locate(self.request)
        drifted = self.request.model_copy(update={"prompt": "different request"})

        with self.assertRaises(TaskManagerValidationError):
            self.runner.start_or_locate(drifted)

    def test_rejects_dirty_workspace_before_running_checks(self) -> None:
        (Path(self.workspace.path) / "untracked.txt").write_text(
            "must block validation\n",
            encoding="utf-8",
        )

        with self.assertRaises(TaskManagerValidationError):
            self.runner.start_or_locate(self.request)
        self.assertEqual(tuple((self.state_root / "validations").iterdir()), ())

    def test_injects_bound_dependency_and_rejects_post_run_drift(self) -> None:
        dependency = self.root / "dependency"
        dependency.mkdir()
        (dependency / "fixture.txt").write_text("fixed\n", encoding="utf-8")
        attachment = resolve_local_dependency_attachment(
            LocalDependencyAttachmentConfig(
                attachment_id="fixture-dependency",
                kind="directory",
                environment_variable="FIXTURE_DEPENDENCY_ROOT",
                expected_sha256=directory_sha256(dependency),
            ),
            dependency,
        )
        binding = self.binding.model_copy(
            update={"dependency_attachments": (attachment,)}
        )
        request = self.request.model_copy(update={"execution_binding": binding})

        reference = self.runner.start_or_locate(request)
        evidence = self.runner.collect_verification_evidence(reference)

        self.assertEqual(evidence.dependency_attachments, (attachment,))
        self.assertEqual(
            evidence.validation_checks[0].result.environment,
            (attachment.command_environment(),),
        )
        self.assertIn(str(dependency.resolve()), evidence.validation_checks[0].result.stdout)

        (dependency / "fixture.txt").write_text("drifted\n", encoding="utf-8")
        with self.assertRaises(TaskManagerValidationError):
            self.runner.collect_verification_evidence(reference)


if __name__ == "__main__":
    unittest.main()
