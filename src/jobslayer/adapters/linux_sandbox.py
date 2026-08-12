"""Fail-closed Linux namespace and rlimit sandbox adapter."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import platform
import shutil
import subprocess

from jobslayer.execution import ProcessSupervisor, native_process_supervisor
from jobslayer.workers import (
    NetworkPolicy,
    SandboxCapabilities,
    SandboxLaunchPlan,
    SandboxRequest,
    SandboxResult,
    SandboxUnavailableError,
)


class LinuxNamespaceSandbox:
    """Run a command in bubblewrap namespaces with kernel rlimits.

    Capability reporting is based on executable/platform probes. ``execute``
    still fails closed if namespace creation itself is disabled by the host.
    """

    def __init__(
        self,
        *,
        bubblewrap_binary: str | os.PathLike[str] = "bwrap",
        prlimit_binary: str | os.PathLike[str] = "prlimit",
        process_supervisor: ProcessSupervisor | None = None,
    ):
        self.bubblewrap_binary = os.fspath(bubblewrap_binary)
        self.prlimit_binary = os.fspath(prlimit_binary)
        self.process_supervisor = process_supervisor or native_process_supervisor()

    def capabilities(self) -> SandboxCapabilities:
        linux = os.name == "posix" and platform.system() == "Linux"
        bubblewrap = self._available(self.bubblewrap_binary)
        prlimit = self._available(self.prlimit_binary)
        return SandboxCapabilities(
            adapter="linux-bubblewrap-rlimit-v1",
            network_isolation=linux and bubblewrap,
            mount_isolation=linux and bubblewrap,
            cpu_limit=linux and bubblewrap and prlimit,
            memory_limit=linux and bubblewrap and prlimit,
            process_limit=linux and bubblewrap and prlimit,
            process_tree_termination=linux and bubblewrap,
            wall_timeout=linux and bubblewrap,
        )

    def execute(self, request: SandboxRequest) -> SandboxResult:
        plan = self.prepare(request)
        policy = request.policy
        started_at = datetime.now(UTC)
        try:
            process = subprocess.Popen(
                list(plan.argv),
                cwd=plan.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=plan.environment,
                **self.process_supervisor.popen_kwargs(),
            )
        except OSError as exc:
            raise SandboxUnavailableError("sandbox process could not start") from exc
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=policy.timeout_seconds)
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            self.process_supervisor.terminate(process)
            stdout, stderr = process.communicate()
            exit_code = None
        finished_at = datetime.now(UTC)
        return SandboxResult(
            request_id=request.request_id,
            run_id=request.run_id,
            adapter=plan.adapter,
            exit_code=exit_code,
            timed_out=timed_out,
            started_at=started_at,
            finished_at=finished_at,
            stdout=stdout[-1_000_000:],
            stderr=stderr[-1_000_000:],
            capabilities=plan.capabilities,
        )

    def prepare(self, request: SandboxRequest) -> SandboxLaunchPlan:
        capabilities = self.capabilities()
        unmet = capabilities.unmet(request.policy)
        if unmet:
            raise SandboxUnavailableError(
                "sandbox cannot enforce required capabilities: " + ", ".join(unmet)
            )
        try:
            workspace = request.workspace.resolve(strict=True)
        except OSError as exc:
            raise SandboxUnavailableError("sandbox workspace does not exist") from exc
        if not workspace.is_dir() or workspace.is_symlink():
            raise SandboxUnavailableError("sandbox workspace must be a real directory")

        policy = request.policy
        argv = [
            self.bubblewrap_binary,
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
        ]
        if policy.network is NetworkPolicy.DENY:
            argv.append("--unshare-net")
        # Bubblewrap starts with an empty tmpfs root. Bind only the runtime
        # directories required to execute local tools; never expose the host
        # root or the operator's home/credential directories.
        runtime_roots = tuple(
            path
            for path in ("/usr", "/bin", "/lib", "/lib64", "/sbin")
            if Path(path).exists()
        )
        for runtime_root in runtime_roots:
            argv.extend(("--ro-bind", runtime_root, runtime_root))
        argv.extend(
            [
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--dir",
                "/workspace",
                "--bind",
                str(workspace),
                "/workspace",
                "--chdir",
                "/workspace",
                "--clearenv",
                "--setenv",
                "HOME",
                "/tmp",
                "--setenv",
                "PATH",
                os.defpath,
                self.prlimit_binary,
                f"--cpu={policy.cpu_seconds}",
                f"--as={policy.memory_bytes}",
                f"--nproc={policy.process_limit}",
                "--",
                *request.argv,
            ]
        )
        return SandboxLaunchPlan(
            request_id=request.request_id,
            run_id=request.run_id,
            adapter=capabilities.adapter,
            argv=tuple(argv),
            cwd=workspace,
            environment={"PATH": os.defpath},
            capabilities=capabilities,
        )

    @staticmethod
    def _available(command: str) -> bool:
        candidate = Path(command)
        if candidate.is_absolute():
            return candidate.is_file() and os.access(candidate, os.X_OK)
        return shutil.which(command) is not None


__all__ = ["LinuxNamespaceSandbox"]
