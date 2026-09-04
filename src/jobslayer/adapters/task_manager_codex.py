"""Durable TaskManager adapter for one local, logged-in Codex CLI.

The adapter persists the provider identity and launch envelope before a trusted
worker claims the external side effect.  The worker survives an API process
restart, while a repeated ``start_or_locate`` call reuses the exact start key,
workspace, provider id, and evidence artifacts.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from pydantic import ValidationError

from jobslayer.adapters.codex_common import (
    CodexCommandConfigurationError,
    normalize_codex_command,
)
from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.domain.models import WorkspaceManifest, WorkspaceSpec
from jobslayer.orchestration import TaskPlanNodeKind
from jobslayer.task_manager.execution import (
    ManagedExecutionObservation,
    ManagedExecutionReference,
    ManagedExecutionRequest,
    ManagedExecutionStatus,
    ManagedVerificationEvidence,
)


class TaskManagerCodexError(RuntimeError):
    """A durable Codex launch or observation failed closed."""


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DurableTaskManagerCodexExecutor:
    """Start or rediscover TaskManager Codex workers from durable local state."""

    adapter_id = "codex_cli"
    producer = "task-manager-codex-cli"

    def __init__(
        self,
        state_root: str | Path,
        artifacts: ArtifactRegistry,
        *,
        codex_binary: str | os.PathLike[str] | Sequence[str] = "codex",
        worker_command: Sequence[str] | None = None,
        startup_timeout_seconds: float = 3.0,
    ):
        self.state_root = Path(state_root).resolve(strict=False)
        self.providers_root = self.state_root / "providers"
        self.workspaces_root = self.state_root / "workspaces"
        self.runs_root = self.state_root / "runs"
        for path in (self.providers_root, self.workspaces_root, self.runs_root):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifacts = artifacts
        try:
            self.codex_command = normalize_codex_command(codex_binary)
        except CodexCommandConfigurationError as exc:
            raise TaskManagerCodexError(str(exc)) from exc
        self.worker_command = tuple(
            worker_command
            or (
                sys.executable,
                "-m",
                "jobslayer.adapters.task_manager_codex_worker",
            )
        )
        if not self.worker_command or any(not item for item in self.worker_command):
            raise TaskManagerCodexError("worker command must contain non-empty arguments")
        if startup_timeout_seconds <= 0:
            raise ValueError("worker startup timeout must be positive")
        self.startup_timeout_seconds = startup_timeout_seconds
        self._lock = threading.Lock()
        self._workers: dict[str, subprocess.Popen[bytes]] = {}

    def start_or_locate(
        self,
        request: ManagedExecutionRequest,
    ) -> ManagedExecutionReference:
        if request.execution_binding.executor_adapter != self.adapter_id:
            raise TaskManagerCodexError("request target is not bound to codex_cli")
        if request.node.kind not in {
            TaskPlanNodeKind.TASK,
            TaskPlanNodeKind.MILESTONE,
        }:
            raise TaskManagerCodexError(
                "Codex adapter accepts executable task and milestone nodes only"
            )

        with self._lock:
            workspace = self._workspace_for(request)
            state_directory = self._provider_directory(request.provider_start_key)
            state_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            request_payload = {
                "schema_version": "1.0",
                "request": request.model_dump(mode="json"),
                "workspace": workspace.model_dump(mode="json"),
            }
            request_bytes = _canonical(request_payload)
            request_sha256 = hashlib.sha256(request_bytes).hexdigest()
            request_path = state_directory / "request.json"
            if request_path.exists():
                if hashlib.sha256(request_path.read_bytes()).hexdigest() != request_sha256:
                    raise TaskManagerCodexError(
                        "provider start key was reused for a different request"
                    )
            else:
                _atomic_write(request_path, request_bytes)

            prompt = self._prompt_for(request)
            maximum_context = request.execution_binding.invocation.run_spec.maximum_context_bytes
            if maximum_context is None or len(prompt) > maximum_context:
                raise TaskManagerCodexError("node prompt exceeds the target context budget")
            prompt_path = state_directory / "prompt.txt"
            if prompt_path.exists():
                if prompt_path.read_bytes() != prompt:
                    raise TaskManagerCodexError("persisted Codex prompt does not match request")
            else:
                _atomic_write(prompt_path, prompt)

            launch_path = state_directory / "launch.json"
            launch_bytes = _canonical(
                {
                    "schema_version": "1.0",
                    "provider_start_key": request.provider_start_key,
                    "argv": self._codex_argv(request, workspace),
                    "cwd": workspace.path,
                    "timeout_seconds": (
                        request.execution_binding.invocation.run_spec.timeout_seconds
                    ),
                }
            )
            if launch_path.exists():
                if launch_path.read_bytes() != launch_bytes:
                    raise TaskManagerCodexError("persisted Codex launch envelope drifted")
            else:
                _atomic_write(launch_path, launch_bytes)

            record_path = state_directory / "provider-reference.json"
            if record_path.exists():
                reference, recorded_sha = self._load_reference(record_path)
                if recorded_sha != request_sha256:
                    raise TaskManagerCodexError("provider reference belongs to another request")
                self._verify_reference_evidence(reference, request)
            else:
                evidence = self.artifacts.register_bytes(
                    task_id=request.workflow_task_id,
                    run_id=request.run_id,
                    artifact_type="task-manager-codex-start-request",
                    producer=self.producer,
                    content=request_bytes,
                    metadata={
                        "provider_start_key": request.provider_start_key,
                        "request_sha256": request_sha256,
                        "workspace_id": workspace.workspace_id,
                    },
                )
                reference = ManagedExecutionReference(
                    provider_start_key=request.provider_start_key,
                    adapter_id=self.adapter_id,
                    provider_run_id=self._provider_run_id(request.provider_start_key),
                    started_at=datetime.now(UTC),
                    evidence_artifact_ids=(evidence.artifact_id,),
                )
                _atomic_write(
                    record_path,
                    _canonical(
                        {
                            "schema_version": "1.0",
                            "request_sha256": request_sha256,
                            "workflow_task_id": request.workflow_task_id,
                            "run_id": request.run_id,
                            "reference": reference.model_dump(mode="json"),
                        }
                    ),
                )

            self._ensure_worker(state_directory)
            return reference

    def observe(
        self,
        reference: ManagedExecutionReference,
        *,
        after_cursor: str | None,
    ) -> ManagedExecutionObservation:
        del after_cursor
        state_directory = self._provider_directory(reference.provider_start_key)
        record_path = state_directory / "provider-reference.json"
        persisted, _ = self._load_reference(record_path)
        if persisted != reference:
            raise TaskManagerCodexError("provider reference does not match durable state")
        record = self._read_json(record_path)
        task_id = str(record["workflow_task_id"])
        run_id = str(record["run_id"])

        raw_events = self._read_optional(state_directory / "codex-events.jsonl")
        stderr = self._read_optional(state_directory / "codex-stderr.log")
        terminal = self._read_optional_json(state_directory / "terminal.json")
        if terminal is not None:
            self._reap_worker(state_directory)
        claim = self._read_optional_json(state_directory / "worker-claim.json")
        status, summary = self._status_and_summary(raw_events, terminal, claim)
        cursor_payload = {
            "provider_run_id": reference.provider_run_id,
            "status": status.value,
            "events_sha256": hashlib.sha256(raw_events).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "terminal": terminal,
            "worker_lost": status is ManagedExecutionStatus.FAILED and terminal is None,
        }
        cursor = f"tmcursor-{hashlib.sha256(_canonical(cursor_payload)).hexdigest()}"
        observation_path = state_directory / "observations" / f"{cursor}.json"
        if observation_path.exists():
            try:
                return ManagedExecutionObservation.model_validate(
                    self._read_json(observation_path)
                )
            except (ValidationError, TypeError, ValueError) as exc:
                raise TaskManagerCodexError("durable observation is invalid") from exc

        evidence_ids: list[str] = []
        if raw_events:
            evidence_ids.append(
                self.artifacts.register_bytes(
                    task_id=task_id,
                    run_id=run_id,
                    artifact_type="codex.raw_jsonl",
                    producer=self.producer,
                    content=raw_events,
                    metadata={"cursor": cursor},
                ).artifact_id
            )
        if stderr:
            evidence_ids.append(
                self.artifacts.register_bytes(
                    task_id=task_id,
                    run_id=run_id,
                    artifact_type="codex.stderr",
                    producer=self.producer,
                    content=stderr,
                    metadata={"cursor": cursor},
                ).artifact_id
            )
        envelope = self.artifacts.register_bytes(
            task_id=task_id,
            run_id=run_id,
            artifact_type="task-manager-codex-observation",
            producer=self.producer,
            content=_canonical(cursor_payload),
            metadata={"cursor": cursor, "status": status.value},
        )
        evidence_ids.append(envelope.artifact_id)
        observation = ManagedExecutionObservation(
            provider_run_id=reference.provider_run_id,
            status=status,
            cursor=cursor,
            summary=summary[:12_000],
            observed_at=datetime.now(UTC),
            evidence_artifact_ids=tuple(evidence_ids),
        )
        _atomic_write(observation_path, _canonical(observation.model_dump(mode="json")))
        return observation

    def collect_verification_evidence(
        self,
        reference: ManagedExecutionReference,
    ) -> ManagedVerificationEvidence:
        """Inspect the bound workspace and retain facts without accepting the node."""

        state_directory = self._provider_directory(reference.provider_start_key)
        persisted, _ = self._load_reference(state_directory / "provider-reference.json")
        if persisted != reference:
            raise TaskManagerCodexError("provider reference does not match durable state")
        terminal = self._read_optional_json(state_directory / "terminal.json")
        if terminal is None or terminal.get("status") != "succeeded":
            raise TaskManagerCodexError(
                "verification evidence requires a successful terminal provider result"
            )
        request_payload = self._read_json(state_directory / "request.json")
        try:
            request = ManagedExecutionRequest.model_validate(request_payload["request"])
            workspace = WorkspaceManifest.model_validate(request_payload["workspace"])
        except (KeyError, ValidationError, TypeError, ValueError) as exc:
            raise TaskManagerCodexError("durable verification request is invalid") from exc
        if (
            request.provider_start_key != reference.provider_start_key
            or request.run_id != str(
                self._read_json(state_directory / "provider-reference.json")["run_id"]
            )
        ):
            raise TaskManagerCodexError("verification request binding drifted")

        manager = GitWorktreeManager(workspace.repository_root, self.workspaces_root)
        run_inspection = manager.inspect(workspace)
        checkpoint_workspace = workspace.model_copy(
            update={
                "requested_base_commit": run_inspection.head_commit,
                "resolved_base_commit": run_inspection.head_commit,
            }
        )
        inspection = manager.inspect(checkpoint_workspace)
        evidence_ids: list[str] = []
        inspection_artifact = self.artifacts.register_bytes(
            task_id=request.workflow_task_id,
            run_id=request.run_id,
            artifact_type="task-manager-workspace-inspection",
            producer=self.producer,
            content=_canonical(inspection.model_dump(mode="json")),
            metadata={
                "provider_run_id": reference.provider_run_id,
                "workspace_id": workspace.workspace_id,
                "changed_paths": list(inspection.changed_paths),
            },
        )
        evidence_ids.append(inspection_artifact.artifact_id)
        patch_sha256 = None
        if inspection.changed_paths:
            collection_task = request.execution_binding.task.model_copy(
                update={
                    "base_commit": inspection.head_commit,
                    "allowed_paths": (".",),
                    "forbidden_paths": (),
                }
            )
            patch = manager.collect_patch(checkpoint_workspace, collection_task)
            patch_artifact = self.artifacts.register_bytes(
                task_id=request.workflow_task_id,
                run_id=request.run_id,
                artifact_type="task-manager-workspace-patch",
                producer=self.producer,
                content=_canonical(patch.model_dump(mode="json")),
                metadata={
                    "provider_run_id": reference.provider_run_id,
                    "workspace_id": workspace.workspace_id,
                    "changed_paths": list(patch.changed_paths),
                },
            )
            evidence_ids.append(patch_artifact.artifact_id)
            patch_sha256 = patch.sha256
        return ManagedVerificationEvidence(
            provider_run_id=reference.provider_run_id,
            source_commit=inspection.head_commit,
            source_patch_sha256=patch_sha256,
            workspace=inspection,
            collected_at=datetime.now(UTC),
            evidence_artifact_ids=tuple(evidence_ids),
        )

    def _workspace_for(self, request: ManagedExecutionRequest) -> WorkspaceManifest:
        binding = request.execution_binding
        checkout = Path(binding.testbed_inspection.checkout_path).resolve(strict=True)
        manager = GitWorktreeManager(checkout, self.workspaces_root)
        run_directory = self.runs_root / hashlib.sha256(
            request.run_id.encode("utf-8")
        ).hexdigest()
        manifest_path = run_directory / "workspace.json"
        if manifest_path.exists():
            try:
                manifest = WorkspaceManifest.model_validate(self._read_json(manifest_path))
            except (ValidationError, TypeError, ValueError) as exc:
                raise TaskManagerCodexError("durable workspace manifest is invalid") from exc
            if (
                manifest.task_id != binding.task.task_id
                or manifest.repository_root != str(checkout)
                or manifest.resolved_base_commit.lower() != binding.task.base_commit.lower()
            ):
                raise TaskManagerCodexError("TaskManager run workspace binding drifted")
            manager.inspect(manifest)
            return manifest

        workspace_id = "tmws-" + hashlib.sha256(
            request.run_id.encode("utf-8")
        ).hexdigest()[:24]
        manifest = manager.create(
            WorkspaceSpec(
                workspace_id=workspace_id,
                task_id=binding.task.task_id,
                base_commit=binding.task.base_commit,
            )
        )
        _atomic_write(manifest_path, _canonical(manifest.model_dump(mode="json")))
        return manifest

    def _codex_argv(
        self,
        request: ManagedExecutionRequest,
        workspace: WorkspaceManifest,
    ) -> list[str]:
        binding = request.execution_binding
        spec = binding.invocation.run_spec
        if spec.permission_profile != "workspace_write":
            raise TaskManagerCodexError("TaskManager Codex requires workspace_write")
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
            "workspace-write",
            "--cd",
            workspace.path,
        ]
        if binding.executor_model is not None:
            command.extend(("--model", binding.executor_model))
        if binding.executor_reasoning_effort is not None:
            command.extend(
                (
                    "--config",
                    f'model_reasoning_effort="{binding.executor_reasoning_effort}"',
                )
            )
        command.append("-")
        return command

    @staticmethod
    def _prompt_for(request: ManagedExecutionRequest) -> bytes:
        return (
            "You are executing exactly one authorized node from a finalized "
            "TaskManager DAG. The overall target below is context, not permission "
            "to perform downstream nodes. Preserve useful work already present in "
            "the shared run workspace.\n\n"
            "--- Overall source-controlled target ---\n"
            + request.execution_binding.invocation.prompt.rstrip()
            + "\n\n--- Authorized TaskManager DAG node ---\n"
            + request.prompt.rstrip()
            + "\nDo not implement or claim downstream DAG nodes.\n"
        ).encode("utf-8")

    def _ensure_worker(self, state_directory: Path) -> None:
        if (state_directory / "terminal.json").exists():
            return
        claim_path = state_directory / "worker-claim.json"
        if claim_path.exists():
            return
        worker_stdout = (state_directory / "worker-stdout.log").open("ab")
        worker_stderr = (state_directory / "worker-stderr.log").open("ab")
        try:
            process = subprocess.Popen(
                (*self.worker_command, "--state-dir", str(state_directory)),
                stdin=subprocess.DEVNULL,
                stdout=worker_stdout,
                stderr=worker_stderr,
                close_fds=True,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise TaskManagerCodexError("could not start durable Codex worker") from exc
        finally:
            worker_stdout.close()
            worker_stderr.close()
        self._workers[str(state_directory)] = process

        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if claim_path.exists() or (state_directory / "terminal.json").exists():
                return
            if process.poll() is not None:
                break
            time.sleep(0.02)
        raise TaskManagerCodexError("durable Codex worker did not claim the launch")

    def _reap_worker(self, state_directory: Path) -> None:
        process = self._workers.get(str(state_directory))
        if process is None:
            return
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            return
        self._workers.pop(str(state_directory), None)

    def _status_and_summary(
        self,
        raw_events: bytes,
        terminal: dict[str, Any] | None,
        claim: dict[str, Any] | None,
    ) -> tuple[ManagedExecutionStatus, str]:
        if terminal is not None:
            raw_status = terminal.get("status")
            status = {
                "succeeded": ManagedExecutionStatus.SUCCEEDED,
                "failed": ManagedExecutionStatus.FAILED,
                "cancelled": ManagedExecutionStatus.CANCELLED,
            }.get(raw_status)
            if status is None:
                raise TaskManagerCodexError("worker terminal status is invalid")
            summary = str(
                terminal.get("final_message")
                or terminal.get("error_summary")
                or f"Codex worker ended as {raw_status}"
            )
            return status, summary
        if claim is None:
            raise TaskManagerCodexError("provider run has no worker claim or terminal result")
        pid = claim.get("worker_pid")
        if not isinstance(pid, int) or pid <= 0:
            raise TaskManagerCodexError("worker claim has an invalid pid")
        if not self._pid_alive(pid):
            return ManagedExecutionStatus.FAILED, "Codex worker disappeared before terminal evidence"
        last_message = self._last_agent_message(raw_events)
        return (
            ManagedExecutionStatus.RUNNING,
            last_message or "Codex worker is running; raw progress evidence is retained.",
        )

    @staticmethod
    def _last_agent_message(raw_events: bytes) -> str | None:
        result = None
        for line in raw_events.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item")
            if (
                event.get("type") == "item.completed"
                and isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                result = item["text"]
        return result

    def _verify_reference_evidence(
        self,
        reference: ManagedExecutionReference,
        request: ManagedExecutionRequest,
    ) -> None:
        for artifact_id in reference.evidence_artifact_ids:
            manifest = self.artifacts.get(artifact_id)
            if (
                manifest.task_id != request.workflow_task_id
                or manifest.run_id != request.run_id
                or not self.artifacts.verify(manifest)
            ):
                raise TaskManagerCodexError("provider start evidence failed verification")

    @staticmethod
    def _load_reference(path: Path) -> tuple[ManagedExecutionReference, str]:
        payload = DurableTaskManagerCodexExecutor._read_json(path)
        try:
            reference = ManagedExecutionReference.model_validate(payload["reference"])
            request_sha = str(payload["request_sha256"])
        except (KeyError, ValidationError, TypeError, ValueError) as exc:
            raise TaskManagerCodexError("durable provider reference is invalid") from exc
        if len(request_sha) != 64:
            raise TaskManagerCodexError("durable provider request hash is invalid")
        return reference, request_sha

    def _provider_directory(self, provider_start_key: str) -> Path:
        return self.providers_root / hashlib.sha256(
            provider_start_key.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _provider_run_id(provider_start_key: str) -> str:
        return "codex-task-" + hashlib.sha256(
            provider_start_key.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TaskManagerCodexError(f"durable state is unavailable: {path.name}") from exc
        if not isinstance(payload, dict):
            raise TaskManagerCodexError(f"durable state is not an object: {path.name}")
        return payload

    @staticmethod
    def _read_optional_json(path: Path) -> dict[str, Any] | None:
        return DurableTaskManagerCodexExecutor._read_json(path) if path.exists() else None

    @staticmethod
    def _read_optional(path: Path) -> bytes:
        try:
            return path.read_bytes() if path.exists() else b""
        except OSError as exc:
            raise TaskManagerCodexError(f"could not read provider output: {path.name}") from exc

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


__all__ = ["DurableTaskManagerCodexExecutor", "TaskManagerCodexError"]
