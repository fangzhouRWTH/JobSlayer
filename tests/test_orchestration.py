from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from jobslayer.adapters.local_orchestration import (
    LocalTaskPlanStore,
    TaskPlanJournalError,
)
from jobslayer.adapters.local_planning_agent import LocalPlanningAgent
from jobslayer.application.task_orchestration import (
    StaleTaskPlanRevisionError,
    TaskOrchestrationService,
    TaskPlanProposalMismatchError,
)
from jobslayer.orchestration import (
    TaskPlanEdge,
    TaskPlanEdgeRelation,
    TaskPlanNode,
    TaskPlanNodeKind,
    TaskPlanProposal,
    TaskPlanStatus,
)


class TaskOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = LocalTaskPlanStore(self.root)
        self.service = TaskOrchestrationService(
            self.store,
            LocalPlanningAgent(),
            actor_id="planner@example.invalid",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_discussion_proposes_graph_but_only_user_application_changes_plan(self) -> None:
        created = self.service.create(
            "实现一个允许讨论、分支和最终确认的任务编排框架",
            plan_id="plan-demo",
        )

        self.assertEqual(created.sequence, 1)
        self.assertEqual(created.snapshot.nodes, ())
        self.assertEqual(len(created.snapshot.pending_proposal.nodes), 5)
        self.assertEqual(
            [message.role.value for message in created.snapshot.conversation],
            ["user", "agent"],
        )

        refined = self.service.discuss(
            "plan-demo",
            "把实施阶段拆分一个子任务：先定义稳定的计划契约",
            expected_revision=1,
            selected_node_id="implement",
        )
        self.assertEqual(refined.snapshot.nodes, ())
        self.assertEqual(len(refined.snapshot.pending_proposal.nodes), 6)
        self.assertEqual(
            refined.snapshot.pending_proposal.edges[-1].relation,
            TaskPlanEdgeRelation.SUBTASK,
        )

        applied = self.service.apply_proposal(
            "plan-demo",
            refined.snapshot.pending_proposal.proposal_id,
            expected_revision=2,
        )

        self.assertEqual(applied.sequence, 3)
        self.assertEqual(len(applied.snapshot.nodes), 6)
        self.assertIsNone(applied.snapshot.pending_proposal)
        self.assertEqual(
            [record.operation for record in self.store.history("plan-demo")],
            [
                "plan.created_with_agent_proposal",
                "discussion.proposal_recorded",
                "agent_proposal.applied_by_user",
            ],
        )

    def test_node_crud_branch_subtask_and_revisioned_finalization(self) -> None:
        created = self.service.create("编排发布流程", plan_id="plan-crud")
        applied = self.service.apply_proposal(
            "plan-crud",
            created.snapshot.pending_proposal.proposal_id,
            expected_revision=1,
        )
        updated = self.service.update_node(
            "plan-crud",
            "implement",
            expected_revision=applied.sequence,
            title="实现最小纵向切片",
            description="先闭合一个可验证路径。",
            kind=TaskPlanNodeKind.TASK,
            executor_hint="codex-compatible",
        )
        branched = self.service.split_node(
            "plan-crud",
            "implement",
            expected_revision=updated.sequence,
            title="并行评估兼容性",
            description="不阻塞主线路。",
            relation=TaskPlanEdgeRelation.BRANCH,
        )
        branch = branched.snapshot.nodes[-1]
        self.assertEqual(branched.snapshot.edges[-1].relation, TaskPlanEdgeRelation.BRANCH)

        deleted = self.service.delete_node(
            "plan-crud", branch.node_id, expected_revision=branched.sequence
        )
        self.assertNotIn(branch.node_id, {node.node_id for node in deleted.snapshot.nodes})
        finalized = self.service.finalize(
            "plan-crud", expected_revision=deleted.sequence
        )

        self.assertEqual(finalized.snapshot.status, TaskPlanStatus.FINALIZED)
        self.assertEqual(
            finalized.snapshot.latest_finalized_revision, finalized.sequence
        )
        self.assertEqual(finalized.snapshot.finalized_by, "planner@example.invalid")

        revised = self.service.update_node(
            "plan-crud",
            "verify",
            expected_revision=finalized.sequence,
            title="扩大验证矩阵",
            description="最终路径确认后新增的修改。",
            kind=TaskPlanNodeKind.VALIDATION,
            executor_hint="verification engine",
        )
        self.assertEqual(revised.snapshot.status, TaskPlanStatus.DRAFT)
        self.assertEqual(
            revised.snapshot.latest_finalized_revision, finalized.sequence
        )
        self.assertIsNone(revised.snapshot.finalized_by)
        self.assertEqual(len(self.store.history("plan-crud")), revised.sequence)

    def test_stale_revision_and_wrong_proposal_do_not_append(self) -> None:
        created = self.service.create("测试并发保护", plan_id="plan-stale")

        with self.assertRaises(TaskPlanProposalMismatchError):
            self.service.apply_proposal(
                "plan-stale", "proposal-other", expected_revision=1
            )
        with self.assertRaises(StaleTaskPlanRevisionError):
            self.service.discuss(
                "plan-stale", "增加支线", expected_revision=0
            )

        self.assertEqual(len(self.store.history("plan-stale")), 1)
        self.assertEqual(created.record_hash, self.store.history("plan-stale")[0].record_hash)

    def test_hash_chain_detects_snapshot_tampering(self) -> None:
        self.service.create("保护计划历史", plan_id="plan-tamper")
        path = self.root / "plan-tamper.jsonl"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["snapshot"]["task_description"] = "tampered"
        path.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(TaskPlanJournalError, "record hash mismatch"):
            self.store.history("plan-tamper")

    def test_proposed_graph_rejects_cycles(self) -> None:
        first = TaskPlanNode(node_id="one", title="One")
        second = TaskPlanNode(node_id="two", title="Two")
        with self.assertRaisesRegex(ValidationError, "acyclic"):
            TaskPlanProposal(
                proposal_id="proposal-cycle",
                based_on_revision=1,
                summary="invalid cycle",
                agent_adapter="fixture",
                nodes=(first, second),
                edges=(
                    TaskPlanEdge(
                        edge_id="one-two",
                        source_node_id="one",
                        target_node_id="two",
                        relation=TaskPlanEdgeRelation.SEQUENCE,
                    ),
                    TaskPlanEdge(
                        edge_id="two-one",
                        source_node_id="two",
                        target_node_id="one",
                        relation=TaskPlanEdgeRelation.DEPENDENCY,
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
