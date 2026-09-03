from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.source_ui_designs import SourceControlledUIDesignRegistry
from jobslayer.adapters.ui_ux_pro_max import (
    UIUXProMaxAdvisor,
    UIUXProMaxExecutionError,
    UIUXProMaxLockError,
)
from jobslayer.application.ui_advice import UIAdviceService
from jobslayer.ui_advice import (
    UIAdviceMode,
    UIAdviceRecommendationKind,
    UIAdviceRequest,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPOSITORY_ROOT / "integrations" / "ui-ux-pro-max" / "lock.json"
CATALOG_PATH = REPOSITORY_ROOT / "ui-designs" / "catalog.json"


class UIAdviceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.active = SourceControlledUIDesignRegistry(CATALOG_PATH).get_active(
            "task-manager"
        )

    def request(self, **changes: object) -> UIAdviceRequest:
        values: dict[str, object] = {
            "request_id": "ui-advice-test-001",
            "page_id": self.active.binding.page_id,
            "scheme_id": self.active.binding.scheme_id,
            "revision": self.active.binding.revision,
            "descriptor_sha256": self.active.binding.descriptor_sha256,
            "query": "live updates accessibility",
            "mode": UIAdviceMode.STACK,
            "stack": "react",
        }
        values.update(changes)
        return UIAdviceRequest.model_validate(values)

    def test_request_requires_exactly_one_mode_specific_selector(self) -> None:
        with self.assertRaisesRegex(ValidationError, "exactly one domain"):
            self.request(mode=UIAdviceMode.DOMAIN, stack=None)
        with self.assertRaisesRegex(ValidationError, "cannot select"):
            self.request(
                mode=UIAdviceMode.DESIGN_SYSTEM,
                domain="ux",
                stack=None,
            )
        with self.assertRaisesRegex(ValidationError, "design-dial"):
            self.request(mode=UIAdviceMode.STACK, density=8)

    def test_pinned_core_snapshot_and_upstream_data_validation_pass(self) -> None:
        advisor = UIUXProMaxAdvisor(REPOSITORY_ROOT, LOCK_PATH)

        identity = advisor.validate_snapshot(run_upstream_validation=True)

        self.assertEqual(identity.provider_id, "ui-ux-pro-max")
        self.assertEqual(identity.provider_version, "2.15.0")
        self.assertEqual(identity.source_ref, "v2.15.0")
        self.assertEqual(advisor.lock.expected_file_count, 45)
        self.assertFalse((advisor.snapshot_root / "package.json").exists())
        self.assertFalse((advisor.snapshot_root / "templates").exists())
        self.assertFalse((advisor.snapshot_root / "skills").exists())
        self.assertEqual(list(advisor.snapshot_root.rglob("__pycache__")), [])

    def test_snapshot_content_drift_is_rejected_before_execution(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "third_party" / "ui-ux-pro-max" / "2.15.0"
            shutil.copytree(
                REPOSITORY_ROOT / "third_party" / "ui-ux-pro-max" / "2.15.0",
                snapshot,
            )
            lock = root / "integrations" / "ui-ux-pro-max" / "lock.json"
            lock.parent.mkdir(parents=True)
            shutil.copy2(LOCK_PATH, lock)
            with (snapshot / "data" / "ux-guidelines.csv").open(
                "ab"
            ) as stream:
                stream.write(b"\ncontent-drift")

            advisor = UIUXProMaxAdvisor(root, lock)
            with self.assertRaisesRegex(UIUXProMaxLockError, "hash mismatch"):
                advisor.advise(self.request())

    def test_symlinked_lock_is_rejected_before_loading(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            integrations = root / "integrations" / "ui-ux-pro-max"
            integrations.mkdir(parents=True)
            link = integrations / "lock.json"
            try:
                link.symlink_to(LOCK_PATH)
            except OSError as exc:  # pragma: no cover - platform privilege fallback
                self.skipTest(f"symlink creation unavailable: {exc}")

            with self.assertRaisesRegex(UIUXProMaxLockError, "cannot be a symlink"):
                UIUXProMaxAdvisor(root, link)

    def test_unsupported_provider_selector_is_rejected(self) -> None:
        advisor = UIUXProMaxAdvisor(REPOSITORY_ROOT, LOCK_PATH)
        request = self.request(
            mode=UIAdviceMode.DOMAIN,
            domain="private-network",
            stack=None,
        )

        with self.assertRaisesRegex(UIUXProMaxExecutionError, "unsupported"):
            advisor.advise(request)

    def test_stack_advice_is_normalized_and_raw_output_is_retained(self) -> None:
        advisor = UIUXProMaxAdvisor(REPOSITORY_ROOT, LOCK_PATH)

        response = advisor.advise(self.request())

        self.assertEqual(len(response.recommendations), 3)
        self.assertEqual(
            response.recommendations[0].kind,
            UIAdviceRecommendationKind.STACK_GUIDELINE,
        )
        self.assertEqual(response.recommendations[0].title, "Announce dynamic content")
        raw = json.loads(response.raw_output)
        self.assertEqual(raw["stack"], "react")
        self.assertEqual(raw["count"], 3)

    def test_query_cannot_inject_a_provider_persistence_flag(self) -> None:
        advisor = UIUXProMaxAdvisor(REPOSITORY_ROOT, LOCK_PATH)

        response = advisor.advise(
            self.request(
                mode=UIAdviceMode.DESIGN_SYSTEM,
                query="--persist",
                stack=None,
            )
        )

        raw = json.loads(response.raw_output)
        self.assertIsNone(raw["persistence"])

    def test_service_registers_raw_and_normalized_suid_bound_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            artifacts = LocalArtifactRegistry(Path(directory) / "artifacts")
            collection = UIAdviceService(
                UIUXProMaxAdvisor(REPOSITORY_ROOT, LOCK_PATH), artifacts
            ).collect(task_id="ui-task-manager", request=self.request())

            self.assertEqual(
                collection.raw_artifact.artifact_type,
                "ui_advice.provider_raw",
            )
            self.assertEqual(
                collection.normalized_artifact.artifact_type,
                "ui_advice.normalized_evidence",
            )
            self.assertTrue(artifacts.verify(collection.raw_artifact))
            self.assertTrue(artifacts.verify(collection.normalized_artifact))
            raw = artifacts.read(collection.raw_artifact)
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(),
                collection.evidence.raw_output_sha256,
            )
            self.assertEqual(
                collection.evidence.request.descriptor_sha256,
                self.active.binding.descriptor_sha256,
            )
            normalized = json.loads(artifacts.read(collection.normalized_artifact))
            self.assertEqual(normalized["evidence_id"], collection.evidence.evidence_id)
            self.assertEqual(
                collection.normalized_artifact.metadata["raw_artifact_id"],
                collection.raw_artifact.artifact_id,
            )


if __name__ == "__main__":
    unittest.main()
