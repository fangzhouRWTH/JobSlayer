from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from jobslayer.adapters.local_orchestration import LocalTaskPlanStore
from jobslayer.adapters.local_planning_agent import LocalPlanningAgent
from jobslayer.application.task_orchestration import TaskOrchestrationService
from jobslayer.identity import AuthenticatedPrincipal, AuthenticationMethod
from jobslayer.orchestration.web import (
    TaskOrchestrationServerError,
    create_task_orchestration_server,
)


class TaskOrchestrationWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        now = datetime.now(UTC)
        self.principal = AuthenticatedPrincipal(
            session_id="session-planner",
            subject_id="planner@example.invalid",
            display_name="Task Planner",
            roles=("planner",),
            authentication_method=AuthenticationMethod.LOCAL_SIGNED_SESSION,
            issuer="test-issuer",
            authenticated_at=now,
            valid_until=now + timedelta(minutes=5),
        )
        service = TaskOrchestrationService(
            LocalTaskPlanStore(root),
            LocalPlanningAgent(),
            actor_id=self.principal.subject_id,
        )
        self.server = create_task_orchestration_server(
            service, self.principal, port=0
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}/api/orchestration"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def get_json(self, path: str) -> dict:
        with self.opener.open(self.base + path, timeout=2) as response:
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            return json.loads(response.read())

    def mutate(
        self,
        method: str,
        path: str,
        payload: dict,
        *,
        token: str | None,
    ) -> dict:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-JobSlayer-Session"] = token
        request = Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method=method,
        )
        with self.opener.open(request, timeout=2) as response:
            return json.loads(response.read())

    def test_authenticated_api_supports_discussion_crud_split_and_finalization(self) -> None:
        session = self.get_json("/session")
        self.assertEqual(session["principal"]["subject_id"], self.principal.subject_id)
        self.assertFalse(session["capabilities"]["workflow_execution"])
        self.assertEqual(session["agent_adapter"], "local-planning-fixture-v1")
        token = session["submission_token"]

        with self.assertRaises(HTTPError) as unauthorized:
            self.mutate(
                "POST",
                "/plans",
                {"task_description": "unauthorized"},
                token=None,
            )
        self.assertEqual(unauthorized.exception.code, 403)

        created = self.mutate(
            "POST",
            "/plans",
            {"task_description": "实现可调整的任务编排", "plan_id": "plan-web"},
            token=token,
        )
        proposal = created["snapshot"]["pending_proposal"]
        applied = self.mutate(
            "POST",
            "/plans/plan-web/proposals/apply",
            {"proposal_id": proposal["proposal_id"], "expected_revision": 1},
            token=token,
        )
        created_node = self.mutate(
            "POST",
            "/plans/plan-web/nodes",
            {
                "title": "记录回滚策略",
                "description": "新增一个明确任务。",
                "source_node_id": "design",
                "relation": "subtask",
                "expected_revision": applied["sequence"],
            },
            token=token,
        )
        node = created_node["snapshot"]["nodes"][-1]
        updated = self.mutate(
            "PATCH",
            f"/plans/plan-web/nodes/{node['node_id']}",
            {
                "title": "记录版本与回滚策略",
                "description": "保留每次编排调整。",
                "kind": "validation",
                "executor_hint": None,
                "expected_revision": created_node["sequence"],
            },
            token=token,
        )
        split = self.mutate(
            "POST",
            "/plans/plan-web/nodes/design/split",
            {
                "title": "并行 UI 评审",
                "description": "保持主线不阻塞。",
                "relation": "branch",
                "expected_revision": updated["sequence"],
            },
            token=token,
        )
        branch_id = split["snapshot"]["nodes"][-1]["node_id"]
        deleted = self.mutate(
            "DELETE",
            f"/plans/plan-web/nodes/{branch_id}",
            {"expected_revision": split["sequence"]},
            token=token,
        )
        finalized = self.mutate(
            "POST",
            "/plans/plan-web/finalize",
            {"expected_revision": deleted["sequence"]},
            token=token,
        )

        self.assertEqual(finalized["snapshot"]["status"], "finalized")
        self.assertEqual(
            finalized["snapshot"]["latest_finalized_revision"],
            finalized["sequence"],
        )
        history = self.get_json("/plans/plan-web/history")["history"]
        self.assertEqual(len(history), finalized["sequence"])
        self.assertEqual(history[-1]["record_hash"], finalized["record_hash"])

        with self.assertRaises(HTTPError) as stale:
            self.mutate(
                "POST",
                "/plans/plan-web/messages",
                {"content": "增加支线", "expected_revision": 1},
                token=token,
            )
        self.assertEqual(stale.exception.code, 409)

    def test_rejects_non_loopback_binding(self) -> None:
        with self.assertRaises(TaskOrchestrationServerError):
            create_task_orchestration_server(
                self.server.orchestration_service,
                self.principal,
                host="0.0.0.0",
            )

    def test_expired_planner_session_rejects_mutation(self) -> None:
        now = datetime.now(UTC)
        self.server.principal = self.principal.model_copy(
            update={
                "authenticated_at": now - timedelta(minutes=10),
                "valid_until": now - timedelta(minutes=5),
            }
        )

        with self.assertRaises(HTTPError) as expired:
            self.mutate(
                "POST",
                "/plans",
                {"task_description": "must be rejected"},
                token=self.server.session_token,
            )

        self.assertEqual(expired.exception.code, 403)
        self.assertEqual(
            self.server.orchestration_service.list_latest(), ()
        )


if __name__ == "__main__":
    unittest.main()
