from __future__ import annotations

import json
import hashlib
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
    ArchivedTaskPlanError,
    IncompleteTaskPlanError,
    PendingTaskPlanProposalError,
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

    def test_execution_target_selection_is_revisioned_and_rejects_unsafe_writes(self) -> None:
        created = self.service.create("绑定外部执行目标", plan_id="plan-target")
        with self.assertRaises(PendingTaskPlanProposalError):
            self.service.set_execution_target(
                created.plan_id,
                "brave-new-world-suspension-v1",
                "a" * 64,
                expected_revision=created.sequence,
            )
        self.assertEqual(self.service.get(created.plan_id), created)

        proposal = created.snapshot.pending_proposal
        assert proposal is not None
        applied = self.service.apply_proposal(
            created.plan_id,
            proposal.proposal_id,
            expected_revision=created.sequence,
        )
        selected = self.service.set_execution_target(
            created.plan_id,
            "brave-new-world-suspension-v1",
            "a" * 64,
            expected_revision=applied.sequence,
        )
        self.assertEqual(
            selected.snapshot.execution_target_id,
            "brave-new-world-suspension-v1",
        )
        self.assertEqual(
            selected.operation,
            "plan.execution_target_selected:brave-new-world-suspension-v1",
        )
        with self.assertRaises(StaleTaskPlanRevisionError):
            self.service.set_execution_target(
                created.plan_id,
                "another-target",
                "b" * 64,
                expected_revision=applied.sequence,
            )
        self.assertEqual(self.service.get(created.plan_id), selected)

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

    def test_rejects_proposal_without_changing_the_applied_graph(self) -> None:
        created = self.service.create("评审提案", plan_id="plan-reject")
        rejected = self.service.reject_proposal(
            "plan-reject",
            created.snapshot.pending_proposal.proposal_id,
            expected_revision=created.sequence,
        )

        self.assertEqual(rejected.snapshot.nodes, ())
        self.assertIsNone(rejected.snapshot.pending_proposal)
        self.assertEqual(rejected.operation, "agent_proposal.rejected_by_user")

    def test_edge_crud_rejects_cycles_and_keeps_revision_stable(self) -> None:
        created = self.service.create("编辑依赖", plan_id="plan-edges")
        applied = self.service.apply_proposal(
            "plan-edges",
            created.snapshot.pending_proposal.proposal_id,
            expected_revision=created.sequence,
        )
        added = self.service.create_edge(
            "plan-edges",
            expected_revision=applied.sequence,
            source_node_id="scope",
            target_node_id="verify",
            relation=TaskPlanEdgeRelation.DEPENDENCY,
            label="范围通过后验证",
            edge_id="scope-verify",
        )
        updated = self.service.update_edge(
            "plan-edges",
            "scope-verify",
            expected_revision=added.sequence,
            relation=TaskPlanEdgeRelation.BRANCH,
            label="并行验证入口",
        )
        self.assertEqual(updated.snapshot.edges[-1].relation, TaskPlanEdgeRelation.BRANCH)

        with self.assertRaisesRegex(ValidationError, "acyclic"):
            self.service.create_edge(
                "plan-edges",
                expected_revision=updated.sequence,
                source_node_id="finalize",
                target_node_id="scope",
                relation=TaskPlanEdgeRelation.DEPENDENCY,
            )
        self.assertEqual(self.service.get("plan-edges").sequence, updated.sequence)

        deleted = self.service.delete_edge(
            "plan-edges", "scope-verify", expected_revision=updated.sequence
        )
        self.assertNotIn(
            "scope-verify", {edge.edge_id for edge in deleted.snapshot.edges}
        )

    def test_assessment_archive_and_revision_derivation_are_governed(self) -> None:
        created = self.service.create("版本比较", plan_id="plan-history")
        applied = self.service.apply_proposal(
            "plan-history",
            created.snapshot.pending_proposal.proposal_id,
            expected_revision=created.sequence,
        )
        incomplete = self.service.update_node(
            "plan-history",
            "verify",
            expected_revision=applied.sequence,
            title="验证",
            description="缺少验证要求",
            kind=TaskPlanNodeKind.VALIDATION,
            executor_hint="verification engine",
            verification_requirements=(),
        )
        assessment = self.service.assess("plan-history")
        self.assertFalse(assessment.ready_to_finalize)
        self.assertIn(
            "node.verification_missing", {issue.code for issue in assessment.issues}
        )
        with self.assertRaises(IncompleteTaskPlanError):
            self.service.finalize(
                "plan-history", expected_revision=incomplete.sequence
            )

        derived = self.service.derive_from_revision(
            "plan-history",
            applied.sequence,
            expected_revision=incomplete.sequence,
        )
        self.assertEqual(derived.snapshot.nodes, applied.snapshot.nodes)
        self.assertEqual(
            derived.operation, f"plan.derived_from_revision:{applied.sequence}"
        )
        archived = self.service.set_archived(
            "plan-history", archived=True, expected_revision=derived.sequence
        )
        self.assertTrue(archived.snapshot.is_archived)
        with self.assertRaises(ArchivedTaskPlanError):
            self.service.create_node(
                "plan-history",
                expected_revision=archived.sequence,
                title="不允许修改",
            )
        restored = self.service.set_archived(
            "plan-history", archived=False, expected_revision=archived.sequence
        )
        self.assertFalse(restored.snapshot.is_archived)

    def test_hash_chain_detects_snapshot_tampering(self) -> None:
        self.service.create("保护计划历史", plan_id="plan-tamper")
        path = self.root / "plan-tamper.jsonl"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["snapshot"]["task_description"] = "tampered"
        path.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(TaskPlanJournalError, "record hash mismatch"):
            self.store.history("plan-tamper")

    def test_legacy_records_are_hashed_before_default_field_upgrade(self) -> None:
        self.service.create("读取旧版计划", plan_id="plan-legacy")
        path = self.root / "plan-legacy.jsonl"
        raw = json.loads(path.read_text(encoding="utf-8"))
        for name in ("is_archived", "archived_by", "archived_at"):
            raw["snapshot"].pop(name)
        introduced_node_fields = (
            "acceptance_criteria",
            "deliverables",
            "constraints",
            "risks",
            "verification_requirements",
            "requires_human_decision",
        )
        for node in raw["snapshot"]["pending_proposal"]["nodes"]:
            for name in introduced_node_fields:
                node.pop(name)
        unhashed = dict(raw)
        unhashed.pop("record_hash")
        raw["record_hash"] = hashlib.sha256(
            json.dumps(
                unhashed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        path.write_text(
            json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )

        loaded = self.store.history("plan-legacy")

        self.assertFalse(loaded[0].snapshot.is_archived)
        self.assertEqual(
            loaded[0].snapshot.pending_proposal.nodes[0].acceptance_criteria, ()
        )

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
