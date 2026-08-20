from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.task_manager_codex import (
    DurableTaskManagerCodexExecutor,
    TaskManagerCodexError,
)
from jobslayer.adapters.task_manager_git_checkpoint import (
    LocalTaskManagerGitCheckpointIntegrator,
    TaskManagerGitCheckpointError,
)
from jobslayer.domain.models import (
    ActorType,
    AgentInvocation,
    CheckResult,
    CheckStatus,
    ReviewReport,
    ReviewStatus,
    TestbedInspection,
    VerificationReport,
)
from jobslayer.orchestration import TaskPlanNode, TaskPlanNodeKind
from jobslayer.task_manager import (
    ManagedCheckpointRequest,
    ManagedExecutionRequest,
    ManagedExecutionStatus,
)
from tests.task_manager_fixtures import fixture_execution_binding


class DurableTaskManagerCodexExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "source"
        self.repository.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "JobSlayer Test")
        self._git("config", "user.email", "jobslayer@example.invalid")
        (self.repository / "src").mkdir()
        (self.repository / "src" / "value.txt").write_text(
            "baseline\n", encoding="utf-8"
        )
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.base_commit = self._git("rev-parse", "HEAD").strip()
        self.counter = self.root / "invocations.txt"
        self.fake_codex = self.root / "fake_codex.py"
        self.fake_codex.write_text(
            """import json
from pathlib import Path
import sys
import time

counter = Path(sys.argv[1])
existing = counter.read_text(encoding='utf-8') if counter.exists() else ''
counter.write_text(existing + 'started\\n', encoding='utf-8')
prompt = sys.stdin.read()
print(json.dumps({'type': 'thread.started', 'thread_id': 'fixture-thread'}), flush=True)
time.sleep(0.1)
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'fixture completed: ' + str(len(prompt))}}), flush=True)
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 120, 'output_tokens': 20}}), flush=True)
""",
            encoding="utf-8",
        )
        self.artifacts = LocalArtifactRegistry(self.root / "artifacts")
        self.binding = self._binding()
        self.request = ManagedExecutionRequest(
            provider_start_key="tmstart-durable-fixture",
            run_id="tmrun-durable-fixture",
            plan_id="plan-durable-fixture",
            plan_revision=7,
            plan_record_hash="c" * 64,
            workflow_task_id="tmnode-durable-fixture",
            execution_binding=self.binding,
            node=TaskPlanNode(
                node_id="implementation",
                title="Implement the fixture",
                description="Change only the fixture source file.",
                kind=TaskPlanNodeKind.TASK,
                acceptance_criteria=("The fixture is changed",),
                deliverables=("src/value.txt",),
            ),
            prompt="Implement this one finalized task node and report evidence.",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def _binding(self):
        original = fixture_execution_binding(executor_adapter="codex_cli")
        task = original.task.model_copy(
            update={
                "repository": str(self.repository),
                "base_commit": self.base_commit,
            }
        )
        run_spec = original.invocation.run_spec.model_copy(
            update={
                "executor_type": "codex_cli",
                "model_profile": "fixture-sol-xhigh",
                "task_id": task.task_id,
            }
        )
        invocation = AgentInvocation(
            run_spec=run_spec,
            prompt="Obey the source-controlled fixture task and do not claim approval.",
        )
        return original.model_copy(
            update={
                "task": task,
                "invocation": invocation,
                "testbed_inspection": TestbedInspection(
                    testbed_id=task.project_id,
                    checkout_path=str(self.repository),
                    baseline_commit=self.base_commit,
                    head_commit=self.base_commit,
                    tag="fixture-v1",
                    tag_commit=self.base_commit,
                    origin_url=str(self.repository),
                    working_tree_clean=True,
                    head_matches_baseline=True,
                    tag_matches_baseline=True,
                    origin_registered=True,
                    baseline_published=False,
                ),
                "executor_adapter": "codex_cli",
                "executor_model": "gpt-5.6-sol",
                "executor_reasoning_effort": "xhigh",
                "resolved_at": datetime.now(UTC),
            }
        )

    def _executor(self) -> DurableTaskManagerCodexExecutor:
        return DurableTaskManagerCodexExecutor(
            self.root / "executor-state",
            self.artifacts,
            codex_binary=(sys.executable, str(self.fake_codex), str(self.counter)),
        )

    def _terminal_observation(self, executor, reference):
        deadline = time.monotonic() + 5
        observation = executor.observe(reference, after_cursor=None)
        while (
            observation.status is ManagedExecutionStatus.RUNNING
            and time.monotonic() < deadline
        ):
            time.sleep(0.03)
            observation = executor.observe(
                reference,
                after_cursor=observation.cursor,
            )
        self.assertEqual(observation.status, ManagedExecutionStatus.SUCCEEDED)
        return observation

    def test_restart_locates_same_provider_without_duplicate_codex_start(self) -> None:
        first_executor = self._executor()
        reference = first_executor.start_or_locate(self.request)
        first_observation = self._terminal_observation(first_executor, reference)

        restarted_executor = self._executor()
        located = restarted_executor.start_or_locate(self.request)
        repeated_observation = restarted_executor.observe(
            located,
            after_cursor=first_observation.cursor,
        )
        verification = restarted_executor.collect_verification_evidence(located)

        self.assertEqual(located, reference)
        self.assertEqual(repeated_observation, first_observation)
        self.assertEqual(
            self.counter.read_text(encoding="utf-8").splitlines(),
            ["started"],
        )
        self.assertIn("fixture completed", repeated_observation.summary)
        self.assertEqual(verification.provider_run_id, reference.provider_run_id)
        self.assertEqual(verification.workspace.changed_paths, ())
        self.assertTrue(verification.workspace.working_tree_clean)
        self.assertIsNone(verification.source_patch_sha256)
        for artifact_id in (
            *reference.evidence_artifact_ids,
            *repeated_observation.evidence_artifact_ids,
            *verification.evidence_artifact_ids,
        ):
            self.assertTrue(self.artifacts.verify(self.artifacts.get(artifact_id)))
        workspaces = tuple((self.root / "executor-state" / "workspaces").iterdir())
        self.assertEqual(len(workspaces), 1)
        self.assertEqual(self._git("status", "--short"), "")

    def test_same_start_key_rejects_request_drift(self) -> None:
        executor = self._executor()
        reference = executor.start_or_locate(self.request)
        self._terminal_observation(executor, reference)
        changed = self.request.model_copy(update={"prompt": "different node instructions"})

        with self.assertRaises(TaskManagerCodexError):
            executor.start_or_locate(changed)

        self.assertEqual(
            self.counter.read_text(encoding="utf-8").splitlines(),
            ["started"],
        )

    def test_adapter_rejects_non_task_nodes_before_workspace_or_process(self) -> None:
        request = self.request.model_copy(
            update={
                "node": self.request.node.model_copy(
                    update={"kind": TaskPlanNodeKind.VALIDATION}
                )
            }
        )

        with self.assertRaises(TaskManagerCodexError):
            self._executor().start_or_locate(request)

        self.assertFalse(self.counter.exists())
        self.assertEqual(
            tuple((self.root / "executor-state" / "workspaces").iterdir()),
            (),
        )

    def test_checkpoint_commits_exact_patch_only_to_run_branch_and_is_idempotent(self) -> None:
        executor = self._executor()
        reference = executor.start_or_locate(self.request)
        self._terminal_observation(executor, reference)
        workspace = next((self.root / "executor-state" / "workspaces").iterdir())
        (workspace / "src" / "value.txt").write_text(
            "reviewed change\n",
            encoding="utf-8",
        )
        evidence = executor.collect_verification_evidence(reference)
        self.assertEqual(evidence.workspace.changed_paths, ("src/value.txt",))
        self.assertFalse(evidence.workspace.working_tree_clean)
        self.assertIsNotNone(evidence.source_patch_sha256)
        report = VerificationReport(
            report_id="tmverify-checkpoint-fixture",
            task_id=self.request.workflow_task_id,
            source_commit=evidence.source_commit,
            source_patch_sha256=evidence.source_patch_sha256,
            checks=(
                CheckResult(
                    check_id="fixture-check",
                    status=CheckStatus.PASSED,
                    required=True,
                    artifact_ids=evidence.evidence_artifact_ids,
                    summary="fixture source evidence passed",
                    evidence_hash="e" * 64,
                ),
            ),
            required_checks_passed=True,
        )
        review = ReviewReport(
            review_id="tmreview-checkpoint-fixture",
            task_id=self.request.workflow_task_id,
            reviewer_actor_type=ActorType.HUMAN,
            reviewer_id="reviewer@example.invalid",
            patch_sha256=evidence.source_patch_sha256,
            status=ReviewStatus.ACCEPTED,
            summary="reviewed exact fixture patch",
            evidence_ids=(report.report_id, *evidence.evidence_artifact_ids),
        )
        request = ManagedCheckpointRequest(
            integration_key="tmintegrate-checkpoint-fixture",
            run_id=self.request.run_id,
            workflow_task_id=self.request.workflow_task_id,
            node_id=self.request.node.node_id,
            provider_reference=reference,
            execution_binding=self.binding,
            verification_report=report,
            verification_evidence=evidence,
            source_review=review,
            approved_by="approver@example.invalid",
        )
        integrator = LocalTaskManagerGitCheckpointIntegrator(
            self.root / "executor-state",
            self.artifacts,
        )
        main_head_before = self._git("rev-parse", "HEAD").strip()
        first = integrator.integrate_checkpoint(request)
        repeated = integrator.integrate_checkpoint(request)

        self.assertEqual(repeated, first)
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(workspace), "status", "--short"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout,
            "",
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(workspace), "rev-parse", "HEAD^"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            self.base_commit,
        )
        self.assertEqual(first.integration_result.target_commit, first.integration_result.commit)
        self.assertEqual(first.integration_result.changed_paths, ("src/value.txt",))
        self.assertEqual(self._git("rev-parse", "HEAD").strip(), main_head_before)
        self.assertEqual(self._git("status", "--short"), "")
        self.assertTrue(
            self.artifacts.verify(
                self.artifacts.get(first.evidence_artifact_ids[0])
            )
        )

        with self.assertRaises(TaskManagerGitCheckpointError):
            integrator.integrate_checkpoint(
                request.model_copy(update={"approved_by": "another@example.invalid"})
            )


if __name__ == "__main__":
    unittest.main()
