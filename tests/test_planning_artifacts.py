from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.application.planning_artifacts import (
    PlanningArtifactNotFoundError,
    PlanningArtifactQuery,
    PlanningArtifactQueryError,
)


class PlanningArtifactQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.registry = LocalArtifactRegistry(self.temporary_directory.name)
        self.query = PlanningArtifactQuery(
            self.registry, max_preview_bytes=1_024
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def register(self, *, content: bytes = b"planning evidence"):
        return self.registry.register_bytes(
            task_id="plan-query",
            run_id="planning-invocation-query",
            artifact_type="task_plan.agent.raw_events",
            producer="codex-cli-planning-v1",
            content=content,
            metadata={"result": "completed"},
        )

    def test_lists_only_planning_evidence_without_storage_uri(self) -> None:
        manifest = self.register()
        self.registry.register_bytes(
            task_id="plan-query",
            artifact_type="private-unrelated",
            producer="fixture",
            content=b"hidden",
        )

        descriptors = self.query.list_for_plan("plan-query")

        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0].artifact_id, manifest.artifact_id)
        self.assertNotIn("uri", descriptors[0].model_dump(mode="json"))

    def test_preview_is_plan_bound_hash_verified_and_bounded(self) -> None:
        manifest = self.register(content=b"x" * 1_500)

        preview = self.query.preview("plan-query", manifest.artifact_id)

        self.assertTrue(preview.content_verified)
        self.assertTrue(preview.truncated)
        self.assertEqual(preview.preview_size_bytes, 1_024)
        with self.assertRaises(PlanningArtifactNotFoundError):
            self.query.preview("another-plan", manifest.artifact_id)

    def test_tampered_content_is_rejected_instead_of_previewed(self) -> None:
        manifest = self.register()
        path = Path(url2pathname(unquote(urlsplit(manifest.uri).path)))
        path.chmod(0o600)
        path.write_bytes(b"tampered")

        with self.assertRaises(PlanningArtifactQueryError):
            self.query.list_for_plan("plan-query")


if __name__ == "__main__":
    unittest.main()
