"""Immutable execution-target bindings and deterministic plan preflight."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field, model_validator

from jobslayer.domain.models import (
    AgentInvocation,
    CommandEnvironmentVariable,
    DomainModel,
    TaskSpec,
    TestbedInspection,
    ValidationProfile,
)
from jobslayer.orchestration import (
    IDENTIFIER_PATTERN,
    TaskPlanIssueSeverity,
    TaskPlanSnapshot,
)


class TaskManagerSourceDigest(DomainModel):
    schema_version: str = "1.0"
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskManagerDependencyAttachment(DomainModel):
    """Resolved local resource identity; commands may only consume its exposed path."""

    schema_version: str = "1.0"
    attachment_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    kind: Literal["git_checkout", "directory", "file"]
    environment_variable: str = Field(
        pattern=r"^[A-Z][A-Z0-9_]*$",
        max_length=128,
    )
    access_mode: Literal["read_only"] = "read_only"
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{40}$",
    )
    observed_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{40}$",
    )
    repository_urls: tuple[str, ...] = ()
    observed_repository_url: str | None = Field(default=None, min_length=1)
    root_path: str | None = Field(default=None, min_length=1, max_length=4_096)
    exposed_path: str | None = Field(default=None, min_length=1, max_length=4_096)
    working_tree_clean: bool | None = None
    issue: str | None = Field(default=None, min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_attachment(self) -> TaskManagerDependencyAttachment:
        if len(self.repository_urls) != len(set(self.repository_urls)):
            raise ValueError("dependency attachment repository URLs must be unique")
        if self.kind == "git_checkout":
            if self.expected_revision is None or not self.repository_urls:
                raise ValueError(
                    "git dependency attachments require revision and repository URLs"
                )
        elif any(
            item is not None
            for item in (
                self.expected_revision,
                self.observed_revision,
                self.observed_repository_url,
                self.working_tree_clean,
            )
        ) or self.repository_urls:
            raise ValueError(
                "only git dependency attachments may contain repository facts"
            )
        if (self.root_path is None) != (self.exposed_path is None):
            raise ValueError("dependency root and exposed paths must be present together")
        return self

    @property
    def ready(self) -> bool:
        base_ready = (
            self.issue is None
            and self.root_path is not None
            and self.exposed_path is not None
            and self.observed_sha256 == self.expected_sha256
        )
        if self.kind != "git_checkout":
            return base_ready
        return bool(
            base_ready
            and self.observed_revision is not None
            and self.expected_revision is not None
            and self.observed_revision.lower() == self.expected_revision.lower()
            and self.observed_repository_url in self.repository_urls
            and self.working_tree_clean is True
        )

    def command_environment(self) -> CommandEnvironmentVariable:
        if not self.ready or self.exposed_path is None:
            raise ValueError("unready dependency attachment has no command environment")
        assert self.observed_sha256 is not None
        return CommandEnvironmentVariable(
            name=self.environment_variable,
            value=self.exposed_path,
            source_id=self.attachment_id,
            source_sha256=self.observed_sha256,
        )


class TaskManagerExecutionBinding(DomainModel):
    """Exact source inputs and observed local baseline for one target."""

    schema_version: str = "1.0"
    target_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_digests: tuple[TaskManagerSourceDigest, ...] = Field(min_length=4)
    dependency_attachments: tuple[TaskManagerDependencyAttachment, ...] = ()
    validation_environment: tuple[CommandEnvironmentVariable, ...] = ()
    task: TaskSpec
    validation_profile: ValidationProfile
    invocation: AgentInvocation
    testbed_inspection: TestbedInspection
    executor_adapter: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    executor_model: str | None = Field(default=None, min_length=1, max_length=160)
    executor_reasoning_effort: Literal[
        "none", "low", "medium", "high", "xhigh", "max"
    ] | None = None
    resolved_at: datetime

    @model_validator(mode="after")
    def validate_binding(self) -> TaskManagerExecutionBinding:
        if self.resolved_at.tzinfo is None:
            raise ValueError("execution-target resolution time needs a timezone")
        paths = tuple(item.path for item in self.source_digests)
        if len(paths) != len(set(paths)):
            raise ValueError("execution-target source paths must be unique")
        attachment_ids = tuple(
            item.attachment_id for item in self.dependency_attachments
        )
        environment_variables = tuple(
            item.environment_variable for item in self.dependency_attachments
        )
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError("execution-target dependency ids must be unique")
        if len(environment_variables) != len(set(environment_variables)):
            raise ValueError("execution-target dependency environments must be unique")
        runtime_names = tuple(item.name for item in self.validation_environment)
        if len(runtime_names) != len(set(runtime_names)):
            raise ValueError("execution-target validation environments must be unique")
        if set(runtime_names).intersection(environment_variables):
            raise ValueError(
                "execution-target dependency and validation environments overlap"
            )
        if self.task.project_id != self.testbed_inspection.testbed_id:
            raise ValueError("execution target task and testbed do not match")
        if self.task.base_commit != self.testbed_inspection.baseline_commit:
            raise ValueError("execution target task and observed baseline do not match")
        if self.task.validation_profile != self.validation_profile.profile_id:
            raise ValueError("execution target validation profile does not match task")
        spec = self.invocation.run_spec
        if spec.task_id != self.task.task_id:
            raise ValueError("execution target invocation does not match task")
        if spec.executor_type != self.executor_adapter:
            raise ValueError("execution target invocation does not match adapter")
        return self

    def command_environment(self) -> tuple[CommandEnvironmentVariable, ...]:
        return (
            *(item.command_environment() for item in self.dependency_attachments),
            *self.validation_environment,
        )


class TaskManagerExecutionTarget(DomainModel):
    """Safe read projection of a resolved target for TaskManager clients."""

    schema_version: str = "1.0"
    target_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    testbed_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=160)
    task_title: str = Field(min_length=1, max_length=500)
    repository: str = Field(min_length=1, max_length=1_000)
    baseline_commit: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    checkout_path: str = Field(min_length=1, max_length=2_000)
    allowed_paths: tuple[str, ...] = Field(min_length=1)
    forbidden_paths: tuple[str, ...] = ()
    validation_profile_id: str = Field(min_length=1, max_length=160)
    validation_commands: tuple[tuple[str, ...], ...] = Field(min_length=1)
    executor_adapter: str = Field(pattern=IDENTIFIER_PATTERN, max_length=160)
    model_profile: str = Field(min_length=1, max_length=160)
    executor_model: str | None = Field(default=None, min_length=1, max_length=160)
    executor_reasoning_effort: str | None = Field(
        default=None,
        pattern=r"^(none|low|medium|high|xhigh|max)$",
    )
    timeout_seconds: int = Field(gt=0)
    maximum_input_tokens: int = Field(gt=0)
    maximum_output_tokens: int = Field(gt=0)
    maximum_context_bytes: int = Field(gt=0)
    maximum_cost_usd: float = Field(ge=0)
    local_baseline_ready: bool
    dependencies_ready: bool = True
    dependency_attachments: tuple[TaskManagerDependencyAttachment, ...] = ()
    validation_environment_names: tuple[str, ...] = ()
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskManagerExecutionTargetIssue(DomainModel):
    schema_version: str = "1.0"
    code: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    severity: TaskPlanIssueSeverity
    message: str = Field(min_length=1, max_length=1_000)
    node_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
        max_length=96,
    )


class TaskManagerExecutionTargetAssessment(DomainModel):
    schema_version: str = "1.0"
    plan_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    revision: int = Field(ge=1)
    target_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    ready: bool
    issues: tuple[TaskManagerExecutionTargetIssue, ...] = ()


class TaskManagerExecutionTargetRegistry(Protocol):
    def list_targets(self) -> tuple[TaskManagerExecutionBinding, ...]:
        """Resolve all explicitly registered targets from source-controlled inputs."""

    def get(self, target_id: str) -> TaskManagerExecutionBinding:
        """Resolve one explicitly registered target or raise a lookup error."""


def describe_execution_target(
    binding: TaskManagerExecutionBinding,
) -> TaskManagerExecutionTarget:
    spec = binding.invocation.run_spec
    if (
        spec.maximum_input_tokens is None
        or spec.maximum_output_tokens is None
        or spec.maximum_context_bytes is None
        or binding.task.max_cost_usd is None
    ):
        raise ValueError("TaskManager execution targets require explicit budgets")
    return TaskManagerExecutionTarget(
        target_id=binding.target_id,
        display_name=binding.display_name,
        testbed_id=binding.testbed_inspection.testbed_id,
        task_id=binding.task.task_id,
        task_title=binding.task.title,
        repository=binding.task.repository,
        baseline_commit=binding.task.base_commit,
        checkout_path=binding.testbed_inspection.checkout_path,
        allowed_paths=binding.task.allowed_paths,
        forbidden_paths=binding.task.forbidden_paths,
        validation_profile_id=binding.validation_profile.profile_id,
        validation_commands=tuple(
            check.argv for check in binding.validation_profile.checks if check.required
        ),
        executor_adapter=binding.executor_adapter,
        model_profile=spec.model_profile,
        executor_model=binding.executor_model,
        executor_reasoning_effort=binding.executor_reasoning_effort,
        timeout_seconds=spec.timeout_seconds,
        maximum_input_tokens=spec.maximum_input_tokens,
        maximum_output_tokens=spec.maximum_output_tokens,
        maximum_context_bytes=spec.maximum_context_bytes,
        maximum_cost_usd=binding.task.max_cost_usd,
        local_baseline_ready=binding.testbed_inspection.valid_local_baseline,
        dependencies_ready=all(
            item.ready for item in binding.dependency_attachments
        ),
        dependency_attachments=binding.dependency_attachments,
        validation_environment_names=tuple(
            item.name for item in binding.validation_environment
        ),
        source_bundle_sha256=binding.source_bundle_sha256,
    )


def assess_plan_for_target(
    snapshot: TaskPlanSnapshot,
    binding: TaskManagerExecutionBinding,
) -> TaskManagerExecutionTargetAssessment:
    """Reject target drift and cross-project instructions before finalization."""

    issues: list[TaskManagerExecutionTargetIssue] = []

    def add(
        code: str,
        severity: TaskPlanIssueSeverity,
        message: str,
        node_id: str | None = None,
    ) -> None:
        issues.append(
            TaskManagerExecutionTargetIssue(
                code=code,
                severity=severity,
                message=message,
                node_id=node_id,
            )
        )

    if snapshot.execution_target_id != binding.target_id:
        add(
            "target.selection_mismatch",
            TaskPlanIssueSeverity.BLOCKER,
            "当前计划没有绑定到所评估的执行目标。",
        )
    if snapshot.execution_target_source_sha256 is None:
        add(
            "target.source_binding_missing",
            TaskPlanIssueSeverity.BLOCKER,
            "当前计划尚未锁定执行目标源包哈希。",
        )
    elif snapshot.execution_target_source_sha256 != binding.source_bundle_sha256:
        add(
            "target.source_binding_drift",
            TaskPlanIssueSeverity.BLOCKER,
            "执行目标 runbook/task/profile 已从计划锁定的源包发生漂移。",
        )
    if not binding.testbed_inspection.valid_local_baseline:
        add(
            "target.baseline_not_ready",
            TaskPlanIssueSeverity.BLOCKER,
            "BraveNewWorld 本地检出不是已注册的干净固定基线。",
        )
    for attachment in binding.dependency_attachments:
        if not attachment.ready:
            detail = attachment.issue or "实际内容与源控声明不一致"
            add(
                "target.dependency_attachment_not_ready",
                TaskPlanIssueSeverity.BLOCKER,
                f"外部依赖 {attachment.attachment_id} 未就绪：{detail}。",
            )

    nodes = (
        snapshot.pending_proposal.nodes
        if snapshot.pending_proposal is not None
        else snapshot.nodes
    )
    forbidden_markers = (
        "./jobslayer",
        ".\\jobslayer",
        "jobslayer.domain",
        "workflowkernel",
    )
    for node in nodes:
        text = "\n".join(
            (
                node.title,
                node.description,
                *node.acceptance_criteria,
                *node.deliverables,
                *node.constraints,
                *node.risks,
                *node.verification_requirements,
                *node.attributes.values(),
            )
        ).lower()
        marker = next((item for item in forbidden_markers if item in text), None)
        if marker is not None:
            add(
                "target.cross_project_instruction",
                TaskPlanIssueSeverity.BLOCKER,
                f"节点包含不属于 BraveNewWorld 的指令：{marker}。",
                node.node_id,
            )

    required_commands = tuple(
        " ".join(check.argv)
        for check in binding.validation_profile.checks
        if check.required
    )
    all_text = "\n".join(
        (
            snapshot.task_description,
            *(
                item
                for node in nodes
                for item in (
                    node.title,
                    node.description,
                    *node.acceptance_criteria,
                    *node.deliverables,
                    *node.constraints,
                    *node.verification_requirements,
                )
            ),
        )
    )
    for command in required_commands:
        if command not in all_text:
            add(
                "target.validation_command_missing",
                TaskPlanIssueSeverity.BLOCKER,
                f"任务图尚未明确包含目标门禁命令：{command}。",
            )

    ready = not any(
        issue.severity is TaskPlanIssueSeverity.BLOCKER for issue in issues
    )
    return TaskManagerExecutionTargetAssessment(
        plan_id=snapshot.plan_id,
        revision=snapshot.revision,
        target_id=binding.target_id,
        ready=ready,
        issues=tuple(issues),
    )


__all__ = [
    "assess_plan_for_target",
    "describe_execution_target",
    "TaskManagerDependencyAttachment",
    "TaskManagerExecutionBinding",
    "TaskManagerExecutionTarget",
    "TaskManagerExecutionTargetAssessment",
    "TaskManagerExecutionTargetIssue",
    "TaskManagerExecutionTargetRegistry",
    "TaskManagerSourceDigest",
]
