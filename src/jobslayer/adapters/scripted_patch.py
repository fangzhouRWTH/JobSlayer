from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

from jobslayer.agents.events import RunEventBuffer
from jobslayer.agents.executor import (
    AgentExecutorError,
    AgentRunNotFoundError,
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
from jobslayer.workspace.manager import WorkspaceManager


class ScriptedPatchError(AgentExecutorError):
    """Raised when deterministic patch replay is misconfigured."""


@dataclass(frozen=True)
class _ScriptedRun:
    handle: AgentRunHandle
    events: RunEventBuffer
    result: AgentRunResult


class ScriptedPatchExecutor:
    """Deterministically replay a pre-reviewed patch for framework validation.

    This adapter is intentionally not an AI agent. It exists to exercise the
    same workspace, evidence, verification, and review path without model cost
    or network access.
    """

    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        artifact_root: str | Path,
        *,
        patch_bytes: bytes,
        patch_sha256: str,
        git_executable: str = "git",
    ):
        if not isinstance(patch_bytes, bytes):
            raise TypeError("patch_bytes must be bytes")
        digest = hashlib.sha256(patch_bytes).hexdigest()
        if digest != patch_sha256:
            raise ScriptedPatchError("scripted patch bytes do not match sha256")
        self.workspace_manager = workspace_manager
        self.artifact_root = Path(artifact_root).resolve(strict=False)
        self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.patch_bytes = patch_bytes
        self.patch_sha256 = patch_sha256
        self.git_executable = git_executable
        self._runs: dict[str, _ScriptedRun] = {}

    def start(
        self,
        invocation: AgentInvocation,
        workspace: WorkspaceManifest,
    ) -> AgentRunHandle:
        spec = invocation.run_spec
        self._validate_invocation(invocation, workspace)
        if spec.run_id in self._runs:
            raise ScriptedPatchError(f"run id already exists: {spec.run_id}")
        self.workspace_manager.inspect(workspace)

        run_directory = self.artifact_root / hashlib.sha256(
            spec.run_id.encode("utf-8")
        ).hexdigest()
        try:
            run_directory.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise ScriptedPatchError(
                "scripted executor artifact directory already exists"
            ) from exc
        raw_log = run_directory / "scripted-events.jsonl"
        stderr_log = run_directory / "scripted-stderr.log"
        started_at = datetime.now(UTC)
        external_id = f"scripted-{self.patch_sha256[:16]}"
        handle = AgentRunHandle(
            run_id=spec.run_id,
            external_id=external_id,
            executor_type="scripted_patch",
            workspace_id=workspace.workspace_id,
            started_at=started_at,
        )
        events = RunEventBuffer(spec.run_id)
        raw_events = [
            {
                "type": "run.started",
                "executor_type": "scripted_patch",
                "workspace_id": workspace.workspace_id,
            }
        ]
        events.append("run.started", raw_events[0])

        status = AgentRunStatus.COMPLETED
        exit_code: int | None = 0
        error_summary: str | None = None
        stderr_parts: list[bytes] = []
        for check_only in (True, False):
            command = [
                self.git_executable,
                "-c",
                f"core.hooksPath={os.devnull}",
                "-C",
                workspace.path,
                "apply",
                "--whitespace=error-all",
                "--recount",
            ]
            if check_only:
                command.append("--check")
            command.append("-")
            try:
                result = subprocess.run(
                    command,
                    input=self.patch_bytes,
                    capture_output=True,
                    check=False,
                    timeout=spec.timeout_seconds,
                    env={
                        "PATH": os.defpath,
                        "LANG": "C.UTF-8",
                        "LC_ALL": "C.UTF-8",
                        "GIT_TERMINAL_PROMPT": "0",
                    },
                )
            except subprocess.TimeoutExpired as exc:
                status = AgentRunStatus.TIMED_OUT
                exit_code = None
                error_summary = "scripted git apply timed out"
                stderr_parts.append(exc.stderr or b"")
                break
            except OSError as exc:
                status = AgentRunStatus.FAILED
                exit_code = 127
                error_summary = f"could not launch scripted git apply: {exc}"
                break
            stderr_parts.extend((result.stdout, result.stderr))
            if result.returncode != 0:
                status = AgentRunStatus.FAILED
                exit_code = result.returncode
                phase = "check" if check_only else "apply"
                error_summary = f"scripted patch {phase} failed"
                break

        if status is AgentRunStatus.COMPLETED:
            terminal_raw = {
                "type": "patch.applied",
                "patch_sha256": self.patch_sha256,
            }
            events.append("file.changed", terminal_raw)
            final_message = "deterministic scripted patch applied"
        else:
            terminal_raw = {
                "type": "patch.failed",
                "patch_sha256": self.patch_sha256,
                "error": error_summary,
            }
            events.append("agent.error", terminal_raw)
            final_message = None
        raw_events.append(terminal_raw)
        raw_log.write_text(
            "".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                for item in raw_events
            ),
            encoding="utf-8",
        )
        stderr_log.write_bytes(b"".join(stderr_parts))
        raw_log.chmod(0o600)
        stderr_log.chmod(0o600)
        finished_at = datetime.now(UTC)
        result = AgentRunResult(
            run_id=spec.run_id,
            external_id=external_id,
            executor_type="scripted_patch",
            workspace_id=workspace.workspace_id,
            status=status,
            exit_code=exit_code,
            event_count=len(events.events()),
            final_message=final_message,
            usage={},
            raw_event_log_path=str(raw_log),
            raw_event_log_sha256=hashlib.sha256(raw_log.read_bytes()).hexdigest(),
            stderr_log_path=str(stderr_log),
            stderr_log_sha256=hashlib.sha256(stderr_log.read_bytes()).hexdigest(),
            started_at=started_at,
            finished_at=finished_at,
            error_summary=error_summary,
        )
        self._runs[spec.run_id] = _ScriptedRun(
            handle=handle,
            events=events,
            result=result,
        )
        return handle

    def events(
        self, run_id: str, *, after_sequence: int = 0
    ) -> tuple[RunEvent, ...]:
        return self._run(run_id).events.events(after_sequence=after_sequence)

    def cancel(self, run_id: str) -> AgentCancellationResult:
        run = self._run(run_id)
        return AgentCancellationResult(
            run_id=run_id,
            cancellation_requested=False,
            already_terminal=True,
            status=run.result.status,
        )

    def collect(self, run_id: str) -> AgentRunResult:
        return self._run(run_id).result

    def _run(self, run_id: str) -> _ScriptedRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise AgentRunNotFoundError(f"unknown scripted run: {run_id}") from exc

    @staticmethod
    def _validate_invocation(
        invocation: AgentInvocation, workspace: WorkspaceManifest
    ) -> None:
        spec = invocation.run_spec
        if spec.executor_type != "scripted_patch":
            raise ScriptedPatchError("run spec is not assigned to scripted_patch")
        if spec.task_id != workspace.task_id or spec.workspace_id != workspace.workspace_id:
            raise ScriptedPatchError("run spec does not match the workspace")
        if spec.model_profile != "deterministic-replay-v1":
            raise ScriptedPatchError("unsupported scripted model profile")
        if spec.permission_profile != "workspace_write":
            raise ScriptedPatchError("scripted patch requires workspace_write")
        if spec.output_schema != "unified_diff" or spec.max_attempts != 1:
            raise ScriptedPatchError("scripted patch requires one unified-diff attempt")
