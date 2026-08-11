import hashlib
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from jobslayer.adapters.local_artifacts import (
    ArtifactIntegrityError,
    LocalArtifactRegistry,
)


class LocalArtifactRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.registry = LocalArtifactRegistry(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_registers_content_and_persists_a_retrievable_manifest(self) -> None:
        content = b"deterministic evidence\n"

        manifest = self.registry.register_bytes(
            task_id="task-1",
            run_id="run-1",
            artifact_type="test-log",
            producer="unit-test",
            content=content,
        )

        self.assertEqual(manifest.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(self.registry.read(manifest), content)
        self.assertEqual(self.registry.get(manifest.artifact_id), manifest)
        self.assertTrue(self.registry.verify(manifest))

    def test_deduplicates_bytes_but_keeps_distinct_evidence_ids(self) -> None:
        arguments = {
            "task_id": "task-1",
            "artifact_type": "test-log",
            "producer": "unit-test",
            "content": b"same bytes",
        }

        first = self.registry.register_bytes(**arguments)
        second = self.registry.register_bytes(**arguments)

        self.assertNotEqual(first.artifact_id, second.artifact_id)
        self.assertEqual(first.uri, second.uri)
        self.assertEqual(first.sha256, second.sha256)

    def test_detects_content_tampering(self) -> None:
        manifest = self.registry.register_bytes(
            task_id="task-1",
            artifact_type="test-log",
            producer="unit-test",
            content=b"original",
        )
        artifact_path = Path(
            url2pathname(unquote(urlsplit(manifest.uri).path))
        )
        artifact_path.chmod(0o600)
        artifact_path.write_bytes(b"tampered")

        self.assertFalse(self.registry.verify(manifest))
        with self.assertRaises(ArtifactIntegrityError):
            self.registry.read(manifest)


if __name__ == "__main__":
    unittest.main()
