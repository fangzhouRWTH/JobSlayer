from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class DevelopmentCheckConfigurationError(RuntimeError):
    """Raised when the requested checkout cannot run the governed dev suite."""


@dataclass(frozen=True)
class DevelopmentCheckStep:
    step_id: str
    description: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class DevelopmentCheckResult:
    step: DevelopmentCheckStep
    exit_code: int

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class DevelopmentCheckReport:
    repository_root: Path
    results: tuple[DevelopmentCheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)


CheckExecutor = Callable[[tuple[str, ...], Path], int]


def _looks_like_repository_root(path: Path) -> bool:
    return all(
        candidate.exists()
        for candidate in (
            path / "pyproject.toml",
            path / "src" / "jobslayer",
            path / "tests",
            path / "testbeds" / "brave-new-world.json",
        )
    )


def find_repository_root(explicit_root: str | Path | None = None) -> Path:
    """Resolve the JobSlayer checkout used by the development-only checks."""

    if explicit_root is not None:
        candidate = Path(explicit_root).resolve(strict=False)
        if not _looks_like_repository_root(candidate):
            raise DevelopmentCheckConfigurationError(
                f"not a JobSlayer source checkout: {candidate}"
            )
        return candidate

    starts = (Path.cwd().resolve(), Path(__file__).resolve())
    visited: set[Path] = set()
    for start in starts:
        for candidate in (start, *start.parents):
            if candidate in visited:
                continue
            visited.add(candidate)
            if _looks_like_repository_root(candidate):
                return candidate
    raise DevelopmentCheckConfigurationError(
        "could not locate a JobSlayer source checkout; pass --root"
    )


class DevelopmentCheckRunner:
    """Run the repository's single documented verification sequence."""

    def __init__(
        self,
        repository_root: str | Path,
        *,
        python_executable: str | Path | None = None,
        executor: CheckExecutor | None = None,
    ):
        self.repository_root = find_repository_root(repository_root)
        self.python_executable = str(python_executable or sys.executable)
        self.executor = executor or self._execute

    def steps(self) -> tuple[DevelopmentCheckStep, ...]:
        python = self.python_executable
        return (
            DevelopmentCheckStep(
                step_id="tests",
                description="complete deterministic unit and integration suite",
                argv=(python, "-m", "unittest", "discover", "-s", "tests", "-v"),
            ),
            DevelopmentCheckStep(
                step_id="compile",
                description="Python source and test bytecode compilation",
                argv=(python, "-m", "compileall", "-q", "src", "tests"),
            ),
            DevelopmentCheckStep(
                step_id="dependencies",
                description="installed Python dependency consistency",
                argv=(python, "-m", "pip", "check"),
            ),
            DevelopmentCheckStep(
                step_id="testbed",
                description="BraveNewWorld testbed contract validation",
                argv=(
                    python,
                    "-m",
                    "jobslayer",
                    "validate-testbed",
                    "testbeds/brave-new-world.json",
                ),
            ),
            DevelopmentCheckStep(
                step_id="runbook",
                description="BraveNewWorld task, profile, and replay input binding",
                argv=(
                    python,
                    "-m",
                    "jobslayer",
                    "validate-runbook",
                    "runbooks/bnw-scenario-slow-001.json",
                ),
            ),
            DevelopmentCheckStep(
                step_id="codex-runbook",
                description="BraveNewWorld real Codex task and validation binding",
                argv=(
                    python,
                    "-m",
                    "jobslayer",
                    "validate-runbook",
                    "runbooks/bnw-filter-demo-001-codex.json",
                ),
            ),
            DevelopmentCheckStep(
                step_id="diff",
                description="normalized Git whitespace and conflict-marker validation",
                argv=("git", "-c", "core.autocrlf=true", "diff", "--check"),
            ),
        )

    def run(self) -> DevelopmentCheckReport:
        results: list[DevelopmentCheckResult] = []
        steps = self.steps()
        for index, step in enumerate(steps, start=1):
            print(
                f"[check {index}/{len(steps)}] {step.step_id}: {step.description}",
                flush=True,
            )
            exit_code = self.executor(step.argv, self.repository_root)
            result = DevelopmentCheckResult(step=step, exit_code=exit_code)
            results.append(result)
            print(
                f"[{'pass' if result.passed else 'fail'}] {step.step_id}",
                flush=True,
            )
        report = DevelopmentCheckReport(
            repository_root=self.repository_root,
            results=tuple(results),
        )
        passed = sum(result.passed for result in report.results)
        print(
            f"development checks: {passed}/{len(report.results)} passed "
            f"for {report.repository_root}",
            flush=True,
        )
        return report

    @staticmethod
    def _execute(argv: tuple[str, ...], cwd: Path) -> int:
        try:
            return subprocess.run(list(argv), cwd=cwd, check=False).returncode
        except OSError as exc:
            print(f"could not launch {argv[0]}: {exc}")
            return 127
