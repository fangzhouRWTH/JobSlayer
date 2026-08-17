from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from jobslayer.adapters.codex_planning_agent import (
    CodexPlanningAgent,
    CodexPlanningAgentConfigurationError,
    CodexPlanningAgentInvocationError,
    CodexPlanningAgentProtocolError,
)
from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.local_orchestration import LocalTaskPlanStore
from jobslayer.adapters.local_planning_agent import LocalPlanningAgent
from jobslayer.application.task_orchestration import TaskOrchestrationService
from jobslayer.cli import _planning_agent_for, build_parser
from jobslayer.orchestration import (
    DiscussionRole,
    TaskPlanMessage,
    TaskPlanNode,
)


FAKE_CODEX_PLANNER = r'''import json
import os
from pathlib import Path
import sys
import time

arguments = sys.argv[1:]
prompt = sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": "planning-fixture"}), flush=True)
print(json.dumps({"type": "turn.started"}), flush=True)
sys.stderr.write(json.dumps({
    "arguments": arguments,
    "api_key_seen": "OPENAI_API_KEY" in os.environ,
    "prompt_contains_context": "PLANNING_CONTEXT_JSON" in prompt,
}) + "\n")
sys.stderr.flush()
if "LARGE_OUTPUT_FIXTURE" in prompt:
    sys.stderr.write("x" * 2048)
    sys.stderr.flush()
if "SLOW_PLANNING_FIXTURE" in prompt:
    time.sleep(10)
if "EXIT_FAILURE_FIXTURE" in prompt:
    raise SystemExit(3)

output_path = Path(arguments[arguments.index("--output-last-message") + 1])
if "INVALID_OUTPUT_FIXTURE" in prompt:
    output_path.write_text("{}", encoding="utf-8")
else:
    output_path.write_text(json.dumps({
        "summary": "建议保留范围节点并增加确定性验证节点。",
        "nodes": [
            {
                "node_id": "scope",
                "title": "确认范围",
                "description": "确认目标、边界和约束。",
                "kind": "milestone",
                "executor_hint": "human + planner",
                "acceptance_criteria": ["范围已经人工确认"],
                "deliverables": ["范围说明"],
                "constraints": [],
                "risks": [],
                "verification_requirements": [],
                "requires_human_decision": False
            },
            {
                "node_id": "verify",
                "title": "执行确定性验证",
                "description": "运行项目统一检查。",
                "kind": "validation",
                "executor_hint": "verification engine",
                "acceptance_criteria": ["统一检查通过"],
                "deliverables": [],
                "constraints": [],
                "risks": [],
                "verification_requirements": ["运行统一检查入口并保存结果"],
                "requires_human_decision": False
            }
        ],
        "edges": [
            {
                "edge_id": "scope-verify",
                "source_node_id": "scope",
                "target_node_id": "verify",
                "relation": "sequence",
                "label": None
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")

if "BAD_JSONL_FIXTURE" in prompt:
    print("not-json", flush=True)
else:
    print(json.dumps({
        "type": "item.completed",
        "item": {"id": "message", "type": "agent_message", "text": "structured"}
    }), flush=True)
    print(json.dumps({
        "type": "turn.completed",
        "usage": {"input_tokens": 20, "output_tokens": 10}
    }), flush=True)
'''


class CodexPlanningAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.fake_codex = self.root / "fake_codex_planner.py"
        self.fake_codex.write_text(FAKE_CODEX_PLANNER, encoding="utf-8")
        self.registry = LocalArtifactRegistry(self.root / "artifacts")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def agent(self, **overrides) -> CodexPlanningAgent:
        options = {
            "external_call_authorized": True,
            "codex_binary": (sys.executable, str(self.fake_codex)),
            "model": "fixture-model",
            "timeout_seconds": 3,
        }
        options.update(overrides)
        return CodexPlanningAgent(
            self.workspace,
            self.registry,
            **options,
        )

    @staticmethod
    def message(content: str) -> TaskPlanMessage:
        return TaskPlanMessage(
            message_id="message-fixture",
            role=DiscussionRole.USER,
            content=content,
        )

    def propose(self, agent: CodexPlanningAgent, message: str = "细化计划"):
        existing = TaskPlanNode(
            node_id="scope",
            title="旧范围",
            attributes={"owner": "jobslayer"},
        )
        return agent.propose(
            plan_id="plan-codex",
            task_description="设计受治理的交互式计划",
            based_on_revision=2,
            nodes=(existing,),
            edges=(),
            conversation=(self.message(message),),
            user_message=message,
            selected_node_id="scope",
        )

    def test_returns_validated_draft_and_registers_complete_raw_evidence(self) -> None:
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "must-not-reach-planning-adapter"
        try:
            draft = self.propose(self.agent())
        finally:
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key

        self.assertEqual(draft.summary, "建议保留范围节点并增加确定性验证节点。")
        self.assertEqual(len(draft.nodes), 2)
        self.assertEqual(draft.nodes[0].attributes["owner"], "jobslayer")
        self.assertEqual(
            draft.nodes[0].attributes["proposal_source"],
            CodexPlanningAgent.adapter_id,
        )
        self.assertIsNotNone(draft.agent_invocation_id)
        self.assertEqual(len(draft.evidence_artifact_ids), 4)

        manifests = self.registry.list_manifests(
            task_id="plan-codex",
            run_id=draft.agent_invocation_id,
        )
        self.assertEqual(
            {manifest.artifact_type for manifest in manifests},
            {
                "task_plan.agent.prompt",
                "task_plan.agent.raw_events",
                "task_plan.agent.stderr",
                "task_plan.agent.final_output",
            },
        )
        self.assertEqual(
            set(draft.evidence_artifact_ids),
            {manifest.artifact_id for manifest in manifests},
        )
        stderr_manifest = next(
            manifest
            for manifest in manifests
            if manifest.artifact_type == "task_plan.agent.stderr"
        )
        stderr = json.loads(self.registry.read(stderr_manifest))
        self.assertFalse(stderr["api_key_seen"])
        self.assertTrue(stderr["prompt_contains_context"])
        self.assertIn("read-only", stderr["arguments"])
        self.assertIn("--output-schema", stderr["arguments"])
        self.assertIn("fixture-model", stderr["arguments"])

    def test_application_wraps_codex_content_in_a_jobslayer_owned_proposal(self) -> None:
        service = TaskOrchestrationService(
            LocalTaskPlanStore(self.root / "plans"),
            self.agent(),
            actor_id="planner@example.invalid",
        )

        created = service.create(
            "设计受治理的交互式计划",
            plan_id="plan-codex-service",
        )

        proposal = created.snapshot.pending_proposal
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertTrue(proposal.proposal_id.startswith("proposal-"))
        self.assertEqual(proposal.based_on_revision, 0)
        self.assertEqual(proposal.agent_adapter, CodexPlanningAgent.adapter_id)
        self.assertEqual(len(proposal.evidence_artifact_ids), 4)
        self.assertEqual(created.snapshot.nodes, ())
        self.assertEqual(created.operation, "plan.created_with_agent_proposal")

    def test_refuses_an_external_call_without_explicit_authorization(self) -> None:
        agent = self.agent(external_call_authorized=False)

        with self.assertRaisesRegex(
            CodexPlanningAgentConfigurationError,
            "explicit operator authorization",
        ):
            self.propose(agent)

        self.assertEqual(self.registry.list_manifests(), ())

    def test_invalid_structured_output_is_rejected_after_evidence_is_saved(self) -> None:
        with self.assertRaises(CodexPlanningAgentProtocolError):
            self.propose(self.agent(), "INVALID_OUTPUT_FIXTURE")

        manifests = self.registry.list_manifests(task_id="plan-codex")
        self.assertEqual(len(manifests), 4)
        self.assertTrue(
            all(
                manifest.metadata["result"] == "completed"
                for manifest in manifests
                if manifest.artifact_type != "task_plan.agent.prompt"
            )
        )

    def test_invalid_jsonl_is_rejected_after_evidence_is_saved(self) -> None:
        with self.assertRaises(CodexPlanningAgentProtocolError):
            self.propose(self.agent(), "BAD_JSONL_FIXTURE")

        raw = next(
            manifest
            for manifest in self.registry.list_manifests(task_id="plan-codex")
            if manifest.artifact_type == "task_plan.agent.raw_events"
        )
        self.assertIn(b"not-json", self.registry.read(raw))

    def test_nonzero_exit_is_not_retried_or_converted_into_a_proposal(self) -> None:
        with self.assertRaisesRegex(CodexPlanningAgentInvocationError, "exit code 3"):
            self.propose(self.agent(), "EXIT_FAILURE_FIXTURE")

        manifests = self.registry.list_manifests(task_id="plan-codex")
        self.assertEqual(len(manifests), 4)
        result_metadata = {
            manifest.metadata.get("result")
            for manifest in manifests
            if manifest.artifact_type != "task_plan.agent.prompt"
        }
        self.assertEqual(result_metadata, {"failed"})

    def test_timeout_terminates_the_call_and_preserves_timed_out_evidence(self) -> None:
        with self.assertRaisesRegex(
            CodexPlanningAgentInvocationError,
            "exceeded 1 seconds",
        ):
            self.propose(
                self.agent(timeout_seconds=1),
                "SLOW_PLANNING_FIXTURE",
            )

        manifests = self.registry.list_manifests(task_id="plan-codex")
        self.assertEqual(len(manifests), 4)
        self.assertEqual(
            {
                manifest.metadata.get("result")
                for manifest in manifests
                if manifest.artifact_type != "task_plan.agent.prompt"
            },
            {"timed_out"},
        )

    def test_output_limit_rejects_the_draft_after_preserving_file_backed_logs(self) -> None:
        with self.assertRaisesRegex(
            CodexPlanningAgentProtocolError,
            "evidence limit",
        ):
            self.propose(
                self.agent(max_output_bytes=1_024),
                "LARGE_OUTPUT_FIXTURE",
            )

        stderr = next(
            manifest
            for manifest in self.registry.list_manifests(task_id="plan-codex")
            if manifest.artifact_type == "task_plan.agent.stderr"
        )
        self.assertGreater(stderr.size_bytes, 2_048)

    def test_cli_factory_keeps_local_default_and_gates_codex_configuration(self) -> None:
        local = _planning_agent_for(
            "local",
            repository_root=self.workspace,
            artifact_root=self.root / "unused-artifacts",
            allow_external=False,
            codex_binary="codex",
            codex_model=None,
            codex_timeout_seconds=120,
        )
        self.assertIsInstance(local, LocalPlanningAgent)

        with self.assertRaisesRegex(ValueError, "allow-external-planning-agent"):
            _planning_agent_for(
                "codex",
                repository_root=self.workspace,
                artifact_root=self.root / "gated-artifacts",
                allow_external=False,
                codex_binary="codex",
                codex_model="fixture-model",
                codex_timeout_seconds=120,
            )
        with self.assertRaisesRegex(ValueError, "explicit --codex-model"):
            _planning_agent_for(
                "codex",
                repository_root=self.workspace,
                artifact_root=self.root / "gated-artifacts",
                allow_external=True,
                codex_binary="codex",
                codex_model=None,
                codex_timeout_seconds=120,
            )

        arguments = build_parser().parse_args(
            [
                "orchestration-api",
                "--identity-session",
                "session.json",
                "--identity-key",
                "key.json",
                "--planning-agent",
                "codex",
                "--allow-external-planning-agent",
                "--codex-model",
                "fixture-model",
            ]
        )
        self.assertEqual(arguments.planning_agent, "codex")
        self.assertTrue(arguments.allow_external_planning_agent)
        self.assertEqual(arguments.codex_model, "fixture-model")


if __name__ == "__main__":
    unittest.main()
