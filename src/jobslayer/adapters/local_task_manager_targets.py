"""Source-controlled local execution-target registry for TaskManager."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from jobslayer.adapters.local_dependency_attachments import (
    resolve_local_dependency_attachment,
)
from jobslayer.adapters.local_testbed import LocalGitTestbedInspector
from jobslayer.application.runbook import CodexCliConfig, LocalRunbookLoader, RunbookError
from jobslayer.domain.models import CommandEnvironmentVariable
from jobslayer.task_manager.binding import (
    TaskManagerExecutionBinding,
    TaskManagerSourceDigest,
)


class TaskManagerExecutionTargetNotFoundError(LookupError):
    pass


class LocalTaskManagerExecutionTargetRegistry:
    """Resolve only targets explicitly named by the host application."""

    def __init__(
        self,
        repository_root: str | Path,
        targets: dict[str, str],
        *,
        dependency_paths: dict[str, str | Path] | None = None,
        validation_environment: dict[str, str] | None = None,
    ):
        self.repository_root = Path(repository_root).resolve(strict=True)
        self.targets = dict(targets)
        self.dependency_paths = dict(dependency_paths or {})
        self.validation_environment = dict(validation_environment or {})
        if not self.targets:
            raise ValueError("TaskManager needs at least one explicit execution target")

    def list_targets(self) -> tuple[TaskManagerExecutionBinding, ...]:
        return tuple(self.get(target_id) for target_id in sorted(self.targets))

    def get(self, target_id: str) -> TaskManagerExecutionBinding:
        try:
            runbook_path = self.targets[target_id]
        except KeyError as exc:
            raise TaskManagerExecutionTargetNotFoundError(
                f"TaskManager execution target does not exist: {target_id}"
            ) from exc
        prepared = LocalRunbookLoader(self.repository_root).load(runbook_path)
        hint = prepared.testbed.local_checkout_hint
        if hint is None:
            raise RunbookError("execution target testbed has no local checkout hint")
        checkout = (self.repository_root / hint).resolve(strict=False)
        inspection = LocalGitTestbedInspector(checkout).inspect(prepared.testbed)
        source_paths = (
            self._relative(prepared.source_path),
            prepared.runbook.testbed_path,
            prepared.runbook.task_path,
            prepared.runbook.validation_profile_path,
        )
        digests = tuple(
            TaskManagerSourceDigest(
                path=path,
                sha256=hashlib.sha256(
                    (self.repository_root / path).read_bytes()
                ).hexdigest(),
            )
            for path in sorted(source_paths)
        )
        attachments = tuple(
            resolve_local_dependency_attachment(
                config,
                self.dependency_paths.get(config.attachment_id),
            )
            for config in prepared.runbook.dependency_attachments
        )
        unexpected_environment = set(self.validation_environment).difference(
            prepared.runbook.validation_environment_allowlist
        )
        if unexpected_environment:
            raise RunbookError(
                "validation environment was not source-control allowlisted: "
                + ", ".join(sorted(unexpected_environment))
            )
        validation_environment = tuple(
            CommandEnvironmentVariable(
                name=name,
                value=self.validation_environment[name],
                source_id=f"runtime-{name.lower().replace('_', '-')}",
                source_sha256=hashlib.sha256(
                    self.validation_environment[name].encode("utf-8")
                ).hexdigest(),
            )
            for name in prepared.runbook.validation_environment_allowlist
            if name in self.validation_environment
        )
        bundle = hashlib.sha256(
            json.dumps(
                {
                    "source_digests": [
                        item.model_dump(mode="json") for item in digests
                    ],
                    "dependency_identities": [
                        item.model_dump(
                            mode="json",
                            exclude={"root_path", "exposed_path", "issue"},
                        )
                        for item in attachments
                    ],
                    "validation_environment": [
                        item.model_dump(mode="json")
                        for item in validation_environment
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        executor_model = None
        executor_reasoning_effort = None
        if isinstance(prepared.runbook.executor, CodexCliConfig):
            executor_model = prepared.runbook.executor.model
            executor_reasoning_effort = prepared.runbook.executor.reasoning_effort
        return TaskManagerExecutionBinding(
            target_id=target_id,
            display_name=f"{prepared.testbed.display_name} · {prepared.task.title}",
            source_bundle_sha256=bundle,
            source_digests=digests,
            dependency_attachments=attachments,
            validation_environment=validation_environment,
            task=prepared.task,
            validation_profile=prepared.validation_profile,
            invocation=prepared.runbook.invocation,
            testbed_inspection=inspection,
            executor_adapter=prepared.runbook.executor.adapter,
            executor_model=executor_model,
            executor_reasoning_effort=executor_reasoning_effort,
            resolved_at=datetime.now(UTC),
        )

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.repository_root).as_posix()


__all__ = [
    "LocalTaskManagerExecutionTargetRegistry",
    "TaskManagerExecutionTargetNotFoundError",
]
