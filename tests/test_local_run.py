import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from jobslayer.application.local_run import LocalRunCoordinator, LocalRunError
from jobslayer.application.run_records import LocalRunLedger
from jobslayer.domain.models import (
    ActorType,
    ApprovalAuthority,
    DecisionCard,
    DecisionKind,
    ReviewStatus,
)
from jobslayer.supervision.decision import create_human_decision


class LocalRunCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.control_root = self.root / "control"
        self.external = self.root / "external"
        self.control_root.mkdir()
        self.external.mkdir()
        for directory in ("testbeds", "tasks", "profiles", "patches", "runbooks"):
            (self.control_root / directory).mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "JobSlayer Test")
        self._git("config", "user.email", "jobslayer@example.invalid")
        (self.external / "value.txt").write_text("base\n", encoding="utf-8")
        verifier = self.external / "verify"
        verifier.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "if Path('value.txt').read_text() != 'changed\\n':\n"
            "    raise SystemExit(7)\n"
            "print('verified')\n",
            encoding="utf-8",
        )
        verifier.chmod(0o755)
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.commit = self._git("rev-parse", "HEAD")
        self._git("tag", "-a", "fixture-0", "-m", "fixture baseline")
        self._git(
            "remote", "add", "origin", "https://example.invalid/fixture.git"
        )
        self.patch = (
            "diff --git a/value.txt b/value.txt\n"
            "--- a/value.txt\n"
            "+++ b/value.txt\n"
            "@@ -1 +1 @@\n"
            "-base\n"
            "+changed\n"
        ).encode()
        (self.control_root / "patches" / "change.diff").write_bytes(self.patch)
        self._write_inputs()
        self.coordinator = LocalRunCoordinator(
            self.control_root, state_root=self.control_root / ".state"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ("git", "-C", str(self.external), *arguments),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _write_inputs(self) -> None:
        testbed = {
            "schema_version": "1.0",
            "testbed_id": "fixture",
            "display_name": "Fixture",
            "purpose": "Local run integration fixture",
            "status": "bootstrapping",
            "repository": {
                "clone_url": "https://example.invalid/fixture.git",
                "default_branch": "main",
            },
            "baseline": {
                "commit": self.commit,
                "tag": "fixture-0",
                "published": False,
                "verification_command": ["./verify"],
            },
            "local_checkout_hint": "../external",
            "architecture_areas": ["fixture"],
            "capability_targets": ["file_change"],
        }
        task = {
            "schema_version": "1.0",
            "task_id": "fixture-task",
            "project_id": "fixture",
            "title": "Change fixture value",
            "objective": "Exercise the complete local run assembly",
            "repository": "https://example.invalid/fixture.git",
            "base_commit": self.commit,
            "allowed_paths": ["value.txt"],
            "required_capabilities": ["file_change"],
            "acceptance_criteria": ["verification passes"],
            "validation_profile": "fixture-v1",
            "risk": "low",
            "max_cost_usd": 0,
        }
        profile = {
            "schema_version": "1.0",
            "profile_id": "fixture-v1",
            "command_policy": {
                "schema_version": "1.0",
                "policy_id": "fixture-policy",
                "rules": [
                    {
                        "rule_id": "verify",
                        "argv_prefix": ["./verify"],
                        "max_timeout_seconds": 3,
                    }
                ],
                "max_timeout_seconds": 3,
            },
            "checks": [
                {
                    "check_id": "verify",
                    "title": "Verify fixture",
                    "argv": ["./verify"],
                    "timeout_seconds": 2,
                }
            ],
        }
        runbook = {
            "schema_version": "1.0",
            "testbed_id": "fixture",
            "testbed_path": "testbeds/fixture.json",
            "task_path": "tasks/fixture.json",
            "validation_profile_path": "profiles/fixture.json",
            "invocation": {
                "schema_version": "1.0",
                "run_spec": {
                    "schema_version": "1.0",
                    "run_id": "fixture-run",
                    "task_id": "fixture-task",
                    "executor_type": "scripted_patch",
                    "model_profile": "deterministic-replay-v1",
                    "context_package_id": "fixture-context",
                    "workspace_id": "fixture-workspace",
                    "permission_profile": "workspace_write",
                    "timeout_seconds": 3,
                    "max_attempts": 1,
                    "output_schema": "unified_diff",
                },
                "prompt": "Replay the fixture patch.",
            },
            "executor": {
                "adapter": "scripted_patch",
                "patch_path": "patches/change.diff",
                "patch_sha256": hashlib.sha256(self.patch).hexdigest(),
            },
        }
        for path, payload in (
            (self.control_root / "testbeds" / "fixture.json", testbed),
            (self.control_root / "tasks" / "fixture.json", task),
            (self.control_root / "profiles" / "fixture.json", profile),
            (self.control_root / "runbooks" / "fixture.json", runbook),
        ):
            path.write_text(json.dumps(payload), encoding="utf-8")

    def test_executes_persists_reviews_and_exposes_a_real_decision_card(self) -> None:
        execution = self.coordinator.execute("runbooks/fixture.json")

        self.assertEqual(execution["state"], "reviewing")
        self.assertTrue(execution["verification"]["passes_gate"])
        self.assertTrue(execution["artifacts_valid"])
        self.assertEqual(execution["workspace"]["changed_paths"], ["value.txt"])

        run_directory = Path(execution["run_directory"])
        with self.assertRaisesRegex(LocalRunError, "agent or human"):
            self.coordinator.review(
                run_directory,
                actor_type=ActorType.POLICY,
                actor_id="invalid-policy-reviewer",
                status=ReviewStatus.ACCEPTED,
                summary="Policy cannot claim an implementation review.",
            )

        reviewed = self.coordinator.review(
            run_directory,
            actor_type=ActorType.AGENT,
            actor_id="fixture-review-agent",
            status=ReviewStatus.ACCEPTED,
            summary="The scoped patch and verification evidence agree.",
        )

        self.assertEqual(reviewed["state"], "merge_review")
        self.assertEqual(reviewed["records"], 2)
        self.assertTrue(reviewed["decision"]["card_available"])
        self.assertTrue(reviewed["capabilities"]["decision_recording"])
        self.assertFalse(reviewed["capabilities"]["git_merge"])
        card_path = Path(reviewed["decision"]["card_path"])
        self.assertTrue(card_path.is_file())

        card = DecisionCard.model_validate_json(card_path.read_text(encoding="utf-8"))
        decision = create_human_decision(
            card,
            actor_id="fixture-human",
            selected_option_id="approve",
            rationale="The fixture evidence is complete.",
        )
        decision_path = run_directory / "decision.json"
        decision_path.write_text(decision.model_dump_json(indent=2), encoding="utf-8")
        now = datetime.now(UTC)
        authority = ApprovalAuthority(
            authorization_id="fixture-merge-authority",
            actor_id="fixture-human",
            allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
            issued_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=1),
        )
        authority_path = self.control_root / "authority.json"
        authority_path.write_text(authority.model_dump_json(indent=2), encoding="utf-8")

        applied = self.coordinator.apply_decision(
            run_directory,
            authority_path=authority_path,
            now=now,
        )

        self.assertEqual(applied["state"], "integrating")
        self.assertEqual(applied["records"], 3)
        self.assertTrue(applied["decision"]["applied"])
        self.assertEqual(applied["decision"]["result_state"], "integrating")
        self.assertTrue(applied["capabilities"]["source_integration"])
        self.assertFalse(applied["capabilities"]["git_merge"])

        integrated = self.coordinator.integrate(run_directory)

        self.assertEqual(integrated["state"], "completed")
        self.assertEqual(integrated["records"], 4)
        self.assertTrue(integrated["capabilities"]["git_merge"])
        self.assertTrue(integrated["capabilities"]["workspace_cleanup"])
        self.assertEqual(self._git("branch", "--show-current"), "main")
        self.assertEqual(
            (self.external / "value.txt").read_text(encoding="utf-8"),
            "changed\n",
        )
        self.assertEqual(
            self._git("rev-parse", "HEAD"), integrated["integration"]["commit"]
        )

        cleaned = self.coordinator.cleanup(run_directory)

        self.assertEqual(cleaned["state"], "completed")
        self.assertEqual(cleaned["records"], 5)
        self.assertFalse(cleaned["workspace"]["present"])
        self.assertFalse(cleaned["capabilities"]["workspace_cleanup"])
        self.assertIn(
            "jobslayer/fixture-workspace",
            self._git("branch", "--list", "jobslayer/fixture-workspace"),
        )

    def test_refuses_to_overwrite_an_existing_run(self) -> None:
        self.coordinator.execute("runbooks/fixture.json")

        with self.assertRaisesRegex(LocalRunError, "already exists"):
            self.coordinator.execute("runbooks/fixture.json")

    def test_scripted_run_rejects_a_human_authorization_override(self) -> None:
        with self.assertRaisesRegex(LocalRunError, "registered policy"):
            self.coordinator.execute(
                "runbooks/fixture.json",
                authorized_by="misleading-human-override",
            )

        self.assertFalse(
            (self.control_root / ".state" / "runs" / "fixture-run").exists()
        )

    def test_codex_run_requires_human_authorization_and_uses_the_real_adapter(self) -> None:
        fake_codex = self.control_root / "fake-codex"
        fake_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "from pathlib import Path\n"
            "import sys\n"
            "sys.stdin.read()\n"
            "Path('value.txt').write_text('changed\\n', encoding='utf-8')\n"
            "events = [\n"
            "    {'type': 'thread.started', 'thread_id': 'fixture-thread'},\n"
            "    {'type': 'turn.started'},\n"
            "    {'type': 'item.completed', 'item': {\n"
            "        'type': 'file_change', 'changes': [{'path': 'value.txt'}]}},\n"
            "    {'type': 'item.completed', 'item': {\n"
            "        'type': 'agent_message', 'text': 'Changed the fixture.'}},\n"
            "    {'type': 'turn.completed', 'usage': {'input_tokens': 12, 'output_tokens': 4}},\n"
            "]\n"
            "for event in events:\n"
            "    print(json.dumps(event), flush=True)\n",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        runbook_path = self.control_root / "runbooks" / "fixture-codex.json"
        payload = json.loads(
            (self.control_root / "runbooks" / "fixture.json").read_text(
                encoding="utf-8"
            )
        )
        payload["executor"] = {"adapter": "codex_cli"}
        payload["invocation"]["run_spec"].update(
            {
                "run_id": "fixture-codex-run",
                "executor_type": "codex_cli",
                "model_profile": "default",
                "workspace_id": "fixture-codex-workspace",
                "output_schema": "none",
            }
        )
        payload["invocation"]["prompt"] = "Change value.txt to changed."
        runbook_path.write_text(json.dumps(payload), encoding="utf-8")
        coordinator = LocalRunCoordinator(
            self.control_root,
            state_root=self.control_root / ".codex-state",
            codex_binary=fake_codex,
        )

        with self.assertRaisesRegex(LocalRunError, "authorized_by"):
            coordinator.execute(runbook_path)
        self.assertFalse(
            (
                self.control_root
                / ".codex-state"
                / "runs"
                / "fixture-codex-run"
            ).exists()
        )

        execution = coordinator.execute(
            runbook_path,
            authorized_by="fixture-human-operator",
        )

        self.assertEqual(execution["state"], "reviewing")
        self.assertEqual(execution["executor"]["type"], "codex_cli")
        self.assertEqual(execution["executor"]["usage"]["input_tokens"], 12)
        self.assertEqual(execution["workspace"]["changed_paths"], ["value.txt"])
        record = LocalRunLedger(
            Path(execution["run_directory"]) / "records.jsonl",
            run_id="fixture-codex-run",
        ).read_all()[0]
        authorization = record.payload["context"]["authorization"]
        self.assertEqual(authorization["actor_type"], "human")
        self.assertEqual(authorization["actor_id"], "fixture-human-operator")


if __name__ == "__main__":
    unittest.main()
