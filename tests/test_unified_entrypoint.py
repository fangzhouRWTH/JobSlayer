import json
import os
import stat
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_LAUNCHER = REPOSITORY_ROOT / "jobslayer"
WINDOWS_LAUNCHER = REPOSITORY_ROOT / "jobslayer.cmd"


class UnifiedEntrypointTests(unittest.TestCase):
    @staticmethod
    def repository_command() -> tuple[str, ...]:
        if os.name == "nt":
            return (str(WINDOWS_LAUNCHER),)
        return (str(SOURCE_LAUNCHER),)

    def run_command(self, *argv: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["JOBSLAYER_PYTHON"] = sys.executable
        return subprocess.run(
            list(argv),
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_repository_launcher_is_executable_and_exposes_primary_commands(self) -> None:
        if os.name == "nt":
            self.assertTrue(WINDOWS_LAUNCHER.is_file())
        else:
            mode = SOURCE_LAUNCHER.stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR)

        result = self.run_command(*self.repository_command(), "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("serve-review,ui,check", result.stdout)
        self.assertIn("run-task", result.stdout)
        self.assertIn("validate-runbook", result.stdout)
        self.assertIn("inspect-run", result.stdout)
        self.assertIn("inspect-readiness", result.stdout)
        self.assertIn("inspect-recovery", result.stdout)
        self.assertIn("recover-run", result.stdout)
        self.assertIn("review-run", result.stdout)
        self.assertIn("run-ui", result.stdout)
        self.assertIn("apply-run-decision", result.stdout)
        self.assertIn("integrate-run", result.stdout)
        self.assertIn("cleanup-run", result.stdout)

    def test_source_and_module_entrypoints_share_the_same_ui_alias(self) -> None:
        source = self.run_command(*self.repository_command(), "ui", "--help")
        module = self.run_command(sys.executable, "-m", "jobslayer", "ui", "--help")

        self.assertEqual(source.returncode, 0, source.stderr)
        self.assertEqual(module.returncode, 0, module.stderr)
        self.assertEqual(source.stdout, module.stdout)

    def test_readiness_command_reports_missing_evidence_with_a_nonzero_exit(self) -> None:
        with TemporaryDirectory() as state_root:
            result = self.run_command(
                *self.repository_command(),
                "inspect-readiness",
                "--state-root",
                state_root,
                "--required-reviewed-tasks",
                "1",
            )

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["automated_gate_passes"])
        self.assertEqual(payload["counts"]["discovered_runs"], 0)
        self.assertTrue(payload["manual_confirmation_required"])

    def test_installed_console_script_targets_the_public_launcher(self) -> None:
        configuration = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )

        self.assertEqual(
            configuration["project"]["scripts"]["jobslayer"],
            "jobslayer.launcher:main",
        )

    def test_invalid_explicit_python_fails_without_fallback(self) -> None:
        environment = os.environ.copy()
        environment["JOBSLAYER_PYTHON"] = str(
            REPOSITORY_ROOT / ".missing-python"
        )

        result = subprocess.run(
            [*self.repository_command(), "--help"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("configured JobSlayer Python does not exist", result.stderr)

    def test_posix_launcher_is_checked_out_with_a_portable_shebang(self) -> None:
        first_line = SOURCE_LAUNCHER.read_bytes().splitlines(keepends=True)[0]

        self.assertEqual(first_line, b"#!/usr/bin/env python3\n")


if __name__ == "__main__":
    unittest.main()
