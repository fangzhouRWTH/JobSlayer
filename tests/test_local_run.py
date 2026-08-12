import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jobslayer.adapters.local_recovery import LocalRunRecoveryManager
from jobslayer.adapters.local_identity import LocalIdentityProvider
from jobslayer.application.local_run import LocalRunCoordinator, LocalRunError
from jobslayer.application.executor_comparison import ExecutorComparisonEvaluator
from jobslayer.application.run_records import LocalRunLedger, build_run_record
from jobslayer.domain.models import (
    ActorType,
    ApprovalAuthority,
    DecisionCard,
    DecisionKind,
    ReviewStatus,
    RiskLevel,
)
from jobslayer.supervision.decision import create_human_decision
from jobslayer.recovery import RecoveryError, RecoveryStatus


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
        verifier = self.external / "verify.py"
        verifier.write_text(
            "from pathlib import Path\n"
            "if Path('value.txt').read_text() != 'changed\\n':\n"
            "    raise SystemExit(7)\n"
            "print('verified')\n",
            encoding="utf-8",
        )
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
                "verification_command": [sys.executable, "verify.py"],
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
                        "argv_prefix": [sys.executable, "verify.py"],
                        "max_timeout_seconds": 3,
                    }
                ],
                "max_timeout_seconds": 3,
            },
            "checks": [
                {
                    "check_id": "verify",
                    "title": "Verify fixture",
                    "argv": [sys.executable, "verify.py"],
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

    def _create_reviewed_run(self) -> Path:
        execution = self.coordinator.execute("runbooks/fixture.json")
        reviewed = self.coordinator.review(
            execution["run_directory"],
            actor_type=ActorType.AGENT,
            actor_id="fixture-review-agent",
            status=ReviewStatus.ACCEPTED,
            summary="The scoped patch and verification evidence agree.",
        )
        return Path(reviewed["run_directory"])

    def _create_approved_run(self) -> Path:
        run_directory = self._create_reviewed_run()
        authority_path, now = self._prepare_approval_files(run_directory)
        self.coordinator.apply_decision(
            run_directory,
            authority_path=authority_path,
            now=now,
        )
        return run_directory

    def _prepare_approval_files(self, run_directory: Path) -> tuple[Path, datetime]:
        card_path = run_directory / "decision-card.json"
        card = DecisionCard.model_validate_json(
            card_path.read_text(encoding="utf-8")
        )
        decision = create_human_decision(
            card,
            actor_id="fixture-human",
            selected_option_id="approve",
            rationale="The fixture evidence is complete.",
        )
        (run_directory / "decision.json").write_text(
            decision.model_dump_json(indent=2), encoding="utf-8"
        )
        now = datetime.now(UTC)
        authority = ApprovalAuthority(
            authorization_id="fixture-merge-authority",
            actor_id="fixture-human",
            allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
            issued_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=1),
        )
        authority_path = self.control_root / "authority.json"
        authority_path.write_text(
            authority.model_dump_json(indent=2), encoding="utf-8"
        )
        return authority_path, now

    def _create_completed_integration_record_gap(self) -> tuple[Path, str]:
        run_directory = self._create_approved_run()
        integrated = self.coordinator.integrate(run_directory)
        integrated_commit = str(integrated["integration"]["commit"])
        records_path = run_directory / "records.jsonl"
        records_path.write_bytes(
            b"".join(records_path.read_bytes().splitlines(keepends=True)[:3])
        )
        return run_directory, integrated_commit

    def _create_integrated_run(self) -> tuple[Path, str]:
        run_directory = self._create_approved_run()
        integrated = self.coordinator.integrate(run_directory)
        return run_directory, str(integrated["integration"]["commit"])

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

    def test_recovery_restores_a_missing_decision_card_projection_idempotently(self) -> None:
        run_directory = self._create_reviewed_run()
        card_path = run_directory / "decision-card.json"
        expected = DecisionCard.model_validate_json(
            card_path.read_text(encoding="utf-8")
        )
        card_path.unlink()
        manager = LocalRunRecoveryManager(self.coordinator)

        assessment = manager.assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.RECOVERABLE)
        self.assertEqual(assessment.repair_action, "restore_decision_card")
        recovered = manager.recover(run_directory)
        repeated = manager.recover(run_directory)
        self.assertEqual(recovered.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(repeated.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(
            DecisionCard.model_validate_json(card_path.read_text(encoding="utf-8")),
            expected,
        )

    def test_recovery_refuses_to_overwrite_a_tampered_projection(self) -> None:
        run_directory = self._create_reviewed_run()
        card_path = run_directory / "decision-card.json"
        card_path.write_text("{}\n", encoding="utf-8")
        manager = LocalRunRecoveryManager(self.coordinator)

        assessment = manager.assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.INVALID_EVIDENCE)
        with self.assertRaisesRegex(RecoveryError, "cannot be repaired automatically"):
            manager.recover(run_directory)
        self.assertEqual(card_path.read_text(encoding="utf-8"), "{}\n")

    def test_recovery_removes_its_partial_projection_after_a_write_fault(self) -> None:
        run_directory = self._create_reviewed_run()
        card_path = run_directory / "decision-card.json"
        card_path.unlink()
        manager = LocalRunRecoveryManager(self.coordinator)

        with (
            patch(
                "jobslayer.adapters.local_recovery.os.write",
                side_effect=OSError("injected projection write fault"),
            ),
            self.assertRaisesRegex(RecoveryError, "incomplete file was removed"),
        ):
            manager.recover(run_directory)

        self.assertFalse(card_path.exists())
        self.assertEqual(
            manager.assess(run_directory).status,
            RecoveryStatus.RECOVERABLE,
        )

    def test_recovery_escalates_a_journal_ledger_commit_gap(self) -> None:
        run_directory = self._create_reviewed_run()
        records_path = run_directory / "records.jsonl"
        first_record = records_path.read_bytes().splitlines(keepends=True)[0]
        records_path.write_bytes(first_record)

        assessment = LocalRunRecoveryManager(self.coordinator).assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.MANUAL_INTERVENTION)
        self.assertIn("do not form a consistent", assessment.reason)

    def test_recovery_reports_an_untouched_reviewed_run_as_consistent(self) -> None:
        run_directory = self._create_reviewed_run()

        assessment = LocalRunRecoveryManager(self.coordinator).assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(assessment.run_stage, "implementation_review")
        self.assertEqual(assessment.workflow_state, "merge_review")

    def test_subprocess_execution_crash_recovers_persisted_outcome_without_rerun(self) -> None:
        run_directory = (
            self.control_root / ".state" / "runs" / "fixture-run"
        )
        crash_script = (
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            "from unittest.mock import patch\n"
            "from jobslayer.application.local_run import LocalRunCoordinator\n"
            "from jobslayer.application.run_records import LocalRunLedger\n"
            "coordinator = LocalRunCoordinator(\n"
            "    Path(sys.argv[1]), state_root=Path(sys.argv[2]))\n"
            "def crash_before_record(*args, **kwargs):\n"
            "    os._exit(88)\n"
            "with patch.object(LocalRunLedger, 'append', crash_before_record):\n"
            "    coordinator.execute(Path(sys.argv[3]))\n"
        )

        crashed = subprocess.run(
            (
                sys.executable,
                "-c",
                crash_script,
                str(self.control_root),
                str(self.control_root / ".state"),
                str(self.control_root / "runbooks" / "fixture.json"),
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(crashed.returncode, 88, crashed.stderr)
        self.assertTrue(run_directory.is_dir())
        self.assertFalse((run_directory / "records.jsonl").exists())
        manager = LocalRunRecoveryManager(self.coordinator)
        assessment = manager.assess(run_directory)
        self.assertEqual(assessment.status, RecoveryStatus.RECOVERABLE)
        self.assertEqual(assessment.repair_action, "resume_execution_record")
        with patch.object(
            self.coordinator,
            "execute",
            side_effect=AssertionError("recovery must not rerun execution"),
        ):
            recovered = manager.recover(run_directory)
        repeated = manager.recover(run_directory)

        self.assertEqual(recovered.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(repeated.status, RecoveryStatus.CONSISTENT)
        summary = self.coordinator.inspect(run_directory)
        self.assertEqual(summary["state"], "reviewing")
        self.assertEqual(summary["records"], 1)
        record = LocalRunLedger(
            run_directory / "records.jsonl",
            run_id="fixture-run",
        ).read_all()[0]
        self.assertIn("execution_intent_artifact", record.payload)
        self.assertIn("execution_outcome_artifact", record.payload)

    def test_inspect_normalizes_compatible_defaults_in_historical_context(self) -> None:
        summary = self.coordinator.execute("runbooks/fixture.json")
        run_directory = Path(summary["run_directory"])
        ledger_path = run_directory / "records.jsonl"
        record = LocalRunLedger(ledger_path, run_id="fixture-run").read_all()[0]
        payload = json.loads(json.dumps(record.payload))
        run_spec = payload["context"]["invocation"]["run_spec"]
        for field in (
            "max_repairs",
            "maximum_input_tokens",
            "maximum_output_tokens",
            "maximum_context_bytes",
        ):
            run_spec.pop(field)
        historical = build_run_record(
            (),
            run_id=record.run_id,
            task_id=record.task_id,
            stage=record.stage,
            payload=payload,
            recorded_at=record.recorded_at,
        )
        ledger_path.write_text(
            historical.model_dump_json() + "\n", encoding="utf-8"
        )

        inspected = self.coordinator.inspect(run_directory)

        self.assertEqual(inspected["run_id"], "fixture-run")
        self.assertTrue(inspected["record_chain_valid"])
        self.assertTrue(inspected["artifacts_valid"])

    def test_execution_intent_without_outcome_never_reruns_agent(self) -> None:
        run_directory = (
            self.control_root / ".state" / "runs" / "fixture-run"
        )
        crash_script = (
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            "from unittest.mock import patch\n"
            "from jobslayer.application.local_run import (\n"
            "    LocalRunCoordinator, TaskExecutionController)\n"
            "coordinator = LocalRunCoordinator(\n"
            "    Path(sys.argv[1]), state_root=Path(sys.argv[2]))\n"
            "def crash_before_execution(*args, **kwargs):\n"
            "    os._exit(89)\n"
            "with patch.object(\n"
            "        TaskExecutionController, 'execute_implementation',\n"
            "        crash_before_execution):\n"
            "    coordinator.execute(Path(sys.argv[3]))\n"
        )

        crashed = subprocess.run(
            (
                sys.executable,
                "-c",
                crash_script,
                str(self.control_root),
                str(self.control_root / ".state"),
                str(self.control_root / "runbooks" / "fixture.json"),
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(crashed.returncode, 89, crashed.stderr)
        self.assertTrue(run_directory.is_dir())
        self.assertFalse((run_directory / "records.jsonl").exists())
        manager = LocalRunRecoveryManager(self.coordinator)
        assessment = manager.assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.MANUAL_INTERVENTION)
        self.assertEqual(assessment.run_stage, "execution_intent")
        self.assertIn("will not rerun the Agent", assessment.reason)
        with self.assertRaisesRegex(RecoveryError, "cannot be repaired automatically"):
            manager.recover(run_directory)
        self.assertFalse((run_directory / "records.jsonl").exists())

    def test_execution_recovery_rejects_changed_outcome_artifact_binding(self) -> None:
        execution = self.coordinator.execute("runbooks/fixture.json")
        run_directory = Path(execution["run_directory"])
        record = LocalRunLedger(
            run_directory / "records.jsonl",
            run_id="fixture-run",
        ).read_all()[0]
        outcome_artifact = record.payload["execution_outcome_artifact"]
        (run_directory / "records.jsonl").unlink()
        manifest_path = (
            run_directory
            / "artifacts"
            / "manifests"
            / f"{outcome_artifact['artifact_id']}.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["producer"] = "untrusted-coordinator"
        manifest_path.chmod(0o600)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manager = LocalRunRecoveryManager(self.coordinator)

        assessment = manager.assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.INVALID_EVIDENCE)
        self.assertIn("artifact binding is invalid", assessment.reason)
        with self.assertRaisesRegex(RecoveryError, "cannot be repaired automatically"):
            manager.recover(run_directory)
        self.assertFalse((run_directory / "records.jsonl").exists())

    def test_subprocess_decision_crash_recovers_without_reapplying_transition(self) -> None:
        run_directory = self._create_reviewed_run()
        authority_path, _ = self._prepare_approval_files(run_directory)
        crash_script = (
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            "from unittest.mock import patch\n"
            "from jobslayer.application.local_run import LocalRunCoordinator\n"
            "from jobslayer.application.run_records import LocalRunLedger\n"
            "coordinator = LocalRunCoordinator(\n"
            "    Path(sys.argv[1]), state_root=Path(sys.argv[2]))\n"
            "def crash_before_record(*args, **kwargs):\n"
            "    os._exit(85)\n"
            "with patch.object(LocalRunLedger, 'append', crash_before_record):\n"
            "    coordinator.apply_decision(\n"
            "        Path(sys.argv[3]), authority_path=Path(sys.argv[4]))\n"
        )

        crashed = subprocess.run(
            (
                sys.executable,
                "-c",
                crash_script,
                str(self.control_root),
                str(self.control_root / ".state"),
                str(run_directory),
                str(authority_path),
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(crashed.returncode, 85, crashed.stderr)
        self.assertEqual(
            len((run_directory / "records.jsonl").read_bytes().splitlines()),
            2,
        )
        journal_path = run_directory / "workflow.jsonl"
        journal_before = journal_path.read_bytes()
        self.assertEqual(
            json.loads(journal_before.splitlines()[-1])["to_state"],
            "integrating",
        )
        manager = LocalRunRecoveryManager(self.coordinator)
        assessment = manager.assess(run_directory)
        self.assertEqual(assessment.status, RecoveryStatus.RECOVERABLE)
        self.assertEqual(
            assessment.repair_action,
            "resume_decision_application_record",
        )
        with patch(
            "jobslayer.adapters.local_recovery.DecisionApplicationService.apply",
            side_effect=AssertionError("recovery must not reapply the transition"),
        ):
            recovered = manager.recover(run_directory)
        repeated = manager.recover(run_directory)

        self.assertEqual(recovered.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(repeated.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(journal_path.read_bytes(), journal_before)
        self.assertEqual(
            len((run_directory / "records.jsonl").read_bytes().splitlines()),
            3,
        )

    def test_decision_recovery_rejects_changed_artifact_producer(self) -> None:
        run_directory = self._create_reviewed_run()
        authority_path, now = self._prepare_approval_files(run_directory)
        with patch(
            "jobslayer.application.local_run.LocalRunLedger.append",
            side_effect=OSError("injected post-transition failure"),
        ):
            with self.assertRaises(OSError):
                self.coordinator.apply_decision(
                    run_directory,
                    authority_path=authority_path,
                    now=now,
                )
        transition = json.loads(
            (run_directory / "workflow.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[-1]
        )
        for evidence_id in transition["evidence_ids"]:
            if not evidence_id.startswith("artifact-"):
                continue
            manifest_path = (
                run_directory / "artifacts" / "manifests" / f"{evidence_id}.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["artifact_type"] == "human-decision":
                manifest["producer"] = "different-human"
                manifest_path.chmod(0o600)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                break
        else:
            self.fail("decision transition did not reference its decision artifact")

        manager = LocalRunRecoveryManager(self.coordinator)
        assessment = manager.assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.INVALID_EVIDENCE)
        self.assertIn("producer binding is invalid", assessment.reason)
        with self.assertRaisesRegex(RecoveryError, "cannot be repaired automatically"):
            manager.recover(run_directory)
        self.assertEqual(
            len((run_directory / "records.jsonl").read_bytes().splitlines()),
            2,
        )

    def test_recovery_resumes_only_the_missing_integration_record_idempotently(self) -> None:
        run_directory, integrated_commit = (
            self._create_completed_integration_record_gap()
        )
        records_path = run_directory / "records.jsonl"
        journal_path = run_directory / "workflow.jsonl"
        journal_before = journal_path.read_bytes()
        manager = LocalRunRecoveryManager(self.coordinator)

        assessment = manager.assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.RECOVERABLE)
        self.assertEqual(
            assessment.repair_action,
            "resume_source_integration_record",
        )
        with patch(
            "jobslayer.application.local_run.LocalGitIntegrator.integrate",
            side_effect=AssertionError("recovery must not reintegrate source"),
        ):
            recovered = manager.recover(run_directory)
        repeated = manager.recover(run_directory)

        self.assertEqual(recovered.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(repeated.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(len(records_path.read_bytes().splitlines()), 4)
        self.assertEqual(journal_path.read_bytes(), journal_before)
        self.assertEqual(self._git("rev-parse", "HEAD"), integrated_commit)
        self.assertEqual(
            subprocess.run(
                (
                    "git",
                    "-C",
                    str(
                        self.control_root
                        / ".state"
                        / "workspaces"
                        / "fixture-workspace"
                    ),
                    "rev-parse",
                    "HEAD",
                ),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            integrated_commit,
        )

    def test_subprocess_crash_after_completed_recovers_without_reintegration(self) -> None:
        run_directory = self._create_approved_run()
        crash_script = (
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            "from unittest.mock import patch\n"
            "from jobslayer.application.local_run import LocalRunCoordinator\n"
            "from jobslayer.application.run_records import LocalRunLedger\n"
            "coordinator = LocalRunCoordinator(\n"
            "    Path(sys.argv[1]), state_root=Path(sys.argv[2]))\n"
            "def crash_before_record(*args, **kwargs):\n"
            "    os._exit(86)\n"
            "with patch.object(LocalRunLedger, 'append', crash_before_record):\n"
            "    coordinator.integrate(Path(sys.argv[3]))\n"
        )

        crashed = subprocess.run(
            (
                sys.executable,
                "-c",
                crash_script,
                str(self.control_root),
                str(self.control_root / ".state"),
                str(run_directory),
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(crashed.returncode, 86, crashed.stderr)
        self.assertEqual(
            len((run_directory / "records.jsonl").read_bytes().splitlines()),
            3,
        )
        completed_transition = json.loads(
            (run_directory / "workflow.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[-1]
        )
        self.assertEqual(completed_transition["to_state"], "completed")
        integrated_commit = self._git("rev-parse", "HEAD")
        self.assertNotEqual(integrated_commit, self.commit)

        manager = LocalRunRecoveryManager(self.coordinator)
        assessment = manager.assess(run_directory)
        self.assertEqual(assessment.status, RecoveryStatus.RECOVERABLE)
        self.assertEqual(
            assessment.repair_action,
            "resume_source_integration_record",
        )
        with patch(
            "jobslayer.application.local_run.LocalGitIntegrator.integrate",
            side_effect=AssertionError("recovery must not reintegrate source"),
        ):
            recovered = manager.recover(run_directory)

        self.assertEqual(recovered.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(self._git("rev-parse", "HEAD"), integrated_commit)
        self.assertEqual(
            len((run_directory / "records.jsonl").read_bytes().splitlines()),
            4,
        )

    def test_recovery_rejects_completed_integration_after_target_drift(self) -> None:
        run_directory, integrated_commit = (
            self._create_completed_integration_record_gap()
        )
        (self.external / "drift.txt").write_text("drift\n", encoding="utf-8")
        self._git("add", "drift.txt")
        self._git("commit", "-m", "post-integration drift")
        drifted_commit = self._git("rev-parse", "HEAD")
        manager = LocalRunRecoveryManager(self.coordinator)

        assessment = manager.assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.INVALID_EVIDENCE)
        self.assertIn("current Git facts", assessment.reason)
        with self.assertRaisesRegex(RecoveryError, "cannot be repaired automatically"):
            manager.recover(run_directory)
        self.assertEqual(self._git("rev-parse", "HEAD"), drifted_commit)
        self.assertNotEqual(drifted_commit, integrated_commit)
        self.assertEqual(
            len((run_directory / "records.jsonl").read_bytes().splitlines()),
            3,
        )

    def test_recovery_rejects_changed_integration_artifact_binding(self) -> None:
        run_directory, integrated_commit = (
            self._create_completed_integration_record_gap()
        )
        completed_transition = json.loads(
            (run_directory / "workflow.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[-1]
        )
        artifact_id = next(
            evidence_id
            for evidence_id in completed_transition["evidence_ids"]
            if evidence_id.startswith("artifact-")
        )
        manifest_path = (
            run_directory / "artifacts" / "manifests" / f"{artifact_id}.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["producer"] = "untrusted-integrator"
        manifest_path.chmod(0o600)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manager = LocalRunRecoveryManager(self.coordinator)

        assessment = manager.assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.INVALID_EVIDENCE)
        self.assertIn("binding is invalid", assessment.reason)
        with self.assertRaisesRegex(RecoveryError, "cannot be repaired automatically"):
            manager.recover(run_directory)
        self.assertEqual(self._git("rev-parse", "HEAD"), integrated_commit)
        self.assertEqual(
            len((run_directory / "records.jsonl").read_bytes().splitlines()),
            3,
        )

    def test_subprocess_cleanup_crash_recovers_without_removing_again(self) -> None:
        run_directory, integrated_commit = self._create_integrated_run()
        records = LocalRunLedger(
            run_directory / "records.jsonl",
            run_id=run_directory.name,
        ).read_all()
        workspace_path = Path(records[0].payload["outcome"]["workspace"]["path"])
        crash_script = (
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            "from unittest.mock import patch\n"
            "from jobslayer.application.local_run import LocalRunCoordinator\n"
            "from jobslayer.application.run_records import LocalRunLedger\n"
            "coordinator = LocalRunCoordinator(\n"
            "    Path(sys.argv[1]), state_root=Path(sys.argv[2]))\n"
            "def crash_before_record(*args, **kwargs):\n"
            "    os._exit(87)\n"
            "with patch.object(LocalRunLedger, 'append', crash_before_record):\n"
            "    coordinator.cleanup(Path(sys.argv[3]))\n"
        )

        crashed = subprocess.run(
            (
                sys.executable,
                "-c",
                crash_script,
                str(self.control_root),
                str(self.control_root / ".state"),
                str(run_directory),
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(crashed.returncode, 87, crashed.stderr)
        self.assertFalse(workspace_path.exists())
        self.assertEqual(
            len((run_directory / "records.jsonl").read_bytes().splitlines()),
            4,
        )
        manager = LocalRunRecoveryManager(self.coordinator)
        assessment = manager.assess(run_directory)
        self.assertEqual(assessment.status, RecoveryStatus.RECOVERABLE)
        self.assertEqual(
            assessment.repair_action,
            "resume_workspace_cleanup_record",
        )
        with patch(
            "jobslayer.application.local_run.GitWorktreeManager.remove",
            side_effect=AssertionError("recovery must not remove the workspace again"),
        ):
            recovered = manager.recover(run_directory)
        repeated = manager.recover(run_directory)

        self.assertEqual(recovered.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(repeated.status, RecoveryStatus.CONSISTENT)
        self.assertEqual(self._git("rev-parse", "HEAD"), integrated_commit)
        cleanup_record = LocalRunLedger(
            run_directory / "records.jsonl",
            run_id=run_directory.name,
        ).read_all()[-1]
        self.assertEqual(cleanup_record.stage.value, "workspace_cleanup")
        self.assertTrue(
            cleanup_record.payload["removal_inspection"]["branch_commit"]
            == integrated_commit
        )

    def test_cleanup_recovery_rejects_preserved_branch_drift(self) -> None:
        run_directory, integrated_commit = self._create_integrated_run()
        self.coordinator.cleanup(run_directory)
        records_path = run_directory / "records.jsonl"
        records_path.write_bytes(
            b"".join(records_path.read_bytes().splitlines(keepends=True)[:4])
        )
        self._git(
            "branch",
            "-f",
            "jobslayer/fixture-workspace",
            self.commit,
        )
        manager = LocalRunRecoveryManager(self.coordinator)

        assessment = manager.assess(run_directory)

        self.assertEqual(assessment.status, RecoveryStatus.INVALID_EVIDENCE)
        self.assertIn("preserved branch drifted", assessment.reason)
        with self.assertRaisesRegex(RecoveryError, "cannot be repaired automatically"):
            manager.recover(run_directory)
        self.assertEqual(self._git("rev-parse", "HEAD"), integrated_commit)
        self.assertEqual(
            len(records_path.read_bytes().splitlines()),
            4,
        )

    def test_codex_run_requires_human_authorization_and_uses_the_real_adapter(self) -> None:
        fake_codex = self.control_root / "fake_codex.py"
        fake_codex.write_text(
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
                "maximum_input_tokens": 100,
                "maximum_output_tokens": 50,
                "maximum_context_bytes": 65536,
            }
        )
        payload["invocation"]["prompt"] = "Change value.txt to changed."
        runbook_path.write_text(json.dumps(payload), encoding="utf-8")
        identity_key = self.control_root / ".codex-state" / "identity-key.json"
        identity = LocalIdentityProvider(identity_key)
        identity.create_key()
        session = identity.issue(
            subject_id="fixture-human-operator",
            display_name="Fixture Human Operator",
            roles=("executor",),
        )
        authority = identity.issue_execution_authorization(
            session,
            task_id="fixture-task",
            run_id="fixture-codex-run",
            maximum_risk=RiskLevel.LOW,
        )
        coordinator = LocalRunCoordinator(
            self.control_root,
            state_root=self.control_root / ".codex-state",
            codex_binary=(sys.executable, str(fake_codex)),
            execution_authority_verifier=(
                lambda supplied, task_id, run_id, now: identity.verify_execution_authorization(
                    supplied,
                    task_id=task_id,
                    run_id=run_id,
                    now=now,
                )
            ),
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
            execution_authorization=authority,
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
        self.assertEqual(authorization["run_id"], "fixture-codex-run")
        self.assertIsNotNone(authorization["proof"])

        scripted = self.coordinator.execute("runbooks/fixture.json")
        comparison = ExecutorComparisonEvaluator().evaluate_runs(
            (
                Path(scripted["run_directory"]),
                Path(execution["run_directory"]),
            )
        )
        self.assertEqual(
            {item.executor_type for item in comparison.samples},
            {"scripted_patch", "codex_cli"},
        )
        self.assertEqual(
            len({item.task_contract_sha256 for item in comparison.samples}),
            1,
        )
        self.assertEqual(
            len(
                {
                    item.validation_contract_sha256
                    for item in comparison.samples
                }
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
