from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from jobslayer.adapters.codex_quick_agent import CodexQuickAgent
from jobslayer.quick_agent import QuickAgentBusyError, QuickAgentMode, QuickAgentState


_FAKE_APP_SERVER = r'''
import json
from pathlib import Path
import sys

request_log = Path(__REQUEST_LOG__)
active_turn = None

def emit(payload):
    print(json.dumps(payload, separators=(",", ":")), flush=True)

for line in sys.stdin:
    message = json.loads(line)
    with request_log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(message, separators=(",", ":")) + "\n")
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        continue
    if method == "initialize":
        emit({"id": request_id, "result": {"userAgent": "fake-codex"}})
    elif method == "account/rateLimits/read":
        rate_limit = {
            "limitId": "codex",
            "limitName": "Codex",
            "planType": "pro",
            "primary": {"usedPercent": 37, "windowDurationMins": 300, "resetsAt": 1800000000},
            "secondary": {"usedPercent": 12, "windowDurationMins": 10080, "resetsAt": 1800500000}
        }
        emit({"id": request_id, "result": {
            "rateLimits": rate_limit,
            "rateLimitsByLimitId": {"codex": rate_limit},
            "rateLimitResetCredits": {"availableCount": 2}
        }})
    elif method == "model/list":
        emit({"id": request_id, "result": {"data": [
            {
                "id": "gpt-5.6-sol",
                "model": "gpt-5.6-sol",
                "displayName": "GPT-5.6-Sol",
                "description": "Reliable agentic workhorse.",
                "hidden": False,
                "defaultReasoningEffort": "low",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low", "description": "Faster"},
                    {"reasoningEffort": "xhigh", "description": "Deeper"},
                    {"reasoningEffort": "ultra", "description": "Delegated"}
                ],
                "inputModalities": ["text", "image"],
                "supportsPersonality": False,
                "multiAgentVersion": "v2",
                "serviceTiers": [
                    {"id": "priority", "name": "Fast", "description": "1.5x speed"}
                ],
                "defaultServiceTier": None,
                "isDefault": True,
                "upgrade": None,
                "upgradeInfo": None
            }
        ], "nextCursor": None}})
    elif method == "thread/start":
        sandbox = message["params"].get("sandbox")
        if sandbox not in {"read-only", "workspace-write"}:
            emit({"id": request_id, "error": {"message": "invalid sandbox"}})
            continue
        emit({"id": request_id, "result": {"thread": {"id": "thread-fake"}}})
    elif method == "thread/resume":
        emit({"id": request_id, "result": {"thread": {"id": message["params"]["threadId"]}}})
    elif method == "turn/start":
        active_turn = "turn-fake"
        emit({"id": request_id, "result": {"turn": {"id": active_turn, "status": "inProgress"}}})
        emit({"method": "turn/started", "params": {"threadId": "thread-fake", "turn": {"id": active_turn, "status": "inProgress"}}})
        content = message["params"]["input"][0]["text"]
        if content != "hold":
            item = {"id": "agent-1", "type": "agentMessage", "text": "", "status": "inProgress"}
            emit({"method": "item/started", "params": {"threadId": "thread-fake", "turnId": active_turn, "item": item}})
            emit({"method": "item/agentMessage/delta", "params": {"threadId": "thread-fake", "turnId": active_turn, "itemId": "agent-1", "delta": "收到，"}})
            emit({"method": "item/agentMessage/delta", "params": {"threadId": "thread-fake", "turnId": active_turn, "itemId": "agent-1", "delta": "已处理。"}})
            item.update({"text": "收到，已处理。", "status": "completed"})
            emit({"method": "item/completed", "params": {"threadId": "thread-fake", "turnId": active_turn, "item": item}})
            emit({"method": "thread/tokenUsage/updated", "params": {
                "threadId": "thread-fake", "turnId": active_turn,
                "tokenUsage": {"total": {"inputTokens": 40, "cachedInputTokens": 4, "outputTokens": 8, "reasoningOutputTokens": 2, "totalTokens": 48}, "last": {}}
            }})
            emit({"method": "turn/completed", "params": {"threadId": "thread-fake", "turn": {"id": active_turn, "status": "completed"}}})
            active_turn = None
    elif method == "turn/interrupt":
        emit({"id": request_id, "result": {}})
        emit({"method": "turn/completed", "params": {"threadId": "thread-fake", "turn": {"id": active_turn or "turn-fake", "status": "interrupted"}}})
        active_turn = None
    else:
        emit({"id": request_id, "error": {"message": "unsupported fake method " + str(method)}})
'''


class CodexQuickAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.request_log = self.root / "requests.jsonl"
        self.script = self.root / "fake_codex.py"
        self.script.write_text(
            _FAKE_APP_SERVER.replace("__REQUEST_LOG__", repr(str(self.request_log))),
            encoding="utf-8",
        )
        self.agent = CodexQuickAgent(
            self.root,
            self.root / "state",
            codex_binary=(sys.executable, str(self.script)),
            maximum_turn_seconds=30,
        )

    def tearDown(self) -> None:
        self.agent.close()
        self.temporary_directory.cleanup()

    def _await_state(self, expected: QuickAgentState) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.agent.snapshot().state is expected:
                return
            time.sleep(0.01)
        self.fail(f"Quick Agent did not reach {expected.value}")

    def _requests(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.request_log.read_text(encoding="utf-8").splitlines()
        ]

    def test_capacity_is_provider_reported_and_cached(self) -> None:
        first = self.agent.capacity()
        second = self.agent.capacity()

        self.assertTrue(first.available)
        self.assertEqual(first.buckets[0].limit_id, "codex")
        self.assertEqual(first.buckets[0].primary.remaining_percent, 63)
        self.assertEqual(first.buckets[0].secondary.remaining_percent, 88)
        self.assertEqual(first.reset_credit_count, 2)
        self.assertEqual(first, second)
        capacity_requests = [
            item for item in self._requests()
            if item.get("method") == "account/rateLimits/read"
        ]
        self.assertEqual(len(capacity_requests), 1)

    def test_model_catalog_is_provider_reported_and_cached(self) -> None:
        first = self.agent.models()
        second = self.agent.models()

        self.assertTrue(first.available)
        self.assertEqual(first.models[0].model_id, "gpt-5.6-sol")
        self.assertEqual(first.models[0].multi_agent_version, "v2")
        self.assertEqual(
            [item.effort for item in first.models[0].reasoning_efforts],
            ["low", "xhigh", "ultra"],
        )
        self.assertEqual(first.models[0].service_tiers[0].tier_id, "priority")
        self.assertEqual(first, second)
        requests = [
            item for item in self._requests() if item.get("method") == "model/list"
        ]
        self.assertEqual(len(requests), 1)

    def test_discussion_streams_events_and_uses_fail_closed_policy(self) -> None:
        started = self.agent.start_turn("inspect the repository", mode=QuickAgentMode.DISCUSS)
        self.assertIn(started.state, {QuickAgentState.RUNNING, QuickAgentState.COMPLETED})
        self._await_state(QuickAgentState.COMPLETED)

        snapshot = self.agent.snapshot()
        self.assertEqual(snapshot.thread_id, "thread-fake")
        self.assertEqual(snapshot.usage["total_tokens"], 48)
        self.assertIn("收到，已处理。", [item.content for item in snapshot.events])
        thread = next(item for item in self._requests() if item.get("method") == "thread/start")
        self.assertEqual(thread["params"]["sandbox"], "read-only")
        turn = next(item for item in self._requests() if item.get("method") == "turn/start")
        self.assertEqual(turn["params"]["approvalPolicy"], "never")
        self.assertNotIn("runtimeWorkspaceRoots", turn["params"])
        self.assertEqual(turn["params"]["sandboxPolicy"]["type"], "readOnly")
        self.assertFalse(turn["params"]["sandboxPolicy"]["networkAccess"])
        self.assertNotIn("dangerFullAccess", json.dumps(self._requests()))
        self.assertFalse(any(item.get("method", "").startswith("process/") for item in self._requests()))

    def test_execution_is_explicitly_workspace_scoped_and_offline(self) -> None:
        self.agent.start_turn("make a small edit", mode=QuickAgentMode.EXECUTE)
        self._await_state(QuickAgentState.COMPLETED)

        turn = next(item for item in self._requests() if item.get("method") == "turn/start")
        policy = turn["params"]["sandboxPolicy"]
        self.assertEqual(policy["type"], "workspaceWrite")
        self.assertEqual(policy["writableRoots"], [str(self.root.resolve())])
        self.assertFalse(policy["networkAccess"])

    def test_turn_accepts_only_provider_advertised_effort_and_speed(self) -> None:
        self.agent.start_turn(
            "inspect",
            mode=QuickAgentMode.DISCUSS,
            model="gpt-5.6-sol",
            reasoning_effort="ultra",
            service_tier="priority",
        )
        self._await_state(QuickAgentState.COMPLETED)

        snapshot = self.agent.snapshot()
        self.assertEqual(snapshot.reasoning_effort, "ultra")
        self.assertEqual(snapshot.service_tier, "priority")
        thread = next(item for item in self._requests() if item.get("method") == "thread/start")
        turn = next(item for item in self._requests() if item.get("method") == "turn/start")
        self.assertEqual(thread["params"]["serviceTier"], "priority")
        self.assertEqual(turn["params"]["effort"], "ultra")
        self.assertEqual(turn["params"]["serviceTier"], "priority")

        self.agent.new_session()
        with self.assertRaisesRegex(ValueError, "does not advertise"):
            self.agent.start_turn(
                "inspect",
                mode=QuickAgentMode.DISCUSS,
                reasoning_effort="unsupported",
            )

    def test_one_active_turn_at_a_time_and_interrupt_are_enforced(self) -> None:
        self.agent.start_turn("hold", mode=QuickAgentMode.DISCUSS)
        with self.assertRaisesRegex(QuickAgentBusyError, "active turn"):
            self.agent.start_turn("second", mode=QuickAgentMode.DISCUSS)

        self.agent.cancel()
        self._await_state(QuickAgentState.CANCELLED)
        self.assertTrue(any(item.get("method") == "turn/interrupt" for item in self._requests()))

    def test_input_bounds_are_checked_before_codex_starts(self) -> None:
        with self.assertRaisesRegex(ValueError, "1-16000"):
            self.agent.start_turn("", mode=QuickAgentMode.DISCUSS)
        with self.assertRaisesRegex(ValueError, "1-16000"):
            self.agent.start_turn("x" * 16_001, mode=QuickAgentMode.DISCUSS)
        self.assertFalse(self.request_log.exists())


if __name__ == "__main__":
    unittest.main()
