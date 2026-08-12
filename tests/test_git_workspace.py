import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from jobslayer.adapters.git_workspace import (
    GitWorktreeManager,
    PathPolicyViolation,
    WorkspaceDirtyError,
    WorkspaceError,
)
from jobslayer.domain.models import (
    RiskLevel,
    TaskSpec,
    WorkspacePatch,
    WorkspaceSpec,
)


class GitWorktreeManagerTests(unittest.TestCase):
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
        (self.repository / "README.md").write_text(
            "# Fixture\n", encoding="utf-8"
        )
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.base_commit = self._git("rev-parse", "HEAD").strip()

        self.workspace_root = root / "worktrees"
        self.manager = GitWorktreeManager(self.repository, self.workspace_root)

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

    def workspace_spec(self, workspace_id: str, task_id: str = "task-1") -> WorkspaceSpec:
        return WorkspaceSpec(
            workspace_id=workspace_id,
            task_id=task_id,
            base_commit=self.base_commit,
        )

    def task_spec(
        self,
        *,
        task_id: str = "task-1",
        allowed_paths: tuple[str, ...] = ("src/",),
        forbidden_paths: tuple[str, ...] = (),
    ) -> TaskSpec:
        return TaskSpec(
            task_id=task_id,
            project_id="fixture",
            title="Change a fixture",
            objective="Exercise workspace isolation and patch collection",
            repository=str(self.repository),
            base_commit=self.base_commit,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            required_capabilities=("file_change",),
            acceptance_criteria=("patch is captured",),
            validation_profile="fixture_v1",
            risk=RiskLevel.LOW,
        )

    def test_creates_independent_worktrees_at_the_fixed_base(self) -> None:
        first = self.manager.create(self.workspace_spec("workspace-one", "task-1"))
        second = self.manager.create(self.workspace_spec("workspace-two", "task-2"))

        (Path(first.path) / "src" / "value.txt").write_text(
            "changed in first\n", encoding="utf-8"
        )

        self.assertEqual(first.resolved_base_commit, self.base_commit)
        self.assertEqual(second.resolved_base_commit, self.base_commit)
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(
            (Path(second.path) / "src" / "value.txt").read_text(encoding="utf-8"),
            "baseline\n",
        )
        self.assertEqual(
            (self.repository / "src" / "value.txt").read_text(encoding="utf-8"),
            "baseline\n",
        )

    def test_collects_tracked_and_untracked_changes_with_a_hash(self) -> None:
        manifest = self.manager.create(self.workspace_spec("patch-workspace"))
        workspace = Path(manifest.path)
        (workspace / "src" / "value.txt").write_text("changed\n", encoding="utf-8")
        (workspace / "src" / "new.txt").write_text("new\n", encoding="utf-8")

        patch = self.manager.collect_patch(manifest, self.task_spec())

        self.assertEqual(patch.changed_paths, ("src/new.txt", "src/value.txt"))
        self.assertIn("src/value.txt", patch.patch_text)
        self.assertIn("src/new.txt", patch.patch_text)
        self.assertEqual(
            patch.sha256,
            hashlib.sha256(patch.patch_text.encode("utf-8")).hexdigest(),
        )

    def test_forbidden_path_overrides_an_allowed_parent(self) -> None:
        manifest = self.manager.create(self.workspace_spec("policy-workspace"))
        private = Path(manifest.path) / "src" / "private"
        private.mkdir()
        (private / "secret.txt").write_text("not allowed\n", encoding="utf-8")
        task = self.task_spec(forbidden_paths=("src/private/",))

        with self.assertRaises(PathPolicyViolation) as raised:
            self.manager.collect_patch(manifest, task)

        self.assertEqual(raised.exception.paths, ("src/private/secret.txt",))

    def test_rejects_a_change_outside_the_allowlist(self) -> None:
        manifest = self.manager.create(self.workspace_spec("scope-workspace"))
        (Path(manifest.path) / "README.md").write_text(
            "outside scope\n", encoding="utf-8"
        )

        with self.assertRaises(PathPolicyViolation):
            self.manager.collect_patch(manifest, self.task_spec())

    def test_refuses_to_remove_a_dirty_workspace(self) -> None:
        manifest = self.manager.create(self.workspace_spec("dirty-workspace"))
        (Path(manifest.path) / "src" / "value.txt").write_text(
            "uncommitted\n", encoding="utf-8"
        )

        with self.assertRaises(WorkspaceDirtyError):
            self.manager.remove(manifest)

        self.assertTrue(Path(manifest.path).exists())

    def test_removes_a_clean_worktree_but_preserves_its_branch(self) -> None:
        manifest = self.manager.create(self.workspace_spec("clean-workspace"))

        self.manager.remove(manifest)

        removal = self.manager.inspect_removal(
            manifest,
            expected_commit=self.base_commit,
        )

        self.assertFalse(Path(manifest.path).exists())
        branches = self._git("branch", "--list", manifest.branch_name)
        self.assertIn(manifest.branch_name, branches)
        self.assertTrue(removal.safely_removed)
        self.assertTrue(removal.path_absent)
        self.assertTrue(removal.registration_absent)
        self.assertEqual(removal.branch_commit, self.base_commit)

    def test_removed_workspace_attestation_detects_source_branch_drift(self) -> None:
        manifest = self.manager.create(self.workspace_spec("drifted-cleanup"))
        self.manager.remove(manifest)
        (self.repository / "post-cleanup.txt").write_text(
            "drift\n", encoding="utf-8"
        )
        self._git("add", "post-cleanup.txt")
        self._git("commit", "-m", "post cleanup drift")
        self._git("branch", "-f", manifest.branch_name, "HEAD")

        removal = self.manager.inspect_removal(
            manifest,
            expected_commit=self.base_commit,
        )

        self.assertFalse(removal.safely_removed)
        self.assertNotEqual(removal.branch_commit, self.base_commit)

    def test_rejects_an_unknown_base_commit(self) -> None:
        spec = WorkspaceSpec(
            workspace_id="unknown-base",
            task_id="task-1",
            base_commit="deadbeef",
        )

        with self.assertRaises(WorkspaceError):
            self.manager.create(spec)

        self.assertFalse((self.workspace_root / "unknown-base").exists())

    def test_workspace_id_cannot_escape_the_root(self) -> None:
        with self.assertRaises(ValidationError):
            WorkspaceSpec(
                workspace_id="../escape",
                task_id="task-1",
                base_commit=self.base_commit,
            )

    def test_workspace_id_must_be_a_valid_git_branch_component(self) -> None:
        for workspace_id in ("bad..id", "bad.", "bad.lock"):
            with self.subTest(workspace_id=workspace_id):
                with self.assertRaises(ValidationError):
                    WorkspaceSpec(
                        workspace_id=workspace_id,
                        task_id="task-1",
                        base_commit=self.base_commit,
                    )

    def test_patch_contract_rejects_an_incorrect_hash(self) -> None:
        with self.assertRaises(ValidationError):
            WorkspacePatch(
                workspace_id="workspace-one",
                task_id="task-1",
                base_commit=self.base_commit,
                changed_paths=(),
                patch_text="diff content",
                sha256="0" * 64,
            )


if __name__ == "__main__":
    unittest.main()
