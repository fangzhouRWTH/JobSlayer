from __future__ import annotations

import argparse
import hashlib
import json
import sys
import webbrowser
from datetime import timedelta
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
from jobslayer.application.executor_comparison import ExecutorComparisonEvaluator
from jobslayer.application.phase0_corpus import Phase0CorpusBuilder, Phase0CorpusError
from jobslayer.application.readiness import (
    Phase0ReadinessEvaluator,
    ReadinessInspectionError,
)
from jobslayer.adapters.local_recovery import LocalRunRecoveryManager
from jobslayer.adapters.local_identity import (
    LocalIdentityError,
    LocalIdentityProvider,
    RoleBasedAuthorizer,
)
from jobslayer.adapters.local_management import LocalManagementQuery
from jobslayer.adapters.local_orchestration import (
    LocalTaskPlanStore,
    TaskPlanJournalError,
)
from jobslayer.adapters.local_planning_agent import LocalPlanningAgent
from jobslayer.adapters.codex_planning_agent import (
    CodexPlanningAgent,
    CodexPlanningAgentConfigurationError,
)
from jobslayer.adapters.persistent_management import PersistentManagementQuery
from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.sqlite_state import SqliteControlPlaneStore
from jobslayer.application.runbook import LocalRunbookLoader, RunbookError
from jobslayer.application.task_orchestration import TaskOrchestrationService
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
    DecisionKind,
    HumanDecision,
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
from jobslayer.recovery import RecoveryError, RecoveryStatus
from jobslayer.identity import (
    AuthenticatedPrincipal,
    AuthorizationAction,
    AuthorizationRequest,
)
from jobslayer.management import ManagementQueryError
from jobslayer.management.web import (
    ManagementServerError,
    create_management_server,
)
from jobslayer.orchestration.web import (
    TaskOrchestrationServerError,
    create_task_orchestration_server,
)
from jobslayer.orchestration import PlanningAgent
from jobslayer.evaluation import ExecutorComparisonError


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _authenticated_principal(
    *,
    identity_session: Path | None,
    identity_key: Path | None,
    action: AuthorizationAction,
    task_id: str | None = None,
    run_id: str | None = None,
) -> AuthenticatedPrincipal:
    if identity_session is None or identity_key is None:
        raise LocalIdentityError(
            "protected operation requires both --identity-session and --identity-key"
        )
    principal = LocalIdentityProvider(identity_key).load_session(identity_session)
    verdict = RoleBasedAuthorizer().authorize(
        AuthorizationRequest(
            principal=principal,
            action=action,
            task_id=task_id,
            run_id=run_id,
        )
    )
    if not verdict.permitted:
        raise LocalIdentityError(f"authorization denied: {verdict.reason}")
    return principal


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
    identity_session: Path,
    identity_key: Path,
    selected_option_id: str | None,
    rationale: str | None,
    output: Path | None,
) -> int:
    try:
        card = DecisionCard.model_validate_json(path.read_text(encoding="utf-8"))
        principal = _authenticated_principal(
            identity_session=identity_session,
            identity_key=identity_key,
            action=AuthorizationAction.RECORD_DECISION,
            task_id=card.task_id,
        )
    except (OSError, ValidationError, LocalIdentityError) as exc:
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
            actor_id=principal.subject_id,
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
    identity_session: Path,
    identity_key: Path,
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
        principal = LocalIdentityProvider(identity_key).load_session(identity_session)
        session = ReviewSession(
            card=card,
            principal=principal,
            authorizer=RoleBasedAuthorizer(),
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
        LocalIdentityError,
    ) as exc:
        print(f"could not start review UI: {exc}", file=sys.stderr)
        return 1

    host, actual_port = server.server_address[:2]
    url = f"http://{host}:{actual_port}/"
    print(f"local review UI: {url}")
    print(f"decision output: {output}")
    print(
        f"authenticated subject: {session.principal.subject_id} "
        f"(session {session.principal.session_id})"
    )
    print("RBAC permits recording only; decisions are not auto-applied")
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
    *,
    root: Path | None,
    state_root: Path | None,
    execution_authority_verifier=None,
) -> LocalRunCoordinator:
    repository_root = find_repository_root(root)
    return LocalRunCoordinator(
        repository_root,
        state_root=state_root,
        execution_authority_verifier=execution_authority_verifier,
    )


def _cmd_run_task(
    path: Path,
    *,
    root: Path | None,
    state_root: Path | None,
    identity_session: Path | None,
    identity_key: Path | None,
) -> int:
    try:
        coordinator = _local_run_coordinator(root=root, state_root=state_root)
        prepared = LocalRunbookLoader(coordinator.repository_root).load(path)
        execution_authorization = None
        if prepared.runbook.executor.adapter == "codex_cli":
            if identity_session is None or identity_key is None:
                raise LocalIdentityError(
                    "codex_cli execution requires both --identity-session and --identity-key"
                )
            provider = LocalIdentityProvider(identity_key)
            signed_session = provider.load_signed_session(identity_session)
            execution_authorization = provider.issue_execution_authorization(
                signed_session,
                task_id=prepared.task.task_id,
                run_id=prepared.runbook.invocation.run_spec.run_id,
                maximum_risk=prepared.task.risk,
                lifetime=timedelta(
                    seconds=max(
                        15 * 60,
                        prepared.runbook.invocation.run_spec.timeout_seconds + 60,
                    )
                ),
            )
            coordinator = _local_run_coordinator(
                root=root,
                state_root=state_root,
                execution_authority_verifier=(
                    lambda authority, task_id, run_id, now: provider.verify_execution_authorization(
                        authority,
                        task_id=task_id,
                        run_id=run_id,
                        now=now,
                    )
                ),
            )
        elif identity_session is not None or identity_key is not None:
            raise LocalIdentityError(
                "scripted replay uses policy authorization and accepts no identity session"
            )
        summary = coordinator.execute(
            path,
            execution_authorization=execution_authorization,
        )
    except (RuntimeError, OSError, ValidationError, LocalIdentityError) as exc:
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


def _cmd_inspect_readiness(
    *,
    root: Path | None,
    state_root: Path | None,
    required_reviewed_tasks: int,
) -> int:
    try:
        coordinator = _local_run_coordinator(root=root, state_root=state_root)
        report = Phase0ReadinessEvaluator(
            coordinator,
            state_root=coordinator.state_root,
            required_reviewed_tasks=required_reviewed_tasks,
        ).evaluate()
    except (
        DevelopmentCheckConfigurationError,
        ReadinessInspectionError,
        OSError,
        ValueError,
    ) as exc:
        print(f"readiness inspection failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.automated_gate_passes else 1


def _cmd_compare_executors(run_directories: tuple[Path, ...]) -> int:
    try:
        report = ExecutorComparisonEvaluator().evaluate_runs(run_directories)
    except (ExecutorComparisonError, OSError, ValueError) as exc:
        print(f"executor comparison failed: {exc}", file=sys.stderr)
        return 1
    print(report.model_dump_json(indent=2))
    return 0


def _cmd_build_phase0_corpus(
    *,
    root: Path | None,
    definition: Path | None,
    output_root: Path | None,
) -> int:
    try:
        repository_root = find_repository_root(root)
        definition_path = definition or (
            repository_root / "corpora" / "phase0-foundation-v1.json"
        )
        if not definition_path.is_absolute():
            definition_path = repository_root / definition_path
        corpus_root = output_root or (
            repository_root / ".jobslayer" / "phase0-corpus"
        )
        if not corpus_root.is_absolute():
            corpus_root = repository_root / corpus_root
        report = Phase0CorpusBuilder(definition_path, corpus_root).build()
    except (
        DevelopmentCheckConfigurationError,
        Phase0CorpusError,
        OSError,
        ValueError,
    ) as exc:
        print(f"Phase 0 corpus build failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_serve_dashboard(
    *,
    root: Path | None,
    state_root: Path | None,
    identity_session: Path,
    identity_key: Path,
    control_plane_db: Path | None,
    artifact_root: Path | None,
    port: int,
    open_browser: bool,
) -> int:
    try:
        principal = _authenticated_principal(
            identity_session=identity_session,
            identity_key=identity_key,
            action=AuthorizationAction.VIEW_CONTROL_PLANE,
        )
        if control_plane_db is None:
            if artifact_root is not None:
                raise ManagementQueryError(
                    "--artifact-root requires --control-plane-db"
                )
            coordinator = _local_run_coordinator(root=root, state_root=state_root)
            query = LocalManagementQuery(coordinator)
        else:
            if artifact_root is None:
                raise ManagementQueryError(
                    "--control-plane-db requires --artifact-root"
                )
            database = control_plane_db.resolve(strict=True)
            artifacts_path = artifact_root.resolve(strict=True)
            if database.is_symlink() or not database.is_file():
                raise ManagementQueryError(
                    "control-plane database must be an existing regular file"
                )
            if artifacts_path.is_symlink() or not artifacts_path.is_dir():
                raise ManagementQueryError(
                    "artifact root must be an existing real directory"
                )
            query = PersistentManagementQuery(
                SqliteControlPlaneStore(database),
                LocalArtifactRegistry(artifacts_path),
                source_name=f"sqlite://{database}",
            )
        query.snapshot()
        server = create_management_server(query, principal, port=port)
    except (
        DevelopmentCheckConfigurationError,
        LocalIdentityError,
        ManagementQueryError,
        ManagementServerError,
        OSError,
        ValueError,
    ) as exc:
        print(f"could not start management dashboard: {exc}", file=sys.stderr)
        return 1
    host, actual_port = server.server_address[:2]
    url = f"http://{host}:{actual_port}/"
    print(f"Agent management dashboard: {url}")
    print(f"authenticated subject: {principal.subject_id}")
    print("read-only persisted-event view; press Ctrl+C to stop")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nmanagement dashboard stopped")
    finally:
        server.server_close()
    return 0


def _cmd_serve_task_orchestration(
    *,
    root: Path | None,
    state_root: Path | None,
    identity_session: Path,
    identity_key: Path,
    planning_agent_name: str,
    allow_external_planning_agent: bool,
    planning_artifact_root: Path | None,
    codex_binary: str,
    codex_model: str | None,
    codex_timeout_seconds: float,
    port: int,
    open_browser: bool,
) -> int:
    try:
        repository_root = find_repository_root(root)
        principal = _authenticated_principal(
            identity_session=identity_session,
            identity_key=identity_key,
            action=AuthorizationAction.MANAGE_TASK_PLAN,
        )
        plan_root = state_root or (repository_root / ".jobslayer" / "orchestration")
        if not plan_root.is_absolute():
            plan_root = repository_root / plan_root
        artifact_root = planning_artifact_root or (plan_root / "artifacts")
        if not artifact_root.is_absolute():
            artifact_root = repository_root / artifact_root
        planning_agent = _planning_agent_for(
            planning_agent_name,
            repository_root=repository_root,
            artifact_root=artifact_root,
            allow_external=allow_external_planning_agent,
            codex_binary=codex_binary,
            codex_model=codex_model,
            codex_timeout_seconds=codex_timeout_seconds,
        )
        service = TaskOrchestrationService(
            LocalTaskPlanStore(plan_root),
            planning_agent,
            actor_id=principal.subject_id,
        )
        service.list_latest()
        server = create_task_orchestration_server(service, principal, port=port)
    except (
        DevelopmentCheckConfigurationError,
        LocalIdentityError,
        TaskPlanJournalError,
        TaskOrchestrationServerError,
        CodexPlanningAgentConfigurationError,
        OSError,
        ValueError,
    ) as exc:
        print(f"could not start task orchestration API: {exc}", file=sys.stderr)
        return 1
    host, actual_port = server.server_address[:2]
    api_url = f"http://{host}:{actual_port}/api/orchestration"
    ui_url = "http://127.0.0.1:4173/#/orchestration"
    print(f"Task orchestration API: {api_url}")
    print(f"authenticated planner: {principal.subject_id}")
    print(f"planning adapter: {planning_agent.adapter_id}")
    print("append-only plan revisions; agent output remains a pending proposal")
    print("Workbench: sh ./init.sh -- npm --prefix ui-framework run dev")
    print(f"Open after Vite starts: {ui_url}")
    if open_browser:
        webbrowser.open(ui_url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\ntask orchestration API stopped")
    finally:
        server.server_close()
    return 0


def _planning_agent_for(
    name: str,
    *,
    repository_root: Path,
    artifact_root: Path,
    allow_external: bool,
    codex_binary: str,
    codex_model: str | None,
    codex_timeout_seconds: float,
) -> PlanningAgent:
    if name == "local":
        return LocalPlanningAgent()
    if name != "codex":
        raise ValueError(f"unknown planning agent: {name}")
    if not allow_external:
        raise ValueError(
            "Codex planning requires --allow-external-planning-agent"
        )
    if codex_model is None or not codex_model.strip():
        raise ValueError("Codex planning requires an explicit --codex-model")
    return CodexPlanningAgent(
        repository_root,
        LocalArtifactRegistry(artifact_root),
        external_call_authorized=True,
        codex_binary=codex_binary,
        model=codex_model,
        timeout_seconds=codex_timeout_seconds,
    )


def _cmd_create_identity_key(path: Path) -> int:
    try:
        key_id = LocalIdentityProvider(path).create_key()
    except LocalIdentityError as exc:
        print(f"identity key creation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"created": True, "key_id": key_id, "path": str(path)}, indent=2))
    return 0


def _cmd_issue_identity_session(
    *,
    key: Path,
    subject_id: str,
    display_name: str,
    roles: tuple[str, ...],
    lifetime_minutes: int,
    output: Path,
) -> int:
    try:
        session = LocalIdentityProvider(key).issue(
            subject_id=subject_id,
            display_name=display_name,
            roles=roles,
            lifetime=timedelta(minutes=lifetime_minutes),
        )
        LocalIdentityProvider.create_session_file(output, session)
    except (LocalIdentityError, ValidationError, ValueError) as exc:
        print(f"identity session issuance failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "created": True,
                "session_id": session.principal.session_id,
                "subject_id": session.principal.subject_id,
                "roles": list(session.principal.roles),
                "valid_until": session.principal.valid_until.isoformat(),
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_issue_approval_authority(
    *,
    key: Path,
    identity_session: Path,
    decision_kinds: tuple[str, ...],
    lifetime_minutes: int,
    output: Path,
) -> int:
    try:
        provider = LocalIdentityProvider(key)
        session = provider.load_signed_session(identity_session)
        authority = provider.issue_approval_authority(
            session,
            allowed_decision_kinds=tuple(
                DecisionKind(value) for value in decision_kinds
            ),
            lifetime=timedelta(minutes=lifetime_minutes),
        )
        provider.create_approval_authority_file(output, authority)
    except (LocalIdentityError, ValidationError, ValueError) as exc:
        print(f"approval authority issuance failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "created": True,
                "authorization_id": authority.authorization_id,
                "actor_id": authority.actor_id,
                "allowed_decision_kinds": [
                    item.value for item in authority.allowed_decision_kinds
                ],
                "valid_until": authority.valid_until.isoformat(),
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_inspect_recovery(
    path: Path,
    *,
    root: Path | None,
    state_root: Path | None,
) -> int:
    try:
        manager = LocalRunRecoveryManager(
            _local_run_coordinator(root=root, state_root=state_root)
        )
        assessment = manager.assess(path)
    except (DevelopmentCheckConfigurationError, OSError, ValueError) as exc:
        print(f"recovery inspection failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(assessment.to_dict(), ensure_ascii=False, indent=2))
    return 0 if assessment.status is RecoveryStatus.CONSISTENT else 1


def _cmd_recover_run(
    path: Path,
    *,
    root: Path | None,
    state_root: Path | None,
    identity_session: Path,
    identity_key: Path,
) -> int:
    try:
        _authenticated_principal(
            identity_session=identity_session,
            identity_key=identity_key,
            action=AuthorizationAction.RECOVER_RUN,
            run_id=path.name,
        )
        manager = LocalRunRecoveryManager(
            _local_run_coordinator(root=root, state_root=state_root)
        )
        assessment = manager.recover(path)
    except (
        DevelopmentCheckConfigurationError,
        RecoveryError,
        OSError,
        ValueError,
        LocalIdentityError,
    ) as exc:
        print(f"run recovery failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(assessment.to_dict(), ensure_ascii=False, indent=2))
    return 0 if assessment.status is RecoveryStatus.CONSISTENT else 1


def _cmd_review_run(
    path: Path,
    *,
    identity_session: Path,
    identity_key: Path,
    status: str,
    summary: str,
    findings: tuple[str, ...],
    root: Path | None,
    state_root: Path | None,
) -> int:
    try:
        coordinator = _local_run_coordinator(root=root, state_root=state_root)
        current = coordinator.inspect(path)
        principal = _authenticated_principal(
            identity_session=identity_session,
            identity_key=identity_key,
            action=AuthorizationAction.REVIEW_IMPLEMENTATION,
            task_id=current["task_id"],
            run_id=current["run_id"],
        )
        result = coordinator.review(
            path,
            actor_type=ActorType.HUMAN,
            actor_id=principal.subject_id,
            status=ReviewStatus(status),
            summary=summary,
            findings=findings,
        )
    except (RuntimeError, OSError, ValidationError, LocalIdentityError) as exc:
        print(f"implementation review failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_serve_run(
    path: Path,
    *,
    identity_session: Path,
    identity_key: Path,
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
        identity_session=identity_session,
        identity_key=identity_key,
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
    identity_session: Path,
    identity_key: Path,
) -> int:
    try:
        coordinator = _local_run_coordinator(root=root, state_root=state_root)
        current = coordinator.inspect(path)
        decision_file = decision or Path(current["run_directory"]) / "decision.json"
        recorded_decision = HumanDecision.model_validate_json(
            decision_file.read_text(encoding="utf-8")
        )
        principal = _authenticated_principal(
            identity_session=identity_session,
            identity_key=identity_key,
            action=AuthorizationAction.APPLY_DECISION,
            task_id=current["task_id"],
            run_id=current["run_id"],
        )
        if recorded_decision.actor_id != principal.subject_id:
            raise LocalIdentityError(
                "authenticated subject does not own the recorded decision"
            )
        verified_authority = LocalIdentityProvider(
            identity_key
        ).load_approval_authority(authority)
        if verified_authority.actor_id != principal.subject_id:
            raise LocalIdentityError(
                "verified approval authority belongs to another subject"
            )
        result = coordinator.apply_decision(
            path,
            authority_path=authority,
            decision_path=decision_file,
        )
    except (RuntimeError, OSError, ValidationError, LocalIdentityError) as exc:
        print(f"decision application failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_integrate_run(
    path: Path,
    *,
    root: Path | None,
    state_root: Path | None,
    identity_session: Path,
    identity_key: Path,
) -> int:
    try:
        coordinator = _local_run_coordinator(root=root, state_root=state_root)
        current = coordinator.inspect(path)
        decision = HumanDecision.model_validate_json(
            (Path(current["run_directory"]) / "decision.json").read_text(
                encoding="utf-8"
            )
        )
        principal = _authenticated_principal(
            identity_session=identity_session,
            identity_key=identity_key,
            action=AuthorizationAction.INTEGRATE_SOURCE,
            task_id=current["task_id"],
            run_id=current["run_id"],
        )
        if decision.actor_id != principal.subject_id:
            raise LocalIdentityError(
                "authenticated subject does not own the integration approval"
            )
        result = coordinator.integrate(path)
    except (RuntimeError, OSError, ValidationError, LocalIdentityError) as exc:
        print(f"source integration failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["state"] == TaskState.COMPLETED.value else 1


def _cmd_cleanup_run(
    path: Path,
    *,
    root: Path | None,
    state_root: Path | None,
    identity_session: Path,
    identity_key: Path,
) -> int:
    try:
        coordinator = _local_run_coordinator(root=root, state_root=state_root)
        current = coordinator.inspect(path)
        decision = HumanDecision.model_validate_json(
            (Path(current["run_directory"]) / "decision.json").read_text(
                encoding="utf-8"
            )
        )
        principal = _authenticated_principal(
            identity_session=identity_session,
            identity_key=identity_key,
            action=AuthorizationAction.CLEANUP_WORKSPACE,
            task_id=current["task_id"],
            run_id=current["run_id"],
        )
        if decision.actor_id != principal.subject_id:
            raise LocalIdentityError(
                "authenticated subject does not own the completed approval"
            )
        result = coordinator.cleanup(path)
    except (RuntimeError, OSError, ValidationError, LocalIdentityError) as exc:
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
    review.add_argument("--identity-session", type=Path, required=True)
    review.add_argument("--identity-key", type=Path, required=True)
    review.add_argument("--select", dest="selected_option_id")
    review.add_argument("--rationale")
    review.add_argument("--output", type=Path)

    serve_review = commands.add_parser(
        "serve-review",
        aliases=["ui"],
        help="serve a loopback-only visual UI for one decision card",
    )
    serve_review.add_argument("path", type=Path)
    serve_review.add_argument("--identity-session", type=Path, required=True)
    serve_review.add_argument("--identity-key", type=Path, required=True)
    serve_review.add_argument("--output", type=Path, required=True)
    serve_review.add_argument("--journal", type=Path)
    serve_review.add_argument("--port", type=int, default=8765)
    serve_review.add_argument("--open-browser", action="store_true")

    dashboard = commands.add_parser(
        "serve-dashboard",
        aliases=["dashboard"],
        help="serve the authenticated read-only Agent management dashboard",
    )
    dashboard.add_argument("--root", type=Path)
    dashboard.add_argument("--state-root", type=Path)
    dashboard.add_argument(
        "--control-plane-db",
        type=Path,
        help="existing transactional SQLite control-plane database",
    )
    dashboard.add_argument(
        "--artifact-root",
        type=Path,
        help="artifact registry paired with --control-plane-db",
    )
    dashboard.add_argument("--identity-session", type=Path, required=True)
    dashboard.add_argument("--identity-key", type=Path, required=True)
    dashboard.add_argument("--port", type=int, default=8770)
    dashboard.add_argument("--open-browser", action="store_true")

    orchestration = commands.add_parser(
        "serve-task-orchestration",
        aliases=["orchestration-api"],
        help="serve the authenticated task discussion and graph-planning API",
    )
    orchestration.add_argument("--root", type=Path)
    orchestration.add_argument(
        "--state-root",
        type=Path,
        help="append-only task-plan journal root",
    )
    orchestration.add_argument("--identity-session", type=Path, required=True)
    orchestration.add_argument("--identity-key", type=Path, required=True)
    orchestration.add_argument(
        "--planning-agent",
        choices=("local", "codex"),
        default="local",
        help="proposal adapter; local remains deterministic and offline",
    )
    orchestration.add_argument(
        "--allow-external-planning-agent",
        action="store_true",
        help="explicitly authorize external model calls for this server process",
    )
    orchestration.add_argument(
        "--planning-artifact-root",
        type=Path,
        help="immutable raw planning-interaction artifact registry",
    )
    orchestration.add_argument(
        "--codex-binary",
        default="codex",
        help="Codex CLI executable used only with --planning-agent codex",
    )
    orchestration.add_argument(
        "--codex-model",
        help="explicit Codex model used only with --planning-agent codex",
    )
    orchestration.add_argument(
        "--codex-timeout-seconds",
        type=float,
        default=120,
        help="single-attempt Codex planning timeout (1-900 seconds)",
    )
    orchestration.add_argument("--port", type=int, default=8780)
    orchestration.add_argument("--open-browser", action="store_true")

    create_identity_key = commands.add_parser(
        "create-local-identity-key",
        help="create one protected local operator-session signing key",
    )
    create_identity_key.add_argument("path", type=Path)

    issue_identity = commands.add_parser(
        "issue-local-identity-session",
        help="issue one short-lived signed local operator session",
    )
    issue_identity.add_argument("--key", type=Path, required=True)
    issue_identity.add_argument("--subject-id", required=True)
    issue_identity.add_argument("--display-name", required=True)
    issue_identity.add_argument(
        "--role",
        action="append",
        choices=[
            "observer",
            "executor",
            "reviewer",
            "approver",
            "worker-admin",
            "planner",
            "operator-admin",
        ],
        required=True,
    )
    issue_identity.add_argument("--lifetime-minutes", type=int, default=30)
    issue_identity.add_argument("--output", type=Path, required=True)

    issue_authority = commands.add_parser(
        "issue-approval-authority",
        help="issue one short-lived verifiable approval authority",
    )
    issue_authority.add_argument("--key", type=Path, required=True)
    issue_authority.add_argument("--identity-session", type=Path, required=True)
    issue_authority.add_argument(
        "--decision-kind",
        action="append",
        choices=[item.value for item in DecisionKind],
        required=True,
    )
    issue_authority.add_argument("--lifetime-minutes", type=int, default=15)
    issue_authority.add_argument("--output", type=Path, required=True)

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
    run_task.add_argument("--identity-session", type=Path)
    run_task.add_argument("--identity-key", type=Path)

    inspect_run = commands.add_parser(
        "inspect-run",
        help="verify and summarize a persisted local task run",
    )
    inspect_run.add_argument("path", type=Path)
    inspect_run.add_argument("--root", type=Path)
    inspect_run.add_argument("--state-root", type=Path)

    inspect_readiness = commands.add_parser(
        "inspect-readiness",
        help="inspect the evidence-backed Phase 0 run-corpus readiness gate",
    )
    inspect_readiness.add_argument("--root", type=Path)
    inspect_readiness.add_argument("--state-root", type=Path)
    inspect_readiness.add_argument(
        "--required-reviewed-tasks",
        type=int,
        default=20,
        help="minimum number of distinct integrity-verified reviewed tasks (default: 20)",
    )

    compare_executors = commands.add_parser(
        "compare-executors",
        help="compare two or more executor runs under exact shared contracts",
    )
    compare_executors.add_argument(
        "--run",
        dest="run_directories",
        action="append",
        type=Path,
        required=True,
        help="persisted run directory; provide at least two",
    )

    build_corpus = commands.add_parser(
        "build-phase0-corpus",
        help="build the source-defined deterministic Phase 0 evidence corpus",
    )
    build_corpus.add_argument("--root", type=Path)
    build_corpus.add_argument(
        "--definition",
        type=Path,
        help="corpus definition; defaults to corpora/phase0-foundation-v1.json",
    )
    build_corpus.add_argument(
        "--output-root",
        type=Path,
        help="new output directory; defaults to .jobslayer/phase0-corpus",
    )

    inspect_recovery = commands.add_parser(
        "inspect-recovery",
        help="classify one persisted run without changing workflow state",
    )
    inspect_recovery.add_argument("path", type=Path)
    inspect_recovery.add_argument("--root", type=Path)
    inspect_recovery.add_argument("--state-root", type=Path)

    recover_run = commands.add_parser(
        "recover-run",
        help="apply one supported evidence-backed idempotent run repair",
    )
    recover_run.add_argument("path", type=Path)
    recover_run.add_argument("--root", type=Path)
    recover_run.add_argument("--state-root", type=Path)
    recover_run.add_argument("--identity-session", type=Path, required=True)
    recover_run.add_argument("--identity-key", type=Path, required=True)

    review_run = commands.add_parser(
        "review-run",
        help="record an agent or human implementation review for a verified run",
    )
    review_run.add_argument("path", type=Path)
    review_run.add_argument(
        "--identity-session",
        type=Path,
        required=True,
    )
    review_run.add_argument("--identity-key", type=Path, required=True)
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
    run_ui.add_argument("--identity-session", type=Path, required=True)
    run_ui.add_argument("--identity-key", type=Path, required=True)
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
    apply_run_decision.add_argument("--identity-session", type=Path, required=True)
    apply_run_decision.add_argument("--identity-key", type=Path, required=True)

    integrate_run = commands.add_parser(
        "integrate-run",
        help="commit an approved patch and fast-forward its local target branch",
    )
    integrate_run.add_argument("path", type=Path)
    integrate_run.add_argument("--root", type=Path)
    integrate_run.add_argument("--state-root", type=Path)
    integrate_run.add_argument("--identity-session", type=Path, required=True)
    integrate_run.add_argument("--identity-key", type=Path, required=True)

    cleanup_run = commands.add_parser(
        "cleanup-run",
        help="remove a completed clean worktree while preserving its branch",
    )
    cleanup_run.add_argument("path", type=Path)
    cleanup_run.add_argument("--root", type=Path)
    cleanup_run.add_argument("--state-root", type=Path)
    cleanup_run.add_argument("--identity-session", type=Path, required=True)
    cleanup_run.add_argument("--identity-key", type=Path, required=True)

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
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
            selected_option_id=arguments.selected_option_id,
            rationale=arguments.rationale,
            output=arguments.output,
        )
    if arguments.command in {"serve-review", "ui"}:
        return _cmd_serve_review(
            arguments.path,
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
            output=arguments.output,
            journal_path=arguments.journal,
            port=arguments.port,
            open_browser=arguments.open_browser,
        )
    if arguments.command in {"serve-dashboard", "dashboard"}:
        return _cmd_serve_dashboard(
            root=arguments.root,
            state_root=arguments.state_root,
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
            control_plane_db=arguments.control_plane_db,
            artifact_root=arguments.artifact_root,
            port=arguments.port,
            open_browser=arguments.open_browser,
        )
    if arguments.command in {"serve-task-orchestration", "orchestration-api"}:
        return _cmd_serve_task_orchestration(
            root=arguments.root,
            state_root=arguments.state_root,
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
            planning_agent_name=arguments.planning_agent,
            allow_external_planning_agent=arguments.allow_external_planning_agent,
            planning_artifact_root=arguments.planning_artifact_root,
            codex_binary=arguments.codex_binary,
            codex_model=arguments.codex_model,
            codex_timeout_seconds=arguments.codex_timeout_seconds,
            port=arguments.port,
            open_browser=arguments.open_browser,
        )
    if arguments.command == "create-local-identity-key":
        return _cmd_create_identity_key(arguments.path)
    if arguments.command == "issue-local-identity-session":
        return _cmd_issue_identity_session(
            key=arguments.key,
            subject_id=arguments.subject_id,
            display_name=arguments.display_name,
            roles=tuple(arguments.role),
            lifetime_minutes=arguments.lifetime_minutes,
            output=arguments.output,
        )
    if arguments.command == "issue-approval-authority":
        return _cmd_issue_approval_authority(
            key=arguments.key,
            identity_session=arguments.identity_session,
            decision_kinds=tuple(arguments.decision_kind),
            lifetime_minutes=arguments.lifetime_minutes,
            output=arguments.output,
        )
    if arguments.command == "check":
        return _cmd_check(arguments.root)
    if arguments.command == "run-task":
        return _cmd_run_task(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
        )
    if arguments.command == "inspect-run":
        return _cmd_inspect_run(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
        )
    if arguments.command == "inspect-readiness":
        return _cmd_inspect_readiness(
            root=arguments.root,
            state_root=arguments.state_root,
            required_reviewed_tasks=arguments.required_reviewed_tasks,
        )
    if arguments.command == "compare-executors":
        return _cmd_compare_executors(tuple(arguments.run_directories))
    if arguments.command == "build-phase0-corpus":
        return _cmd_build_phase0_corpus(
            root=arguments.root,
            definition=arguments.definition,
            output_root=arguments.output_root,
        )
    if arguments.command == "inspect-recovery":
        return _cmd_inspect_recovery(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
        )
    if arguments.command == "recover-run":
        return _cmd_recover_run(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
        )
    if arguments.command == "review-run":
        return _cmd_review_run(
            arguments.path,
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
            status=arguments.status,
            summary=arguments.summary,
            findings=tuple(arguments.finding),
            root=arguments.root,
            state_root=arguments.state_root,
        )
    if arguments.command == "run-ui":
        return _cmd_serve_run(
            arguments.path,
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
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
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
        )
    if arguments.command == "integrate-run":
        return _cmd_integrate_run(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
        )
    if arguments.command == "cleanup-run":
        return _cmd_cleanup_run(
            arguments.path,
            root=arguments.root,
            state_root=arguments.state_root,
            identity_session=arguments.identity_session,
            identity_key=arguments.identity_key,
        )
    if arguments.command == "demo":
        return _cmd_demo(arguments.journal)
    raise AssertionError(f"unhandled command: {arguments.command}")
