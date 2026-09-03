from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from jobslayer.adapters.codex_human_action_assistant import (
    CodexHumanActionAssistant,
    CodexHumanActionAssistantError,
)
from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.domain.models import ActorType
from jobslayer.task_manager import (
    TaskManagerHumanActionGuidance,
    TaskManagerHumanActionKind,
    TaskManagerHumanDecisionOption,
)


FAKE_CODEX_ASSISTANT = r'''import json
import os
from pathlib import Path
import sys

arguments = sys.argv[1:]
prompt = sys.stdin.read()
output_path = Path(arguments[arguments.index("--output-last-message") + 1])
sys.stderr.write(json.dumps({
    "arguments": arguments,
    "api_key_seen": "OPENAI_API_KEY" in os.environ,
    "prompt_has_contract": "read-only assistant" in prompt,
    "prompt_has_guidance": "completion_approval" in prompt,
}) + "\n")
if "INVALID_JSONL" in prompt:
    print("not-json", flush=True)
else:
    print(json.dumps({"type": "turn.completed"}), flush=True)
output_path.write_text(json.dumps({
    "response": "请先核对验证报告；正式批准只能由授权人类通过结构化按钮提交。"
}, ensure_ascii=False), encoding="utf-8")
'''


class CodexHumanActionAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.fake_codex = self.root / "fake_codex_assistant.py"
        self.fake_codex.write_text(FAKE_CODEX_ASSISTANT, encoding="utf-8")
        self.artifacts = LocalArtifactRegistry(self.root / "artifacts")
        self.assistant = CodexHumanActionAssistant(
            self.workspace,
            self.artifacts,
            codex_binary=(sys.executable, str(self.fake_codex)),
            model="fixture-model",
            reasoning_effort="xhigh",
            timeout_seconds=3,
        )
        self.guidance = TaskManagerHumanActionGuidance(
            guidance_id="human-action-run-fixture-finalize-r14",
            kind=TaskManagerHumanActionKind.COMPLETION_APPROVAL,
            node_id="finalize",
            title="最终人工验收",
            summary="核对验证证据后决定是否完成。",
            permitted_actor_types=(ActorType.HUMAN,),
            required_capability="completion_approval",
            requirements=("验证报告必须通过",),
            steps=("打开并核对验证报告",),
            decisions=(
                TaskManagerHumanDecisionOption(
                    decision_id="approve-completion",
                    label="批准完成",
                    effect="通过正式治理命令完成任务。",
                    command="approve_completion",
                ),
                TaskManagerHumanDecisionOption(
                    decision_id="request-changes",
                    label="要求修改",
                    effect="只记录反馈，节点继续等待。",
                ),
            ),
            evidence_to_review=("artifact-verification-report",),
            prohibited_actions=("Agent 不得代替人类批准",),
            expected_plan_revision=17,
            expected_run_revision=14,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_read_only_turn_returns_schema_output_and_retains_raw_evidence(self) -> None:
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "must-not-reach-assistant"
        try:
            reply = self.assistant.assist(
                task_id="plan-fixture",
                run_id="run-fixture",
                guidance=self.guidance,
                interactions=(),
                user_message="请帮我梳理批准前还需要检查什么。",
            )
        finally:
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key

        self.assertIn("只能由授权人类", reply.content)
        self.assertEqual(len(reply.evidence_artifact_ids), 4)
        manifests = self.artifacts.list_manifests(
            task_id="plan-fixture",
            run_id="run-fixture",
        )
        self.assertEqual(
            {item.artifact_type for item in manifests},
            {
                "task-manager-human-assistant-prompt",
                "task-manager-human-assistant-events",
                "task-manager-human-assistant-stderr",
                "task-manager-human-assistant-output",
            },
        )
        stderr_manifest = next(
            item for item in manifests
            if item.artifact_type == "task-manager-human-assistant-stderr"
        )
        stderr = json.loads(self.artifacts.read(stderr_manifest))
        self.assertFalse(stderr["api_key_seen"])
        self.assertTrue(stderr["prompt_has_contract"])
        self.assertTrue(stderr["prompt_has_guidance"])
        self.assertIn("read-only", stderr["arguments"])
        self.assertIn("--output-schema", stderr["arguments"])
        self.assertIn("fixture-model", stderr["arguments"])
        self.assertIn('model_reasoning_effort="xhigh"', stderr["arguments"])

    def test_invalid_jsonl_is_rejected_after_evidence_is_preserved(self) -> None:
        with self.assertRaisesRegex(CodexHumanActionAssistantError, "JSONL"):
            self.assistant.assist(
                task_id="plan-fixture",
                run_id="run-fixture",
                guidance=self.guidance,
                interactions=(),
                user_message="INVALID_JSONL",
            )
        self.assertEqual(
            len(
                self.artifacts.list_manifests(
                    task_id="plan-fixture",
                    run_id="run-fixture",
                )
            ),
            4,
        )


if __name__ == "__main__":
    unittest.main()
