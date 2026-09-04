import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import Mock, call, patch

import start as desktop_launcher
from jobslayer.adapters.local_identity import LocalIdentityProvider
from jobslayer.desktop.app import (
    DesktopAppConfig,
    DesktopAppError,
    OwnedProcess,
    _DIRECT_PROXY_HANDLER,
    _backend_argv,
    _frontend_argv,
    _open_desktop_window,
    _prepare_identity,
    _require_available_port,
    _stop_processes,
    _validate_checkout,
    _wait_for_http,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class _FakeProcess:
    def __init__(self, return_code: int | None = None) -> None:
        self.return_code = return_code

    def poll(self) -> int | None:
        return self.return_code


class _FakeResponse:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class DesktopAppTests(unittest.TestCase):
    def _config(self, root: Path) -> DesktopAppConfig:
        npm = root / ("npm.cmd" if os.name == "nt" else "npm")
        npm.write_text("placeholder", encoding="utf-8")
        return DesktopAppConfig(
            repository_root=root,
            npm_executable=npm,
            api_port=18780,
            ui_port=14173,
        )

    def test_configuration_rejects_colliding_or_invalid_ports(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be different"):
            DesktopAppConfig(Path("."), Path("npm"), api_port=8780, ui_port=8780)
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            DesktopAppConfig(Path("."), Path("npm"), api_port=0)
        with self.assertRaisesRegex(ValueError, "between 1 and 300"):
            DesktopAppConfig(Path("."), Path("npm"), startup_timeout_seconds=0)

    def test_identity_is_create_only_short_lived_and_scoped_for_desktop(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            identity = _prepare_identity(root)
            principal = LocalIdentityProvider(identity.key_path).load_session(
                identity.session_path
            )

            self.assertEqual(principal.subject_id, "desktop-planner")
            self.assertEqual(
                principal.roles,
                ("planner", "executor", "quick-agent", "reviewer", "approver"),
            )
            self.assertLessEqual(
                (principal.valid_until - principal.authenticated_at).total_seconds(),
                24 * 60 * 60,
            )
            second = _prepare_identity(root)
            self.assertEqual(second.key_path, identity.key_path)
            self.assertNotEqual(second.session_path, identity.session_path)

    def test_service_commands_are_explicit_and_use_the_resolved_ports(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            identity = _prepare_identity(root)

            backend = _backend_argv(config, identity)
            frontend = _frontend_argv(config)

            self.assertEqual(backend[:4], (sys.executable, "-m", "jobslayer", "task-manager-api"))
            self.assertIn(str(config.api_port), backend)
            self.assertIn(str(identity.key_path), backend)
            self.assertIn("--planning-agent", backend)
            self.assertIn("codex", backend)
            self.assertIn("--allow-external-planning-agent", backend)
            self.assertIn("--allow-external-task-execution", backend)
            self.assertIn("--allow-task-manager-local-validation", backend)
            self.assertIn("--allow-task-manager-checkpoint-integration", backend)
            self.assertIn("--allow-quick-agent", backend)
            self.assertEqual(frontend[0], str(config.npm_executable))
            self.assertEqual(frontend[-5:], ("--host", "127.0.0.1", "--port", "14173", "--strictPort"))

    def test_desktop_backend_auto_binds_explicit_anygine_overrides(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "anygine-source"
            toolchain = root / "anygine-toolchain"
            source.mkdir()
            toolchain.mkdir()
            config = self._config(root)
            identity = _prepare_identity(root)

            with patch.dict(
                os.environ,
                {
                    "JOBSLAYER_ANYGINE_SOURCE_ROOT": str(source),
                    "JOBSLAYER_ANYGINE_TOOLCHAIN_ROOT": str(toolchain),
                },
            ):
                backend = _backend_argv(config, identity)

            attachments = tuple(
                backend[index + 1]
                for index, value in enumerate(backend[:-1])
                if value == "--task-manager-dependency-attachment"
            )
            self.assertEqual(
                attachments,
                (
                    f"anygine-source={source.resolve()}",
                    f"anygine-conan-toolchain={toolchain.resolve()}",
                ),
            )

    def test_health_wait_returns_json_and_reports_early_process_exit(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "service.log"
            log_path.write_text("deterministic failure", encoding="utf-8")
            running = OwnedProcess("API", _FakeProcess(), log_path)  # type: ignore[arg-type]
            payload = _wait_for_http(
                "http://127.0.0.1/health",
                processes=(running,),
                timeout_seconds=1,
                opener=lambda *_args, **_kwargs: _FakeResponse({"ready": True}),
            )
            self.assertEqual(payload, {"ready": True})

            exited = OwnedProcess("API", _FakeProcess(3), log_path)  # type: ignore[arg-type]
            with self.assertRaisesRegex(
                DesktopAppError,
                "(?s)exited during startup with code 3.*deterministic failure",
            ):
                _wait_for_http(
                    "http://127.0.0.1/health",
                    processes=(exited,),
                    timeout_seconds=1,
                )

    def test_desktop_health_checks_ignore_external_proxy_configuration(self) -> None:
        self.assertEqual(_DIRECT_PROXY_HANDLER.proxies, {})

    def test_port_preflight_fails_closed_when_another_process_owns_the_port(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        try:
            with self.assertRaisesRegex(DesktopAppError, "already in use"):
                _require_available_port(port)
        finally:
            listener.close()

    def test_port_preflight_uses_the_native_safe_reuse_option(self) -> None:
        probe = Mock()
        with patch("jobslayer.desktop.app.socket.socket", return_value=probe):
            _require_available_port(18780)

        option = socket.SO_EXCLUSIVEADDRUSE if os.name == "nt" else socket.SO_REUSEADDR
        probe.setsockopt.assert_called_once_with(socket.SOL_SOCKET, option, 1)
        probe.bind.assert_called_once_with(("127.0.0.1", 18780))
        probe.close.assert_called_once_with()

    def test_linux_service_modes_do_not_require_a_display_but_window_mode_does(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (root / "ui-framework").mkdir()
            (root / "ui-framework" / "package.json").write_text(
                "{}\n", encoding="utf-8"
            )
            (root / "jobslayer").write_text("launcher\n", encoding="utf-8")
            npm = root / "npm"
            npm.write_text("launcher\n", encoding="utf-8")

            with patch("jobslayer.desktop.app.platform.system", return_value="Linux"), patch.dict(
                os.environ,
                {},
                clear=True,
            ):
                _validate_checkout(
                    DesktopAppConfig(root, npm, smoke_test=True)
                )
                _validate_checkout(
                    DesktopAppConfig(root, npm, headless=True)
                )
                with self.assertRaisesRegex(
                    DesktopAppError,
                    "requires DISPLAY or WAYLAND_DISPLAY",
                ):
                    _validate_checkout(DesktopAppConfig(root, npm))

    def test_cleanup_stops_owned_processes_in_reverse_order(self) -> None:
        first = OwnedProcess("API", _FakeProcess(), Path("api.log"))  # type: ignore[arg-type]
        second = OwnedProcess("UI", _FakeProcess(), Path("ui.log"))  # type: ignore[arg-type]
        with patch("jobslayer.desktop.app.terminate_process_group") as terminate:
            failures = _stop_processes((first, second))

        self.assertEqual(failures, [])
        self.assertEqual(
            terminate.call_args_list,
            [
                call(second.process, timeout_seconds=3.0),
                call(first.process, timeout_seconds=3.0),
            ],
        )

    def test_webview_wrapper_forces_modern_platform_renderer(self) -> None:
        fake = types.SimpleNamespace(
            create_window=Mock(),
            start=Mock(),
        )
        with patch.dict(sys.modules, {"webview": fake}), patch.object(
            os, "name", "nt"
        ):
            _open_desktop_window("http://127.0.0.1:4173/", debug=False)

        fake.create_window.assert_called_once()
        fake.start.assert_called_once_with(
            gui="edgechromium", debug=False, private_mode=True
        )

    def test_linux_webview_wrapper_forces_qt_renderer(self) -> None:
        fake = types.SimpleNamespace(
            create_window=Mock(),
            start=Mock(),
        )
        with patch.dict(sys.modules, {"webview": fake}), patch.object(
            os, "name", "posix"
        ):
            _open_desktop_window("http://127.0.0.1:4173/", debug=True)

        fake.start.assert_called_once_with(gui="qt", debug=True, private_mode=True)

    def test_root_script_help_does_not_require_initialization(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPOSITORY_ROOT / "start.py"), "--help"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("native desktop window", result.stdout)
        self.assertIn("--headless", result.stdout)

    def test_desktop_launcher_detects_the_venv_by_prefix_not_symlink_target(self) -> None:
        with TemporaryDirectory() as directory:
            venv = Path(directory) / ".venv"
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

            with patch.object(desktop_launcher.sys, "prefix", str(venv)):
                self.assertTrue(
                    desktop_launcher._running_in_initialized_python(python)
                )
            with patch.object(desktop_launcher.sys, "prefix", str(Path(directory) / "system")):
                self.assertFalse(
                    desktop_launcher._running_in_initialized_python(python)
                )


if __name__ == "__main__":
    unittest.main()
