from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jobslayer.adapters.local_decisions import LocalDecisionStore
from jobslayer.application.local_run import LocalRunCoordinator
from jobslayer.application.readiness import Phase0ReadinessEvaluator
from jobslayer.domain.models import (
    ActorType,
    ApprovalAuthority,
    DecisionCard,
    DecisionKind,
    ReviewStatus,
)
from jobslayer.supervision.decision import create_human_decision


class Phase0CorpusError(RuntimeError):
    """Raised when a deterministic Phase 0 evidence corpus cannot be built."""


class _CorpusModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Phase0CorpusCase(_CorpusModel):
    case_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    action: Literal[
        "approve_complete",
        "request_changes",
        "reject",
        "await_decision",
        "verification_failure",
    ]


class Phase0CorpusDefinition(_CorpusModel):
    schema_version: Literal["1.0"] = "1.0"
    corpus_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    required_reviewed_tasks: int = Field(ge=3)
    cases: tuple[Phase0CorpusCase, ...] = Field(min_length=4)

    @model_validator(mode="after")
    def validate_coverage(self) -> Phase0CorpusDefinition:
        case_ids = tuple(item.case_id for item in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("corpus case ids must be unique")
        actions = tuple(item.action for item in self.cases)
        reviewed = sum(action != "verification_failure" for action in actions)
        if reviewed != self.required_reviewed_tasks:
            raise ValueError(
                "required_reviewed_tasks must equal the number of reviewable cases"
            )
        required_actions = {
            "approve_complete",
            "request_changes",
            "reject",
            "verification_failure",
        }
        missing = required_actions.difference(actions)
        if missing:
            raise ValueError(
                "corpus must cover completion, changes, cancellation, and failure: "
                + ", ".join(sorted(missing))
            )
        if actions.count("approve_complete") != 1:
            raise ValueError("corpus must contain exactly one completed integration")
        return self


class Phase0CorpusBuilder:
    """Build a disposable but retainable evidence corpus through public services."""

    _REMOTE = "https://example.invalid/jobslayer-phase0-corpus.git"
    _TESTBED_ID = "phase0-corpus"
    _PROFILE_ID = "phase0-corpus-v1"
    _TAG = "phase0-corpus-baseline-v1"
    _FIXTURE_ACTOR = "phase0-corpus-fixture-operator"

    def __init__(self, definition_path: str | Path, output_root: str | Path):
        self.definition_path = Path(definition_path).resolve(strict=True)
        self.output_root = Path(output_root).resolve(strict=False)
        self.control_root = self.output_root / "control"
        self.testbed_root = self.output_root / "testbed"
        self.state_root = self.output_root / "state"

    def build(self) -> dict:
        definition_bytes = self.definition_path.read_bytes()
        try:
            definition = Phase0CorpusDefinition.model_validate_json(definition_bytes)
        except ValidationError as exc:
            raise Phase0CorpusError(f"invalid corpus definition: {exc}") from exc
        try:
            self.output_root.mkdir(parents=True, mode=0o700)
        except FileExistsError as exc:
            raise Phase0CorpusError(
                f"refusing to reuse an existing corpus directory: {self.output_root}"
            ) from exc
        try:
            baseline_commit = self._create_testbed()
            runbooks = self._create_control_inputs(
                definition,
                definition_bytes=definition_bytes,
                baseline_commit=baseline_commit,
            )
            coordinator = LocalRunCoordinator(
                self.control_root,
                state_root=self.state_root,
            )
            summaries = self._execute_cases(
                definition,
                runbooks=runbooks,
                coordinator=coordinator,
            )
            readiness = Phase0ReadinessEvaluator(
                coordinator,
                state_root=self.state_root,
                required_reviewed_tasks=definition.required_reviewed_tasks,
            ).evaluate()
            if not readiness.automated_gate_passes:
                raise Phase0CorpusError(
                    "generated corpus did not pass its automated readiness gate: "
                    + "; ".join(readiness.unmet_criteria)
                )
            report = {
                "schema_version": "1.0",
                "corpus_id": definition.corpus_id,
                "evidence_class": "deterministic_fixture",
                "human_confirmation_claimed": False,
                "definition_path": str(self.definition_path),
                "definition_sha256": hashlib.sha256(definition_bytes).hexdigest(),
                "output_root": str(self.output_root),
                "control_commit": self._git(self.control_root, "rev-parse", "HEAD"),
                "testbed_baseline_commit": baseline_commit,
                "runs": [
                    {
                        "case_id": case.case_id,
                        "action": case.action,
                        "run_id": summary["run_id"],
                        "task_id": summary["task_id"],
                        "state": summary["state"],
                    }
                    for case, summary in zip(definition.cases, summaries, strict=True)
                ],
                "readiness": readiness.to_dict(),
            }
            self._write_json(self.output_root / "corpus-report.json", report)
            return report
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
            if isinstance(exc, Phase0CorpusError):
                raise
            raise Phase0CorpusError(
                f"corpus build stopped; partial evidence was retained at "
                f"{self.output_root}: {exc}"
            ) from exc

    def _create_testbed(self) -> str:
        self.testbed_root.mkdir()
        self._git(self.testbed_root, "init", "-b", "main")
        self._git(self.testbed_root, "config", "user.name", "JobSlayer Corpus")
        self._git(
            self.testbed_root,
            "config",
            "user.email",
            "jobslayer-corpus@example.invalid",
        )
        verifier = (
            "from pathlib import Path\n"
            "root = Path('changes')\n"
            "for path in sorted(root.glob('*.txt')) if root.exists() else ():\n"
            "    if path.read_text(encoding='utf-8') != f'{path.stem}\\n':\n"
            "        raise SystemExit(7)\n"
            "print('phase0 fixture verified')\n"
        )
        (self.testbed_root / "verify_fixture.py").write_text(
            verifier,
            encoding="utf-8",
            newline="\n",
        )
        self._git(self.testbed_root, "add", ".")
        self._git(self.testbed_root, "commit", "-m", "phase0 corpus baseline")
        baseline_commit = self._git(self.testbed_root, "rev-parse", "HEAD")
        self._git(
            self.testbed_root,
            "tag",
            "-a",
            self._TAG,
            "-m",
            "phase0 corpus baseline",
        )
        self._git(self.testbed_root, "remote", "add", "origin", self._REMOTE)
        return baseline_commit

    def _create_control_inputs(
        self,
        definition: Phase0CorpusDefinition,
        *,
        definition_bytes: bytes,
        baseline_commit: str,
    ) -> dict[str, Path]:
        for directory in (
            "definitions",
            "testbeds",
            "tasks",
            "profiles",
            "patches",
            "runbooks",
        ):
            (self.control_root / directory).mkdir(parents=True, exist_ok=True)
        (self.control_root / "definitions" / "corpus.json").write_bytes(
            definition_bytes
        )
        testbed = {
            "schema_version": "1.0",
            "testbed_id": self._TESTBED_ID,
            "display_name": "JobSlayer Phase 0 deterministic corpus",
            "purpose": "Cross-platform workflow, evidence, and negative-path fixture",
            "status": "active",
            "repository": {
                "clone_url": self._REMOTE,
                "default_branch": "main",
            },
            "baseline": {
                "commit": baseline_commit,
                "tag": self._TAG,
                "published": False,
                "verification_command": [sys.executable, "verify_fixture.py"],
            },
            "local_checkout_hint": "../testbed",
            "architecture_areas": ["workflow-control-plane"],
            "capability_targets": ["deterministic-fixture"],
        }
        profile = {
            "schema_version": "1.0",
            "profile_id": self._PROFILE_ID,
            "command_policy": {
                "schema_version": "1.0",
                "policy_id": "phase0-corpus-command-policy-v1",
                "rules": [
                    {
                        "rule_id": "fixture-verification",
                        "argv_prefix": [sys.executable, "verify_fixture.py"],
                        "allow_additional_arguments": False,
                        "accepted_exit_codes": [0],
                        "max_timeout_seconds": 10,
                    }
                ],
                "max_timeout_seconds": 10,
                "max_output_bytes_per_stream": 100000,
            },
            "checks": [
                {
                    "check_id": "fixture-verification",
                    "title": "Verify deterministic corpus change",
                    "argv": [sys.executable, "verify_fixture.py"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "required": True,
                }
            ],
        }
        self._write_json(self.control_root / "testbeds" / "fixture.json", testbed)
        self._write_json(self.control_root / "profiles" / "fixture.json", profile)
        runbooks: dict[str, Path] = {}
        for case in definition.cases:
            task_id = f"{definition.corpus_id}-{case.case_id}"
            run_id = f"{task_id}-run"
            workspace_id = f"{task_id}-workspace"
            content = (
                "invalid-fixture-content"
                if case.action == "verification_failure"
                else case.case_id
            )
            patch_bytes = self._new_file_patch(case.case_id, content)
            patch_path = self.control_root / "patches" / f"{case.case_id}.diff"
            patch_path.write_bytes(patch_bytes)
            task = {
                "schema_version": "1.0",
                "task_id": task_id,
                "project_id": self._TESTBED_ID,
                "version": 1,
                "title": f"Phase 0 corpus {case.case_id}",
                "objective": "Exercise one governed deterministic corpus path",
                "repository": self._REMOTE,
                "base_commit": baseline_commit,
                "allowed_paths": ["changes/"],
                "forbidden_paths": ["verify_fixture.py"],
                "required_capabilities": ["deterministic-fixture"],
                "acceptance_criteria": [
                    "The fixture verifier deterministically evaluates the patch"
                ],
                "validation_profile": self._PROFILE_ID,
                "risk": "low",
                "max_cost_usd": 0,
            }
            runbook = {
                "schema_version": "1.0",
                "testbed_id": self._TESTBED_ID,
                "testbed_path": "testbeds/fixture.json",
                "task_path": f"tasks/{case.case_id}.json",
                "validation_profile_path": "profiles/fixture.json",
                "invocation": {
                    "schema_version": "1.0",
                    "run_spec": {
                        "schema_version": "1.0",
                        "run_id": run_id,
                        "task_id": task_id,
                        "executor_type": "scripted_patch",
                        "model_profile": "deterministic-replay-v1",
                        "context_package_id": f"{task_id}-context",
                        "workspace_id": workspace_id,
                        "permission_profile": "workspace_write",
                        "timeout_seconds": 10,
                        "max_attempts": 1,
                        "output_schema": "unified_diff",
                    },
                    "prompt": "Replay the source-bound deterministic corpus patch.",
                },
                "executor": {
                    "adapter": "scripted_patch",
                    "patch_path": f"patches/{case.case_id}.diff",
                    "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
                },
            }
            task_path = self.control_root / "tasks" / f"{case.case_id}.json"
            runbook_path = self.control_root / "runbooks" / f"{case.case_id}.json"
            self._write_json(task_path, task)
            self._write_json(runbook_path, runbook)
            runbooks[case.case_id] = runbook_path

        self._git(self.control_root, "init", "-b", "main")
        self._git(self.control_root, "config", "user.name", "JobSlayer Corpus")
        self._git(
            self.control_root,
            "config",
            "user.email",
            "jobslayer-corpus@example.invalid",
        )
        self._git(self.control_root, "add", ".")
        self._git(self.control_root, "commit", "-m", "bind phase0 corpus inputs")
        return runbooks

    def _execute_cases(
        self,
        definition: Phase0CorpusDefinition,
        *,
        runbooks: dict[str, Path],
        coordinator: LocalRunCoordinator,
    ) -> list[dict]:
        summaries: list[dict] = []
        for case in definition.cases:
            summary = coordinator.execute(runbooks[case.case_id])
            if case.action != "verification_failure":
                summary = coordinator.review(
                    summary["run_directory"],
                    actor_type=ActorType.AGENT,
                    actor_id="phase0-corpus-fixture-reviewer",
                    status=ReviewStatus.ACCEPTED,
                    summary="Deterministic fixture patch and verification evidence agree.",
                )
            summaries.append(summary)

        for index, case in enumerate(definition.cases):
            if case.action not in {
                "approve_complete",
                "request_changes",
                "reject",
            }:
                continue
            run_directory = Path(summaries[index]["run_directory"])
            now = datetime.now(UTC)
            card = DecisionCard.model_validate_json(
                (run_directory / "decision-card.json").read_text(encoding="utf-8")
            )
            option = {
                "approve_complete": "approve",
                "request_changes": "request_changes",
                "reject": "reject",
            }[case.action]
            decision = create_human_decision(
                card,
                actor_id=self._FIXTURE_ACTOR,
                selected_option_id=option,
                rationale=(
                    "Deterministic fixture decision; this is not a real human "
                    "experience confirmation."
                ),
            )
            LocalDecisionStore(run_directory / "decision.json").create(decision)
            authority = ApprovalAuthority(
                authorization_id=f"fixture-authority-{case.case_id}",
                actor_id=self._FIXTURE_ACTOR,
                allowed_decision_kinds=(DecisionKind.MERGE_REVIEW,),
                issued_at=now - timedelta(minutes=1),
                valid_until=now + timedelta(hours=1),
            )
            authority_path = (
                self.output_root / "fixture-authorities" / f"{case.case_id}.json"
            )
            authority_path.parent.mkdir(exist_ok=True)
            self._write_json(
                authority_path,
                authority.model_dump(mode="json"),
            )
            summaries[index] = coordinator.apply_decision(
                run_directory,
                authority_path=authority_path,
                now=now,
            )
            if case.action == "approve_complete":
                summaries[index] = coordinator.integrate(run_directory)
                summaries[index] = coordinator.cleanup(run_directory)
        return summaries

    @staticmethod
    def _new_file_patch(case_id: str, content: str) -> bytes:
        path = f"changes/{case_id}.txt"
        return (
            f"diff --git a/{path} b/{path}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{path}\n"
            "@@ -0,0 +1 @@\n"
            f"+{content}\n"
        ).encode("utf-8")

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ("git", "-C", str(repository), *arguments),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return completed.stdout.strip()
