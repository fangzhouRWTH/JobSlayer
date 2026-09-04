import unittest
from pathlib import Path

from jobslayer.domain.models import TestbedSpec, TestbedStatus


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class TestbedRegistryTests(unittest.TestCase):
    def test_brave_new_world_registration_is_valid(self) -> None:
        manifest_path = REPOSITORY_ROOT / "testbeds" / "brave-new-world.json"
        testbed = TestbedSpec.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )

        self.assertEqual(testbed.status, TestbedStatus.ACTIVE)
        self.assertEqual(
            testbed.repository.clone_url,
            "https://github.com/fangzhouRWTH/BraveNewWorld.git",
        )
        self.assertIn(
            "git@github.com:fangzhouRWTH/BraveNewWorld.git",
            testbed.repository.alternative_clone_urls,
        )
        self.assertIsNotNone(testbed.baseline)
        self.assertEqual(
            testbed.baseline.commit,
            "d4947e7fdca4f70970c04fcf61221b55afddfb25",
        )
        self.assertEqual(testbed.baseline.tag, "bnw-life-game-1")
        self.assertFalse(testbed.baseline.published)
        self.assertEqual(testbed.baseline.verification_command, ("./bnw", "contract"))



if __name__ == "__main__":
    unittest.main()
