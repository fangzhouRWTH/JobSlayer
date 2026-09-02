#!/usr/bin/env python3
"""One-command bootstrap and desktop launcher for a JobSlayer source checkout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = REPOSITORY_ROOT / "src"
INITIALIZED_MARKER = "JOBSLAYER_DESKTOP_INITIALIZED"
NPM_MARKER = "JOBSLAYER_DESKTOP_NPM"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "initialize JobSlayer, start its TaskManager API and UI, and open "
            "the UI in a native desktop window"
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="inspect initialization state without installing or starting services",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable output with --check",
    )
    parser.add_argument(
        "--no-init",
        action="store_true",
        help="fail if dependencies are not already initialized",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="forbid downloads while preparing the environment",
    )
    parser.add_argument(
        "--force-init",
        action="store_true",
        help="reapply manifest-managed Python and UI dependencies",
    )
    parser.add_argument("--api-port", type=int, default=8780)
    parser.add_argument("--ui-port", type=int, default=4173)
    parser.add_argument("--startup-timeout", type=float, default=45.0)
    launch_mode = parser.add_mutually_exclusive_group()
    launch_mode.add_argument(
        "--headless",
        action="store_true",
        help="start both services without creating the desktop window",
    )
    launch_mode.add_argument(
        "--smoke-test",
        action="store_true",
        help="start, health-check and stop both services without opening a window",
    )
    launch_mode.add_argument(
        "--window-smoke-seconds",
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--debug-webview",
        action="store_true",
        help="enable the native WebView developer tools",
    )
    return parser


def _supported_host() -> bool:
    return os.name == "nt" or platform.system() == "Linux"


def _run_initialized(arguments: argparse.Namespace, npm: Path) -> int:
    sys.path.insert(0, str(SOURCE_ROOT))
    from jobslayer.desktop.app import DesktopAppConfig, run_desktop_app

    try:
        config = DesktopAppConfig(
            repository_root=REPOSITORY_ROOT,
            npm_executable=npm,
            api_port=arguments.api_port,
            ui_port=arguments.ui_port,
            startup_timeout_seconds=arguments.startup_timeout,
            headless=arguments.headless,
            smoke_test=arguments.smoke_test,
            window_smoke_seconds=arguments.window_smoke_seconds,
            debug_webview=arguments.debug_webview,
        )
    except ValueError as exc:
        print(f"invalid desktop launch configuration: {exc}", file=sys.stderr)
        return 2
    return run_desktop_app(config)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.json and not arguments.check:
        print("--json is supported only with --check", file=sys.stderr)
        return 2
    if not _supported_host():
        print("JobSlayer desktop app supports native Windows and Linux hosts", file=sys.stderr)
        return 1

    if os.environ.get(INITIALIZED_MARKER) == "1":
        npm_value = os.environ.get(NPM_MARKER)
        if not npm_value or not Path(npm_value).is_file():
            print("initialized desktop launch is missing its resolved npm executable", file=sys.stderr)
            return 1
        return _run_initialized(arguments, Path(npm_value))

    from scripts.bootstrap import BootstrapError, BootstrapManager

    try:
        manager = BootstrapManager(
            REPOSITORY_ROOT,
            offline=arguments.offline,
            force=arguments.force_init,
            extras=("desktop",),
        )
        if arguments.check or arguments.no_init:
            result = manager.inspect(include_ui=True)
            if arguments.check:
                if arguments.json:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(
                        "JobSlayer desktop environment: "
                        + ("ready" if result["ready"] else "not ready")
                    )
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["ready"] else 1
            if not result["ready"]:
                print(
                    "desktop environment is not ready; rerun without --no-init",
                    file=sys.stderr,
                )
                return 1

        print("[1/4] checking and initializing Python, Node and UI dependencies", flush=True)
        python, runtime = manager.install(include_ui=True)
        if runtime is None:
            raise BootstrapError("desktop launch requires the initialized UI toolchain")

        environment = os.environ.copy()
        environment[INITIALIZED_MARKER] = "1"
        environment[NPM_MARKER] = str(runtime.npm)
        environment["PATH"] = os.pathsep.join(
            (str(runtime.path_entry), environment.get("PATH", ""))
        )
        if Path(sys.executable).resolve() != python.resolve():
            command = (str(python), str(Path(__file__).resolve()), *(argv or sys.argv[1:]))
            if os.name == "nt":
                return subprocess.call(
                    command,
                    cwd=REPOSITORY_ROOT,
                    env=environment,
                )
            os.execve(
                python,
                command,
                environment,
            )
        os.environ.update(
            {
                INITIALIZED_MARKER: "1",
                NPM_MARKER: str(runtime.npm),
                "PATH": environment["PATH"],
            }
        )
        return _run_initialized(arguments, runtime.npm)
    except (BootstrapError, OSError) as exc:
        print(f"JobSlayer desktop initialization failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
