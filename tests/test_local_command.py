import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.adapters.local_command import (
    CommandPolicyError,
    CommandWorkingDirectoryError,
    GovernedLocalCommandRunner,
)
from jobslayer.domain.models import (
    CommandPolicy,
    CommandRequest,
    CommandRule,
    CommandStatus,
    WorkspaceSpec,
)


class GovernedLocalCommandRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.repository = root / "source"
        self.repository.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "JobSlayer Test")
        self._git("config", "user.email", "jobslayer@example.invalid")
        (self.repository / "verify.py").write_text(
            "print('verification passed')\n", encoding="utf-8"
        )
        (self.repository / "subdir").mkdir()
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        base_commit = self._git("rev-parse", "HEAD").strip()

        self.manager = GitWorktreeManager(self.repository, root / "worktrees")
        self.manifest = self.manager.create(
            WorkspaceSpec(
                workspace_id="command-workspace",
                task_id="task-command",
                base_commit=base_commit,
            )
        )
        self.runner = GovernedLocalCommandRunner(self.manager)

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

    def policy_for(
        self,
        argv: tuple[str, ...],
        *,
        max_timeout_seconds: float = 2,
        max_output_bytes: int = 1_000,
        allow_additional_arguments: bool = False,
        accepted_exit_codes: tuple[int, ...] = (0,),
    ) -> CommandPolicy:
        return CommandPolicy(
            policy_id="test-policy",
            rules=(
                CommandRule(
                    rule_id="test-rule",
                    argv_prefix=argv,
                    allow_additional_arguments=allow_additional_arguments,
                    accepted_exit_codes=accepted_exit_codes,
                    max_timeout_seconds=max_timeout_seconds,
                ),
            ),
            max_timeout_seconds=max_timeout_seconds,
            max_output_bytes_per_stream=max_output_bytes,
        )

    def request_for(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float = 1,
        cwd: str = ".",
    ) -> CommandRequest:
        return CommandRequest(
            command_id="command-1",
            workspace_id=self.manifest.workspace_id,
            task_id=self.manifest.task_id,
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )

    def test_runs_an_exact_approved_command_with_structured_evidence(self) -> None:
        argv = (sys.executable, "verify.py")

        result = self.runner.run(
            self.manifest,
            self.request_for(argv),
            self.policy_for(argv),
        )

        self.assertEqual(result.status, CommandStatus.PASSED)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "verification passed\n")
        self.assertEqual(
            result.stdout_sha256,
            hashlib.sha256(b"verification passed\n").hexdigest(),
        )
        self.assertFalse(result.stdout_truncated)

    def test_rejects_unapproved_additional_arguments(self) -> None:
        allowed = (sys.executable, "verify.py")
        requested = (*allowed, "--unexpected")

        with self.assertRaises(CommandPolicyError):
            self.runner.run(
                self.manifest,
                self.request_for(requested),
                self.policy_for(allowed),
            )

    def test_rejects_a_timeout_larger_than_policy(self) -> None:
        argv = (sys.executable, "verify.py")

        with self.assertRaises(CommandPolicyError):
            self.runner.run(
                self.manifest,
                self.request_for(argv, timeout_seconds=3),
                self.policy_for(argv, max_timeout_seconds=2),
            )

    def test_times_out_and_terminates_the_process_group(self) -> None:
        argv = (sys.executable, "-c", "import time; time.sleep(5)")

        result = self.runner.run(
            self.manifest,
            self.request_for(argv, timeout_seconds=0.1),
            self.policy_for(argv, max_timeout_seconds=0.2),
        )

        self.assertEqual(result.status, CommandStatus.TIMED_OUT)
        self.assertIsNone(result.exit_code)
        self.assertLess(result.duration_ms, 2_000)

    def test_cleans_up_a_child_that_outlives_its_parent(self) -> None:
        code = (
            "import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])"
        )
        argv = (sys.executable, "-c", code)

        result = self.runner.run(
            self.manifest,
            self.request_for(argv),
            self.policy_for(argv),
        )

        self.assertEqual(result.status, CommandStatus.PASSED)
        self.assertLess(result.duration_ms, 2_000)

    def test_truncates_stored_output_but_hashes_the_complete_stream(self) -> None:
        argv = (sys.executable, "-c", "print('x' * 200)")
        complete_output = ("x" * 200 + "\n").encode()

        result = self.runner.run(
            self.manifest,
            self.request_for(argv),
            self.policy_for(argv, max_output_bytes=32),
        )

        self.assertTrue(result.stdout_truncated)
        self.assertEqual(len(result.stdout.encode()), 32)
        self.assertEqual(result.stdout_bytes, len(complete_output))
        self.assertEqual(
            result.stdout_sha256,
            hashlib.sha256(complete_output).hexdigest(),
        )

    def test_does_not_inherit_an_unapproved_environment_secret(self) -> None:
        variable = "JOBSLAYER_TEST_SECRET"
        previous = os.environ.get(variable)
        os.environ[variable] = "must-not-leak"
        argv = (
            sys.executable,
            "-c",
            f"import os; print(os.getenv('{variable}', 'missing'))",
        )
        try:
            result = self.runner.run(
                self.manifest,
                self.request_for(argv),
                self.policy_for(argv),
            )
        finally:
            if previous is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = previous

        self.assertEqual(result.stdout, "missing\n")

    def test_reports_a_non_accepted_exit_code_as_failed(self) -> None:
        argv = (sys.executable, "-c", "import sys; sys.exit(7)")

        result = self.runner.run(
            self.manifest,
            self.request_for(argv),
            self.policy_for(argv),
        )

        self.assertEqual(result.status, CommandStatus.FAILED)
        self.assertEqual(result.exit_code, 7)

    def test_rejects_a_working_directory_symlink_that_escapes(self) -> None:
        workspace = Path(self.manifest.path)
        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        (workspace / "escape").symlink_to(outside, target_is_directory=True)
        argv = (sys.executable, "verify.py")

        with self.assertRaises(CommandWorkingDirectoryError):
            self.runner.run(
                self.manifest,
                self.request_for(argv, cwd="escape"),
                self.policy_for(argv),
            )


if __name__ == "__main__":
    unittest.main()
