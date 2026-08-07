import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.local_command import GovernedLocalCommandRunner
from jobslayer.domain.models import (
    CheckStatus,
    CommandPolicy,
    CommandRule,
    RiskLevel,
    TaskSpec,
    ValidationCheckSpec,
    ValidationProfile,
    WorkspaceSpec,
)
from jobslayer.execution.runner import CommandExecutionError
from jobslayer.verification.engine import VerificationEngine


class _RejectingRunner:
    def run(self, manifest, request, policy):
        raise CommandExecutionError("fixture rejection")


class VerificationEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.repository = root / "source"
        self.repository.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "JobSlayer Test")
        self._git("config", "user.email", "jobslayer@example.invalid")
        (self.repository / "src").mkdir()
        (self.repository / "src" / "value.txt").write_text(
            "baseline\n", encoding="utf-8"
        )
        (self.repository / "verify.py").write_text(
            "from pathlib import Path\n"
            "value = Path('src/value.txt').read_text()\n"
            "raise SystemExit(0 if value == 'changed\\n' else 7)\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.base_commit = self._git("rev-parse", "HEAD").strip()
        self.manager = GitWorktreeManager(self.repository, root / "worktrees")
        self.workspace = self.manager.create(
            WorkspaceSpec(
                workspace_id="verification-workspace",
                task_id="task-verification",
                base_commit=self.base_commit,
            )
        )
        self.registry = LocalArtifactRegistry(root / "artifacts")
        self.task = TaskSpec(
            task_id="task-verification",
            project_id="fixture",
            title="Verify a patch",
            objective="Test the verification evidence path",
            repository=str(self.repository),
            base_commit=self.base_commit,
            allowed_paths=("src/",),
            required_capabilities=("file_change",),
            acceptance_criteria=("verification passes",),
            validation_profile="fixture-v1",
            risk=RiskLevel.LOW,
        )
        argv = (sys.executable, "verify.py")
        self.profile = ValidationProfile(
            profile_id="fixture-v1",
            command_policy=CommandPolicy(
                policy_id="fixture-policy",
                rules=(
                    CommandRule(
                        rule_id="verify",
                        argv_prefix=argv,
                        max_timeout_seconds=2,
                    ),
                ),
                max_timeout_seconds=2,
            ),
            checks=(
                ValidationCheckSpec(
                    check_id="unit",
                    title="Fixture verification",
                    argv=argv,
                    timeout_seconds=1,
                ),
            ),
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

    def test_passing_command_produces_registered_evidence(self) -> None:
        (Path(self.workspace.path) / "src" / "value.txt").write_text(
            "changed\n", encoding="utf-8"
        )
        patch = self.manager.collect_patch(self.workspace, self.task)
        engine = VerificationEngine(
            GovernedLocalCommandRunner(self.manager), self.registry
        )

        report = engine.verify(
            task=self.task,
            workspace=self.workspace,
            patch=patch,
            profile=self.profile,
        )

        self.assertTrue(report.passes_gate)
        self.assertEqual(report.source_patch_sha256, patch.sha256)
        self.assertEqual(report.checks[0].status, CheckStatus.PASSED)
        artifact = self.registry.get(report.checks[0].artifact_ids[0])
        self.assertEqual(artifact.sha256, report.checks[0].evidence_hash)

    def test_policy_rejection_becomes_error_evidence(self) -> None:
        (Path(self.workspace.path) / "src" / "value.txt").write_text(
            "changed\n", encoding="utf-8"
        )
        patch = self.manager.collect_patch(self.workspace, self.task)
        engine = VerificationEngine(_RejectingRunner(), self.registry)

        report = engine.verify(
            task=self.task,
            workspace=self.workspace,
            patch=patch,
            profile=self.profile,
        )

        self.assertFalse(report.passes_gate)
        self.assertEqual(report.checks[0].status, CheckStatus.ERROR)
        artifact = self.registry.get(report.checks[0].artifact_ids[0])
        self.assertEqual(artifact.artifact_type, "validation-command-error")

    def test_profile_rejects_a_check_outside_its_command_policy(self) -> None:
        with self.assertRaises(ValidationError):
            ValidationProfile(
                profile_id="invalid-profile",
                command_policy=self.profile.command_policy,
                checks=(
                    ValidationCheckSpec(
                        check_id="unapproved",
                        title="Unapproved command",
                        argv=(sys.executable, "other.py"),
                    ),
                ),
            )

    def test_profile_rejects_a_timeout_above_the_matched_rule(self) -> None:
        with self.assertRaises(ValidationError):
            ValidationProfile(
                profile_id="invalid-timeout",
                command_policy=self.profile.command_policy,
                checks=(
                    ValidationCheckSpec(
                        check_id="too-slow",
                        title="Overlong verification",
                        argv=(sys.executable, "verify.py"),
                        timeout_seconds=3,
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
