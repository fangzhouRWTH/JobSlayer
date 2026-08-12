from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout
import io

from jobslayer.adapters.local_identity import (
    LocalIdentityError,
    LocalIdentityProvider,
    RoleBasedAuthorizer,
)
from jobslayer.identity import (
    AuthorizationAction,
    AuthorizationRequest,
    SignedIdentitySession,
)
from jobslayer.cli import main
from jobslayer.domain.models import ApprovalAuthority, DecisionKind, RiskLevel


class LocalIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.key_path = self.root / "identity-key.json"
        self.provider = LocalIdentityProvider(self.key_path)
        self.key_id = self.provider.create_key()
        self.now = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _session(self, *, roles: tuple[str, ...] = ("approver",)):
        return self.provider.issue(
            subject_id="operator@example.invalid",
            display_name="Local Operator",
            roles=roles,
            lifetime=timedelta(minutes=15),
            now=self.now,
        )

    def test_issues_and_verifies_a_short_lived_session_after_restart(self) -> None:
        session = self._session()
        session_path = self.root / "session.json"
        self.provider.create_session_file(session_path, session)

        principal = LocalIdentityProvider(self.key_path).load_session(
            session_path,
            now=self.now + timedelta(minutes=1),
        )

        self.assertEqual(principal.subject_id, "operator@example.invalid")
        self.assertEqual(principal.roles, ("approver",))
        self.assertEqual(principal.issuer, self.key_id)

    def test_tampered_or_expired_session_is_rejected(self) -> None:
        session = self._session()
        raw = session.model_dump(mode="json")
        raw["principal"]["roles"] = ["operator-admin"]
        tampered = SignedIdentitySession.model_validate(raw)

        with self.assertRaisesRegex(LocalIdentityError, "signature"):
            self.provider.verify(tampered, now=self.now)
        with self.assertRaisesRegex(LocalIdentityError, "not currently valid"):
            self.provider.verify(
                session,
                now=self.now + timedelta(minutes=15),
            )

    def test_key_and_session_files_are_create_only(self) -> None:
        with self.assertRaisesRegex(LocalIdentityError, "overwrite"):
            self.provider.create_key()
        session_path = self.root / "session.json"
        session = self._session()
        self.provider.create_session_file(session_path, session)
        with self.assertRaisesRegex(LocalIdentityError, "overwrite"):
            self.provider.create_session_file(session_path, session)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not authoritative")
    def test_rejects_a_group_readable_signing_key(self) -> None:
        self.key_path.chmod(0o640)

        with self.assertRaisesRegex(LocalIdentityError, "permissions"):
            self.provider.issue(
                subject_id="operator",
                display_name="Operator",
                roles=("approver",),
                now=self.now,
            )

    def test_rbac_allows_approver_decision_and_denies_execution(self) -> None:
        principal = self.provider.verify(self._session(), now=self.now)
        authorizer = RoleBasedAuthorizer()

        allowed = authorizer.authorize(
            AuthorizationRequest(
                principal=principal,
                action=AuthorizationAction.RECORD_DECISION,
                task_id="task-1",
                run_id="run-1",
            ),
            now=self.now,
        )
        rejected = authorizer.authorize(
            AuthorizationRequest(
                principal=principal,
                action=AuthorizationAction.EXECUTE_TASK,
                task_id="task-1",
                run_id="run-1",
            ),
            now=self.now,
        )

        self.assertTrue(allowed.permitted)
        self.assertFalse(rejected.permitted)
        self.assertEqual(allowed.evidence_ids, (principal.session_id,))

    def test_unknown_role_is_denied_by_default(self) -> None:
        principal = self.provider.verify(
            self._session(roles=("invented-role",)),
            now=self.now,
        )

        verdict = RoleBasedAuthorizer().authorize(
            AuthorizationRequest(
                principal=principal,
                action=AuthorizationAction.VIEW_CONTROL_PLANE,
            ),
            now=self.now,
        )

        self.assertFalse(verdict.permitted)

    def test_issues_and_verifies_short_lived_approval_authority(self) -> None:
        authority = self.provider.issue_approval_authority(
            self._session(),
            allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
            lifetime=timedelta(minutes=5),
            now=self.now,
        )

        verified = LocalIdentityProvider(
            self.key_path
        ).verify_approval_authority(
            authority,
            now=self.now + timedelta(minutes=1),
        )

        self.assertEqual(verified.actor_id, "operator@example.invalid")
        self.assertEqual(
            verified.allowed_decision_kinds,
            (DecisionKind.MERGE_REVIEW,),
        )
        self.assertIsNotNone(verified.proof)

    def test_rejects_unsigned_tampered_expired_or_unauthorized_authority(self) -> None:
        unsigned = ApprovalAuthority(
            authorization_id="unsigned",
            actor_id="operator@example.invalid",
            allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
            issued_at=self.now,
            valid_until=self.now + timedelta(minutes=5),
        )
        with self.assertRaisesRegex(LocalIdentityError, "no verifiable proof"):
            self.provider.verify_approval_authority(unsigned, now=self.now)

        authority = self.provider.issue_approval_authority(
            self._session(),
            allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
            lifetime=timedelta(minutes=5),
            now=self.now,
        )
        tampered = authority.model_copy(update={"actor_id": "attacker"})
        with self.assertRaisesRegex(LocalIdentityError, "signature"):
            self.provider.verify_approval_authority(tampered, now=self.now)
        with self.assertRaisesRegex(LocalIdentityError, "not currently valid"):
            self.provider.verify_approval_authority(
                authority,
                now=self.now + timedelta(minutes=5),
            )
        with self.assertRaisesRegex(LocalIdentityError, "authorization denied"):
            self.provider.issue_approval_authority(
                self._session(roles=("reviewer",)),
                allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
                now=self.now,
            )

    def test_execution_authority_is_signed_and_bound_to_session_task_and_run(self) -> None:
        session = self._session(roles=("executor",))
        authority = self.provider.issue_execution_authorization(
            session,
            task_id="task-1",
            run_id="run-1",
            maximum_risk=RiskLevel.LOW,
            lifetime=timedelta(minutes=5),
            now=self.now,
        )

        verified = LocalIdentityProvider(
            self.key_path
        ).verify_execution_authorization(
            authority,
            task_id="task-1",
            run_id="run-1",
            now=self.now + timedelta(minutes=1),
        )

        self.assertEqual(verified.actor_id, "operator@example.invalid")
        self.assertEqual(verified.run_id, "run-1")
        self.assertEqual(
            verified.proof.subject_session_id,
            session.principal.session_id,
        )

    def test_execution_authority_rejects_tampering_expiry_and_wrong_role(self) -> None:
        authority = self.provider.issue_execution_authorization(
            self._session(roles=("executor",)),
            task_id="task-1",
            run_id="run-1",
            maximum_risk=RiskLevel.LOW,
            lifetime=timedelta(minutes=5),
            now=self.now,
        )
        with self.assertRaisesRegex(LocalIdentityError, "signature"):
            self.provider.verify_execution_authorization(
                authority.model_copy(update={"actor_id": "attacker"}),
                task_id="task-1",
                run_id="run-1",
                now=self.now,
            )
        with self.assertRaisesRegex(LocalIdentityError, "another task or run"):
            self.provider.verify_execution_authorization(
                authority,
                task_id="task-1",
                run_id="run-2",
                now=self.now,
            )
        with self.assertRaisesRegex(LocalIdentityError, "not currently valid"):
            self.provider.verify_execution_authorization(
                authority,
                task_id="task-1",
                run_id="run-1",
                now=self.now + timedelta(minutes=5),
            )
        with self.assertRaisesRegex(LocalIdentityError, "authorization denied"):
            self.provider.issue_execution_authorization(
                self._session(roles=("reviewer",)),
                task_id="task-1",
                run_id="run-1",
                maximum_risk=RiskLevel.LOW,
                now=self.now,
            )

    def test_cli_creates_key_and_issues_a_verifiable_session(self) -> None:
        cli_key = self.root / "cli-key.json"
        cli_session = self.root / "cli-session.json"
        cli_authority = self.root / "cli-authority.json"

        with redirect_stdout(io.StringIO()):
            create_exit = main(["create-local-identity-key", str(cli_key)])
            issue_exit = main(
                [
                    "issue-local-identity-session",
                    "--key",
                    str(cli_key),
                    "--subject-id",
                    "cli-operator",
                    "--display-name",
                    "CLI Operator",
                    "--role",
                    "approver",
                    "--lifetime-minutes",
                    "5",
                    "--output",
                    str(cli_session),
                ]
            )
            authority_exit = main(
                [
                    "issue-approval-authority",
                    "--key",
                    str(cli_key),
                    "--identity-session",
                    str(cli_session),
                    "--decision-kind",
                    "merge_review",
                    "--lifetime-minutes",
                    "5",
                    "--output",
                    str(cli_authority),
                ]
            )

        self.assertEqual(create_exit, 0)
        self.assertEqual(issue_exit, 0)
        self.assertEqual(authority_exit, 0)
        principal = LocalIdentityProvider(cli_key).load_session(cli_session)
        self.assertEqual(principal.subject_id, "cli-operator")
        authority = LocalIdentityProvider(cli_key).load_approval_authority(
            cli_authority
        )
        self.assertEqual(authority.actor_id, "cli-operator")


if __name__ == "__main__":
    unittest.main()
