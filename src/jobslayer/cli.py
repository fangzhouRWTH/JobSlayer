from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from jobslayer.domain.models import (
    ActorType,
    CheckResult,
    CheckStatus,
    TaskSpec,
    TaskState,
    VerificationReport,
)
from jobslayer.workflow.journal import AuditIntegrityError, JsonlAuditJournal
from jobslayer.workflow.kernel import WorkflowKernel


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cmd_validate_task(path: Path) -> int:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        task = TaskSpec.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"task is invalid: {exc}", file=sys.stderr)
        return 1
    print(task.model_dump_json(indent=2))
    return 0


def _cmd_verify_journal(path: Path) -> int:
    try:
        records = JsonlAuditJournal(path).read_all()
    except AuditIntegrityError as exc:
        print(f"journal integrity check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"valid": True, "records": len(records)}, indent=2))
    return 0


def _cmd_demo(path: Path) -> int:
    task_id = f"demo-{uuid4().hex[:12]}"
    journal = JsonlAuditJournal(path)
    kernel = WorkflowKernel(journal)

    kernel.transition(
        task_id=task_id,
        to_state=TaskState.PLANNED,
        actor_type=ActorType.SYSTEM,
        actor_id="demo-controller",
        reason="typed plan produced",
        evidence_ids=("plan-demo-v1",),
    )
    kernel.transition(
        task_id=task_id,
        to_state=TaskState.IMPLEMENTING,
        actor_type=ActorType.POLICY,
        actor_id="low-risk-policy-v1",
        reason="low-risk plan automatically approved",
    )
    kernel.transition(
        task_id=task_id,
        to_state=TaskState.VERIFYING,
        actor_type=ActorType.AGENT,
        actor_id="demo-executor",
        reason="patch artifact produced",
        evidence_ids=("patch-demo-v1",),
    )

    report = VerificationReport(
        report_id=f"verify-{uuid4().hex[:12]}",
        task_id=task_id,
        source_commit="0123456789abcdef0123456789abcdef01234567",
        checks=(
            CheckResult(
                check_id="unit-tests",
                status=CheckStatus.PASSED,
                command=("python", "-m", "unittest"),
                summary="demonstration verification passed",
                evidence_hash=_digest("demonstration verification passed"),
            ),
        ),
        required_checks_passed=True,
    )
    kernel.transition(
        task_id=task_id,
        to_state=TaskState.REVIEWING,
        actor_type=ActorType.SYSTEM,
        actor_id="verification-engine",
        reason="all required deterministic checks passed",
        verification_report=report,
    )
    kernel.transition(
        task_id=task_id,
        to_state=TaskState.MERGE_REVIEW,
        actor_type=ActorType.AGENT,
        actor_id="independent-reviewer",
        reason="diff and evidence review accepted",
        evidence_ids=("review-demo-v1",),
    )
    kernel.transition(
        task_id=task_id,
        to_state=TaskState.COMPLETED,
        actor_type=ActorType.HUMAN,
        actor_id="demo-human-approver",
        reason="merge proposal approved for demonstration",
        verification_report=report,
    )

    history = kernel.history(task_id)
    print(
        json.dumps(
            {
                "task_id": task_id,
                "state": kernel.current_state(task_id).value,
                "transitions": len(history),
                "journal": str(path),
                "journal_records": len(journal.read_all()),
                "note": "control-plane demo only; no external repository was changed",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobslayer",
        description="Governed AI-collaborative engineering control plane",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-task", help="validate a TaskSpec JSON")
    validate.add_argument("path", type=Path)

    verify = commands.add_parser("verify-journal", help="verify an audit hash chain")
    verify.add_argument("path", type=Path)

    demo = commands.add_parser("demo", help="run a no-side-effect workflow demo")
    demo.add_argument(
        "--journal",
        type=Path,
        default=Path(".jobslayer/demo.jsonl"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "validate-task":
        return _cmd_validate_task(arguments.path)
    if arguments.command == "verify-journal":
        return _cmd_verify_journal(arguments.path)
    if arguments.command == "demo":
        return _cmd_demo(arguments.journal)
    raise AssertionError(f"unhandled command: {arguments.command}")

