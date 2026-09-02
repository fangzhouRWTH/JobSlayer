"""Durable, policy-constrained local validation for TaskManager validation nodes."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from pydantic import ValidationError

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.adapters.local_dependency_attachments import (
    reinspect_local_dependency_attachment,
)
from jobslayer.adapters.local_command import GovernedLocalCommandRunner
from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.domain.models import CommandRequest, CommandStatus, WorkspaceManifest
from jobslayer.execution.platforms import local_host_platform
from jobslayer.orchestration import TaskPlanNodeKind
from jobslayer.task_manager.execution import (
    ManagedExecutionObservation,
    ManagedExecutionReference,
    ManagedExecutionRequest,
    ManagedExecutionStatus,
    ManagedValidationCheckEvidence,
    ManagedVerificationEvidence,
)
from jobslayer.task_manager.binding import TaskManagerDependencyAttachment


class TaskManagerValidationError(RuntimeError):
    """A source-bound validation attempt could not produce trustworthy facts."""


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


class LocalTaskManagerValidationRunner:
    """Execute only the finalized validation profile in the existing run worktree.

    The stable request and reference are persisted before commands run. Repeating the
    same key reuses the terminal result; a process loss before terminal persistence may
    rerun only the source-controlled, policy-constrained validation commands.
    """

    adapter_id = "local_validation"
    producer = "task-manager-local-validation"

    def __init__(
        self,
        state_root: str | Path,
        artifacts: ArtifactRegistry,
    ):
        self.state_root = Path(state_root).resolve(strict=False)
        self.validations_root = self.state_root / "validations"
        self.runs_root = self.state_root / "runs"
        self.workspaces_root = self.state_root / "workspaces"
        self.validations_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifacts = artifacts
        self._lock = threading.Lock()

    def start_or_locate(
        self,
        request: ManagedExecutionRequest,
    ) -> ManagedExecutionReference:
        if request.node.kind is not TaskPlanNodeKind.VALIDATION:
            raise TaskManagerValidationError("local validator accepts validation nodes only")
        with self._lock:
            state = self._state_directory(request.provider_start_key)
            workspace = self._checkpoint_workspace(request)
            attachments = self._dependency_attachments(request)
            state.mkdir(parents=True, exist_ok=True, mode=0o700)
            request_payload = {
                "schema_version": "1.0",
                "request": request.model_dump(mode="json"),
                "workspace": workspace.model_dump(mode="json"),
                "dependency_attachments": [
                    item.model_dump(mode="json") for item in attachments
                ],
            }
            request_bytes = _canonical(request_payload)
            request_sha256 = hashlib.sha256(request_bytes).hexdigest()
            request_path = state / "request.json"
            if request_path.exists():
                if request_path.read_bytes() != request_bytes:
                    raise TaskManagerValidationError(
                        "validation key was reused for a different request"
                    )
            else:
                _atomic_write(request_path, request_bytes)

            reference_path = state / "reference.json"
            if reference_path.exists():
                persisted = self._read_json(reference_path)
                if persisted.get("request_sha256") != request_sha256:
                    raise TaskManagerValidationError(
                        "validation reference belongs to another request"
                    )
                try:
                    reference = ManagedExecutionReference.model_validate(
                        persisted["reference"]
                    )
                except (KeyError, TypeError, ValueError, ValidationError) as exc:
                    raise TaskManagerValidationError(
                        "persisted validation reference is invalid"
                    ) from exc
            else:
                start_artifact = self.artifacts.register_bytes(
                    task_id=request.workflow_task_id,
                    run_id=request.run_id,
                    artifact_type="task-manager-validation-request",
                    producer=self.producer,
                    content=request_bytes,
                    metadata={
                        "node_id": request.node.node_id,
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
                    evidence_artifact_ids=(start_artifact.artifact_id,),
                )
                _atomic_write(
                    reference_path,
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

            terminal_path = state / "terminal.json"
            if not terminal_path.exists():
                checks = self._run_checks(request, workspace)
                terminal_attachments = self._dependency_attachments(request)
                _atomic_write(
                    terminal_path,
                    _canonical(
                        {
                            "schema_version": "1.0",
                            "provider_run_id": reference.provider_run_id,
                            "request_sha256": request_sha256,
                            "finished_at": datetime.now(UTC).isoformat(),
                            "checks": [item.model_dump(mode="json") for item in checks],
                            "dependency_attachments": [
                                item.model_dump(mode="json")
                                for item in terminal_attachments
                            ],
                        }
                    ),
                )
            return reference

    def observe(
        self,
        reference: ManagedExecutionReference,
        *,
        after_cursor: str | None,
    ) -> ManagedExecutionObservation:
        del after_cursor
        state, record, terminal_bytes = self._bound_terminal(reference)
        terminal = json.loads(terminal_bytes)
        try:
            checks = tuple(
                ManagedValidationCheckEvidence.model_validate(item)
                for item in terminal["checks"]
            )
            terminal_attachments = tuple(
                TaskManagerDependencyAttachment.model_validate(item)
                for item in terminal.get("dependency_attachments", ())
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise TaskManagerValidationError("validation terminal evidence is invalid") from exc
        cursor = "tmcursor-" + hashlib.sha256(terminal_bytes).hexdigest()
        observation_path = state / "observations" / f"{cursor}.json"
        if observation_path.exists():
            try:
                return ManagedExecutionObservation.model_validate(
                    self._read_json(observation_path)
                )
            except (TypeError, ValueError, ValidationError) as exc:
                raise TaskManagerValidationError(
                    "persisted validation observation is invalid"
                ) from exc
        terminal_artifact = self.artifacts.register_bytes(
            task_id=str(record["workflow_task_id"]),
            run_id=str(record["run_id"]),
            artifact_type="task-manager-validation-terminal",
            producer=self.producer,
            content=terminal_bytes,
            metadata={
                "provider_run_id": reference.provider_run_id,
                "check_count": len(checks),
            },
        )
        passed = sum(item.result.status is CommandStatus.PASSED for item in checks)
        observation = ManagedExecutionObservation(
            provider_run_id=reference.provider_run_id,
            status=ManagedExecutionStatus.SUCCEEDED,
            cursor=cursor,
            summary=(
                f"deterministic validation runner completed {len(checks)} "
                f"source-controlled checks; {passed} command(s) exited with an "
                "accepted status and TaskManager still owns the verification decision"
            ),
            observed_at=datetime.now(UTC),
            evidence_artifact_ids=(terminal_artifact.artifact_id,),
        )
        _atomic_write(
            observation_path,
            _canonical(observation.model_dump(mode="json")),
        )
        return observation

    def collect_verification_evidence(
        self,
        reference: ManagedExecutionReference,
    ) -> ManagedVerificationEvidence:
        state, record, terminal_bytes = self._bound_terminal(reference)
        request_payload = self._read_json(state / "request.json")
        terminal = json.loads(terminal_bytes)
        try:
            request = ManagedExecutionRequest.model_validate(request_payload["request"])
            workspace = WorkspaceManifest.model_validate(request_payload["workspace"])
            checks = tuple(
                ManagedValidationCheckEvidence.model_validate(item)
                for item in terminal["checks"]
            )
            terminal_attachments = tuple(
                TaskManagerDependencyAttachment.model_validate(item)
                for item in terminal.get("dependency_attachments", ())
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise TaskManagerValidationError("validation evidence binding is invalid") from exc
        if (
            request.provider_start_key != reference.provider_start_key
            or request.run_id != str(record["run_id"])
            or request.workflow_task_id != str(record["workflow_task_id"])
        ):
            raise TaskManagerValidationError("validation request binding drifted")
        current_attachments = self._dependency_attachments(request)
        if terminal_attachments != current_attachments:
            raise TaskManagerValidationError(
                "validation dependency attachments drifted after command completion"
            )
        manager = GitWorktreeManager(workspace.repository_root, self.workspaces_root)
        inspection = manager.inspect(workspace)
        inspection_artifact = self.artifacts.register_bytes(
            task_id=request.workflow_task_id,
            run_id=request.run_id,
            artifact_type="task-manager-validation-workspace-inspection",
            producer=self.producer,
            content=_canonical(inspection.model_dump(mode="json")),
            metadata={
                "provider_run_id": reference.provider_run_id,
                "workspace_id": workspace.workspace_id,
                "head_commit": inspection.head_commit,
                "working_tree_clean": inspection.working_tree_clean,
            },
        )
        dependency_artifact_ids: tuple[str, ...] = ()
        if current_attachments:
            dependency_artifact = self.artifacts.register_bytes(
                task_id=request.workflow_task_id,
                run_id=request.run_id,
                artifact_type="task-manager-validation-dependency-attachments",
                producer=self.producer,
                content=_canonical(
                    {
                        "schema_version": "1.0",
                        "provider_run_id": reference.provider_run_id,
                        "attachments": [
                            item.model_dump(mode="json")
                            for item in current_attachments
                        ],
                    }
                ),
                metadata={
                    "provider_run_id": reference.provider_run_id,
                    "attachment_count": len(current_attachments),
                    "attachment_ids": ",".join(
                        item.attachment_id for item in current_attachments
                    ),
                },
            )
            dependency_artifact_ids = (dependency_artifact.artifact_id,)
        evidence_ids = tuple(
            dict.fromkeys(
                (
                    *(item.evidence_artifact_id for item in checks),
                    inspection_artifact.artifact_id,
                    *dependency_artifact_ids,
                )
            )
        )
        return ManagedVerificationEvidence(
            provider_run_id=reference.provider_run_id,
            source_commit=inspection.head_commit,
            source_patch_sha256=None,
            workspace=inspection,
            collected_at=datetime.now(UTC),
            evidence_artifact_ids=evidence_ids,
            validation_checks=checks,
            dependency_attachments=current_attachments,
        )

    def _run_checks(
        self,
        request: ManagedExecutionRequest,
        workspace: WorkspaceManifest,
    ) -> tuple[ManagedValidationCheckEvidence, ...]:
        manager = GitWorktreeManager(workspace.repository_root, self.workspaces_root)
        runner = GovernedLocalCommandRunner(manager)
        evidence: list[ManagedValidationCheckEvidence] = []
        profile = request.execution_binding.validation_profile
        environment = request.execution_binding.command_environment()
        platform = local_host_platform()
        for check in profile.checks:
            argv = check.argv_for(platform)
            result = runner.run(
                workspace,
                CommandRequest(
                    command_id=f"validation-{check.check_id}",
                    workspace_id=workspace.workspace_id,
                    task_id=request.execution_binding.task.task_id,
                    argv=argv,
                    cwd=check.cwd,
                    timeout_seconds=check.timeout_seconds,
                    environment=environment,
                ),
                profile.command_policy,
            )
            artifact = self.artifacts.register_bytes(
                task_id=request.workflow_task_id,
                run_id=request.run_id,
                artifact_type="task-manager-validation-command-result",
                producer=self.producer,
                content=_canonical(result.model_dump(mode="json")),
                metadata={
                    "node_id": request.node.node_id,
                    "check_id": check.check_id,
                    "command_status": result.status.value,
                    "stdout_sha256": result.stdout_sha256,
                    "stderr_sha256": result.stderr_sha256,
                },
            )
            evidence.append(
                ManagedValidationCheckEvidence(
                    check_id=check.check_id,
                    required=check.required,
                    result=result,
                    evidence_artifact_id=artifact.artifact_id,
                )
            )
        return tuple(evidence)

    @staticmethod
    def _dependency_attachments(
        request: ManagedExecutionRequest,
    ) -> tuple[TaskManagerDependencyAttachment, ...]:
        observed = tuple(
            reinspect_local_dependency_attachment(item)
            for item in request.execution_binding.dependency_attachments
        )
        if any(not item.ready for item in observed):
            failed = ", ".join(
                item.attachment_id for item in observed if not item.ready
            )
            raise TaskManagerValidationError(
                "validation dependency attachments are not ready: " + failed
            )
        if observed != request.execution_binding.dependency_attachments:
            raise TaskManagerValidationError(
                "validation dependency attachment identity drifted from the run binding"
            )
        return observed

    def _checkpoint_workspace(
        self,
        request: ManagedExecutionRequest,
    ) -> WorkspaceManifest:
        run_directory = self.runs_root / hashlib.sha256(
            request.run_id.encode("utf-8")
        ).hexdigest()
        try:
            workspace = WorkspaceManifest.model_validate(
                self._read_json(run_directory / "workspace.json")
            )
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise TaskManagerValidationError(
                "validation requires the existing source-bound run workspace"
            ) from exc
        checkout = Path(
            request.execution_binding.testbed_inspection.checkout_path
        ).resolve(strict=True)
        if (
            workspace.task_id != request.execution_binding.task.task_id
            or Path(workspace.repository_root).resolve() != checkout
        ):
            raise TaskManagerValidationError("validation workspace binding drifted")
        manager = GitWorktreeManager(checkout, self.workspaces_root)
        current = manager.inspect(workspace)
        if not current.working_tree_clean:
            raise TaskManagerValidationError(
                "validation requires a clean checkpointed run workspace"
            )
        checkpoint = workspace.model_copy(
            update={
                "requested_base_commit": current.head_commit,
                "resolved_base_commit": current.head_commit,
            }
        )
        inspection = manager.inspect(checkpoint)
        if inspection.changed_paths or not inspection.working_tree_clean:
            raise TaskManagerValidationError(
                "validation workspace is not clean relative to its current checkpoint"
            )
        return checkpoint

    def _bound_terminal(
        self,
        reference: ManagedExecutionReference,
    ) -> tuple[Path, dict[str, Any], bytes]:
        if reference.adapter_id != self.adapter_id:
            raise TaskManagerValidationError("validation reference uses another adapter")
        state = self._state_directory(reference.provider_start_key)
        record = self._read_json(state / "reference.json")
        try:
            persisted = ManagedExecutionReference.model_validate(record["reference"])
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise TaskManagerValidationError(
                "persisted validation reference is invalid"
            ) from exc
        if persisted != reference:
            raise TaskManagerValidationError("validation reference does not match durable state")
        try:
            terminal = (state / "terminal.json").read_bytes()
        except OSError as exc:
            raise TaskManagerValidationError("validation terminal result is unavailable") from exc
        return state, record, terminal

    def _state_directory(self, start_key: str) -> Path:
        digest = hashlib.sha256(start_key.encode("utf-8")).hexdigest()
        return self.validations_root / digest

    @staticmethod
    def _provider_run_id(start_key: str) -> str:
        digest = hashlib.sha256(f"local-validation:{start_key}".encode()).hexdigest()
        return f"validation-{digest}"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TaskManagerValidationError(
                f"durable validation state is unavailable: {path.name}"
            ) from exc
        if not isinstance(payload, dict):
            raise TaskManagerValidationError("durable validation state must be an object")
        return payload


__all__ = ["LocalTaskManagerValidationRunner", "TaskManagerValidationError"]
