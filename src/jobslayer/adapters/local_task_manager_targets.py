"""Source-controlled local execution-target registry for TaskManager."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from jobslayer.adapters.local_testbed import LocalGitTestbedInspector
from jobslayer.application.runbook import CodexCliConfig, LocalRunbookLoader, RunbookError
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
    ):
        self.repository_root = Path(repository_root).resolve(strict=True)
        self.targets = dict(targets)
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
        bundle = hashlib.sha256(
            json.dumps(
                [item.model_dump(mode="json") for item in digests],
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
