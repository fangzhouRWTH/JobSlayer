from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from jobslayer.domain.models import (
    AgentInvocation,
    RiskLevel,
    TaskSpec,
    TestbedSpec,
    ValidationProfile,
)


class RunbookError(RuntimeError):
    """Raised when local files do not form one safe, bound execution input."""


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value.strip() or path.is_absolute() or ".." in path.parts:
        raise ValueError("runbook paths must be repository-relative POSIX paths")
    if value.startswith("./"):
        raise ValueError("runbook paths must be normalized without './'")
    return value


class _RunbookModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScriptedPatchConfig(_RunbookModel):
    adapter: Literal["scripted_patch"] = "scripted_patch"
    patch_path: str
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("patch_path")
    @classmethod
    def validate_patch_path(cls, value: str) -> str:
        return _relative_path(value)


class CodexCliConfig(_RunbookModel):
    """Fixed adapter selection; executable and credentials stay operator-owned."""

    adapter: Literal["codex_cli"] = "codex_cli"


class LocalTaskRunbook(_RunbookModel):
    """Source-controlled references and invocation for one governed local run."""

    schema_version: str = "1.0"
    testbed_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    testbed_path: str
    task_path: str
    validation_profile_path: str
    invocation: AgentInvocation
    executor: Annotated[
        ScriptedPatchConfig | CodexCliConfig,
        Field(discriminator="adapter"),
    ]

    @field_validator("testbed_path", "task_path", "validation_profile_path")
    @classmethod
    def validate_reference_path(cls, value: str) -> str:
        return _relative_path(value)

    @model_validator(mode="after")
    def validate_executor_invocation(self) -> LocalTaskRunbook:
        spec = self.invocation.run_spec
        safe_id = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
        if not safe_id.fullmatch(spec.run_id):
            raise ValueError("run id must be safe for local record names")
        if spec.executor_type != self.executor.adapter:
            raise ValueError("invocation executor_type must match the configured adapter")
        if isinstance(self.executor, ScriptedPatchConfig):
            if spec.model_profile != "deterministic-replay-v1":
                raise ValueError("scripted_patch requires deterministic-replay-v1")
            if spec.permission_profile != "workspace_write":
                raise ValueError("scripted_patch requires workspace_write")
            if spec.output_schema != "unified_diff":
                raise ValueError("scripted_patch output_schema must be unified_diff")
        else:
            if spec.model_profile != "default":
                raise ValueError("codex_cli currently requires the default model profile")
            if spec.permission_profile != "workspace_write":
                raise ValueError("codex_cli implementation runs require workspace_write")
            if spec.output_schema != "none":
                raise ValueError("codex_cli currently requires the none output schema")
            if spec.max_attempts != 1:
                raise ValueError("codex_cli retry policy is not implemented; max_attempts must be 1")
            if (
                spec.maximum_input_tokens is None
                or spec.maximum_output_tokens is None
                or spec.maximum_context_bytes is None
            ):
                raise ValueError(
                    "codex_cli requires explicit input/output/context and task cost budgets"
                )
        return self


@dataclass(frozen=True)
class PreparedLocalRun:
    source_path: Path
    runbook: LocalTaskRunbook
    testbed: TestbedSpec
    task: TaskSpec
    validation_profile: ValidationProfile
    patch_bytes: bytes | None


class LocalRunbookLoader:
    """Resolve source-controlled run inputs without mutating repositories."""

    def __init__(self, repository_root: str | Path):
        self.repository_root = Path(repository_root).resolve(strict=True)

    def load(self, path: str | Path) -> PreparedLocalRun:
        source_path = self._source_file(path)
        runbook = self._model_from_json(source_path, LocalTaskRunbook, "runbook")
        testbed = self._model_from_json(
            self._referenced_file(runbook.testbed_path), TestbedSpec, "testbed"
        )
        task = self._model_from_json(
            self._referenced_file(runbook.task_path), TaskSpec, "task"
        )
        profile = self._model_from_json(
            self._referenced_file(runbook.validation_profile_path),
            ValidationProfile,
            "validation profile",
        )
        patch_bytes = None
        if isinstance(runbook.executor, ScriptedPatchConfig):
            patch_path = self._referenced_file(runbook.executor.patch_path)
            try:
                patch_bytes = patch_path.read_bytes()
            except OSError as exc:
                raise RunbookError(f"could not read scripted patch: {patch_path}") from exc
            digest = hashlib.sha256(patch_bytes).hexdigest()
            if digest != runbook.executor.patch_sha256:
                raise RunbookError("scripted patch sha256 does not match the runbook")
        self._validate_bindings(runbook, testbed, task, profile)
        return PreparedLocalRun(
            source_path=source_path,
            runbook=runbook,
            testbed=testbed,
            task=task,
            validation_profile=profile,
            patch_bytes=patch_bytes,
        )

    def _source_file(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.repository_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise RunbookError(f"runbook does not exist: {candidate}") from exc
        if not resolved.is_file() or not resolved.is_relative_to(self.repository_root):
            raise RunbookError("runbook must be a file inside the JobSlayer checkout")
        return resolved

    def _referenced_file(self, value: str) -> Path:
        candidate = (self.repository_root / value).resolve(strict=False)
        if not candidate.is_relative_to(self.repository_root):
            raise RunbookError("runbook reference escapes the JobSlayer checkout")
        if not candidate.is_file():
            raise RunbookError(f"runbook reference is not a file: {value}")
        return candidate

    @staticmethod
    def _model_from_json(path: Path, model_type, label: str):
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise RunbookError(f"invalid {label}: {path}: {exc}") from exc

    @staticmethod
    def _validate_bindings(
        runbook: LocalTaskRunbook,
        testbed: TestbedSpec,
        task: TaskSpec,
        profile: ValidationProfile,
    ) -> None:
        if testbed.testbed_id != runbook.testbed_id:
            raise RunbookError("runbook testbed_id does not match its registration")
        if testbed.baseline is None:
            raise RunbookError("testbed has no fixed baseline")
        repository_urls = {
            testbed.repository.clone_url,
            *testbed.repository.alternative_clone_urls,
        }
        if task.repository not in repository_urls:
            raise RunbookError("task repository is not registered for the testbed")
        if task.project_id != testbed.testbed_id:
            raise RunbookError("task project_id does not match the testbed")
        if task.base_commit != testbed.baseline.commit:
            raise RunbookError("task base_commit does not match the testbed baseline")
        if task.validation_profile != profile.profile_id:
            raise RunbookError("task validation_profile does not match the profile")
        if runbook.invocation.run_spec.task_id != task.task_id:
            raise RunbookError("invocation task_id does not match the task")
        if isinstance(runbook.executor, CodexCliConfig) and task.max_cost_usd is None:
            raise RunbookError("codex_cli task requires an explicit maximum cost")
        if task.risk is not RiskLevel.LOW:
            raise RunbookError("local runbooks are currently limited to low-risk tasks")
        commands = {check.argv for check in profile.checks if check.required}
        if testbed.baseline.verification_command not in commands:
            raise RunbookError(
                "validation profile does not include the registered baseline command"
            )
