from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobslayer.adapters.local_orchestration import LocalTaskPlanStore
from jobslayer.adapters.local_planning_agent import LocalPlanningAgent
from jobslayer.application.task_manager import (
    RUN_ASSEMBLY_NOT_CONFIGURED,
    TaskManagerService,
)
from jobslayer.application.task_orchestration import (
    StaleTaskPlanRevisionError,
    TaskOrchestrationService,
)
from jobslayer.task_manager import (
    ManagedNodeState,
    ManagedTaskStage,
    TaskManagerHumanActionKind,
)


class TaskManagerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        planning = TaskOrchestrationService(
            LocalTaskPlanStore(Path(self.temporary_directory.name)),
            LocalPlanningAgent(),
            actor_id="planner@example.invalid",
        )
        self.manager = TaskManagerService(planning)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_read_model_tracks_proposal_applied_and_finalized_stages(self) -> None:
        created = self.manager.create(
            "开发一个可视化悬挂系统案例",
            task_id="task-suspension-demo",
        )

        self.assertEqual(created.task.stage, ManagedTaskStage.PROPOSAL_PENDING)
        self.assertTrue(created.task.pending_proposal)
        self.assertEqual(len(created.plan.nodes), 0)
        self.assertGreater(len(created.nodes), 0)
        self.assertTrue(
            all(item.state is ManagedNodeState.PROPOSED for item in created.nodes)
        )
        self.assertEqual(len(created.backlog), len(created.nodes))
        self.assertFalse(created.execution_available)
        self.assertEqual(len(created.human_actions), 1)
        self.assertEqual(
            created.human_actions[0].kind,
            TaskManagerHumanActionKind.PROPOSAL_DECISION,
        )
        self.assertGreaterEqual(len(created.human_actions[0].steps), 5)
        self.assertEqual(created.human_actions[0].expected_plan_revision, created.task.revision)
        self.assertEqual(
            created.execution_blockers,
            (RUN_ASSEMBLY_NOT_CONFIGURED,),
        )

        proposal = created.plan.pending_proposal
        assert proposal is not None
        applied = self.manager.apply_proposal(
            created.task.task_id,
            proposal.proposal_id,
            expected_revision=created.task.revision,
        )
        self.assertEqual(applied.task.stage, ManagedTaskStage.PLANNING)
        self.assertFalse(applied.task.pending_proposal)
        self.assertTrue(
            all(item.state is ManagedNodeState.PLANNED for item in applied.nodes)
        )
        self.assertEqual(
            applied.human_actions[0].kind,
            TaskManagerHumanActionKind.PLAN_FINALIZATION,
        )

        finalized = self.manager.finalize(
            applied.task.task_id,
            expected_revision=applied.task.revision,
        )
        self.assertEqual(finalized.task.stage, ManagedTaskStage.READY)
        self.assertTrue(
            all(item.state is ManagedNodeState.READY for item in finalized.nodes)
        )
        self.assertFalse(finalized.execution_available)
        self.assertEqual(
            finalized.human_actions[0].kind,
            TaskManagerHumanActionKind.RUN_ASSEMBLY,
        )
        self.assertIn("执行运行", finalized.backlog[0].reason)

        summaries = self.manager.list_tasks()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0], finalized.task)

    def test_discussion_is_logged_and_stale_revision_is_rejected(self) -> None:
        created = self.manager.create("规划一个任务管理器", task_id="task-discuss")
        discussed = self.manager.discuss(
            created.task.task_id,
            "把验证步骤拆成独立节点",
            expected_revision=created.task.revision,
            selected_node_id=created.nodes[0].node.node_id,
        )

        conversation_entries = [
            item for item in discussed.log if item.category.value == "conversation"
        ]
        self.assertEqual(len(discussed.plan.conversation), 4)
        self.assertEqual(len(conversation_entries), 4)
        self.assertTrue(
            any("把验证步骤拆成独立节点" in item.summary for item in conversation_entries)
        )
        self.assertEqual(
            len({item.log_id for item in discussed.log}), len(discussed.log)
        )
        self.assertTrue(all(item.record_hash for item in discussed.log))

        with self.assertRaises(StaleTaskPlanRevisionError):
            self.manager.discuss(
                created.task.task_id,
                "这是一个过期写入",
                expected_revision=created.task.revision,
            )


if __name__ == "__main__":
    unittest.main()
