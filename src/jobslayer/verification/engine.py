from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.domain.models import (
    CheckResult,
    CheckStatus,
    CommandRequest,
    CommandStatus,
    TaskSpec,
    ValidationProfile,
    VerificationReport,
    WorkspaceManifest,
    WorkspacePatch,
)
from jobslayer.execution.runner import CommandExecutionError, CommandRunner


class VerificationError(RuntimeError):
    """Raised when verification inputs do not form one governed task context."""


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class VerificationEngine:
    """Execute a trusted validation profile and register structured evidence."""

    def __init__(
        self,
        command_runner: CommandRunner,
        artifact_registry: ArtifactRegistry,
    ):
        self.command_runner = command_runner
        self.artifact_registry = artifact_registry

    def verify(
        self,
        *,
        task: TaskSpec,
        workspace: WorkspaceManifest,
        patch: WorkspacePatch,
        profile: ValidationProfile,
    ) -> VerificationReport:
        self._validate_bindings(task, workspace, patch, profile)
        checks: list[CheckResult] = []
        for check in profile.checks:
            request = CommandRequest(
                command_id=f"validation-{check.check_id}",
                workspace_id=workspace.workspace_id,
                task_id=task.task_id,
                argv=check.argv,
                cwd=check.cwd,
                timeout_seconds=check.timeout_seconds,
            )
            try:
                result = self.command_runner.run(
                    workspace,
                    request,
                    profile.command_policy,
                )
            except CommandExecutionError as exc:
                error_payload = {
                    "schema_version": "1.0",
                    "task_id": task.task_id,
                    "workspace_id": workspace.workspace_id,
                    "command_id": request.command_id,
                    "check_id": check.check_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                artifact = self.artifact_registry.register_bytes(
                    task_id=task.task_id,
                    artifact_type="validation-command-error",
                    producer="verification-engine",
                    content=_canonical_json_bytes(error_payload),
                    metadata={"check_id": check.check_id},
                )
                checks.append(
                    CheckResult(
                        check_id=check.check_id,
                        status=CheckStatus.ERROR,
                        required=check.required,
                        command=check.argv,
                        artifact_ids=(artifact.artifact_id,),
                        summary=f"{check.title}: command execution was rejected",
                        evidence_hash=artifact.sha256,
                    )
                )
                continue

            artifact = self.artifact_registry.register_bytes(
                task_id=task.task_id,
                artifact_type="validation-command-result",
                producer="verification-engine",
                content=_canonical_json_bytes(result.model_dump(mode="json")),
                metadata={
                    "check_id": check.check_id,
                    "command_status": result.status.value,
                },
            )
            status = {
                CommandStatus.PASSED: CheckStatus.PASSED,
                CommandStatus.FAILED: CheckStatus.FAILED,
                CommandStatus.TIMED_OUT: CheckStatus.ERROR,
            }[result.status]
            summary = {
                CheckStatus.PASSED: f"{check.title}: passed",
                CheckStatus.FAILED: (
                    f"{check.title}: failed with exit code {result.exit_code}"
                ),
                CheckStatus.ERROR: f"{check.title}: timed out",
            }[status]
            checks.append(
                CheckResult(
                    check_id=check.check_id,
                    status=status,
                    required=check.required,
                    command=check.argv,
                    artifact_ids=(artifact.artifact_id,),
                    summary=summary,
                    evidence_hash=artifact.sha256,
                )
            )

        required_checks_passed = all(
            check.status is CheckStatus.PASSED
            for check in checks
            if check.required
        )
        return VerificationReport(
            report_id=f"verification-{uuid4().hex}",
            task_id=task.task_id,
            source_commit=workspace.resolved_base_commit,
            source_patch_sha256=patch.sha256,
            checks=tuple(checks),
            required_checks_passed=required_checks_passed,
        )

    @staticmethod
    def _validate_bindings(
        task: TaskSpec,
        workspace: WorkspaceManifest,
        patch: WorkspacePatch,
        profile: ValidationProfile,
    ) -> None:
        if profile.profile_id != task.validation_profile:
            raise VerificationError("validation profile does not match the task")
        if workspace.task_id != task.task_id or patch.task_id != task.task_id:
            raise VerificationError("workspace or patch belongs to a different task")
        if patch.workspace_id != workspace.workspace_id:
            raise VerificationError("patch belongs to a different workspace")
        if patch.base_commit != workspace.resolved_base_commit:
            raise VerificationError("patch and workspace base commits do not match")
