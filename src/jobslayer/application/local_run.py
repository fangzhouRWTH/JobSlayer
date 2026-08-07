from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from jobslayer.adapters.git_workspace import GitWorktreeManager
from jobslayer.adapters.codex_cli import CodexCliExecutor
from jobslayer.adapters.local_artifacts import LocalArtifactRegistry
from jobslayer.adapters.local_command import GovernedLocalCommandRunner
from jobslayer.adapters.local_git_integration import LocalGitIntegrator
from jobslayer.adapters.local_testbed import LocalGitTestbedInspector
from jobslayer.adapters.scripted_patch import ScriptedPatchExecutor
from jobslayer.agents.executor import AgentExecutorError
from jobslayer.application.controller import TaskExecutionController
from jobslayer.application.run_records import (
    LocalRunLedger,
    RunRecordError,
    RunRecordStage,
)
from jobslayer.application.runbook import (
    CodexCliConfig,
    LocalRunbookLoader,
    PreparedLocalRun,
    ScriptedPatchConfig,
)
from jobslayer.domain.models import (
    ActorType,
    AgentInvocation,
    ApprovalAuthority,
    ArtifactManifest,
    HumanDecision,
    ReviewDisposition,
    ReviewReport,
    ReviewStatus,
    SourceIntegrationResult,
    TaskExecutionAuthorization,
    TaskExecutionOutcome,
    TaskSpec,
    TaskState,
    ValidationProfile,
)
from jobslayer.supervision.application import DecisionApplicationService
from jobslayer.verification.engine import VerificationEngine
from jobslayer.workflow.journal import JsonlAuditJournal
from jobslayer.workflow.kernel import WorkflowKernel


class LocalRunError(RuntimeError):
    """Raised when a governed local workflow cannot preserve its bindings."""


class _UnavailableExecutor:
    """Resume-only placeholder; review never calls executor lifecycle methods."""

    def start(self, invocation, workspace):
        raise AgentExecutorError("executor is unavailable while resuming review")

    def events(self, run_id: str, *, after_sequence: int = 0):
        raise AgentExecutorError("executor is unavailable while resuming review")

    def cancel(self, run_id: str):
        raise AgentExecutorError("executor is unavailable while resuming review")

    def collect(self, run_id: str):
        raise AgentExecutorError("executor is unavailable while resuming review")


class LocalRunCoordinator:
    """Assemble one real local task run from source-controlled inputs."""

    def __init__(
        self,
        repository_root: str | Path,
        *,
        state_root: str | Path | None = None,
        codex_binary: str | Path = "codex",
    ):
        self.repository_root = Path(repository_root).resolve(strict=True)
        requested_state = Path(state_root) if state_root is not None else Path(".jobslayer")
        if not requested_state.is_absolute():
            requested_state = self.repository_root / requested_state
        self.state_root = requested_state.resolve(strict=False)
        self.codex_binary = str(codex_binary)
        if self.state_root == self.repository_root:
            raise LocalRunError("state root must not be the JobSlayer repository root")

    def execute(
        self,
        runbook_path: str | Path,
        *,
        authorized_by: str | None = None,
    ) -> dict[str, Any]:
        prepared = LocalRunbookLoader(self.repository_root).load(runbook_path)
        self._preflight_executor(prepared, authorized_by=authorized_by)
        checkout = self._checkout_for(prepared)
        inspection = LocalGitTestbedInspector(checkout).inspect(prepared.testbed)
        if not inspection.valid_local_baseline:
            raise LocalRunError("registered testbed checkout does not pass the baseline gate")

        run_id = prepared.runbook.invocation.run_spec.run_id
        run_directory = self.state_root / "runs" / run_id
        try:
            run_directory.mkdir(parents=True, mode=0o700)
        except FileExistsError as exc:
            raise LocalRunError(f"run directory already exists: {run_directory}") from exc

        now = datetime.now(UTC)
        authorization = self._execution_authorization(
            prepared,
            run_id=run_id,
            now=now,
            authorized_by=authorized_by,
        )
        workspace_manager = GitWorktreeManager(
            checkout, self.state_root / "workspaces"
        )
        artifacts = LocalArtifactRegistry(run_directory / "artifacts")
        executor = self._executor_for(
            prepared,
            workspace_manager=workspace_manager,
            artifact_root=run_directory / "agent-logs",
        )
        journal = JsonlAuditJournal(run_directory / "workflow.jsonl")
        controller = self._controller(
            workspace_manager=workspace_manager,
            artifacts=artifacts,
            journal=journal,
            executor=executor,
        )
        outcome = controller.execute_implementation(
            task=prepared.task,
            invocation=prepared.runbook.invocation,
            validation_profile=prepared.validation_profile,
            authorization=authorization,
            now=now,
        )
        runbook_bytes = prepared.source_path.read_bytes()
        payload = {
            "context": {
                "runbook_path": str(prepared.source_path),
                "runbook_sha256": hashlib.sha256(runbook_bytes).hexdigest(),
                "testbed": prepared.testbed.model_dump(mode="json"),
                "testbed_inspection": inspection.model_dump(mode="json"),
                "task": prepared.task.model_dump(mode="json"),
                "invocation": prepared.runbook.invocation.model_dump(mode="json"),
                "validation_profile": prepared.validation_profile.model_dump(mode="json"),
                "authorization": authorization.model_dump(mode="json"),
            },
            "outcome": outcome.model_dump(mode="json"),
        }
        LocalRunLedger(
            run_directory / "records.jsonl", run_id=run_id
        ).append(
            task_id=prepared.task.task_id,
            stage=RunRecordStage.EXECUTION,
            payload=payload,
        )
        return self.inspect(run_directory)

    def _preflight_executor(
        self,
        prepared: PreparedLocalRun,
        *,
        authorized_by: str | None,
    ) -> None:
        executor = prepared.runbook.executor
        if isinstance(executor, ScriptedPatchConfig):
            if authorized_by is not None:
                raise LocalRunError(
                    "scripted replay uses its registered policy authorization; "
                    "do not provide authorized_by"
                )
            return
        if authorized_by is None or not authorized_by.strip():
            raise LocalRunError(
                "codex_cli execution requires an explicit non-empty authorized_by actor"
            )
        if shutil.which(self.codex_binary) is None:
            raise LocalRunError(f"Codex executable is unavailable: {self.codex_binary}")

    @staticmethod
    def _execution_authorization(
        prepared: PreparedLocalRun,
        *,
        run_id: str,
        now: datetime,
        authorized_by: str | None,
    ) -> TaskExecutionAuthorization:
        if isinstance(prepared.runbook.executor, ScriptedPatchConfig):
            actor_type = ActorType.POLICY
            actor_id = "phase0-local-scripted-policy-v1"
            authorization_id = f"local-scripted-{run_id}"
        else:
            if authorized_by is None:
                raise LocalRunError("codex_cli execution authorization is missing")
            actor_type = ActorType.HUMAN
            actor_id = authorized_by.strip()
            authorization_id = f"local-human-codex-{run_id}"
        validity_seconds = max(
            15 * 60,
            prepared.runbook.invocation.run_spec.timeout_seconds + 60,
        )
        return TaskExecutionAuthorization(
            authorization_id=authorization_id,
            task_id=prepared.task.task_id,
            actor_type=actor_type,
            actor_id=actor_id,
            maximum_risk=prepared.task.risk,
            issued_at=now,
            valid_until=now + timedelta(seconds=validity_seconds),
        )

    def _executor_for(
        self,
        prepared: PreparedLocalRun,
        *,
        workspace_manager: GitWorktreeManager,
        artifact_root: Path,
    ):
        config = prepared.runbook.executor
        if isinstance(config, ScriptedPatchConfig):
            if prepared.patch_bytes is None:
                raise LocalRunError("scripted patch bytes are unavailable")
            return ScriptedPatchExecutor(
                workspace_manager,
                artifact_root,
                patch_bytes=prepared.patch_bytes,
                patch_sha256=config.patch_sha256,
            )
        if isinstance(config, CodexCliConfig):
            return CodexCliExecutor(
                workspace_manager,
                artifact_root,
                codex_binary=self.codex_binary,
            )
        raise LocalRunError(f"unsupported executor adapter: {config.adapter}")

    def review(
        self,
        run_directory: str | Path,
        *,
        actor_type: ActorType,
        actor_id: str,
        status: ReviewStatus,
        summary: str,
        findings: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if not actor_id.strip() or not summary.strip():
            raise LocalRunError("review actor and summary must not be blank")
        if actor_type not in {ActorType.AGENT, ActorType.HUMAN}:
            raise LocalRunError("implementation reviewer must be an agent or human")
        directory, ledger, records = self._records_for(run_directory)
        if len(records) != 1:
            raise LocalRunError("run is not awaiting its first implementation review")
        execution_payload = records[0].payload
        try:
            context = execution_payload["context"]
            task = TaskSpec.model_validate(context["task"])
            invocation = AgentInvocation.model_validate(context["invocation"])
            profile = ValidationProfile.model_validate(context["validation_profile"])
            outcome = TaskExecutionOutcome.model_validate(execution_payload["outcome"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalRunError("execution run record has invalid typed content") from exc
        if outcome.state is not TaskState.REVIEWING or outcome.patch is None:
            raise LocalRunError("run outcome is not awaiting implementation review")
        if outcome.verification_report is None:
            raise LocalRunError("reviewable run has no verification report")

        workspace_manager = GitWorktreeManager(
            outcome.workspace.repository_root,
            Path(outcome.workspace.path).parent,
        )
        artifacts = LocalArtifactRegistry(directory / "artifacts")
        journal = JsonlAuditJournal(directory / "workflow.jsonl")
        controller = self._controller(
            workspace_manager=workspace_manager,
            artifacts=artifacts,
            journal=journal,
            executor=_UnavailableExecutor(),
        )
        report = ReviewReport(
            review_id=f"review-{uuid4().hex}",
            task_id=task.task_id,
            reviewer_actor_type=actor_type,
            reviewer_id=actor_id,
            patch_sha256=outcome.patch.sha256,
            status=status,
            summary=summary,
            findings=findings,
            evidence_ids=(
                (outcome.verification_report.report_id,)
                if status is ReviewStatus.ACCEPTED
                else ()
            ),
        )
        disposition = controller.prepare_merge_review(
            task=task,
            outcome=outcome,
            review_report=report,
        )
        ledger.append(
            task_id=task.task_id,
            stage=RunRecordStage.IMPLEMENTATION_REVIEW,
            payload={
                "review_report": report.model_dump(mode="json"),
                "disposition": disposition.model_dump(mode="json"),
            },
        )
        if disposition.merge_review_package is not None:
            self._create_json(
                directory / "decision-card.json",
                disposition.merge_review_package.decision_card.model_dump(mode="json"),
            )
        return self.inspect(directory)

    def apply_decision(
        self,
        run_directory: str | Path,
        *,
        authority_path: str | Path,
        decision_path: str | Path | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        directory, ledger, records = self._records_for(run_directory)
        if len(records) != 2:
            raise LocalRunError("run is not awaiting one decision application")
        try:
            outcome = TaskExecutionOutcome.model_validate(
                records[0].payload["outcome"]
            )
            disposition = ReviewDisposition.model_validate(
                records[1].payload["disposition"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalRunError("run cannot reconstruct merge decision evidence") from exc
        package = disposition.merge_review_package
        if package is None:
            raise LocalRunError("run has no merge review package")

        decision_file = (
            Path(decision_path)
            if decision_path is not None
            else directory / "decision.json"
        )
        authority_file = Path(authority_path)
        try:
            decision = HumanDecision.model_validate_json(
                decision_file.read_text(encoding="utf-8")
            )
            authority = ApprovalAuthority.model_validate_json(
                authority_file.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise LocalRunError(
                "decision or approval authority is unavailable or invalid"
            ) from exc

        artifacts = LocalArtifactRegistry(directory / "artifacts")
        decision_artifact = artifacts.register_file(
            decision_file,
            task_id=package.task_id,
            artifact_type="human-decision",
            producer=decision.actor_id,
        )
        authority_artifact = artifacts.register_file(
            authority_file,
            task_id=package.task_id,
            artifact_type="approval-authority",
            producer="authority-provider",
        )
        journal = JsonlAuditJournal(directory / "workflow.jsonl")
        transition = DecisionApplicationService(WorkflowKernel(journal)).apply(
            card=package.decision_card,
            decision=decision,
            authority=authority,
            verification_report=package.verification_report,
            now=now,
        )
        ledger.append(
            task_id=package.task_id,
            stage=RunRecordStage.DECISION_APPLICATION,
            payload={
                "decision": decision.model_dump(mode="json"),
                "authority": authority.model_dump(mode="json"),
                "decision_artifact": decision_artifact.model_dump(mode="json"),
                "authority_artifact": authority_artifact.model_dump(mode="json"),
                "transition": transition.model_dump(mode="json"),
            },
        )
        return self.inspect(directory)

    def integrate(self, run_directory: str | Path) -> dict[str, Any]:
        """Commit and fast-forward one explicitly approved local patch."""

        directory, ledger, records = self._records_for(run_directory)
        if len(records) != 3:
            raise LocalRunError("run is not awaiting source integration")
        try:
            execution = records[0].payload
            task = TaskSpec.model_validate(execution["context"]["task"])
            target_ref = str(
                execution["context"]["testbed"]["repository"]["default_branch"]
            )
            outcome = TaskExecutionOutcome.model_validate(execution["outcome"])
            disposition = ReviewDisposition.model_validate(
                records[1].payload["disposition"]
            )
            decision = HumanDecision.model_validate(records[2].payload["decision"])
            authority = ApprovalAuthority.model_validate(
                records[2].payload["authority"]
            )
            decision_transition = records[2].payload["transition"]
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalRunError("run cannot reconstruct integration inputs") from exc
        package = disposition.merge_review_package
        if package is None or outcome.patch is None or outcome.verification_report is None:
            raise LocalRunError("run has no approved, verified patch to integrate")
        if decision.selected_option_id != "approve":
            raise LocalRunError("only an approved merge decision may be integrated")
        if authority.actor_id != decision.actor_id:
            raise LocalRunError("integration approval actor binding is invalid")
        try:
            decision_state = TaskState(decision_transition["to_state"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalRunError("decision transition is invalid") from exc
        if decision_state is not TaskState.INTEGRATING:
            raise LocalRunError("approved decision did not enter integrating")

        workspace_manager = GitWorktreeManager(
            outcome.workspace.repository_root,
            Path(outcome.workspace.path).parent,
        )
        result = LocalGitIntegrator(workspace_manager).integrate(
            task=task,
            workspace=outcome.workspace,
            reviewed_patch=package.patch,
            target_ref=target_ref,
            approved_by=decision.actor_id,
            commit_message=f"JobSlayer: {task.title}",
        )
        artifacts = LocalArtifactRegistry(directory / "artifacts")
        integration_artifact = artifacts.register_bytes(
            task_id=task.task_id,
            run_id=records[0].run_id,
            artifact_type="source-integration-result",
            producer="local-git-integrator",
            content=self._json_bytes(result.model_dump(mode="json")),
        )

        journal = JsonlAuditJournal(directory / "workflow.jsonl")
        kernel = WorkflowKernel(journal)
        current_state = kernel.current_state(task.task_id)
        if current_state is TaskState.INTEGRATING:
            transition = kernel.transition(
                task_id=task.task_id,
                to_state=TaskState.COMPLETED,
                actor_type=ActorType.HUMAN,
                actor_id=decision.actor_id,
                reason=(
                    f"approved patch {package.patch.sha256} was committed and "
                    f"fast-forwarded to {target_ref}"
                ),
                verification_report=package.verification_report,
                integration_result=result,
                evidence_ids=(
                    decision.decision_id,
                    authority.authorization_id,
                    integration_artifact.artifact_id,
                ),
            )
        elif current_state is TaskState.COMPLETED:
            transition = kernel.history(task.task_id)[-1]
            if (
                transition.actor_id != decision.actor_id
                or result.integration_id not in transition.evidence_ids
                or package.verification_report.report_id
                not in transition.evidence_ids
            ):
                raise LocalRunError(
                    "completed workflow does not match the recoverable integration"
                )
        else:
            raise LocalRunError(
                f"source integration requires integrating, task is {current_state.value}"
            )
        ledger.append(
            task_id=task.task_id,
            stage=RunRecordStage.SOURCE_INTEGRATION,
            payload={
                "integration_result": result.model_dump(mode="json"),
                "integration_artifact": integration_artifact.model_dump(mode="json"),
                "transition": transition.model_dump(mode="json"),
            },
        )
        return self.inspect(directory)

    def cleanup(self, run_directory: str | Path) -> dict[str, Any]:
        """Remove a completed clean worktree while preserving its source branch."""

        directory, ledger, records = self._records_for(run_directory)
        if len(records) != 4:
            raise LocalRunError("run is not awaiting workspace cleanup")
        try:
            outcome = TaskExecutionOutcome.model_validate(
                records[0].payload["outcome"]
            )
            result = SourceIntegrationResult.model_validate(
                records[3].payload["integration_result"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalRunError("run cannot reconstruct cleanup inputs") from exc
        journal = JsonlAuditJournal(directory / "workflow.jsonl")
        if WorkflowKernel(journal).current_state(result.task_id) is not TaskState.COMPLETED:
            raise LocalRunError("only a completed run workspace may be removed")

        workspace_path = Path(outcome.workspace.path)
        if workspace_path.exists():
            GitWorktreeManager(
                outcome.workspace.repository_root,
                workspace_path.parent,
            ).remove(outcome.workspace)
        if workspace_path.exists():
            raise LocalRunError("workspace cleanup did not remove the worktree")
        ledger.append(
            task_id=result.task_id,
            stage=RunRecordStage.WORKSPACE_CLEANUP,
            payload={
                "workspace_id": outcome.workspace.workspace_id,
                "path": outcome.workspace.path,
                "branch": outcome.workspace.branch_name,
                "source_commit": result.commit,
                "removed": True,
                "branch_preserved": True,
                "removed_at": datetime.now(UTC).isoformat(),
            },
        )
        return self.inspect(directory)

    def inspect(self, run_directory: str | Path) -> dict[str, Any]:
        directory, _, records = self._records_for(run_directory)
        if not records:
            raise LocalRunError("run ledger has no execution record")
        try:
            execution = records[0].payload
            outcome = TaskExecutionOutcome.model_validate(execution["outcome"])
            task = TaskSpec.model_validate(execution["context"]["task"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalRunError("run execution record cannot be reconstructed") from exc
        history = JsonlAuditJournal(directory / "workflow.jsonl").records_for(task.task_id)
        if not history:
            raise LocalRunError("run has no workflow transitions")
        current_state = history[-1].to_state
        disposition = None
        review_report = None
        if len(records) >= 2:
            try:
                review_report = ReviewReport.model_validate(
                    records[1].payload["review_report"]
                )
                disposition = ReviewDisposition.model_validate(
                    records[1].payload["disposition"]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise LocalRunError("run review record cannot be reconstructed") from exc
        decision_application = records[2].payload if len(records) >= 3 else None
        integration_application = records[3].payload if len(records) >= 4 else None
        cleanup_application = records[4].payload if len(records) >= 5 else None
        decision_result_state = None
        if decision_application is None:
            expected_state = outcome.state if disposition is None else disposition.state
        elif integration_application is not None:
            try:
                decision_result_state = TaskState(
                    decision_application["transition"]["to_state"]
                )
                expected_state = TaskState(
                    integration_application["transition"]["to_state"]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise LocalRunError(
                    "run integration record cannot be reconstructed"
                ) from exc
        else:
            try:
                decision_result_state = TaskState(
                    decision_application["transition"]["to_state"]
                )
                expected_state = decision_result_state
            except (KeyError, TypeError, ValueError) as exc:
                raise LocalRunError(
                    "run decision application record cannot be reconstructed"
                ) from exc
        if current_state is not expected_state:
            raise LocalRunError("run ledger and workflow journal states do not agree")

        if cleanup_application is None:
            workspace_manager = GitWorktreeManager(
                outcome.workspace.repository_root,
                Path(outcome.workspace.path).parent,
            )
            workspace = workspace_manager.inspect(outcome.workspace)
            workspace_summary = {
                "path": outcome.workspace.path,
                "branch": workspace.branch_name,
                "present": True,
                "changed_paths": list(workspace.changed_paths),
                "working_tree_clean": workspace.working_tree_clean,
            }
        else:
            try:
                if cleanup_application["workspace_id"] != outcome.workspace.workspace_id:
                    raise ValueError("workspace id mismatch")
                if not cleanup_application["removed"]:
                    raise ValueError("workspace was not removed")
            except (KeyError, TypeError, ValueError) as exc:
                raise LocalRunError("run cleanup record cannot be reconstructed") from exc
            if Path(outcome.workspace.path).exists():
                raise LocalRunError("cleanup record claims a workspace that still exists")
            workspace_summary = {
                "path": outcome.workspace.path,
                "branch": outcome.workspace.branch_name,
                "present": False,
                "changed_paths": list(outcome.patch.changed_paths if outcome.patch else ()),
                "working_tree_clean": True,
            }
        artifacts = LocalArtifactRegistry(directory / "artifacts")
        manifests = tuple(
            artifact
            for artifact in (
                outcome.task_artifact,
                outcome.authorization_artifact,
                outcome.validation_profile_artifact,
                outcome.agent_run_artifact,
                outcome.raw_event_artifact,
                outcome.stderr_artifact,
                outcome.patch_artifact,
                outcome.verification_artifact,
                outcome.failure_artifact,
            )
            if artifact is not None
        )
        if disposition is not None:
            review_manifests = [disposition.review_artifact]
            if disposition.merge_review_package is not None:
                review_manifests.append(
                    disposition.merge_review_package.decision_card_artifact
                )
            manifests = (*manifests, *review_manifests)
        if decision_application is not None:
            try:
                manifests = (
                    *manifests,
                    ArtifactManifest.model_validate(
                        decision_application["decision_artifact"]
                    ),
                    ArtifactManifest.model_validate(
                        decision_application["authority_artifact"]
                    ),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise LocalRunError("decision evidence cannot be reconstructed") from exc
        integration_result = None
        if integration_application is not None:
            try:
                integration_result = SourceIntegrationResult.model_validate(
                    integration_application["integration_result"]
                )
                manifests = (
                    *manifests,
                    ArtifactManifest.model_validate(
                        integration_application["integration_artifact"]
                    ),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise LocalRunError("integration evidence cannot be reconstructed") from exc
        artifacts_valid = all(artifacts.verify(manifest) for manifest in manifests)
        verification = outcome.verification_report
        card_path = directory / "decision-card.json"
        decision_path = directory / "decision.json"
        return {
            "schema_version": "1.0",
            "run_id": records[0].run_id,
            "task_id": task.task_id,
            "title": task.title,
            "run_directory": str(directory),
            "stage": records[-1].stage.value,
            "state": current_state.value,
            "records": len(records),
            "audit_transitions": len(history),
            "record_chain_valid": True,
            "audit_chain_valid": True,
            "artifacts_valid": artifacts_valid,
            "executor": {
                "type": outcome.agent_run.executor_type,
                "status": outcome.agent_run.status.value,
                "usage": outcome.agent_run.usage,
            },
            "workspace": workspace_summary,
            "verification": None
            if verification is None
            else {
                "report_id": verification.report_id,
                "passes_gate": verification.passes_gate,
                "checks": [check.model_dump(mode="json") for check in verification.checks],
            },
            "review": None
            if review_report is None or disposition is None
            else {
                "review_id": review_report.review_id,
                "reviewer_actor_type": review_report.reviewer_actor_type.value,
                "reviewer_id": review_report.reviewer_id,
                "status": review_report.status.value,
                "summary": review_report.summary,
                "findings": list(review_report.findings),
                "disposition": disposition.status.value,
            },
            "decision": {
                "card_available": card_path.is_file(),
                "card_path": str(card_path) if card_path.is_file() else None,
                "recorded": decision_path.is_file(),
                "record_path": str(decision_path),
                "applied": decision_application is not None,
                "result_state": (
                    decision_result_state.value
                    if decision_result_state is not None
                    else None
                ),
            },
            "integration": None
            if integration_result is None
            else {
                "integration_id": integration_result.integration_id,
                "target_ref": integration_result.target_ref,
                "commit": integration_result.commit,
                "source_patch_sha256": integration_result.source_patch_sha256,
                "approved_by": integration_result.approved_by,
            },
            "capabilities": {
                "implementation_review": len(records) == 1
                and current_state is TaskState.REVIEWING,
                "decision_recording": card_path.is_file()
                and current_state is TaskState.MERGE_REVIEW
                and not decision_path.exists(),
                "decision_application": card_path.is_file()
                and decision_path.is_file()
                and decision_application is None
                and current_state is TaskState.MERGE_REVIEW,
                "source_integration": decision_application is not None
                and integration_application is None
                and current_state is TaskState.INTEGRATING,
                "workspace_cleanup": integration_application is not None
                and cleanup_application is None
                and current_state is TaskState.COMPLETED,
                "network_isolation": False,
                "resource_isolation": False,
                "git_merge": integration_application is not None,
                "deployment": False,
            },
        }

    def _records_for(self, run_directory: str | Path):
        directory = Path(run_directory)
        if not directory.is_absolute():
            directory = self.repository_root / directory
        try:
            directory = directory.resolve(strict=True)
        except OSError as exc:
            raise LocalRunError(f"run directory does not exist: {directory}") from exc
        if not directory.is_dir():
            raise LocalRunError("run path is not a directory")
        ledger = LocalRunLedger(
            directory / "records.jsonl", run_id=directory.name
        )
        try:
            records = ledger.read_all()
        except RunRecordError as exc:
            raise LocalRunError(str(exc)) from exc
        return directory, ledger, records

    def _checkout_for(self, prepared: PreparedLocalRun) -> Path:
        hint = prepared.testbed.local_checkout_hint
        if hint is None:
            raise LocalRunError("testbed has no local checkout hint")
        checkout = Path(hint)
        if not checkout.is_absolute():
            checkout = self.repository_root / checkout
        return checkout.resolve(strict=False)

    @staticmethod
    def _controller(
        *, workspace_manager, artifacts, journal, executor
    ) -> TaskExecutionController:
        runner = GovernedLocalCommandRunner(workspace_manager)
        return TaskExecutionController(
            kernel=WorkflowKernel(journal),
            workspace_manager=workspace_manager,
            agent_executor=executor,
            verification_engine=VerificationEngine(runner, artifacts),
            artifact_registry=artifacts,
        )

    @staticmethod
    def _create_json(path: Path, payload: dict[str, Any]) -> None:
        encoded = LocalRunCoordinator._json_bytes(payload)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise LocalRunError(f"refusing to overwrite run file: {path}") from exc
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _json_bytes(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8") + b"\n"
