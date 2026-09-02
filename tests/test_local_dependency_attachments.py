from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from jobslayer.adapters.local_dependency_attachments import (
    directory_sha256,
    git_tree_sha256,
    reinspect_local_dependency_attachment,
    resolve_local_dependency_attachment,
)
from jobslayer.application.runbook import LocalDependencyAttachmentConfig


class LocalDependencyAttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_resolves_clean_git_checkout_and_detects_later_drift(self) -> None:
        repository = self.root / "engine"
        repository.mkdir()
        self._git(repository, "init", "-b", "main")
        self._git(repository, "config", "user.name", "JobSlayer Test")
        self._git(repository, "config", "user.email", "jobslayer@example.invalid")
        self._git(
            repository,
            "remote",
            "add",
            "origin",
            "https://example.invalid/engine.git",
        )
        (repository / "Engine.txt").write_text("pinned engine\n", encoding="utf-8")
        self._git(repository, "add", ".")
        self._git(repository, "commit", "-m", "baseline")
        revision = self._git(repository, "rev-parse", "HEAD").strip()
        config = LocalDependencyAttachmentConfig(
            attachment_id="engine-source",
            kind="git_checkout",
            environment_variable="ENGINE_SOURCE_ROOT",
            expected_sha256=git_tree_sha256(repository, revision),
            expected_revision=revision,
            repository_urls=("https://example.invalid/engine.git",),
        )

        attachment = resolve_local_dependency_attachment(config, repository)

        self.assertTrue(attachment.ready)
        self.assertEqual(attachment.observed_revision, revision)
        self.assertEqual(
            attachment.command_environment().value,
            str(repository.resolve()),
        )

        (repository / "untracked.txt").write_text("drift\n", encoding="utf-8")
        drifted = reinspect_local_dependency_attachment(attachment)
        self.assertFalse(drifted.ready)
        self.assertIn("tracked or untracked", drifted.issue or "")

    def test_directory_digest_binds_exposed_file_and_rejects_wrong_content(self) -> None:
        toolchain = self.root / "toolchain"
        toolchain.mkdir()
        exposed = toolchain / "conan_toolchain.cmake"
        exposed.write_text("set(PINNED TRUE)\n", encoding="utf-8")
        digest = directory_sha256(toolchain)
        config = LocalDependencyAttachmentConfig(
            attachment_id="toolchain",
            kind="directory",
            environment_variable="ENGINE_TOOLCHAIN",
            expected_sha256=digest,
            expose_relative_path="conan_toolchain.cmake",
        )

        attachment = resolve_local_dependency_attachment(config, toolchain)

        self.assertTrue(attachment.ready)
        self.assertEqual(attachment.exposed_path, str(exposed.resolve()))

        wrong = config.model_copy(update={"expected_sha256": "f" * 64})
        rejected = resolve_local_dependency_attachment(wrong, toolchain)
        self.assertFalse(rejected.ready)
        self.assertIn("SHA-256", rejected.issue or "")

    def test_run_pinned_directory_captures_host_identity_and_detects_drift(self) -> None:
        toolchain = self.root / "generated-toolchain"
        toolchain.mkdir()
        exposed = toolchain / "conan_toolchain.cmake"
        exposed.write_text("set(CMAKE_CXX_COMPILER cl)\n", encoding="utf-8")
        host_platform = (
            "windows"
            if os.name == "nt"
            else "linux"
            if sys.platform.startswith("linux")
            else "macos"
        )
        config = LocalDependencyAttachmentConfig(
            attachment_id="generated-toolchain",
            kind="directory",
            environment_variable="ENGINE_TOOLCHAIN",
            binding_mode="run_pinned",
            supported_platforms=(host_platform,),
            expose_relative_path="conan_toolchain.cmake",
        )

        attachment = resolve_local_dependency_attachment(config, toolchain)

        self.assertTrue(attachment.ready)
        self.assertEqual(attachment.binding_mode, "run_pinned")
        self.assertEqual(attachment.host_platform, host_platform)
        self.assertEqual(attachment.expected_sha256, attachment.observed_sha256)

        exposed.write_text("set(CMAKE_CXX_COMPILER changed)\n", encoding="utf-8")
        drifted = reinspect_local_dependency_attachment(attachment)
        self.assertFalse(drifted.ready)
        self.assertIn("SHA-256", drifted.issue or "")

    def test_missing_path_is_an_explicit_unready_projection(self) -> None:
        config = LocalDependencyAttachmentConfig(
            attachment_id="missing",
            kind="file",
            environment_variable="MISSING_FILE",
            expected_sha256="0" * 64,
        )

        attachment = resolve_local_dependency_attachment(config, None)

        self.assertFalse(attachment.ready)
        self.assertIsNone(attachment.root_path)
        self.assertIn("not configured", attachment.issue or "")

    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout


if __name__ == "__main__":
    unittest.main()
