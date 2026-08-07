import subprocess
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path

from jobslayer.adapters.local_testbed import LocalGitTestbedInspector
from jobslayer.domain.models import TestbedSpec
from jobslayer.testbeds.inspection import TestbedInspectionError


class LocalGitTestbedInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.checkout = Path(self.temporary_directory.name) / "testbed"
        self.checkout.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "JobSlayer Test")
        self._git("config", "user.email", "jobslayer@example.invalid")
        (self.checkout / "README.md").write_text("baseline\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "baseline")
        self.commit = self._git("rev-parse", "HEAD")
        self._git("tag", "-a", "bnw-0", "-m", "baseline")
        self._git("remote", "add", "origin", "https://example.invalid/testbed.git")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ("git", *arguments),
            cwd=self.checkout,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _spec(self, *, include_baseline: bool = True) -> TestbedSpec:
        payload = {
            "testbed_id": "local-testbed",
            "display_name": "Local Testbed",
            "purpose": "Verify read-only local inspection",
            "status": "bootstrapping",
            "repository": {
                "clone_url": "https://example.invalid/testbed.git",
                "default_branch": "main",
            },
            "architecture_areas": ["simulation"],
            "capability_targets": ["deterministic_test"],
        }
        if include_baseline:
            payload["baseline"] = {
                "commit": self.commit,
                "tag": "bnw-0",
                "published": False,
                "verification_command": ["./bnw", "check"],
            }
        return TestbedSpec.model_validate(payload)

    def test_accepts_registered_clean_local_baseline(self) -> None:
        inspection = LocalGitTestbedInspector(self.checkout).inspect(self._spec())

        self.assertTrue(inspection.valid_local_baseline)
        self.assertTrue(inspection.working_tree_clean)
        self.assertTrue(inspection.head_matches_baseline)
        self.assertTrue(inspection.tag_matches_baseline)
        self.assertTrue(inspection.origin_registered)
        self.assertFalse(inspection.baseline_published)

    def test_dirty_checkout_fails_local_baseline_gate(self) -> None:
        (self.checkout / "untracked.txt").write_text("dirty\n", encoding="utf-8")

        inspection = LocalGitTestbedInspector(self.checkout).inspect(self._spec())

        self.assertFalse(inspection.valid_local_baseline)
        self.assertFalse(inspection.working_tree_clean)

    def test_missing_registered_baseline_is_rejected(self) -> None:
        with self.assertRaisesRegex(TestbedInspectionError, "no registered baseline"):
            LocalGitTestbedInspector(self.checkout).inspect(
                self._spec(include_baseline=False)
            )


if __name__ == "__main__":
    unittest.main()
