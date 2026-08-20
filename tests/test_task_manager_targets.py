from __future__ import annotations

from pathlib import Path
import unittest

from jobslayer.adapters.local_task_manager_targets import (
    LocalTaskManagerExecutionTargetRegistry,
    TaskManagerExecutionTargetNotFoundError,
)
from jobslayer.orchestration import (
    TaskPlanNode,
    TaskPlanNodeKind,
    TaskPlanSnapshot,
)
from jobslayer.task_manager.binding import (
    assess_plan_for_target,
    describe_execution_target,
)


class TaskManagerExecutionTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository_root = Path(__file__).resolve().parents[1]
        cls.registry = LocalTaskManagerExecutionTargetRegistry(
            cls.repository_root,
            {
                "brave-new-world-suspension-v1": (
                    "runbooks/bnw-suspension-visualization-001-codex.json"
                )
            },
        )

    def test_source_controlled_bnw_target_is_exact_and_baseline_ready(self) -> None:
        binding = self.registry.get("brave-new-world-suspension-v1")
        target = describe_execution_target(binding)

        self.assertTrue(target.local_baseline_ready)
        self.assertEqual(target.testbed_id, "brave-new-world")
        self.assertEqual(target.model_profile, "gpt-5.6-sol-xhigh")
        self.assertEqual(target.executor_model, "gpt-5.6-sol")
        self.assertEqual(target.executor_reasoning_effort, "xhigh")
        self.assertEqual(target.timeout_seconds, 10_800)
        self.assertEqual(len(binding.source_digests), 4)
        self.assertIn(("./bnw", "check"), target.validation_commands)
        self.assertEqual(
            tuple(item.target_id for item in self.registry.list_targets()),
            (binding.target_id,),
        )
        with self.assertRaises(TaskManagerExecutionTargetNotFoundError):
            self.registry.get("unregistered-target")

    def test_target_preflight_allows_bnw_commands_and_rejects_cross_project_rules(self) -> None:
        binding = self.registry.get("brave-new-world-suspension-v1")
        good = TaskPlanSnapshot(
            plan_id="bnw-good-plan",
            revision=1,
            task_description="开发 BraveNewWorld 悬架可视化案例",
            execution_target_id=binding.target_id,
            execution_target_source_sha256=binding.source_bundle_sha256,
            nodes=(
                TaskPlanNode(
                    node_id="implement",
                    title="实现 BNW 四分之一车辆悬架案例",
                    acceptance_criteria=("无头、API 和 UI 共享确定性内核",),
                ),
                TaskPlanNode(
                    node_id="verify",
                    title="验证 BNW",
                    kind=TaskPlanNodeKind.VALIDATION,
                    verification_requirements=(
                        "./bnw run-scenario scenarios/suspension-quarter-car.json",
                        "./bnw check",
                    ),
                ),
            ),
        )
        self.assertTrue(assess_plan_for_target(good, binding).ready)

        bad_node = good.nodes[0].model_copy(
            update={
                "constraints": (
                    "修改 jobslayer.domain 并通过 WorkflowKernel 推进状态",
                )
            }
        )
        bad = good.model_copy(update={"nodes": (bad_node, good.nodes[1])})
        assessment = assess_plan_for_target(bad, binding)
        self.assertFalse(assessment.ready)
        self.assertIn(
            "target.cross_project_instruction",
            {issue.code for issue in assessment.issues},
        )


if __name__ == "__main__":
    unittest.main()
