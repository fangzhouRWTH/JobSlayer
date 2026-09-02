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
                "brave-new-world-anygine-app-v1": (
                    "runbooks/bnw-anygine-small-app-001-codex.json"
                )
            },
        )
    def test_source_controlled_bnw_target_is_exact_and_uses_sibling_checkout(self) -> None:
        binding = self.registry.get("brave-new-world-anygine-app-v1")
        target = describe_execution_target(binding)

        expected_checkout = (
            self.repository_root / "../TestProjects/BraveNewWorld"
        ).resolve()
        self.assertEqual(Path(target.checkout_path), expected_checkout)
        self.assertEqual(target.testbed_id, "brave-new-world")
        self.assertEqual(target.model_profile, "gpt-5.6-sol-xhigh")
        self.assertEqual(target.executor_model, "gpt-5.6-sol")
        self.assertEqual(target.executor_reasoning_effort, "xhigh")
        self.assertEqual(target.timeout_seconds, 14_400)
        self.assertEqual(len(binding.source_digests), 4)
        self.assertEqual(
            tuple(item.attachment_id for item in binding.dependency_attachments),
            ("anygine-source", "anygine-conan-toolchain"),
        )
        self.assertTrue(all(not item.ready for item in binding.dependency_attachments))
        self.assertEqual(target.validation_environment_names, ())
        self.assertIn(("./bnw", "contract"), target.validation_commands)
        self.assertIn(
            ("./bnw", "test", "--jobs", "4"), target.validation_commands
        )
        self.assertIn(
            ("./bnw", "run", "--jobs", "4"), target.validation_commands
        )
        self.assertEqual(
            tuple(item.target_id for item in self.registry.list_targets()),
            (binding.target_id,),
        )
        with self.assertRaises(TaskManagerExecutionTargetNotFoundError):
            self.registry.get("unregistered-target")

    def test_missing_dependency_paths_block_target_without_hiding_it(self) -> None:
        binding = self.registry.get("brave-new-world-anygine-app-v1")
        target = describe_execution_target(binding)

        self.assertFalse(target.dependencies_ready)
        self.assertEqual(len(target.dependency_attachments), 2)
        self.assertTrue(
            all(not item.ready for item in target.dependency_attachments)
        )

    def test_target_preflight_allows_bnw_commands_and_rejects_cross_project_rules(self) -> None:
        unresolved = self.registry.get("brave-new-world-anygine-app-v1")
        inspection = unresolved.testbed_inspection.model_copy(
            update={
                "head_commit": unresolved.task.base_commit,
                "tag_commit": unresolved.task.base_commit,
                "working_tree_clean": True,
                "head_matches_baseline": True,
                "tag_matches_baseline": True,
                "origin_registered": True,
            }
        )
        ready_attachments = []
        for index, attachment in enumerate(unresolved.dependency_attachments, start=1):
            digest = f"{index}" * 64
            update = {
                "expected_sha256": digest,
                "observed_sha256": digest,
                "root_path": f"/fixture/dependency-{index}",
                "exposed_path": f"/fixture/dependency-{index}",
                "issue": None,
            }
            if attachment.kind == "git_checkout":
                update.update(
                    {
                        "observed_revision": attachment.expected_revision,
                        "observed_repository_url": attachment.repository_urls[0],
                        "working_tree_clean": True,
                    }
                )
            ready_attachments.append(attachment.model_copy(update=update))
        binding = unresolved.model_copy(
            update={
                "testbed_inspection": inspection,
                "dependency_attachments": tuple(ready_attachments),
            }
        )
        good = TaskPlanSnapshot(
            plan_id="bnw-good-plan",
            revision=1,
            task_description="基于 Anygine 开发 BraveNewWorld 小规模原生 App",
            execution_target_id=binding.target_id,
            execution_target_source_sha256=binding.source_bundle_sha256,
            nodes=(
                TaskPlanNode(
                    node_id="implement",
                    title="实现 BNW Anygine 小 App",
                    acceptance_criteria=("只消费固定 Anygine 公共 targets",),
                ),
                TaskPlanNode(
                    node_id="verify",
                    title="验证 BNW",
                    kind=TaskPlanNodeKind.VALIDATION,
                    verification_requirements=(
                        "./bnw contract",
                        "./bnw test --jobs 4",
                        "./bnw run --jobs 4",
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
