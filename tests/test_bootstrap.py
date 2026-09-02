import json
import os
import subprocess
import sys
import tomllib
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.bootstrap import (
    BootstrapError,
    BootstrapManager,
    _python_state_matches,
    parse_version,
    platform_key,
    safe_extract_archive,
    version_at_least,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class BootstrapContractTests(unittest.TestCase):
    def _minimal_repository(self, root: Path) -> None:
        (root / "bootstrap").mkdir(parents=True)
        (root / "ui-framework").mkdir()
        (root / "pyproject.toml").write_text(
            "[build-system]\nrequires = ['setuptools>=69']\n",
            encoding="utf-8",
        )
        (root / "ui-framework" / "package.json").write_text(
            '{"name":"test","private":true}\n',
            encoding="utf-8",
        )
        (root / "ui-framework" / "package-lock.json").write_text(
            '{"name":"test","lockfileVersion":3}\n',
            encoding="utf-8",
        )
        (root / "bootstrap" / "toolchains.json").write_bytes(
            (REPOSITORY_ROOT / "bootstrap" / "toolchains.json").read_bytes()
        )

    def test_supported_platforms_map_to_pinned_distribution_keys(self) -> None:
        self.assertEqual(platform_key("Windows", "AMD64"), "windows-x86_64")
        self.assertEqual(platform_key("Linux", "aarch64"), "linux-arm64")
        self.assertEqual(platform_key("Darwin", "arm64"), "darwin-arm64")

        config = json.loads(
            (REPOSITORY_ROOT / "bootstrap" / "toolchains.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(config["node"]["distributions"]),
            {
                "windows-x86_64",
                "windows-arm64",
                "linux-x86_64",
                "linux-arm64",
                "darwin-x86_64",
                "darwin-arm64",
            },
        )
        for distribution in config["node"]["distributions"].values():
            self.assertRegex(distribution["sha256"], r"^[0-9a-f]{64}$")

    def test_unsupported_platform_is_rejected(self) -> None:
        with self.assertRaisesRegex(BootstrapError, "unsupported bootstrap platform"):
            platform_key("FreeBSD", "x86_64")

    def test_versions_are_parsed_and_minimum_is_enforced(self) -> None:
        self.assertEqual(parse_version("v24.19.0"), (24, 19, 0))
        self.assertTrue(version_at_least("22.12.0", "22.12.0"))
        self.assertFalse(version_at_least("20.19.9", "22.12.0"))
        with self.assertRaises(BootstrapError):
            parse_version("latest")

    def test_python_environment_with_extra_superset_satisfies_base_checks(self) -> None:
        with TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "component": "python",
                        "platform": "windows-x86_64",
                        "manifest_sha256": "abc",
                        "extras": ["desktop", "observability"],
                    }
                ),
                encoding="utf-8",
            )
            expected = {
                "schema_version": "1.0",
                "component": "python",
                "platform": "windows-x86_64",
                "manifest_sha256": "abc",
                "extras": [],
            }

            self.assertTrue(_python_state_matches(state, expected))
            self.assertFalse(
                _python_state_matches(state, {**expected, "extras": ["postgres"]})
            )

    def test_desktop_extra_pins_platform_specific_webview_backends(self) -> None:
        configuration = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )

        desktop = configuration["project"]["optional-dependencies"]["desktop"]
        self.assertEqual(
            desktop,
            [
                "pywebview==6.2.1; sys_platform == 'win32'",
                "pywebview[qt]==6.2.1; sys_platform == 'linux'",
            ],
        )

    def test_archive_extraction_accepts_normal_members_and_rejects_escape(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            safe_archive = root / "safe.zip"
            with zipfile.ZipFile(safe_archive, "w") as bundle:
                bundle.writestr("node/bin/node", b"binary")
            output = root / "safe-output"
            safe_extract_archive(safe_archive, output)
            self.assertEqual((output / "node" / "bin" / "node").read_bytes(), b"binary")

            unsafe_archive = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe_archive, "w") as bundle:
                bundle.writestr("../outside", b"escape")
            with self.assertRaisesRegex(BootstrapError, "escapes extraction root"):
                safe_extract_archive(unsafe_archive, root / "unsafe-output")
            self.assertFalse((root / "outside").exists())

    def test_offline_mode_rejects_a_tampered_cached_archive(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._minimal_repository(root)
            cache = root / "cache"
            manager = BootstrapManager(
                root,
                tool_cache=cache,
                offline=True,
                verbose=False,
            )
            archive = cache / "downloads" / "node.zip"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"tampered")

            with self.assertRaisesRegex(BootstrapError, "verified cached Node archive"):
                manager._download_node_archive(  # noqa: SLF001 - contract test
                    archive,
                    "https://invalid.example/node.zip",
                    "0" * 64,
                )
            self.assertEqual(archive.read_bytes(), b"tampered")

    def test_explicit_missing_node_fails_closed_without_fallback(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._minimal_repository(root)
            manager = BootstrapManager(
                root,
                tool_cache=root / "cache",
                verbose=False,
            )
            with patch.dict(
                os.environ,
                {"JOBSLAYER_NODE": str(root / "missing-node")},
                clear=False,
            ):
                with self.assertRaisesRegex(BootstrapError, "does not exist"):
                    manager.find_node()

    def test_foreign_or_incomplete_venv_is_not_overwritten(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._minimal_repository(root)
            (root / ".venv").mkdir()
            manager = BootstrapManager(
                root,
                tool_cache=root / "cache",
                verbose=False,
            )

            with self.assertRaisesRegex(BootstrapError, "separate checkout"):
                manager.ensure_python()

    def test_check_mode_is_read_only_and_machine_readable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._minimal_repository(root)
            cache = root / "cache"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts" / "bootstrap.py"),
                    "--root",
                    str(root),
                    "--tool-cache",
                    str(cache),
                    "--check",
                    "--json",
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ready"])
            self.assertFalse(payload["python"]["ready"])
            self.assertFalse(payload["ui"]["ready"])
            self.assertFalse((root / ".venv").exists())
            self.assertFalse(cache.exists())

    def test_platform_wrappers_preserve_cross_platform_line_endings(self) -> None:
        windows = (REPOSITORY_ROOT / "init.cmd").read_bytes()
        posix = (REPOSITORY_ROOT / "init.sh").read_bytes()

        self.assertIn(b"\r\n", windows)
        self.assertNotIn(b"\r\r\n", windows)
        self.assertEqual(posix.splitlines(keepends=True)[0], b"#!/bin/sh\n")
        self.assertNotIn(b"\r\n", posix)


if __name__ == "__main__":
    unittest.main()
