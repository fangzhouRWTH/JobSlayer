from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from jobslayer.domain.models import (
    CommandPolicy,
    CommandRequest,
    CommandResult,
    CommandRule,
    CommandStatus,
    WorkspaceManifest,
)
from jobslayer.execution.runner import CommandExecutionError
from jobslayer.execution.processes import (
    ProcessGroupTerminationError,
    ProcessSupervisor,
    native_process_supervisor,
)
from jobslayer.workspace.manager import WorkspaceManager


class CommandRunnerError(CommandExecutionError):
    """Base error for commands rejected before producing a result."""


class CommandPolicyError(CommandRunnerError):
    pass


class CommandWorkingDirectoryError(CommandRunnerError):
    pass


class CommandLaunchError(CommandRunnerError):
    pass


class _OutputCapture:
    def __init__(self, limit: int):
        self.limit = limit
        self.buffer = bytearray()
        self.total_bytes = 0
        self.digest = hashlib.sha256()
        self.error: BaseException | None = None

    def drain(self, stream: BinaryIO) -> None:
        try:
            while chunk := stream.read(65_536):
                self.total_bytes += len(chunk)
                self.digest.update(chunk)
                remaining = self.limit - len(self.buffer)
                if remaining > 0:
                    self.buffer.extend(chunk[:remaining])
        except BaseException as exc:  # surfaced on the controller thread below
            self.error = exc
        finally:
            stream.close()

    @property
    def truncated(self) -> bool:
        return self.total_bytes > len(self.buffer)

    @property
    def text(self) -> str:
        return bytes(self.buffer).decode("utf-8", errors="replace")

    @property
    def sha256(self) -> str:
        return self.digest.hexdigest()


class GovernedLocalCommandRunner:
    """Policy-constrained local runner for trusted Phase 0 validation commands.

    This adapter deliberately does not claim network, device, CPU, memory, or
    system-call isolation. Untrusted agent commands require a later OCI/VM
    implementation of the same CommandRunner protocol.
    """

    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        *,
        process_supervisor: ProcessSupervisor | None = None,
    ):
        self.workspace_manager = workspace_manager
        self.process_supervisor = process_supervisor or native_process_supervisor()

    def run(
        self,
        manifest: WorkspaceManifest,
        request: CommandRequest,
        policy: CommandPolicy,
    ) -> CommandResult:
        if request.workspace_id != manifest.workspace_id:
            raise CommandPolicyError("request and workspace identifiers do not match")
        if request.task_id != manifest.task_id:
            raise CommandPolicyError("request and workspace tasks do not match")

        self.workspace_manager.inspect(manifest)
        rule = self._matching_rule(request, policy)
        allowed_timeout = min(
            policy.max_timeout_seconds,
            rule.max_timeout_seconds or policy.max_timeout_seconds,
        )
        if request.timeout_seconds > allowed_timeout:
            raise CommandPolicyError(
                f"requested timeout exceeds the rule limit of {allowed_timeout} seconds"
            )

        workspace = Path(manifest.path).resolve(strict=True)
        try:
            cwd = (workspace / request.cwd).resolve(strict=True)
        except FileNotFoundError as exc:
            raise CommandWorkingDirectoryError(
                f"working directory does not exist: {request.cwd}"
            ) from exc
        if not cwd.is_dir() or not cwd.is_relative_to(workspace):
            raise CommandWorkingDirectoryError(
                "working directory must resolve to a directory inside the workspace"
            )

        started_at = datetime.now(UTC)
        started_clock = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="jobslayer-command-") as runtime_home:
            environment = self._minimal_environment(Path(runtime_home))
            try:
                process = subprocess.Popen(
                    list(request.argv),
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **self.process_supervisor.popen_kwargs(),
                )
            except OSError as exc:
                raise CommandLaunchError(
                    f"failed to launch approved command: {request.argv[0]}"
                ) from exc

            assert process.stdout is not None
            assert process.stderr is not None
            stdout = _OutputCapture(policy.max_output_bytes_per_stream)
            stderr = _OutputCapture(policy.max_output_bytes_per_stream)
            readers = (
                threading.Thread(target=stdout.drain, args=(process.stdout,), daemon=True),
                threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True),
            )
            for reader in readers:
                reader.start()

            timed_out = False
            try:
                process.wait(timeout=request.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate_process_group(process)
            finally:
                for reader in readers:
                    reader.join(timeout=0.5)
                if any(reader.is_alive() for reader in readers):
                    # A child can keep inherited stdout/stderr pipes open after
                    # its parent exits. Do not leak that untracked process tree.
                    self._terminate_process_group(process)
                    for reader in readers:
                        reader.join(timeout=1)

        for capture in (stdout, stderr):
            if capture.error is not None:
                raise CommandRunnerError("failed while capturing command output") from capture.error
        if any(reader.is_alive() for reader in readers):
            raise CommandRunnerError("output reader did not finish after process termination")

        finished_at = datetime.now(UTC)
        duration_ms = round((time.monotonic() - started_clock) * 1000)
        if timed_out:
            status = CommandStatus.TIMED_OUT
            exit_code = None
        else:
            assert process.returncode is not None
            exit_code = process.returncode
            status = (
                CommandStatus.PASSED
                if exit_code in rule.accepted_exit_codes
                else CommandStatus.FAILED
            )

        return CommandResult(
            command_id=request.command_id,
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            policy_id=policy.policy_id,
            rule_id=rule.rule_id,
            argv=request.argv,
            cwd=request.cwd,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            stdout=stdout.text,
            stderr=stderr.text,
            stdout_bytes=stdout.total_bytes,
            stderr_bytes=stderr.total_bytes,
            stdout_sha256=stdout.sha256,
            stderr_sha256=stderr.sha256,
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
        )

    @staticmethod
    def _matching_rule(
        request: CommandRequest, policy: CommandPolicy
    ) -> CommandRule:
        matches = []
        for rule in policy.rules:
            prefix_length = len(rule.argv_prefix)
            prefix_matches = request.argv[:prefix_length] == rule.argv_prefix
            length_matches = (
                rule.allow_additional_arguments
                or len(request.argv) == prefix_length
            )
            if prefix_matches and length_matches:
                matches.append(rule)
        if not matches:
            raise CommandPolicyError("command does not match an allowed policy rule")
        return max(matches, key=lambda candidate: len(candidate.argv_prefix))

    @staticmethod
    def _minimal_environment(runtime_home: Path) -> dict[str, str]:
        environment = {
            "PATH": os.defpath,
            "HOME": str(runtime_home),
            "TMPDIR": str(runtime_home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONNOUSERSITE": "1",
        }
        if os.name == "nt":
            environment.update(
                {
                    "USERPROFILE": str(runtime_home),
                    "TEMP": str(runtime_home),
                    "TMP": str(runtime_home),
                }
            )
            for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
                value = os.environ.get(name)
                if value:
                    environment[name] = value
        return environment

    def _terminate_process_group(self, process: subprocess.Popen[bytes]) -> None:
        try:
            self.process_supervisor.terminate(process)
        except ProcessGroupTerminationError as exc:
            raise CommandRunnerError("process group did not terminate") from exc
