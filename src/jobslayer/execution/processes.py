from __future__ import annotations

import os
import signal
import subprocess
from typing import Any, Protocol, runtime_checkable


class ProcessGroupTerminationError(RuntimeError):
    """Raised when an owned subprocess tree cannot be stopped."""


@runtime_checkable
class ProcessSupervisor(Protocol):
    """Platform-neutral lifecycle boundary for an owned subprocess tree."""

    def popen_kwargs(self) -> dict[str, Any]:
        """Return the Popen options required to create a supervised group."""

    def terminate(
        self,
        process: subprocess.Popen[Any],
        *,
        timeout_seconds: float = 0.5,
    ) -> None:
        """Terminate the supervised process tree or raise a structured error."""


class PosixProcessSupervisor:
    def popen_kwargs(self) -> dict[str, Any]:
        return {"start_new_session": True}

    def terminate(
        self,
        process: subprocess.Popen[Any],
        *,
        timeout_seconds: float = 0.5,
    ) -> None:
        _terminate_posix_process_group(
            process,
            timeout_seconds=timeout_seconds,
        )


class WindowsProcessSupervisor:
    def popen_kwargs(self) -> dict[str, Any]:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    def terminate(
        self,
        process: subprocess.Popen[Any],
        *,
        timeout_seconds: float = 0.5,
    ) -> None:
        _terminate_windows_process_tree(
            process,
            timeout_seconds=timeout_seconds,
        )


_NATIVE_PROCESS_SUPERVISOR: ProcessSupervisor = (
    WindowsProcessSupervisor() if os.name == "nt" else PosixProcessSupervisor()
)


def native_process_supervisor() -> ProcessSupervisor:
    """Return the native implementation behind the shared supervisor protocol."""

    return _NATIVE_PROCESS_SUPERVISOR


def process_group_launch_kwargs() -> dict[str, Any]:
    """Compatibility facade for callers that only need native launch options."""

    return native_process_supervisor().popen_kwargs()


def terminate_process_group(
    process: subprocess.Popen[Any],
    *,
    timeout_seconds: float = 0.5,
) -> None:
    """Stop a process group using the strongest stdlib/OS primitive available.

    POSIX can signal the group even after its leader exits. Windows first sends
    CTRL_BREAK to the process group and then uses the system ``taskkill`` tool
    for recursive forced termination while the leader is still addressable.
    This is process supervision, not a security sandbox.
    """

    native_process_supervisor().terminate(
        process,
        timeout_seconds=timeout_seconds,
    )


def _terminate_posix_process_group(
    process: subprocess.Popen[Any],
    *,
    timeout_seconds: float,
) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        pass

    # Descendants can keep the group alive after its leader exits.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise ProcessGroupTerminationError(
            "POSIX process group did not terminate"
        ) from exc


def _terminate_windows_process_tree(
    process: subprocess.Popen[Any],
    *,
    timeout_seconds: float,
) -> None:
    try:
        os.kill(process.pid, signal.CTRL_BREAK_EVENT)
    except (OSError, ValueError):
        pass

    if process.poll() is None:
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            pass

    if process.poll() is None:
        try:
            subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(2.0, timeout_seconds),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()

    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise ProcessGroupTerminationError(
            "Windows process tree did not terminate"
        ) from exc
