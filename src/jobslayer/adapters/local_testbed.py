from __future__ import annotations

from pathlib import Path
import subprocess

from jobslayer.domain.models import TestbedInspection, TestbedSpec
from jobslayer.testbeds.inspection import TestbedInspectionError


class LocalGitTestbedInspector:
    """Read-only Git adapter for a locally available external testbed."""

    def __init__(self, checkout_path: str | Path, *, git_executable: str = "git"):
        self.checkout_path = Path(checkout_path).resolve(strict=False)
        self.git_executable = git_executable

    def inspect(self, testbed: TestbedSpec) -> TestbedInspection:
        if testbed.baseline is None:
            raise TestbedInspectionError(
                f"testbed {testbed.testbed_id!r} has no registered baseline"
            )
        if not self.checkout_path.is_dir():
            raise TestbedInspectionError(
                f"testbed checkout does not exist: {self.checkout_path}"
            )

        observed_root = Path(
            self._git("rev-parse", "--show-toplevel", required=True)
        ).resolve()
        if observed_root != self.checkout_path:
            raise TestbedInspectionError(
                f"checkout path is not the Git root: {self.checkout_path}"
            )

        head_commit = self._git("rev-parse", "--verify", "HEAD^{commit}", required=True)
        status = self._git(
            "status", "--porcelain=v1", "--untracked-files=all", required=True
        )
        tag_commit = self._git(
            "rev-parse",
            "--verify",
            f"refs/tags/{testbed.baseline.tag}^{{commit}}",
            required=False,
        )
        origin_url = self._git(
            "config", "--get", "remote.origin.url", required=False
        )
        registered_urls = {
            testbed.repository.clone_url,
            *testbed.repository.alternative_clone_urls,
        }

        return TestbedInspection(
            testbed_id=testbed.testbed_id,
            checkout_path=str(self.checkout_path),
            baseline_commit=testbed.baseline.commit,
            head_commit=head_commit,
            tag=testbed.baseline.tag,
            tag_commit=tag_commit or None,
            origin_url=origin_url or None,
            working_tree_clean=not status,
            head_matches_baseline=head_commit == testbed.baseline.commit,
            tag_matches_baseline=tag_commit == testbed.baseline.commit,
            origin_registered=origin_url in registered_urls,
            baseline_published=testbed.baseline.published,
        )

    def _git(self, *arguments: str, required: bool) -> str:
        try:
            result = subprocess.run(
                (self.git_executable, *arguments),
                cwd=self.checkout_path,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise TestbedInspectionError(f"could not launch Git: {exc}") from exc
        if result.returncode != 0:
            if not required:
                return ""
            detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
            raise TestbedInspectionError(
                f"Git {' '.join(arguments)} failed: {detail}"
            )
        return result.stdout.strip()
