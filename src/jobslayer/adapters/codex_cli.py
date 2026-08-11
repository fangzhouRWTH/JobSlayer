from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, TextIO

from jobslayer.agents.events import RunEventBuffer, RunEventIntegrityError
from jobslayer.agents.executor import (
    AgentExecutorError,
    AgentRunNotFoundError,
    AgentRunStillRunningError,
)
from jobslayer.domain.models import (
    AgentCancellationResult,
    AgentInvocation,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
    RunEvent,
    WorkspaceManifest,
)
from jobslayer.execution.processes import (
    ProcessGroupTerminationError,
    ProcessSupervisor,
    native_process_supervisor,
)
from jobslayer.workspace.manager import WorkspaceManager


class CodexExecutorError(AgentExecutorError):
    pass


class CodexConfigurationError(CodexExecutorError):
    pass


@dataclass
class _CodexRunState:
    invocation: AgentInvocation
    workspace: WorkspaceManifest
    handle: AgentRunHandle
    process: subprocess.Popen[str]
    events: RunEventBuffer
    raw_event_log_path: Path
    stderr_log_path: Path
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancellation_requested: bool = False
    timed_out: bool = False
    protocol_error: bool = False
    failed_event_seen: bool = False
    final_message: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    error_summary: str | None = None
    result: AgentRunResult | None = None


class CodexCliExecutor:
    """Non-blocking adapter for the documented `codex exec --json` surface.

    The adapter permits only read-only and workspace-write Codex sandboxes. It
    intentionally has no danger-full-access mapping and no ambient API-key
    inheritance. A credential provider and outer OCI/VM isolation are required
    before using it for untrusted repositories.
    """

    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        artifact_root: str | Path,
        *,
        codex_binary: str | os.PathLike[str] | Sequence[str] = "codex",
        model_profiles: Mapping[str, str | None] | None = None,
        permission_profiles: Mapping[str, str] | None = None,
        output_schemas: Mapping[str, str | Path | None] | None = None,
        process_supervisor: ProcessSupervisor | None = None,
    ):
        self.workspace_manager = workspace_manager
        self.artifact_root = Path(artifact_root).resolve(strict=False)
        self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if isinstance(codex_binary, (str, os.PathLike)):
            codex_command = (os.fspath(codex_binary),)
        else:
            codex_command = tuple(str(argument) for argument in codex_binary)
        if not codex_command or any(
            not argument or "\x00" in argument for argument in codex_command
        ):
            raise CodexConfigurationError(
                "Codex executable command must contain non-empty arguments"
            )
        self.codex_command = codex_command
        self.process_supervisor = process_supervisor or native_process_supervisor()
        self.model_profiles = dict(model_profiles or {"default": None})
        self.permission_profiles = dict(
            permission_profiles
            or {
                "read_only": "read-only",
                "workspace_write": "workspace-write",
            }
        )
        if any(
            sandbox not in {"read-only", "workspace-write"}
            for sandbox in self.permission_profiles.values()
        ):
            raise CodexConfigurationError(
                "Codex adapter refuses danger-full-access permission mappings"
            )
        self.output_schemas = {
            key: Path(value).resolve(strict=False) if value is not None else None
            for key, value in (output_schemas or {"none": None}).items()
        }
        self._runs: dict[str, _CodexRunState] = {}
        self._runs_lock = threading.Lock()

    def start(
        self,
        invocation: AgentInvocation,
        workspace: WorkspaceManifest,
    ) -> AgentRunHandle:
        spec = invocation.run_spec
        if spec.executor_type != "codex_cli":
            raise CodexConfigurationError("run spec is not assigned to codex_cli")
        if spec.workspace_id != workspace.workspace_id or spec.task_id != workspace.task_id:
            raise CodexConfigurationError("run spec does not match the workspace")
        self.workspace_manager.inspect(workspace)

        with self._runs_lock:
            if spec.run_id in self._runs:
                raise CodexConfigurationError(f"run id already exists: {spec.run_id}")

        command = self._command_for(invocation, workspace)
        run_directory = self.artifact_root / hashlib.sha256(
            spec.run_id.encode("utf-8")
        ).hexdigest()
        try:
            run_directory.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise CodexConfigurationError(
                "artifact directory already exists for this run id"
            ) from exc
        raw_event_log_path = run_directory / "codex-events.jsonl"
        stderr_log_path = run_directory / "codex-stderr.log"
        raw_event_log_path.touch(mode=0o600)
        stderr_log_path.touch(mode=0o600)

        started_at = datetime.now(UTC)
        try:
            process = subprocess.Popen(
                command,
                cwd=Path(workspace.path),
                env=self._codex_environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **self.process_supervisor.popen_kwargs(),
            )
        except OSError as exc:
            raise CodexExecutorError("failed to launch Codex CLI") from exc

        handle = AgentRunHandle(
            run_id=spec.run_id,
            external_id=f"codex-pid-{process.pid}",
            executor_type="codex_cli",
            workspace_id=workspace.workspace_id,
            started_at=started_at,
        )
        state = _CodexRunState(
            invocation=invocation,
            workspace=workspace,
            handle=handle,
            process=process,
            events=RunEventBuffer(spec.run_id),
            raw_event_log_path=raw_event_log_path,
            stderr_log_path=stderr_log_path,
        )
        state.events.append(
            "run.started",
            {
                "executor_type": "codex_cli",
                "external_id": handle.external_id,
                "workspace_id": workspace.workspace_id,
            },
        )
        with self._runs_lock:
            self._runs[spec.run_id] = state

        stdout_thread = threading.Thread(
            target=self._consume_stdout,
            args=(state,),
            name=f"{spec.run_id}-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._consume_stderr,
            args=(state,),
            name=f"{spec.run_id}-stderr",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        assert process.stdin is not None
        try:
            process.stdin.write(invocation.prompt)
            process.stdin.close()
        except OSError as exc:
            with state.lock:
                state.protocol_error = True
                state.error_summary = f"failed to send prompt to Codex: {exc}"
            self._terminate_process_group(process)

        monitor = threading.Thread(
            target=self._monitor,
            args=(state, stdout_thread, stderr_thread),
            name=f"{spec.run_id}-monitor",
            daemon=True,
        )
        monitor.start()
        return handle

    def events(
        self, run_id: str, *, after_sequence: int = 0
    ) -> tuple[RunEvent, ...]:
        return self._state_for(run_id).events.events(after_sequence=after_sequence)

    def cancel(self, run_id: str) -> AgentCancellationResult:
        state = self._state_for(run_id)
        requested_at = datetime.now(UTC)
        with state.lock:
            if state.done.is_set() or state.process.poll() is not None:
                status = state.result.status if state.result else self._status_from_exit(state)
                return AgentCancellationResult(
                    run_id=run_id,
                    cancellation_requested=False,
                    already_terminal=True,
                    status=status,
                    requested_at=requested_at,
                )
            state.cancellation_requested = True
            state.events.append(
                "run.cancel.requested", {"requested_at": requested_at.isoformat()}
            )
        self._terminate_process_group(state.process)
        return AgentCancellationResult(
            run_id=run_id,
            cancellation_requested=True,
            already_terminal=False,
            status=AgentRunStatus.RUNNING,
            requested_at=requested_at,
        )

    def collect(self, run_id: str) -> AgentRunResult:
        state = self._state_for(run_id)
        if not state.done.is_set() or state.result is None:
            raise AgentRunStillRunningError(f"agent run is still active: {run_id}")
        return state.result

    def _command_for(
        self, invocation: AgentInvocation, workspace: WorkspaceManifest
    ) -> list[str]:
        spec = invocation.run_spec
        if spec.permission_profile not in self.permission_profiles:
            raise CodexConfigurationError(
                f"unknown Codex permission profile: {spec.permission_profile}"
            )
        if spec.model_profile not in self.model_profiles:
            raise CodexConfigurationError(
                f"unknown Codex model profile: {spec.model_profile}"
            )
        if spec.output_schema not in self.output_schemas:
            raise CodexConfigurationError(
                f"unknown Codex output schema: {spec.output_schema}"
            )

        command = [
            *self.codex_command,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--color",
            "never",
            "--sandbox",
            self.permission_profiles[spec.permission_profile],
            "--cd",
            workspace.path,
        ]
        model = self.model_profiles[spec.model_profile]
        if model:
            command.extend(("--model", model))
        schema = self.output_schemas[spec.output_schema]
        if schema is not None:
            if not schema.is_file():
                raise CodexConfigurationError(f"output schema does not exist: {schema}")
            command.extend(("--output-schema", str(schema)))
        command.append("-")
        return command

    def _consume_stdout(self, state: _CodexRunState) -> None:
        assert state.process.stdout is not None
        with state.raw_event_log_path.open("a", encoding="utf-8") as raw_log:
            for line in state.process.stdout:
                raw_log.write(line)
                raw_log.flush()
                try:
                    raw_event = json.loads(line)
                except json.JSONDecodeError:
                    with state.lock:
                        state.protocol_error = True
                        state.error_summary = "Codex emitted a non-JSON line in --json mode"
                    self._append_event_safely(
                        state,
                        "executor.output.invalid",
                        {"line_sha256": hashlib.sha256(line.encode()).hexdigest()},
                    )
                    continue
                self._normalize_event(state, raw_event)
        state.process.stdout.close()

    def _consume_stderr(self, state: _CodexRunState) -> None:
        assert state.process.stderr is not None
        with state.stderr_log_path.open("a", encoding="utf-8") as stderr_log:
            for chunk in iter(lambda: state.process.stderr.read(65_536), ""):
                stderr_log.write(chunk)
                stderr_log.flush()
        state.process.stderr.close()

    def _normalize_event(
        self, state: _CodexRunState, raw_event: dict[str, Any]
    ) -> None:
        source_type = str(raw_event.get("type", "unknown"))
        event_type = f"codex.{source_type}"
        item = raw_event.get("item")
        if source_type == "thread.started":
            event_type = "agent.thread.started"
        elif source_type == "turn.started":
            event_type = "agent.turn.started"
        elif source_type == "turn.completed":
            event_type = "agent.turn.completed"
            usage = raw_event.get("usage")
            if isinstance(usage, dict):
                with state.lock:
                    state.usage = {
                        str(key): value
                        for key, value in usage.items()
                        if isinstance(value, int) and not isinstance(value, bool)
                    }
        elif source_type == "turn.failed":
            event_type = "agent.turn.failed"
            with state.lock:
                state.failed_event_seen = True
                state.error_summary = str(raw_event.get("error", "Codex turn failed"))
        elif source_type == "error":
            event_type = "agent.error"
            with state.lock:
                state.failed_event_seen = True
                state.error_summary = str(raw_event.get("message", "Codex error"))
        elif source_type in {"item.started", "item.completed"} and isinstance(item, dict):
            phase = "started" if source_type.endswith("started") else "completed"
            item_type = str(item.get("type", "unknown"))
            event_type = {
                "command_execution": f"command.{phase}",
                "file_change": "file.changed" if phase == "completed" else "file.change.started",
                "agent_message": f"agent.message.{phase}",
                "plan": "plan.proposed" if phase == "completed" else "plan.started",
                "plan_update": "plan.proposed" if phase == "completed" else "plan.started",
            }.get(item_type, f"agent.item.{item_type}.{phase}")
            if item_type == "agent_message" and phase == "completed":
                message = item.get("text")
                if isinstance(message, str):
                    with state.lock:
                        state.final_message = message

        self._append_event_safely(
            state,
            event_type,
            {"source_type": source_type, "raw": raw_event},
        )

    def _monitor(
        self,
        state: _CodexRunState,
        stdout_thread: threading.Thread,
        stderr_thread: threading.Thread,
    ) -> None:
        timeout = state.invocation.run_spec.timeout_seconds
        try:
            state.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            with state.lock:
                state.timed_out = True
                state.error_summary = f"Codex exceeded timeout of {timeout} seconds"
            self._terminate_process_group(state.process)

        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            self._terminate_process_group(state.process)
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                with state.lock:
                    state.protocol_error = True
                    state.error_summary = "Codex output streams did not close"

        with state.lock:
            status = self._status_from_exit(state)
            terminal_event = {
                AgentRunStatus.COMPLETED: "run.completed",
                AgentRunStatus.FAILED: "run.failed",
                AgentRunStatus.CANCELLED: "run.cancelled",
                AgentRunStatus.TIMED_OUT: "run.timed_out",
                AgentRunStatus.RUNNING: "run.failed",
            }[status]
            self._append_event_safely(
                state,
                terminal_event,
                {"status": status.value},
            )
            finished_at = datetime.now(UTC)
            normal_exit_code = (
                state.process.returncode
                if status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED}
                else None
            )
            state.events.verify()
            state.result = AgentRunResult(
                run_id=state.handle.run_id,
                external_id=state.handle.external_id,
                executor_type=state.handle.executor_type,
                workspace_id=state.handle.workspace_id,
                status=status,
                exit_code=normal_exit_code,
                event_count=len(state.events.events()),
                final_message=state.final_message,
                usage=state.usage,
                raw_event_log_path=str(state.raw_event_log_path),
                raw_event_log_sha256=self._file_hash(state.raw_event_log_path),
                stderr_log_path=str(state.stderr_log_path),
                stderr_log_sha256=self._file_hash(state.stderr_log_path),
                started_at=state.handle.started_at,
                finished_at=finished_at,
                error_summary=state.error_summary,
            )
            state.done.set()

    @staticmethod
    def _status_from_exit(state: _CodexRunState) -> AgentRunStatus:
        if state.cancellation_requested:
            return AgentRunStatus.CANCELLED
        if state.timed_out:
            return AgentRunStatus.TIMED_OUT
        if state.protocol_error or state.failed_event_seen:
            return AgentRunStatus.FAILED
        if state.process.returncode == 0:
            return AgentRunStatus.COMPLETED
        return AgentRunStatus.FAILED

    @staticmethod
    def _append_event_safely(
        state: _CodexRunState,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            state.events.append(event_type, payload)
        except RunEventIntegrityError:
            with state.lock:
                state.protocol_error = True
                state.error_summary = "event arrived after the terminal event"

    def _state_for(self, run_id: str) -> _CodexRunState:
        with self._runs_lock:
            try:
                return self._runs[run_id]
            except KeyError as exc:
                raise AgentRunNotFoundError(f"unknown agent run: {run_id}") from exc

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(65_536):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _codex_environment() -> dict[str, str]:
        environment = {"PATH": os.environ.get("PATH", os.defpath)}
        for name in (
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "TEMP",
            "TMP",
            "CODEX_HOME",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        ):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "PYTHONNOUSERSITE": "1",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            }
        )
        return environment

    def _terminate_process_group(self, process: subprocess.Popen[str]) -> None:
        try:
            self.process_supervisor.terminate(process)
        except ProcessGroupTerminationError as exc:
            raise CodexExecutorError("Codex process group did not terminate") from exc
