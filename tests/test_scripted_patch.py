import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.adapters.scripted_patch import ScriptedPatchError, ScriptedPatchExecutor
from jobslayer.domain.models import (
    AgentInvocation,
    AgentRunSpec,
    AgentRunStatus,
    WorkspaceSpec,
)


PATCH = b"""diff --git a/value.txt b/value.txt
index df967b9..5ea2ed4 100644
--- a/value.txt
+++ b/value.txt
@@ -1 +1 @@
-base
+changed
"""


class ScriptedPatchExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "JobSlayer Test")
        self._git("config", "user.email", "jobslayer@example.invalid")
        (self.repository / "value.txt").write_text("base\n", encoding="utf-8")
        self._git("add", "value.txt")
        self._git("commit", "-m", "baseline")
        self.base_commit = self._git("rev-parse", "HEAD")
        self.manager = GitWorktreeManager(self.repository, self.root / "workspaces")
        self.workspace = self.manager.create(
            WorkspaceSpec(
                workspace_id="scripted-workspace",
                task_id="scripted-task",
                base_commit=self.base_commit,
            )
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ("git", "-C", str(self.repository), *arguments),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def invocation(self, *, executor_type: str = "scripted_patch") -> AgentInvocation:
        return AgentInvocation(
            run_spec=AgentRunSpec(
                run_id="scripted-run",
                task_id="scripted-task",
                executor_type=executor_type,
                model_profile="deterministic-replay-v1",
                context_package_id="context-scripted",
                workspace_id="scripted-workspace",
                permission_profile="workspace_write",
                timeout_seconds=2,
                output_schema="unified_diff",
            ),
            prompt="Replay the reviewed fixture patch exactly.",
        )

    def executor(self, *, patch: bytes = PATCH) -> ScriptedPatchExecutor:
        return ScriptedPatchExecutor(
            self.manager,
            self.root / "logs",
            patch_bytes=patch,
            patch_sha256=hashlib.sha256(patch).hexdigest(),
        )

    def test_applies_patch_and_returns_hashed_terminal_evidence(self) -> None:
        executor = self.executor()
        handle = executor.start(self.invocation(), self.workspace)
        result = executor.collect(handle.run_id)

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(
            (Path(self.workspace.path) / "value.txt").read_text(encoding="utf-8"),
            "changed\n",
        )
        self.assertEqual(len(executor.events(handle.run_id)), 2)
        self.assertEqual(
            hashlib.sha256(Path(result.raw_event_log_path).read_bytes()).hexdigest(),
            result.raw_event_log_sha256,
        )

    def test_invalid_patch_is_a_failed_result_without_repository_change(self) -> None:
        executor = self.executor(patch=b"not a patch\n")
        handle = executor.start(self.invocation(), self.workspace)
        result = executor.collect(handle.run_id)

        self.assertEqual(result.status, AgentRunStatus.FAILED)
        self.assertIn("check failed", result.error_summary)
        self.assertEqual(
            (Path(self.workspace.path) / "value.txt").read_text(encoding="utf-8"),
            "base\n",
        )

    def test_rejects_invocation_for_another_executor(self) -> None:
        with self.assertRaisesRegex(ScriptedPatchError, "not assigned"):
            self.executor().start(
                self.invocation(executor_type="codex_cli"), self.workspace
            )


if __name__ == "__main__":
    unittest.main()
