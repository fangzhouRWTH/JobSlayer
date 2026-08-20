from __future__ import annotations

from datetime import UTC, datetime

from jobslayer.domain.models import (
    AgentInvocation,
    AgentRunSpec,
    CommandPolicy,
    CommandRule,
    RiskLevel,
    TaskSpec,
    TestbedInspection,
    ValidationCheckSpec,
    ValidationProfile,
)
from jobslayer.task_manager.binding import (
    TaskManagerExecutionBinding,
    TaskManagerSourceDigest,
)


FIXTURE_TARGET_ID = "fixture-project-target-v1"


def fixture_execution_binding(
    *,
    executor_adapter: str = "fixture-managed-executor",
) -> TaskManagerExecutionBinding:
    baseline = "a" * 40
    profile = ValidationProfile(
        profile_id="fixture-validation-v1",
        command_policy=CommandPolicy(
            policy_id="fixture-command-policy-v1",
            rules=(
                CommandRule(
                    rule_id="complete-suite",
                    argv_prefix=("统一验证入口",),
                ),
            ),
        ),
        checks=(
            ValidationCheckSpec(
                check_id="complete-suite",
                title="fixture full suite",
                argv=("统一验证入口",),
            ),
        ),
    )
    task = TaskSpec(
        task_id="fixture-task",
        project_id="fixture-project",
        title="Fixture execution target",
        objective="Exercise target-bound TaskManager behavior",
        repository="https://example.invalid/fixture.git",
        base_commit=baseline,
        allowed_paths=("src/",),
        required_capabilities=("build_and_test",),
        acceptance_criteria=("fixture plan remains target bound",),
        validation_profile=profile.profile_id,
        risk=RiskLevel.LOW,
        max_cost_usd=1,
    )
    invocation = AgentInvocation(
        run_spec=AgentRunSpec(
            run_id="fixture-run",
            task_id=task.task_id,
            executor_type=executor_adapter,
            model_profile="fixture-model",
            context_package_id="fixture-context",
            workspace_id="fixture-workspace",
            permission_profile="workspace_write",
            timeout_seconds=60,
            max_attempts=1,
            max_repairs=0,
            maximum_input_tokens=1_000,
            maximum_output_tokens=500,
            maximum_context_bytes=16_384,
            output_schema="none",
        ),
        prompt="Execute only the fixture target and report evidence.",
    )
    return TaskManagerExecutionBinding(
        target_id=FIXTURE_TARGET_ID,
        display_name="Fixture project target",
        source_bundle_sha256="b" * 64,
        source_digests=tuple(
            TaskManagerSourceDigest(path=f"fixture-{index}.json", sha256=f"{index}" * 64)
            for index in range(1, 5)
        ),
        task=task,
        validation_profile=profile,
        invocation=invocation,
        testbed_inspection=TestbedInspection(
            testbed_id=task.project_id,
            checkout_path="/tmp/fixture-project",
            baseline_commit=baseline,
            head_commit=baseline,
            tag="fixture-v1",
            tag_commit=baseline,
            origin_url=task.repository,
            working_tree_clean=True,
            head_matches_baseline=True,
            tag_matches_baseline=True,
            origin_registered=True,
            baseline_published=False,
        ),
        executor_adapter=executor_adapter,
        resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class FixtureExecutionTargetRegistry:
    def __init__(self, *, executor_adapter: str = "fixture-managed-executor"):
        self.binding = fixture_execution_binding(executor_adapter=executor_adapter)

    def list_targets(self) -> tuple[TaskManagerExecutionBinding, ...]:
        return (self.binding,)

    def get(self, target_id: str) -> TaskManagerExecutionBinding:
        if target_id != self.binding.target_id:
            raise LookupError(target_id)
        return self.binding
