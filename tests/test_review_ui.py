import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from jobslayer.adapters.local_decisions import LocalDecisionStore
from jobslayer.adapters.local_identity import RoleBasedAuthorizer
from jobslayer.domain.models import (
    ActorType,
    DecisionCard,
    HumanDecision,
    TaskState,
)
from jobslayer.supervision.session import ReviewSession, StaleDecisionCardError
from jobslayer.supervision.session import ReviewAuthorizationError
from jobslayer.identity import AuthenticatedPrincipal, AuthenticationMethod
from jobslayer.supervision.web import ReviewServerError, create_review_server
from jobslayer.workflow.journal import JsonlAuditJournal


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CARD = REPOSITORY_ROOT / "examples" / "decision-card.example.json"


class ReviewUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.card = DecisionCard.model_validate_json(
            EXAMPLE_CARD.read_text(encoding="utf-8")
        )
        self.output = self.root / "decision.json"
        self.journal = JsonlAuditJournal(self.root / "audit.jsonl")
        self.journal.append_transition(
            task_id=self.card.task_id,
            from_state=TaskState.DRAFT,
            to_state=TaskState.PLANNED,
            actor_type=ActorType.SYSTEM,
            actor_id="test-controller",
            reason="test plan",
        )
        for from_state, to_state in (
            (TaskState.PLANNED, TaskState.IMPLEMENTING),
            (TaskState.IMPLEMENTING, TaskState.VERIFYING),
            (TaskState.VERIFYING, TaskState.REVIEWING),
            (TaskState.REVIEWING, TaskState.MERGE_REVIEW),
        ):
            self.journal.append_transition(
                task_id=self.card.task_id,
                from_state=from_state,
                to_state=to_state,
                actor_type=ActorType.SYSTEM,
                actor_id="test-controller",
                reason="test workflow progression",
            )
        self.session = ReviewSession(
            card=self.card,
            actor_id="local-reviewer",
            decision_store=LocalDecisionStore(self.output),
            journal=self.journal,
        )
        self.server = create_review_server(self.session, port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.temporary_directory.cleanup()

    def get_json(self, path: str) -> dict:
        with self.opener.open(f"{self.base_url}{path}", timeout=2) as response:
            return json.loads(response.read())

    def post_decision(self, payload: dict, *, token: str | None = None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-JobSlayer-Session"] = token
        request = Request(
            f"{self.base_url}/api/decisions",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        return self.opener.open(request, timeout=2)

    def test_session_api_exposes_only_real_capabilities_and_audit_state(self) -> None:
        snapshot = self.get_json("/api/session")

        self.assertEqual(snapshot["card"]["card_id"], self.card.card_id)
        self.assertEqual(snapshot["workflow"]["current_state"], "merge_review")
        self.assertTrue(snapshot["workflow"]["card_state_matches"])
        self.assertEqual(len(snapshot["workflow"]["transitions"]), 5)
        self.assertFalse(snapshot["actor"]["authenticated"])
        self.assertTrue(snapshot["capabilities"]["decision_recording"])
        self.assertFalse(snapshot["capabilities"]["decision_application"])
        self.assertFalse(snapshot["capabilities"]["git_merge"])
        self.assertTrue(snapshot["submission_token"])

    def test_visual_assets_are_local_and_hardened(self) -> None:
        with self.opener.open(f"{self.base_url}/", timeout=2) as response:
            page = response.read().decode()
            policy = response.headers["Content-Security-Policy"]
        with self.opener.open(
            f"{self.base_url}/assets/app.js", timeout=2
        ) as response:
            script = response.read().decode()

        self.assertIn("工程决策审查", page)
        self.assertIn('src="/assets/app.js"', page)
        self.assertNotIn("https://", page)
        self.assertIn("default-src 'self'", policy)
        self.assertIn('fetch("/api/decisions"', script)
        self.assertIn("尚未应用到工作流", script)
        self.assertIn("批准决定应用后只进入 Integrating", script)
        self.assertIn("不会自动应用、集成、push 或部署", page)

    def test_post_requires_session_token_and_records_without_applying(self) -> None:
        payload = {
            "selected_option_id": "request_changes",
            "rationale": "需要补充边界条件证据。",
        }
        with self.assertRaises(HTTPError) as missing_token:
            self.post_decision(payload)
        self.assertEqual(missing_token.exception.code, 403)

        token = self.get_json("/api/session")["submission_token"]
        with self.post_decision(payload, token=token) as response:
            result = json.loads(response.read())

        self.assertEqual(response.status, 201)
        self.assertEqual(result["status"], "recorded_not_applied")
        decision = HumanDecision.model_validate_json(
            self.output.read_text(encoding="utf-8")
        )
        self.assertEqual(decision.selected_option_id, "request_changes")
        self.assertEqual(
            self.journal.records_for(self.card.task_id)[-1].to_state,
            TaskState.MERGE_REVIEW,
        )

        with self.assertRaises(HTTPError) as duplicate:
            self.post_decision(payload, token=token)
        self.assertEqual(duplicate.exception.code, 409)

    def test_rejects_non_loopback_binding(self) -> None:
        with self.assertRaises(ReviewServerError):
            create_review_server(self.session, host="0.0.0.0", port=0)

    def test_stale_card_cannot_record_a_decision(self) -> None:
        stale_journal = JsonlAuditJournal(self.root / "stale-audit.jsonl")
        stale_journal.append_transition(
            task_id=self.card.task_id,
            from_state=TaskState.DRAFT,
            to_state=TaskState.PLANNED,
            actor_type=ActorType.SYSTEM,
            actor_id="test-controller",
            reason="card is not current yet",
        )
        stale_output = self.root / "stale-decision.json"
        stale_session = ReviewSession(
            card=self.card,
            actor_id="local-reviewer",
            decision_store=LocalDecisionStore(stale_output),
            journal=stale_journal,
        )

        self.assertFalse(stale_session.snapshot()["capabilities"]["decision_recording"])
        with self.assertRaises(StaleDecisionCardError):
            stale_session.submit(
                selected_option_id="approve",
                rationale="This should be rejected as stale.",
            )
        self.assertFalse(stale_output.exists())

    def test_authenticated_approver_is_bound_to_the_recorded_decision(self) -> None:
        now = datetime.now(UTC)
        principal = AuthenticatedPrincipal(
            session_id="session-review-1",
            subject_id="authenticated-operator",
            display_name="Authenticated Operator",
            roles=("approver",),
            authentication_method=AuthenticationMethod.LOCAL_SIGNED_SESSION,
            issuer="test-issuer",
            authenticated_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=5),
        )
        output = self.root / "authenticated-decision.json"
        session = ReviewSession(
            card=self.card,
            principal=principal,
            authorizer=RoleBasedAuthorizer(),
            decision_store=LocalDecisionStore(output),
            journal=self.journal,
        )

        snapshot = session.snapshot()
        decision = session.submit(
            selected_option_id="request_changes",
            rationale="Authenticated operator requests more evidence.",
        )

        self.assertTrue(snapshot["actor"]["authenticated"])
        self.assertEqual(snapshot["actor"]["session_id"], principal.session_id)
        self.assertTrue(snapshot["capabilities"]["decision_recording"])
        self.assertEqual(decision.actor_id, principal.subject_id)

    def test_authenticated_reviewer_role_cannot_record_a_decision(self) -> None:
        now = datetime.now(UTC)
        principal = AuthenticatedPrincipal(
            session_id="session-reviewer-1",
            subject_id="implementation-reviewer",
            display_name="Implementation Reviewer",
            roles=("reviewer",),
            authentication_method=AuthenticationMethod.LOCAL_SIGNED_SESSION,
            issuer="test-issuer",
            authenticated_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=5),
        )
        output = self.root / "denied-decision.json"
        session = ReviewSession(
            card=self.card,
            principal=principal,
            authorizer=RoleBasedAuthorizer(),
            decision_store=LocalDecisionStore(output),
            journal=self.journal,
        )

        self.assertFalse(session.snapshot()["capabilities"]["decision_recording"])
        with self.assertRaises(ReviewAuthorizationError):
            session.submit(
                selected_option_id="approve",
                rationale="Reviewer must not approve.",
            )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
