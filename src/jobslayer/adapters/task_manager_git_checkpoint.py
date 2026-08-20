"""Idempotent source checkpoint integration for one TaskManager run worktree."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Any

from pydantic import ValidationError

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.domain.models import SourceIntegrationResult, WorkspaceManifest
from jobslayer.task_manager import ManagedCheckpointRequest, ManagedCheckpointResult


class TaskManagerGitCheckpointError(RuntimeError):
    """The reviewed patch could not be attested or checkpointed exactly."""


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


class LocalTaskManagerGitCheckpointIntegrator:
    """Commit an approved patch only to its existing isolated run branch."""

    adapter_id = "local_git_checkpoint"
    producer = "task-manager-git-checkpoint"

    def __init__(
        self,
        executor_state_root: str | Path,
        artifacts: ArtifactRegistry,
        *,
        command_timeout_seconds: float = 30,
    ):
        self.executor_state_root = Path(executor_state_root).resolve(strict=True)
        self.providers_root = self.executor_state_root / "providers"
        self.workspaces_root = self.executor_state_root / "workspaces"
        self.integrations_root = self.executor_state_root / "integrations"
        self.integrations_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if command_timeout_seconds <= 0:
            raise ValueError("checkpoint command timeout must be positive")
        self.command_timeout_seconds = command_timeout_seconds
        self.artifacts = artifacts
        self._lock = threading.Lock()

    def integrate_checkpoint(
        self,
        request: ManagedCheckpointRequest,
    ) -> ManagedCheckpointResult:
        with self._lock:
            directory = self._integration_directory(request.integration_key)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            request_bytes = _canonical(request.model_dump(mode="json"))
            request_sha256 = hashlib.sha256(request_bytes).hexdigest()
            request_path = directory / "request.json"
            if request_path.exists():
                if request_path.read_bytes() != request_bytes:
                    raise TaskManagerGitCheckpointError(
                        "integration key was reused for another reviewed patch"
                    )
            else:
                _atomic_write(request_path, request_bytes)

            result_path = directory / "result.json"
            if result_path.exists():
                result = self._load_result(result_path, request_sha256)
                self._attest(request, result)
                return result

            workspace = self._workspace_for(request)
            manager = GitWorktreeManager(
                workspace.repository_root,
                self.workspaces_root,
            )
            checkpoint_workspace = workspace.model_copy(
                update={
                    "requested_base_commit": request.verification_report.source_commit,
                    "resolved_base_commit": request.verification_report.source_commit,
                }
            )
            task = request.execution_binding.task.model_copy(
                update={"base_commit": request.verification_report.source_commit}
            )
            patch = manager.collect_patch(checkpoint_workspace, task)
            expected_paths = request.verification_evidence.workspace.changed_paths
            if (
                patch.sha256 != request.verification_report.source_patch_sha256
                or patch.changed_paths != expected_paths
            ):
                raise TaskManagerGitCheckpointError(
                    "workspace no longer matches the reviewed source patch"
                )

            head_before = self._git(workspace.path, "rev-parse", "HEAD").strip()
            if head_before.lower() == request.verification_report.source_commit.lower():
                self._commit(request, workspace, patch.changed_paths)
            else:
                parent = self._git(workspace.path, "rev-parse", "HEAD^").strip()
                if parent.lower() != request.verification_report.source_commit.lower():
                    raise TaskManagerGitCheckpointError(
                        "run branch advanced beyond the authorized checkpoint base"
                    )
                if self._git_bytes(
                    workspace.path,
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=all",
                ):
                    raise TaskManagerGitCheckpointError(
                        "already checkpointed run branch has additional changes"
                    )

            commit = self._git(workspace.path, "rev-parse", "HEAD").strip()
            branch = self._git(workspace.path, "branch", "--show-current").strip()
            result_record = SourceIntegrationResult(
                integration_id=(
                    "tmintegration-"
                    + hashlib.sha256(request.integration_key.encode()).hexdigest()[:32]
                ),
                task_id=request.workflow_task_id,
                workspace_id=workspace.workspace_id,
                repository_root=workspace.repository_root,
                source_ref=f"verified-patch:{request.source_review.review_id}",
                target_ref=branch,
                base_commit=request.verification_report.source_commit,
                commit=commit,
                target_previous_commit=request.verification_report.source_commit,
                target_commit=commit,
                source_patch_sha256=patch.sha256,
                changed_paths=patch.changed_paths,
                approved_by=request.approved_by,
                integrated_at=datetime.now(UTC),
            )
            artifact = self.artifacts.register_bytes(
                task_id=request.workflow_task_id,
                run_id=request.run_id,
                artifact_type="task-manager-source-checkpoint",
                producer=self.producer,
                content=_canonical(result_record.model_dump(mode="json")),
                metadata={
                    "integration_key": request.integration_key,
                    "node_id": request.node_id,
                    "target_ref": branch,
                    "commit": commit,
                    "changed_paths": list(patch.changed_paths),
                },
            )
            result = ManagedCheckpointResult(
                integration_key=request.integration_key,
                integration_result=result_record,
                evidence_artifact_ids=(artifact.artifact_id,),
            )
            _atomic_write(
                result_path,
                _canonical(
                    {
                        "schema_version": "1.0",
                        "request_sha256": request_sha256,
                        "result": result.model_dump(mode="json"),
                    }
                ),
            )
            self._attest(request, result)
            return result

    def _commit(
        self,
        request: ManagedCheckpointRequest,
        workspace: WorkspaceManifest,
        changed_paths: Sequence[str],
    ) -> None:
        if not changed_paths:
            raise TaskManagerGitCheckpointError("checkpoint patch has no changed paths")
        self._git(
            workspace.path,
            "-c",
            "core.hooksPath=/dev/null",
            "add",
            "--",
            *changed_paths,
        )
        staged = tuple(
            sorted(
                item
                for item in self._git(
                    workspace.path,
                    "diff",
                    "--cached",
                    "--name-only",
                    "--no-renames",
                    "--",
                ).splitlines()
                if item
            )
        )
        if staged != tuple(sorted(changed_paths)):
            raise TaskManagerGitCheckpointError(
                "Git index does not contain exactly the reviewed changed paths"
            )
        self._git(
            workspace.path,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "user.name=JobSlayer",
            "-c",
            "user.email=jobslayer@local.invalid",
            "commit",
            "--no-gpg-sign",
            "-m",
            f"JobSlayer checkpoint {request.node_id}",
            "-m",
            f"Integration-Key: {request.integration_key}",
        )

    def _attest(
        self,
        request: ManagedCheckpointRequest,
        result: ManagedCheckpointResult,
    ) -> None:
        if result.integration_key != request.integration_key:
            raise TaskManagerGitCheckpointError("checkpoint result uses another key")
        record = result.integration_result
        workspace = self._workspace_for(request)
        if (
            record.task_id != request.workflow_task_id
            or record.workspace_id != workspace.workspace_id
            or record.repository_root != workspace.repository_root
            or record.source_patch_sha256
            != request.verification_report.source_patch_sha256
            or record.changed_paths
            != request.verification_evidence.workspace.changed_paths
            or record.target_ref != workspace.branch_name
            or self._git(workspace.path, "rev-parse", "HEAD").strip().lower()
            != record.commit.lower()
            or self._git_bytes(
                workspace.path,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            )
        ):
            raise TaskManagerGitCheckpointError(
                "run branch no longer matches checkpoint integration evidence"
            )
        for artifact_id in result.evidence_artifact_ids:
            artifact = self.artifacts.get(artifact_id)
            if (
                artifact.task_id != request.workflow_task_id
                or artifact.run_id != request.run_id
                or not self.artifacts.verify(artifact)
            ):
                raise TaskManagerGitCheckpointError(
                    "checkpoint artifact is not task/run-bound and verified"
                )

    def _workspace_for(self, request: ManagedCheckpointRequest) -> WorkspaceManifest:
        provider_directory = self.providers_root / hashlib.sha256(
            request.provider_reference.provider_start_key.encode("utf-8")
        ).hexdigest()
        try:
            payload = json.loads(
                (provider_directory / "request.json").read_text(encoding="utf-8")
            )
            workspace = WorkspaceManifest.model_validate(payload["workspace"])
            persisted = payload["request"]
        except (OSError, json.JSONDecodeError, KeyError, ValidationError, TypeError) as exc:
            raise TaskManagerGitCheckpointError(
                "checkpoint workspace binding is unavailable"
            ) from exc
        if (
            persisted.get("run_id") != request.run_id
            or persisted.get("workflow_task_id") != request.workflow_task_id
            or persisted.get("provider_start_key")
            != request.provider_reference.provider_start_key
            or workspace.task_id != request.execution_binding.task.task_id
        ):
            raise TaskManagerGitCheckpointError(
                "checkpoint workspace belongs to another execution request"
            )
        return workspace

    def _load_result(self, path: Path, request_sha256: str) -> ManagedCheckpointResult:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("request_sha256") != request_sha256:
                raise ValueError("checkpoint request digest drifted")
            return ManagedCheckpointResult.model_validate(payload["result"])
        except (OSError, json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
            raise TaskManagerGitCheckpointError(
                "durable checkpoint result is invalid"
            ) from exc

    def _integration_directory(self, integration_key: str) -> Path:
        return self.integrations_root / hashlib.sha256(
            integration_key.encode("utf-8")
        ).hexdigest()

    def _git(self, workspace: str, *arguments: str) -> str:
        return self._git_process(workspace, arguments).stdout.decode("utf-8")

    def _git_bytes(self, workspace: str, *arguments: str) -> bytes:
        return self._git_process(workspace, arguments).stdout

    def _git_process(
        self,
        workspace: str,
        arguments: Sequence[str],
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["git", "-C", workspace, *arguments]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TaskManagerGitCheckpointError(
                f"Git checkpoint command could not complete: {arguments[0]}"
            ) from exc
        if result.returncode != 0:
            raise TaskManagerGitCheckpointError(
                f"Git checkpoint command failed: {arguments[0]}"
            )
        return result


__all__ = [
    "LocalTaskManagerGitCheckpointIntegrator",
    "TaskManagerGitCheckpointError",
]
