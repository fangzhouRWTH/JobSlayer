from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from http.client import HTTPResponse
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from typing import BinaryIO, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from uuid import uuid4

from jobslayer.adapters.local_identity import LocalIdentityError, LocalIdentityProvider
from jobslayer.execution.processes import (
    ProcessGroupTerminationError,
    process_group_launch_kwargs,
    terminate_process_group,
)


LOOPBACK_HOST = "127.0.0.1"


class DesktopAppError(RuntimeError):
    """Raised when the owned local desktop application cannot start safely."""


@dataclass(frozen=True)
class DesktopAppConfig:
    repository_root: Path
    npm_executable: Path
    api_port: int = 8780
    ui_port: int = 4173
    startup_timeout_seconds: float = 45.0
    headless: bool = False
    smoke_test: bool = False
    window_smoke_seconds: float | None = None
    debug_webview: bool = False

    def __post_init__(self) -> None:
        root = self.repository_root.resolve(strict=False)
        npm = self.npm_executable.resolve(strict=False)
        object.__setattr__(self, "repository_root", root)
        object.__setattr__(self, "npm_executable", npm)
        if self.api_port == self.ui_port:
            raise ValueError("API and UI ports must be different")
        for name, port in (("API", self.api_port), ("UI", self.ui_port)):
            if not 1 <= port <= 65535:
                raise ValueError(f"{name} port must be between 1 and 65535")
        if not 1 <= self.startup_timeout_seconds <= 300:
            raise ValueError("startup timeout must be between 1 and 300 seconds")
        if self.window_smoke_seconds is not None and not (
            0.5 <= self.window_smoke_seconds <= 30
        ):
            raise ValueError("window smoke duration must be between 0.5 and 30 seconds")

    @property
    def api_url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.api_port}/api/task-manager/session"

    @property
    def ui_url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.ui_port}/"

    @property
    def proxied_health_url(self) -> str:
        return f"{self.ui_url}api/task-manager/session"


@dataclass(frozen=True)
class DesktopIdentity:
    key_path: Path
    session_path: Path


@dataclass(frozen=True)
class OwnedProcess:
    name: str
    process: subprocess.Popen[bytes]
    log_path: Path


def _validate_checkout(config: DesktopAppConfig) -> None:
    required = (
        config.repository_root / "pyproject.toml",
        config.repository_root / "ui-framework" / "package.json",
        config.repository_root / "jobslayer",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise DesktopAppError("invalid JobSlayer checkout; missing: " + ", ".join(missing))
    if not config.npm_executable.is_file():
        raise DesktopAppError(f"initialized npm executable is missing: {config.npm_executable}")
    if os.name != "nt" and platform.system() != "Linux":
        raise DesktopAppError("desktop app supports native Windows and Linux hosts")
    if not config.headless and platform.system() == "Linux" and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        raise DesktopAppError(
            "Linux desktop launch requires DISPLAY or WAYLAND_DISPLAY; use --headless for services only"
        )


def _require_available_port(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((LOOPBACK_HOST, port))
    except OSError as exc:
        raise DesktopAppError(f"loopback port {port} is already in use") from exc
    finally:
        probe.close()


def _prepare_identity(root: Path) -> DesktopIdentity:
    identity_root = root / ".jobslayer" / "desktop" / "identity"
    key_path = identity_root / "planner-key.json"
    provider = LocalIdentityProvider(key_path)
    if not key_path.exists():
        provider.create_key()
    session_path = identity_root / f"planner-session-{os.getpid()}-{uuid4().hex}.json"
    session = provider.issue(
        subject_id="desktop-planner",
        display_name="JobSlayer desktop planner",
        roles=("planner",),
        lifetime=timedelta(hours=24),
    )
    provider.create_session_file(session_path, session)
    return DesktopIdentity(key_path=key_path, session_path=session_path)


def _backend_argv(config: DesktopAppConfig, identity: DesktopIdentity) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "jobslayer",
        "task-manager-api",
        "--root",
        str(config.repository_root),
        "--state-root",
        str(config.repository_root / ".jobslayer" / "orchestration"),
        "--identity-session",
        str(identity.session_path),
        "--identity-key",
        str(identity.key_path),
        "--port",
        str(config.api_port),
    )


def _frontend_argv(config: DesktopAppConfig) -> tuple[str, ...]:
    return (
        str(config.npm_executable),
        "--prefix",
        "ui-framework",
        "run",
        "task-manager",
        "--",
        "--host",
        LOOPBACK_HOST,
        "--port",
        str(config.ui_port),
        "--strictPort",
    )


def _tail(path: Path, *, maximum_bytes: int = 4096) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - maximum_bytes))
            return stream.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _launch_process(
    name: str,
    argv: Sequence[str],
    *,
    config: DesktopAppConfig,
    environment: dict[str, str],
    log: BinaryIO,
    log_path: Path,
) -> OwnedProcess:
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=config.repository_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            **process_group_launch_kwargs(),
        )
    except OSError as exc:
        raise DesktopAppError(f"could not launch {name}: {exc}") from exc
    return OwnedProcess(name=name, process=process, log_path=log_path)


def _wait_for_http(
    url: str,
    *,
    processes: Sequence[OwnedProcess],
    timeout_seconds: float,
    opener: Callable[..., HTTPResponse] = urlopen,
) -> dict[str, object] | None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "service did not respond"
    while time.monotonic() < deadline:
        for owned in processes:
            return_code = owned.process.poll()
            if return_code is not None:
                detail = _tail(owned.log_path)
                suffix = f"\n{detail}" if detail else ""
                raise DesktopAppError(
                    f"{owned.name} exited during startup with code {return_code}{suffix}"
                )
        try:
            with opener(url, timeout=0.75) as response:
                body = response.read()
                if response.status == 200:
                    if not body:
                        return None
                    value = json.loads(body.decode("utf-8"))
                    return value if isinstance(value, dict) else None
                last_error = f"HTTP {response.status}"
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise DesktopAppError(f"timed out waiting for {url}: {last_error}")


def _open_desktop_window(
    url: str,
    *,
    debug: bool,
    close_after_seconds: float | None = None,
) -> None:
    try:
        import webview
    except ImportError as exc:
        raise DesktopAppError(
            "pywebview is unavailable; rerun start.py without --no-init"
        ) from exc
    gui = "edgechromium" if os.name == "nt" else "qt"
    window = webview.create_window(
        "JobSlayer TaskManager",
        url,
        width=1440,
        height=920,
        min_size=(1024, 700),
        background_color="#0d1014",
        text_select=True,
    )
    try:
        if close_after_seconds is None:
            webview.start(gui=gui, debug=debug, private_mode=True)
        else:
            def close_after_delay() -> None:
                time.sleep(close_after_seconds)
                window.destroy()

            webview.start(
                close_after_delay,
                gui=gui,
                debug=debug,
                private_mode=True,
            )
    except Exception as exc:  # pywebview backend errors do not share one public base class
        raise DesktopAppError(f"could not start the {gui} desktop WebView: {exc}") from exc


def _run_headless(processes: Sequence[OwnedProcess]) -> None:
    print("Headless services are running; press Ctrl+C to stop", flush=True)
    while True:
        for owned in processes:
            return_code = owned.process.poll()
            if return_code is not None:
                detail = _tail(owned.log_path)
                suffix = f"\n{detail}" if detail else ""
                raise DesktopAppError(
                    f"{owned.name} exited with code {return_code}{suffix}"
                )
        time.sleep(0.25)


def _stop_processes(processes: Sequence[OwnedProcess]) -> list[str]:
    failures: list[str] = []
    for owned in reversed(processes):
        try:
            terminate_process_group(owned.process, timeout_seconds=3.0)
        except ProcessGroupTerminationError as exc:
            failures.append(f"{owned.name}: {exc}")
    return failures


def run_desktop_app(config: DesktopAppConfig) -> int:
    processes: list[OwnedProcess] = []
    identity: DesktopIdentity | None = None
    try:
        print("[2/4] validating desktop host, checkout and loopback ports", flush=True)
        _validate_checkout(config)
        _require_available_port(config.api_port)
        _require_available_port(config.ui_port)
        identity = _prepare_identity(config.repository_root)

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        log_root = config.repository_root / ".jobslayer" / "desktop" / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["JOBSLAYER_API_PORT"] = str(config.api_port)
        environment["PATH"] = os.pathsep.join(
            (str(config.npm_executable.parent), environment.get("PATH", ""))
        )

        with ExitStack() as stack:
            backend_log_path = log_root / f"{timestamp}-{os.getpid()}-api.log"
            frontend_log_path = log_root / f"{timestamp}-{os.getpid()}-ui.log"
            backend_log = stack.enter_context(backend_log_path.open("ab", buffering=0))
            frontend_log = stack.enter_context(frontend_log_path.open("ab", buffering=0))

            print("[3/4] starting TaskManager API and Vite UI", flush=True)
            backend = _launch_process(
                "TaskManager API",
                _backend_argv(config, identity),
                config=config,
                environment=environment,
                log=backend_log,
                log_path=backend_log_path,
            )
            processes.append(backend)
            _wait_for_http(
                config.api_url,
                processes=processes,
                timeout_seconds=config.startup_timeout_seconds,
            )
            frontend = _launch_process(
                "TaskManager UI",
                _frontend_argv(config),
                config=config,
                environment=environment,
                log=frontend_log,
                log_path=frontend_log_path,
            )
            processes.append(frontend)
            session = _wait_for_http(
                config.proxied_health_url,
                processes=processes,
                timeout_seconds=config.startup_timeout_seconds,
            )
            principal = session.get("principal") if session else None
            subject = principal.get("subject_id") if isinstance(principal, dict) else "unknown"
            print(f"[ready] {config.ui_url}", flush=True)
            print(f"[ready] least-privilege identity: {subject} (planner)", flush=True)
            print(f"[logs] {log_root}", flush=True)

            print("[4/4] opening JobSlayer desktop app", flush=True)
            if config.smoke_test:
                print("[smoke-test] startup and proxied health checks passed", flush=True)
            elif config.headless:
                _run_headless(processes)
            else:
                _open_desktop_window(
                    config.ui_url,
                    debug=config.debug_webview,
                    close_after_seconds=config.window_smoke_seconds,
                )
        return 0
    except KeyboardInterrupt:
        print("\nJobSlayer desktop app stopped", flush=True)
        return 0
    except (DesktopAppError, LocalIdentityError, OSError, ValueError) as exc:
        print(f"JobSlayer desktop app failed: {exc}", file=sys.stderr)
        return 1
    finally:
        failures = _stop_processes(processes)
        if identity is not None:
            try:
                identity.session_path.unlink(missing_ok=True)
            except OSError as exc:
                failures.append(f"temporary identity session: {exc}")
        for failure in failures:
            print(f"cleanup warning: {failure}", file=sys.stderr)
