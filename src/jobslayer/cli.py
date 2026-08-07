from __future__ import annotations

import argparse
import hashlib
import json
import sys
import webbrowser
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from jobslayer.adapters.local_decisions import (
    DecisionRecordExistsError,
    DecisionStoreError,
    LocalDecisionStore,
)
from jobslayer.adapters.local_testbed import LocalGitTestbedInspector
from jobslayer.application.local_run import LocalRunCoordinator, LocalRunError
from jobslayer.application.runbook import LocalRunbookLoader, RunbookError
from jobslayer.development.checks import (
    DevelopmentCheckConfigurationError,
    DevelopmentCheckRunner,
    find_repository_root,
)
from jobslayer.domain.models import (
    ActorType,
    CheckResult,
    CheckStatus,
    DecisionCard,
    ReviewStatus,
    TaskSpec,
    TaskState,
    TestbedSpec,
    VerificationReport,
)
from jobslayer.workflow.journal import AuditIntegrityError, JsonlAuditJournal
from jobslayer.workflow.kernel import WorkflowKernel
from jobslayer.supervision.decision import (
    DecisionError,
    create_human_decision,
    render_decision_card,
)
from jobslayer.supervision.session import ReviewSession, ReviewSessionError
from jobslayer.supervision.web import ReviewServerError, create_review_server
from jobslayer.testbeds.inspection import TestbedInspectionError


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


def _cmd_validate_testbed(path: Path) -> int:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        testbed = TestbedSpec.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"testbed is invalid: {exc}", file=sys.stderr)
        return 1
    print(testbed.model_dump_json(indent=2))
    return 0


def _cmd_inspect_testbed(
    path: Path,
    *,
    checkout: Path | None,
    repository_root: Path | None,
) -> int:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        testbed = TestbedSpec.model_validate(raw)
        root = find_repository_root(repository_root)
        checkout_path = checkout
        if checkout_path is None:
            if testbed.local_checkout_hint is None:
                raise TestbedInspectionError(
                    "testbed has no local_checkout_hint; pass --checkout"
                )
            checkout_path = Path(testbed.local_checkout_hint)
            if not checkout_path.is_absolute():
                checkout_path = root / checkout_path
        inspection = LocalGitTestbedInspector(checkout_path).inspect(testbed)
    except (
        OSError,
        json.JSONDecodeError,
        ValidationError,
        DevelopmentCheckConfigurationError,
        TestbedInspectionError,
    ) as exc:
        print(f"testbed inspection failed: {exc}", file=sys.stderr)
        return 1

    payload = inspection.model_dump(mode="json")
    payload["valid_local_baseline"] = inspection.valid_local_baseline
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if inspection.valid_local_baseline else 1


def _cmd_validate_runbook(path: Path, *, repository_root: Path | None) -> int:
    try:
        root = find_repository_root(repository_root)
        prepared = LocalRunbookLoader(root).load(path)
    except (
        OSError,
        ValidationError,
        DevelopmentCheckConfigurationError,
        RunbookError,
    ) as exc:
        print(f"runbook is invalid: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "valid": True,
                "testbed_id": prepared.testbed.testbed_id,
                "task_id": prepared.task.task_id,
                "validation_profile": prepared.validation_profile.profile_id,
                "run_id": prepared.runbook.invocation.run_spec.run_id,
                "executor_type": prepared.runbook.invocation.run_spec.executor_type,
                "patch_sha256": getattr(
                    prepared.runbook.executor, "patch_sha256", None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_verify_journal(path: Path) -> int:
    try:
        records = JsonlAuditJournal(path).read_all()
    except AuditIntegrityError as exc:
        print(f"journal integrity check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"valid": True, "records": len(records)}, indent=2))
    return 0


def _cmd_review_decision(
    path: Path,
    *,
    actor_id: str,
    selected_option_id: str | None,
    rationale: str | None,
    output: Path | None,
) -> int:
    try:
        card = DecisionCard.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        print(f"decision card is invalid: {exc}", file=sys.stderr)
        return 1

    print(render_decision_card(card))
    if selected_option_id is None:
        selected_option_id = input(
            f"选择 option id（默认 {card.default_option_id}，输入 q 取消）: "
        ).strip()
        if selected_option_id.lower() == "q":
            print("decision cancelled; no record was created")
            return 2
        if not selected_option_id:
            selected_option_id = card.default_option_id
    if rationale is None:
        rationale = input("请输入本次决定的理由: ").strip()

    try:
        decision = create_human_decision(
            card,
            actor_id=actor_id,
            selected_option_id=selected_option_id,
            rationale=rationale,
        )
    except DecisionError as exc:
        print(f"decision was rejected: {exc}", file=sys.stderr)
        return 1

    serialized = decision.model_dump_json(indent=2) + "\n"
    if output is not None:
        try:
            LocalDecisionStore(output).create(decision)
        except DecisionRecordExistsError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except DecisionStoreError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"decision record written to {output}")
    else:
        print("\n结构化决定（尚未应用到工作流）:")
        print(serialized, end="")
    return 0


def _cmd_serve_review(
    path: Path,
    *,
    actor_id: str,
    output: Path,
    journal_path: Path | None,
    port: int,
    open_browser: bool,
) -> int:
    try:
        card = DecisionCard.model_validate_json(path.read_text(encoding="utf-8"))
        journal = (
            JsonlAuditJournal(journal_path) if journal_path is not None else None
        )
        session = ReviewSession(
            card=card,
            actor_id=actor_id,
            decision_store=LocalDecisionStore(output),
            journal=journal,
        )
        session.snapshot()
        server = create_review_server(session, port=port)
    except (
        OSError,
        ValidationError,
        AuditIntegrityError,
        DecisionStoreError,
        ReviewSessionError,
        ReviewServerError,
    ) as exc:
        print(f"could not start review UI: {exc}", file=sys.stderr)
        return 1

    host, actual_port = server.server_address[:2]
    url = f"http://{host}:{actual_port}/"
    print(f"local review UI: {url}")
    print(f"decision output: {output}")
    print("identity is declared, not authenticated; decisions are not auto-applied")
    print("press Ctrl+C to stop")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nreview UI stopped")
    finally:
        server.server_close()
    return 0


def _cmd_check(root: Path | None) -> int:
    try:
        repository_root = find_repository_root(root)
        report = DevelopmentCheckRunner(repository_root).run()
    except DevelopmentCheckConfigurationError as exc:
        print(f"development checks could not start: {exc}", file=sys.stderr)
        return 1
    return 0 if report.passed else 1


def _local_run_coordinator(
    *, root: Path | None, state_root: Path | None
) -> LocalRunCoordinator:
    repository_root = find_repository_root(root)
    return LocalRunCoordinator(repository_root, state_root=state_root)


def _cmd_run_task(
    path: Path,
    *,
    root: Path | None,
    state_root: Path | None,
    authorized_by: str | None,
) -> int:
    try:
        summary = _local_run_coordinator(
            root=root, state_root=state_root
        ).execute(path, authorized_by=authorized_by)
    except (RuntimeError, OSError, ValidationError) as exc:
        print(f"task run failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["state"] == TaskState.REVIEWING.value else 1


def _cmd_inspect_run(
    path: Path,
    *,
    root: Path | None,
    state_root: Path | None,
) -> int:
    try:
        summary = _local_run_coordinator(
            root=root, state_root=state_root
        ).inspect(path)
    except (RuntimeError, OSError, ValidationError) as exc:
        print(f"run inspection failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    integrity = (
        summary["record_chain_valid"]
        and summary["audit_chain_valid"]
        and summary["artifacts_valid"]
    )
    return 0 if integrity else 1


def _cmd_review_run(
    path: Path,
    *,
    actor_type: str,
    actor_id: str,
    status: str,
    summary: str,
    findings: tuple[str, ...],
    root: Path | None,
    state_root: Path | None,
) -> int:
    try:
        result = _local_run_coordinator(
            root=root, state_root=state_root
        ).review(
            path,
            actor_type=ActorType(actor_type),
            actor_id=actor_id,
            status=ReviewStatus(status),
            summary=summary,
            findings=findings,
        )
    except (RuntimeError, OSError, ValidationError) as exc:
        print(f"implementation review failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_serve_run(
    path: Path,
    *,
    actor_id: str,
    output: Path | None,
    port: int,
    open_browser: bool,
    root: Path | None,
    state_root: Path | None,
) -> int:
    try:
        summary = _local_run_coordinator(
            root=root, state_root=state_root
        ).inspect(path)
        card_path_raw = summary["decision"]["card_path"]
        if not card_path_raw:
            raise LocalRunError("run has no merge decision card")
        run_directory = Path(summary["run_directory"])
        output_path = output or run_directory / "decision.json"
    except (RuntimeError, OSError, ValidationError) as exc:
        print(f"could not prepare run review UI: {exc}", file=sys.stderr)
        return 1
    return _cmd_serve_review(
        Path(card_path_raw),
        actor_id=actor_id,
        output=output_path,
        journal_path=run_directory / "workflow.jsonl",
        port=port,
        open_browser=open_browser,
    )


def _cmd_apply_run_decision(
    path: Path,
    *,
    authority: Path,
    decision: Path | None,
    root: Path | None,
    state_root: Path | None,
) -> int:
    try:
        result = _local_run_coordinator(
            root=root, state_root=state_root
        ).apply_decision(
            path,
            authority_path=authority,
            decision_path=decision,
        )
    except (RuntimeError, OSError, ValidationError) as exc:
        print(f"decision application failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_integrate_run(
    path: Path,
    *,
    root: Path | None,
    state_root: Path | None,
) -> int:
    try:
        result = _local_run_coordinator(
            root=root, state_root=state_root
        ).integrate(path)
    except (RuntimeError, OSError, ValidationError) as exc:
        print(f"source integration failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["state"] == TaskState.COMPLETED.value else 1


def _cmd_cleanup_run(
    path: Path,
    *,
    root: Path | None,
    state_root: Path | None,
) -> int:
    try:
        result = _local_run_coordinator(
            root=root, state_root=state_root
        ).cleanup(path)
    except (RuntimeError, OSError, ValidationError) as exc:
        print(f"workspace cleanup failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
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
    history = kernel.history(task_id)
    print(
        json.dumps(
            {
                "task_id": task_id,
                "state": kernel.current_state(task_id).value,
                "transitions": len(history),
                "journal": str(path),
                "journal_records": len(journal.read_all()),
                "note": (
                    "control-plane demo stops at merge_review because no external "
                    "repository was integrated"
                ),
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

    validate_testbed = commands.add_parser(
        "validate-testbed", help="validate a TestbedSpec JSON"
    )
    validate_testbed.add_argument("path", type=Path)

    inspect_testbed = commands.add_parser(
        "inspect-testbed",
        help="inspect a registered baseline in a local testbed checkout",
    )
    inspect_testbed.add_argument("path", type=Path)
    inspect_testbed.add_argument(
        "--checkout", type=Path, help="override the registered local checkout hint"
    )
    inspect_testbed.add_argument(
        "--root",
        type=Path,
        help="JobSlayer source checkout used to resolve a relative local hint",
    )

    validate_runbook = commands.add_parser(
        "validate-runbook",
        help="validate a source-controlled local runbook and all references",
    )
    validate_runbook.add_argument("path", type=Path)
    validate_runbook.add_argument("--root", type=Path)

    verify = commands.add_parser("verify-journal", help="verify an audit hash chain")
    verify.add_argument("path", type=Path)

    review = commands.add_parser(
        "review-decision",
        help="inspect a decision card and produce a human decision record",
    )
    review.add_argument("path", type=Path)
    review.add_argument("--actor-id", required=True)
    review.add_argument("--select", dest="selected_option_id")
    review.add_argument("--rationale")
    review.add_argument("--output", type=Path)

    serve_review = commands.add_parser(
        "serve-review",
        aliases=["ui"],
        help="serve a loopback-only visual UI for one decision card",
    )
    serve_review.add_argument("path", type=Path)
    serve_review.add_argument("--actor-id", required=True)
    serve_review.add_argument("--output", type=Path, required=True)
    serve_review.add_argument("--journal", type=Path)
    serve_review.add_argument("--port", type=int, default=8765)
    serve_review.add_argument("--open-browser", action="store_true")

    check = commands.add_parser(
        "check",
        help="run the complete governed development verification sequence",
    )
    check.add_argument(
        "--root",
        type=Path,
        help="JobSlayer source checkout; auto-detected by default",
    )

    run_task = commands.add_parser(
        "run-task",
        help="execute one source-controlled local Phase 0 runbook",
    )
    run_task.add_argument("path", type=Path)
    run_task.add_argument("--root", type=Path)
    run_task.add_argument("--state-root", type=Path)
    run_task.add_argument(
        "--authorized-by",
        help="declared human actor authorizing one real codex_cli execution",
    )

    inspect_run = commands.add_parser(
        "inspect-run",
        help="verify and summarize a persisted local task run",
    )
    inspect_run.add_argument("path", type=Path)
    inspect_run.add_argument("--root", type=Path)
    inspect_run.add_argument("--state-root", type=Path)

    review_run = commands.add_parser(
        "review-run",
        help="record an agent or human implementation review for a verified run",
    )
    review_run.add_argument("path", type=Path)
    review_run.add_argument(
        "--actor-type",
        choices=[ActorType.AGENT.value, ActorType.HUMAN.value],
        required=True,
    )
    review_run.add_argument("--actor-id", required=True)
    review_run.add_argument(
        "--status",
        choices=[item.value for item in ReviewStatus],
        required=True,
    )
    review_run.add_argument("--summary", required=True)
    review_run.add_argument("--finding", action="append", default=[])
    review_run.add_argument("--root", type=Path)
    review_run.add_argument("--state-root", type=Path)

    run_ui = commands.add_parser(
        "run-ui",
        help="serve the real merge decision generated by a persisted run",
    )
    run_ui.add_argument("path", type=Path)
    run_ui.add_argument("--actor-id", required=True)
    run_ui.add_argument("--output", type=Path)
    run_ui.add_argument("--port", type=int, default=8765)
    run_ui.add_argument("--open-browser", action="store_true")
    run_ui.add_argument("--root", type=Path)
    run_ui.add_argument("--state-root", type=Path)

    apply_run_decision = commands.add_parser(
        "apply-run-decision",
        help="apply a recorded run decision using an external approval authority",
    )
    apply_run_decision.add_argument("path", type=Path)
    apply_run_decision.add_argument("--authority", type=Path, required=True)
    apply_run_decision.add_argument("--decision", type=Path)
    apply_run_decision.add_argument("--root", type=Path)
    apply_run_decision.add_argument("--state-root", type=Path)

    integrate_run = commands.add_parser(
        "integrate-run",
        help="commit an approved patch and fast-forward its local target branch",
    )
    integrate_run.add_argument("path", type=Path)
    integrate_run.add_argument("--root", type=Path)
    integrate_run.add_argument("--state-root", type=Path)

    cleanup_run = commands.add_parser(
        "cleanup-run",
        help="remove a completed clean worktree while preserving its branch",
    )
    cleanup_run.add_argument("path", type=Path)
    cleanup_run.add_argument("--root", type=Path)
    cleanup_run.add_argument("--state-root", type=Path)

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
    if arguments.command == "validate-testbed":
        return _cmd_validate_testbed(arguments.path)
    if arguments.command == "inspect-testbed":
        return _cmd_inspect_testbed(
            arguments.path,
            checkout=arguments.checkout,
            repository_root=arguments.root,
        )
    if arguments.command == "validate-runbook":
        return _cmd_validate_runbook(
            arguments.path,
            repository_root=arguments.root,
        )
    if arguments.command == "verify-journal":
        return _cmd_verify_journal(arguments.path)
    if arguments.command == "review-decision":
        return _cmd_review_decision(
            arguments.path,
            actor_id=arguments.actor_id,
            selected_option_id=arguments.selected_option_id,
            rationale=arguments.rationale,
            output=arguments.output,
        )
    if arguments.command in {"serve-review", "ui"}:
        return _cmd_serve_review(
            arguments.path,
            actor_id=arguments.actor_id,
            output=arguments.output,
            journal_path=arguments.journal,
            port=arguments.port,
            open_browser=arguments.open_browser,
        )
    if arguments.command == "check":
        return _cmd_check(arguments.root)
    if arguments.command == "run-task":
        return _cmd_run_task(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
            authorized_by=arguments.authorized_by,
        )
    if arguments.command == "inspect-run":
        return _cmd_inspect_run(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
        )
    if arguments.command == "review-run":
        return _cmd_review_run(
            arguments.path,
            actor_type=arguments.actor_type,
            actor_id=arguments.actor_id,
            status=arguments.status,
            summary=arguments.summary,
            findings=tuple(arguments.finding),
            root=arguments.root,
            state_root=arguments.state_root,
        )
    if arguments.command == "run-ui":
        return _cmd_serve_run(
            arguments.path,
            actor_id=arguments.actor_id,
            output=arguments.output,
            port=arguments.port,
            open_browser=arguments.open_browser,
            root=arguments.root,
            state_root=arguments.state_root,
        )
    if arguments.command == "apply-run-decision":
        return _cmd_apply_run_decision(
            arguments.path,
            authority=arguments.authority,
            decision=arguments.decision,
            root=arguments.root,
            state_root=arguments.state_root,
        )
    if arguments.command == "integrate-run":
        return _cmd_integrate_run(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
        )
    if arguments.command == "cleanup-run":
        return _cmd_cleanup_run(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
        )
    if arguments.command == "demo":
        return _cmd_demo(arguments.journal)
    raise AssertionError(f"unhandled command: {arguments.command}")
