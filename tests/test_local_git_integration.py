import subprocess
import tempfile
import unittest
from pathlib import Path

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.adapters.local_git_integration import (
    LocalGitIntegrationError,
    LocalGitIntegrator,
)
from jobslayer.domain.models import RiskLevel, TaskSpec, WorkspaceSpec


class LocalGitIntegratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.repository = root / "source"
        self.repository.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "Fixture")
        self._git("config", "user.email", "fixture@example.invalid")
        (self.repository / "value.txt").write_text("base\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.base = self._git("rev-parse", "HEAD")
        self.manager = GitWorktreeManager(self.repository, root / "worktrees")
        self.workspace = self.manager.create(
            WorkspaceSpec(
                workspace_id="integration-workspace",
                task_id="integration-task",
                base_commit=self.base,
            )
        )
        self.task = TaskSpec(
            task_id="integration-task",
            project_id="fixture",
            title="Change value",
            objective="Test local source integration",
            repository=str(self.repository),
            base_commit=self.base,
            allowed_paths=("value.txt", "new.txt"),
            required_capabilities=("file_change",),
            acceptance_criteria=("value changes",),
            validation_profile="fixture-v1",
            risk=RiskLevel.LOW,
        )
        (Path(self.workspace.path) / "value.txt").write_text(
            "changed\n", encoding="utf-8"
        )
        (Path(self.workspace.path) / "new.txt").write_text(
            "new\n", encoding="utf-8"
        )
        self.patch = self.manager.collect_patch(self.workspace, self.task)
        self.integrator = LocalGitIntegrator(self.manager)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ("git", "-C", str(self.repository), *arguments),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _workspace_git(self, *arguments: str) -> str:
        return subprocess.run(
            ("git", "-C", self.workspace.path, *arguments),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def integrate(self):
        return self.integrator.integrate(
            task=self.task,
            workspace=self.workspace,
            reviewed_patch=self.patch,
            target_ref="main",
            approved_by="fixture-human",
            commit_message="Integrate fixture task",
        )

    def test_commits_exact_reviewed_patch_and_fast_forwards_target(self) -> None:
        result = self.integrate()

        self.assertEqual(self._git("rev-parse", "HEAD"), result.commit)
        self.assertEqual(result.target_previous_commit, self.base)
        self.assertEqual(result.target_commit, result.commit)
        self.assertEqual(result.changed_paths, ("new.txt", "value.txt"))
        self.assertEqual(
            (self.repository / "value.txt").read_text(encoding="utf-8"),
            "changed\n",
        )
        self.assertEqual(
            (self.repository / "new.txt").read_text(encoding="utf-8"),
            "new\n",
        )
        self.assertTrue(self.manager.inspect(self.workspace).working_tree_clean)
        self.assertIn(
            f"JobSlayer-Patch-SHA256: {self.patch.sha256}",
            self._git("show", "-s", "--format=%B", "HEAD"),
        )

    def test_rejects_workspace_drift_before_creating_a_commit(self) -> None:
        (Path(self.workspace.path) / "value.txt").write_text(
            "changed again\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(LocalGitIntegrationError, "reviewed patch"):
            self.integrate()

        self.assertEqual(self._git("rev-parse", "HEAD"), self.base)
        self.assertEqual(
            subprocess.run(
                ("git", "-C", self.workspace.path, "rev-parse", "HEAD"),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            self.base,
        )

    def test_rejects_target_drift_before_creating_a_commit(self) -> None:
        (self.repository / "other.txt").write_text("drift\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "unrelated target drift")

        with self.assertRaisesRegex(LocalGitIntegrationError, "moved away"):
            self.integrate()

        self.assertNotEqual(self._git("rev-parse", "HEAD"), self.base)

    def test_retry_after_success_is_idempotent(self) -> None:
        first = self.integrate()
        second = self.integrate()

        self.assertEqual(second.integration_id, first.integration_id)
        self.assertEqual(second.commit, first.commit)
        self.assertEqual(self._git("rev-list", "--count", f"{self.base}..HEAD"), "1")

    def test_recovery_rejects_same_paths_and_message_with_different_content(self) -> None:
        message = self.integrator._commit_message(
            commit_message="Integrate fixture task",
            task_id=self.task.task_id,
            patch_sha256=self.patch.sha256,
            approved_by="fixture-human",
        )
        self._workspace_git("add", "--all")
        self._workspace_git("commit", "-m", message)
        (Path(self.workspace.path) / "value.txt").write_text(
            "substituted\n", encoding="utf-8"
        )
        self._workspace_git("add", "--all")
        self._workspace_git("commit", "--amend", "-m", message)

        with self.assertRaisesRegex(LocalGitIntegrationError, "content differs"):
            self.integrate()

        self.assertEqual(self._git("rev-parse", "HEAD"), self.base)


if __name__ == "__main__":
    unittest.main()
