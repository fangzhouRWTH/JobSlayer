from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from jobslayer.adapters.local_management import LocalManagementQuery
from jobslayer.identity import AuthenticatedPrincipal, AuthenticationMethod
from jobslayer.management import (
    ManagedRunSummary,
    ManagementQueryError,
    ManagementSnapshot,
)
from jobslayer.management.web import ManagementServerError, create_management_server


class _FakeQuery:
    def __init__(self):
        self.run = ManagedRunSummary(
            run_id="run-1",
            task_id="task-1",
            title="Managed fixture",
            state="merge_review",
            stage="implementation_review",
            executor_type="scripted_patch",
            executor_status="completed",
            input_tokens=10,
            cached_input_tokens=2,
            output_tokens=3,
            cost_microusd=0,
            review_status="accepted",
            decision_recorded=False,
            decision_applied=False,
            artifacts_valid=True,
            workflow_valid=True,
            run_record_valid=True,
        )

    def snapshot(self) -> ManagementSnapshot:
        return ManagementSnapshot(
            state_root="/fixture",
            runs=(self.run,),
            invalid_runs=(),
            state_counts={"merge_review": 1},
            executor_counts={"scripted_patch": 1},
            total_input_tokens=10,
            total_cached_input_tokens=2,
            total_output_tokens=3,
            total_cost_microusd=0,
        )

    def run_detail(self, run_id: str):
        if run_id != "run-1":
            raise ManagementQueryError("run detail is unavailable")
        return {
            "schema_version": "1.0",
            "summary": self.run.model_dump(mode="json"),
            "workflow": [],
            "run_records": [],
            "artifacts": [],
        }


class ManagementWebTests(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime.now(UTC)
        self.principal = AuthenticatedPrincipal(
            session_id="session-dashboard",
            subject_id="operator",
            display_name="Operator",
            roles=("observer",),
            authentication_method=AuthenticationMethod.LOCAL_SIGNED_SESSION,
            issuer="test-issuer",
            authenticated_at=now,
            valid_until=now + timedelta(minutes=5),
        )
        self.server = create_management_server(
            _FakeQuery(), self.principal, port=0
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def read_json(self, path: str):
        with self.opener.open(self.base + path, timeout=2) as response:
            return response, json.loads(response.read().decode("utf-8"))

    def test_authenticated_read_model_exposes_dashboard_and_run_detail(self) -> None:
        response, session = self.read_json("/api/session")
        self.assertEqual(response.status, 200)
        self.assertEqual(session["principal"]["subject_id"], "operator")
        self.assertFalse(session["capabilities"]["mutations"])

        _, dashboard = self.read_json("/api/dashboard")
        self.assertEqual(dashboard["runs"][0]["run_id"], "run-1")
        _, detail = self.read_json("/api/runs/run-1")
        self.assertEqual(detail["summary"]["task_id"], "task-1")

    def test_dashboard_is_loopback_read_only_and_hardened(self) -> None:
        with self.opener.open(self.base + "/", timeout=2) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
            self.assertNotIn("<script>", html)
        with self.assertRaises(HTTPError) as caught:
            self.opener.open(
                Request(self.base + "/api/dashboard", method="POST"), timeout=2
            )
        self.assertEqual(caught.exception.code, 405)
        with self.assertRaises(ManagementServerError):
            create_management_server(_FakeQuery(), self.principal, host="0.0.0.0")


class LocalManagementQueryTests(unittest.TestCase):
    def test_snapshot_keeps_invalid_runs_out_of_normal_aggregates(self) -> None:
        with TemporaryDirectory() as directory:
            state_root = Path(directory)
            (state_root / "runs" / "run-valid").mkdir(parents=True)
            (state_root / "runs" / "run-invalid").mkdir()

            class Coordinator:
                def __init__(self, root):
                    self.state_root = root

                def inspect(self, path):
                    if Path(path).name == "run-invalid":
                        raise RuntimeError("broken chain")
                    return {
                        "run_id": "run-valid",
                        "task_id": "task-valid",
                        "title": "Valid run",
                        "state": "reviewing",
                        "stage": "execution",
                        "record_chain_valid": True,
                        "audit_chain_valid": True,
                        "artifacts_valid": True,
                        "executor": {
                            "type": "codex_cli",
                            "status": "completed",
                            "usage": {
                                "input_tokens": 12,
                                "cached_input_tokens": 4,
                                "output_tokens": 5,
                            },
                        },
                        "review": None,
                        "decision": {"recorded": False, "applied": False},
                    }

            snapshot = LocalManagementQuery(Coordinator(state_root)).snapshot()

        self.assertEqual(len(snapshot.runs), 1)
        self.assertEqual(snapshot.total_input_tokens, 12)
        self.assertEqual(snapshot.invalid_runs[0].run_id, "run-invalid")
        self.assertEqual(snapshot.state_counts, {"reviewing": 1})


if __name__ == "__main__":
    unittest.main()
