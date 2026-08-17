import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jobslayer.development.checks import (
    DevelopmentCheckConfigurationError,
    DevelopmentCheckRunner,
    find_repository_root,
)


class DevelopmentCheckRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "src" / "jobslayer").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "testbeds").mkdir()
        (self.root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (self.root / "testbeds" / "brave-new-world.json").write_text(
            "{}\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_resolves_an_explicit_source_checkout(self) -> None:
        self.assertEqual(find_repository_root(self.root), self.root.resolve())

    def test_rejects_a_directory_without_the_repository_contract(self) -> None:
        with self.assertRaises(DevelopmentCheckConfigurationError):
            find_repository_root(self.root / "tests")

    def test_runs_every_governed_step_and_reports_failure(self) -> None:
        invocations: list[tuple[tuple[str, ...], Path]] = []

        def execute(argv: tuple[str, ...], cwd: Path) -> int:
            invocations.append((argv, cwd))
            return 3 if argv[:3] == ("fixture-python", "-m", "pip") else 0

        runner = DevelopmentCheckRunner(
            self.root,
            python_executable="fixture-python",
            executor=execute,
        )
        with redirect_stdout(io.StringIO()) as stdout:
            report = runner.run()

        self.assertEqual(
            tuple(result.step.step_id for result in report.results),
            (
                "tests",
                "compile",
                "dependencies",
                "ui",
                "testbed",
                "runbook",
                "codex-runbook",
                "diff",
            ),
        )
        self.assertEqual(len(invocations), 8)
        self.assertTrue(all(cwd == self.root.resolve() for _, cwd in invocations))
        self.assertFalse(report.passed)
        self.assertIn("7/8 passed", stdout.getvalue())
        self.assertEqual(
            report.results[0].step.argv,
            (
                "fixture-python",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-v",
            ),
        )
        self.assertEqual(
            report.results[3].step.argv,
            (
                "fixture-python",
                "scripts/bootstrap.py",
                "--offline",
                "--",
                "npm",
                "--prefix",
                "ui-framework",
                "run",
                "check",
            ),
        )
        self.assertEqual(
            report.results[-1].step.argv,
            ("git", "-c", "core.autocrlf=true", "diff", "--check"),
        )


if __name__ == "__main__":
    unittest.main()
