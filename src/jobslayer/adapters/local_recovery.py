from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jobslayer.adapters.git_workspace import GitWorktreeManager, WorkspaceError
from jobslayer.adapters.local_artifacts import (
    ArtifactRegistryError,
    LocalArtifactRegistry,
)
from jobslayer.adapters.local_git_integration import (
    LocalGitIntegrationError,
    LocalGitIntegrator,
)
from jobslayer.application.local_run import LocalRunCoordinator, LocalRunError
from jobslayer.application.execution_intent import LocalExecutionIntentEnvelope
from jobslayer.application.run_records import (
    LocalRunLedger,
    RunRecordError,
    RunRecordStage,
)
from jobslayer.domain.models import (
    ActorType,
    ApprovalAuthority,
    ArtifactManifest,
    DecisionCard,
    HumanDecision,
    ReviewDisposition,
    SourceIntegrationResult,
    TaskExecutionOutcome,
    TaskSpec,
    TaskState,
    TransitionRecord,
)
from jobslayer.recovery import (
    RecoveryAssessment,
    RecoveryError,
    RecoveryStatus,
)
from jobslayer.supervision.application import (
    DecisionApplicationError,
    DecisionApplicationService,
)
from jobslayer.workflow.journal import AuditIntegrityError, JsonlAuditJournal
from jobslayer.workflow.kernel import WorkflowKernel


class LocalRunRecoveryManager:
    """Recover local derived projections without owning workflow state."""

    _RESTORE_DECISION_CARD = "restore_decision_card"
    _RESUME_EXECUTION_RECORD = "resume_execution_record"
    _RESUME_DECISION_APPLICATION_RECORD = "resume_decision_application_record"
    _RESUME_SOURCE_INTEGRATION_RECORD = "resume_source_integration_record"
    _RESUME_WORKSPACE_CLEANUP_RECORD = "resume_workspace_cleanup_record"

    def __init__(self, coordinator: LocalRunCoordinator):
        self.coordinator = coordinator

    def assess(self, run_directory: str | Path) -> RecoveryAssessment:
        try:
            directory = self._resolve_run_directory(run_directory)
        except (OSError, RecoveryError) as exc:
            return RecoveryAssessment(
                run_id=Path(run_directory).name,
                run_directory=str(run_directory),
                status=RecoveryStatus.INVALID_EVIDENCE,
                reason=str(exc),
            )

        ledger = LocalRunLedger(directory / "records.jsonl", run_id=directory.name)
        try:
            records = ledger.read_all()
        except RunRecordError as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"run ledger integrity failed: {exc}",
            )
        if not records:
            execution_gap = self._assess_execution_gap(directory)
            if execution_gap is not None:
                return execution_gap
            return self._assessment(
                directory,
                RecoveryStatus.MANUAL_INTERVENTION,
                "run directory exists without an authoritative execution record",
            )

        decision_gap = self._assess_decision_application_gap(directory, records)
        if decision_gap is not None:
            return decision_gap
        integration_gap = self._assess_source_integration_gap(directory, records)
        if integration_gap is not None:
            return integration_gap
        cleanup_gap = self._assess_workspace_cleanup_gap(directory, records)
        if cleanup_gap is not None:
            return cleanup_gap

        try:
            summary = self.coordinator.inspect(directory)
        except (LocalRunError, OSError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.MANUAL_INTERVENTION,
                f"run stores do not form a consistent reconstructable snapshot: {exc}",
                run_stage=records[-1].stage.value,
            )
        run_stage = str(summary["stage"])
        workflow_state = str(summary["state"])
        if not all(
            summary.get(key) is True
            for key in ("record_chain_valid", "audit_chain_valid", "artifacts_valid")
        ):
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "run summary reports invalid ledger, journal, or artifact evidence",
                run_stage=run_stage,
                workflow_state=workflow_state,
            )

        try:
            expected_card = self._expected_decision_card(records)
        except (KeyError, TypeError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"review evidence cannot reconstruct its decision card: {exc}",
                run_stage=run_stage,
                workflow_state=workflow_state,
            )
        card_path = directory / "decision-card.json"
        if expected_card is None:
            if card_path.exists() or card_path.is_symlink():
                return self._assessment(
                    directory,
                    RecoveryStatus.INVALID_EVIDENCE,
                    "decision-card projection exists without merge-review evidence",
                    run_stage=run_stage,
                    workflow_state=workflow_state,
                )
        elif card_path.is_symlink():
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "decision-card projection must not be a symbolic link",
                run_stage=run_stage,
                workflow_state=workflow_state,
            )
        elif not card_path.exists():
            return self._assessment(
                directory,
                RecoveryStatus.RECOVERABLE,
                "authoritative merge-review evidence exists but decision-card projection is missing",
                repair_action=self._RESTORE_DECISION_CARD,
                run_stage=run_stage,
                workflow_state=workflow_state,
            )
        else:
            try:
                projected = DecisionCard.model_validate_json(
                    card_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, ValueError) as exc:
                return self._assessment(
                    directory,
                    RecoveryStatus.INVALID_EVIDENCE,
                    f"decision-card projection is invalid: {exc}",
                    run_stage=run_stage,
                    workflow_state=workflow_state,
                )
            if projected != expected_card:
                return self._assessment(
                    directory,
                    RecoveryStatus.INVALID_EVIDENCE,
                    "decision-card projection does not match authoritative review evidence",
                    run_stage=run_stage,
                    workflow_state=workflow_state,
                )

        return self._assessment(
            directory,
            RecoveryStatus.CONSISTENT,
            "run ledger, workflow journal, artifacts, and derived projections agree",
            run_stage=run_stage,
            workflow_state=workflow_state,
        )

    def recover(self, run_directory: str | Path) -> RecoveryAssessment:
        assessment = self.assess(run_directory)
        if assessment.status is RecoveryStatus.CONSISTENT:
            return assessment
        if (
            assessment.status is RecoveryStatus.RECOVERABLE
            and assessment.repair_action == self._RESUME_EXECUTION_RECORD
        ):
            directory = self._resolve_run_directory(run_directory)
            try:
                ledger = LocalRunLedger(
                    directory / "records.jsonl", run_id=directory.name
                )
                if ledger.read_all():
                    concurrent = self.assess(directory)
                    if concurrent.status is RecoveryStatus.CONSISTENT:
                        return concurrent
                    raise RecoveryError("execution ledger changed during recovery")
                (
                    intent_envelope,
                    outcome,
                    intent_artifact,
                    outcome_artifact,
                ) = self._load_execution_gap(directory)
                ledger.append(
                    task_id=outcome.task_id,
                    stage=RunRecordStage.EXECUTION,
                    payload={
                        "context": intent_envelope.context_payload(),
                        "outcome": outcome.model_dump(mode="json"),
                        "execution_intent_artifact": intent_artifact.model_dump(
                            mode="json"
                        ),
                        "execution_outcome_artifact": outcome_artifact.model_dump(
                            mode="json"
                        ),
                    },
                )
            except (OSError, RecoveryError, RunRecordError, ValueError) as exc:
                concurrent = self.assess(directory)
                if concurrent.status is RecoveryStatus.CONSISTENT:
                    return concurrent
                raise RecoveryError(
                    "execution record recovery was refused after facts changed"
                ) from exc
            recovered = self.assess(directory)
            if recovered.status is not RecoveryStatus.CONSISTENT:
                raise RecoveryError(
                    f"recovery did not produce a consistent run: {recovered.reason}"
                )
            return recovered
        if (
            assessment.status is RecoveryStatus.RECOVERABLE
            and assessment.repair_action
            == self._RESUME_DECISION_APPLICATION_RECORD
        ):
            directory = self._resolve_run_directory(run_directory)
            try:
                records = LocalRunLedger(
                    directory / "records.jsonl", run_id=directory.name
                ).read_all()
                if len(records) != 2:
                    concurrent = self.assess(directory)
                    if concurrent.status is RecoveryStatus.CONSISTENT:
                        return concurrent
                    raise RecoveryError(
                        "decision application ledger changed during recovery"
                    )
                (
                    decision,
                    authority,
                    decision_artifact,
                    authority_artifact,
                    transition,
                ) = self._load_applied_decision_gap(directory, records)
                LocalRunLedger(
                    directory / "records.jsonl", run_id=directory.name
                ).append(
                    task_id=records[0].task_id,
                    stage=RunRecordStage.DECISION_APPLICATION,
                    payload={
                        "decision": decision.model_dump(mode="json"),
                        "authority": authority.model_dump(mode="json"),
                        "decision_artifact": decision_artifact.model_dump(mode="json"),
                        "authority_artifact": authority_artifact.model_dump(mode="json"),
                        "transition": transition.model_dump(mode="json"),
                    },
                )
            except (OSError, RecoveryError, RunRecordError, ValueError) as exc:
                concurrent = self.assess(directory)
                if concurrent.status is RecoveryStatus.CONSISTENT:
                    return concurrent
                raise RecoveryError(
                    "decision-application record recovery was refused after facts changed"
                ) from exc
            recovered = self.assess(directory)
            if recovered.status is not RecoveryStatus.CONSISTENT:
                raise RecoveryError(
                    f"recovery did not produce a consistent run: {recovered.reason}"
                )
            return recovered
        if (
            assessment.status is RecoveryStatus.RECOVERABLE
            and assessment.repair_action
            == self._RESUME_SOURCE_INTEGRATION_RECORD
        ):
            directory = self._resolve_run_directory(run_directory)
            try:
                self.coordinator.integrate(directory)
            except (LocalGitIntegrationError, LocalRunError, OSError, ValueError) as exc:
                concurrent = self.assess(directory)
                if concurrent.status is RecoveryStatus.CONSISTENT:
                    return concurrent
                raise RecoveryError(
                    "source-integration record recovery was refused after facts changed"
                ) from exc
            recovered = self.assess(directory)
            if recovered.status is not RecoveryStatus.CONSISTENT:
                raise RecoveryError(
                    f"recovery did not produce a consistent run: {recovered.reason}"
            )
            return recovered
        if (
            assessment.status is RecoveryStatus.RECOVERABLE
            and assessment.repair_action == self._RESUME_WORKSPACE_CLEANUP_RECORD
        ):
            directory = self._resolve_run_directory(run_directory)
            try:
                self.coordinator.cleanup(directory)
            except (LocalRunError, OSError, ValueError, WorkspaceError) as exc:
                concurrent = self.assess(directory)
                if concurrent.status is RecoveryStatus.CONSISTENT:
                    return concurrent
                raise RecoveryError(
                    "workspace-cleanup record recovery was refused after facts changed"
                ) from exc
            recovered = self.assess(directory)
            if recovered.status is not RecoveryStatus.CONSISTENT:
                raise RecoveryError(
                    f"recovery did not produce a consistent run: {recovered.reason}"
                )
            return recovered
        if (
            assessment.status is not RecoveryStatus.RECOVERABLE
            or assessment.repair_action != self._RESTORE_DECISION_CARD
        ):
            raise RecoveryError(
                f"run cannot be repaired automatically: {assessment.reason}"
            )

        directory = self._resolve_run_directory(run_directory)
        try:
            records = LocalRunLedger(
                directory / "records.jsonl", run_id=directory.name
            ).read_all()
            expected_card = self._expected_decision_card(records)
        except (KeyError, RunRecordError, TypeError, ValueError) as exc:
            raise RecoveryError(
                "authoritative review evidence changed during recovery"
            ) from exc
        if expected_card is None:
            raise RecoveryError("merge-review evidence disappeared during recovery")
        encoded = (
            json.dumps(
                expected_card.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ).encode("utf-8")
            + b"\n"
        )
        card_path = directory / "decision-card.json"
        try:
            descriptor = os.open(
                card_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            concurrent = self.assess(directory)
            if concurrent.status is RecoveryStatus.CONSISTENT:
                return concurrent
            raise RecoveryError(
                "decision-card projection appeared concurrently but is not trustworthy"
            )
        try:
            try:
                self._write_all(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            try:
                card_path.unlink()
            except OSError as cleanup_exc:
                raise RecoveryError(
                    "projection recovery failed and its incomplete file could not be removed"
                ) from cleanup_exc
            raise RecoveryError(
                "projection recovery failed; the incomplete file was removed"
            ) from exc

        recovered = self.assess(directory)
        if recovered.status is not RecoveryStatus.CONSISTENT:
            raise RecoveryError(
                f"recovery did not produce a consistent run: {recovered.reason}"
            )
        return recovered

    def _assess_execution_gap(self, directory: Path) -> RecoveryAssessment | None:
        artifact_root = directory / "artifacts"
        if not artifact_root.exists():
            return None
        if artifact_root.is_symlink() or not artifact_root.is_dir():
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "run artifact registry is unsafe",
            )
        artifacts = LocalArtifactRegistry(artifact_root)
        try:
            intents = artifacts.list_manifests(
                artifact_type="task-execution-intent",
                run_id=directory.name,
            )
            outcomes = artifacts.list_manifests(
                artifact_type="task-execution-outcome",
                run_id=directory.name,
            )
        except (ArtifactRegistryError, OSError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"execution persistence manifests are invalid: {exc}",
            )
        if not intents:
            return None
        if len(intents) != 1 or len(outcomes) > 1:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "execution requires exactly one intent and at most one outcome artifact",
                run_stage="execution_intent",
            )
        try:
            intent_envelope = LocalExecutionIntentEnvelope.model_validate_json(
                artifacts.read(intents[0])
            )
            history = JsonlAuditJournal(
                directory / "workflow.jsonl"
            ).records_for(intent_envelope.intent.task.task_id)
        except (ArtifactRegistryError, AuditIntegrityError, OSError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"execution intent or workflow evidence is invalid: {exc}",
                run_stage="execution_intent",
            )
        workflow_state = (
            history[-1].to_state.value if history else TaskState.DRAFT.value
        )
        if not outcomes:
            return self._assessment(
                directory,
                RecoveryStatus.MANUAL_INTERVENTION,
                "execution intent exists without an authoritative outcome; recovery will not rerun the Agent",
                run_stage="execution_intent",
                workflow_state=workflow_state,
            )
        try:
            self._load_execution_gap(directory)
        except RecoveryError as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                str(exc),
                run_stage="execution_outcome",
                workflow_state=workflow_state,
            )
        return self._assessment(
            directory,
            RecoveryStatus.RECOVERABLE,
            "execution intent and outcome are authoritative but the first run record is missing",
            repair_action=self._RESUME_EXECUTION_RECORD,
            run_stage="execution_outcome",
            workflow_state=workflow_state,
        )

    def _load_execution_gap(
        self,
        directory: Path,
    ) -> tuple[
        LocalExecutionIntentEnvelope,
        TaskExecutionOutcome,
        ArtifactManifest,
        ArtifactManifest,
    ]:
        artifact_root = directory / "artifacts"
        if not artifact_root.is_dir() or artifact_root.is_symlink():
            raise RecoveryError("run artifact registry is missing or unsafe")
        artifacts = LocalArtifactRegistry(artifact_root)
        try:
            intents = artifacts.list_manifests(
                artifact_type="task-execution-intent",
                run_id=directory.name,
            )
            outcomes = artifacts.list_manifests(
                artifact_type="task-execution-outcome",
                run_id=directory.name,
            )
        except (ArtifactRegistryError, OSError) as exc:
            raise RecoveryError(
                f"execution persistence manifests are invalid: {exc}"
            ) from exc
        if len(intents) != 1 or len(outcomes) != 1:
            raise RecoveryError(
                "execution recovery requires one intent and one outcome artifact"
            )
        intent_artifact = intents[0]
        outcome_artifact = outcomes[0]
        if (
            intent_artifact.producer != "local-run-coordinator"
            or outcome_artifact.producer != "local-run-coordinator"
            or intent_artifact.task_id != outcome_artifact.task_id
        ):
            raise RecoveryError("execution persistence artifact binding is invalid")
        try:
            intent_envelope = LocalExecutionIntentEnvelope.model_validate_json(
                artifacts.read(intent_artifact)
            )
            outcome = TaskExecutionOutcome.model_validate_json(
                artifacts.read(outcome_artifact)
            )
        except (ArtifactRegistryError, OSError, ValueError) as exc:
            raise RecoveryError(
                f"execution intent or outcome cannot be reconstructed: {exc}"
            ) from exc
        if (
            intent_envelope.intent.run_id != directory.name
            or intent_envelope.intent.task.task_id != outcome.task_id
            or intent_artifact.task_id != outcome.task_id
        ):
            raise RecoveryError("execution intent and outcome identifiers do not match")
        try:
            history = JsonlAuditJournal(
                directory / "workflow.jsonl"
            ).records_for(outcome.task_id)
        except (AuditIntegrityError, OSError) as exc:
            raise RecoveryError(f"workflow journal integrity failed: {exc}") from exc
        if not history or history[-1].to_state is not outcome.state:
            raise RecoveryError(
                "execution outcome state does not match the workflow journal"
            )
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
                intent_artifact,
                outcome_artifact,
            )
            if artifact is not None
        )
        if not all(artifacts.verify(manifest) for manifest in manifests):
            raise RecoveryError("execution recovery depends on invalid artifact evidence")
        try:
            workspace_manager = GitWorktreeManager(
                outcome.workspace.repository_root,
                Path(outcome.workspace.path).parent,
            )
            workspace_manager.inspect(outcome.workspace)
            if outcome.patch is not None:
                actual_patch = workspace_manager.collect_patch(
                    outcome.workspace,
                    intent_envelope.intent.task,
                )
                if actual_patch.model_dump(
                    exclude={"created_at"}
                ) != outcome.patch.model_dump(exclude={"created_at"}):
                    raise RecoveryError(
                        "workspace no longer matches the persisted execution patch"
                    )
        except (OSError, ValueError, WorkspaceError) as exc:
            if isinstance(exc, RecoveryError):
                raise
            raise RecoveryError(
                f"execution workspace facts cannot be verified: {exc}"
            ) from exc
        return (
            intent_envelope,
            outcome,
            intent_artifact,
            outcome_artifact,
        )

    def _assess_decision_application_gap(
        self,
        directory: Path,
        records: tuple[Any, ...],
    ) -> RecoveryAssessment | None:
        if len(records) != 2:
            return None
        try:
            history = JsonlAuditJournal(
                directory / "workflow.jsonl"
            ).records_for(records[0].task_id)
        except (AuditIntegrityError, OSError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"workflow journal integrity failed: {exc}",
                run_stage=records[-1].stage.value,
            )
        if not history or history[-1].to_state is TaskState.MERGE_REVIEW:
            return None
        if history[-1].from_state is not TaskState.MERGE_REVIEW:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "workflow advanced beyond review without a recognizable decision transition",
                run_stage=records[-1].stage.value,
                workflow_state=history[-1].to_state.value,
            )
        try:
            self._load_applied_decision_gap(directory, records)
        except RecoveryError as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                str(exc),
                run_stage=records[-1].stage.value,
                workflow_state=history[-1].to_state.value,
            )
        return self._assessment(
            directory,
            RecoveryStatus.RECOVERABLE,
            "authorized decision transition is authoritative but its run record is missing",
            repair_action=self._RESUME_DECISION_APPLICATION_RECORD,
            run_stage=records[-1].stage.value,
            workflow_state=history[-1].to_state.value,
        )

    def _load_applied_decision_gap(
        self,
        directory: Path,
        records: tuple[Any, ...],
    ) -> tuple[
        HumanDecision,
        ApprovalAuthority,
        ArtifactManifest,
        ArtifactManifest,
        TransitionRecord,
    ]:
        try:
            outcome = TaskExecutionOutcome.model_validate(
                records[0].payload["outcome"]
            )
            disposition = ReviewDisposition.model_validate(
                records[1].payload["disposition"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RecoveryError(
                f"review evidence cannot reconstruct decision inputs: {exc}"
            ) from exc
        package = disposition.merge_review_package
        if package is None:
            raise RecoveryError("review evidence has no merge decision package")
        try:
            history = JsonlAuditJournal(
                directory / "workflow.jsonl"
            ).records_for(package.task_id)
        except (AuditIntegrityError, OSError) as exc:
            raise RecoveryError(f"workflow journal integrity failed: {exc}") from exc
        if not history:
            raise RecoveryError("workflow has no applied decision transition")
        transition = history[-1]

        artifact_root = directory / "artifacts"
        if not artifact_root.is_dir() or artifact_root.is_symlink():
            raise RecoveryError("run artifact registry is missing or unsafe")
        artifacts = LocalArtifactRegistry(artifact_root)
        evidence_manifests: list[ArtifactManifest] = []
        for evidence_id in transition.evidence_ids:
            if not evidence_id.startswith("artifact-"):
                continue
            try:
                evidence_manifests.append(artifacts.get(evidence_id))
            except (ArtifactRegistryError, OSError) as exc:
                raise RecoveryError(
                    "decision transition references unavailable artifact evidence"
                ) from exc
        decisions = tuple(
            manifest
            for manifest in evidence_manifests
            if manifest.artifact_type == "human-decision"
        )
        authorities = tuple(
            manifest
            for manifest in evidence_manifests
            if manifest.artifact_type == "approval-authority"
        )
        if len(decisions) != 1 or len(authorities) != 1:
            raise RecoveryError(
                "decision transition must reference one decision and one authority artifact"
            )
        decision_artifact = decisions[0]
        authority_artifact = authorities[0]
        if any(
            manifest.task_id != package.task_id
            or manifest.run_id != records[0].run_id
            for manifest in (decision_artifact, authority_artifact)
        ):
            raise RecoveryError("decision artifact run/task binding is invalid")
        try:
            decision = HumanDecision.model_validate_json(
                artifacts.read(decision_artifact)
            )
            authority = ApprovalAuthority.model_validate_json(
                artifacts.read(authority_artifact)
            )
            prior_manifests = self._pre_decision_manifests(
                outcome=outcome,
                disposition=disposition,
            )
            execution_manifests = self._execution_persistence_manifests(
                artifacts=artifacts,
                execution_payload=records[0].payload,
                outcome=outcome,
            )
        except (ArtifactRegistryError, OSError, TypeError, ValueError) as exc:
            raise RecoveryError(
                f"decision artifact content cannot be reconstructed: {exc}"
            ) from exc
        if not all(
            artifacts.verify(manifest)
            for manifest in (
                *prior_manifests,
                *execution_manifests,
                decision_artifact,
                authority_artifact,
            )
        ):
            raise RecoveryError("decision recovery depends on invalid artifact evidence")
        if (
            decision_artifact.producer != decision.actor_id
            or authority_artifact.producer != "authority-provider"
        ):
            raise RecoveryError("decision artifact producer binding is invalid")
        try:
            DecisionApplicationService(
                WorkflowKernel(JsonlAuditJournal(directory / "workflow.jsonl"))
            ).validate_applied_transition(
                card=package.decision_card,
                decision=decision,
                authority=authority,
                transition=transition,
                verification_report=package.verification_report,
                required_evidence_ids=(
                    decision_artifact.artifact_id,
                    authority_artifact.artifact_id,
                ),
            )
        except DecisionApplicationError as exc:
            raise RecoveryError(
                f"applied decision transition is not authorized by its evidence: {exc}"
            ) from exc
        return (
            decision,
            authority,
            decision_artifact,
            authority_artifact,
            transition,
        )

    def _assess_source_integration_gap(
        self,
        directory: Path,
        records: tuple[Any, ...],
    ) -> RecoveryAssessment | None:
        if len(records) != 3:
            return None
        task_id = records[0].task_id
        try:
            history = JsonlAuditJournal(
                directory / "workflow.jsonl"
            ).records_for(task_id)
        except (AuditIntegrityError, OSError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"workflow journal integrity failed: {exc}",
                run_stage=records[-1].stage.value,
            )
        if not history or history[-1].to_state is not TaskState.COMPLETED:
            return None

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
            decision = HumanDecision.model_validate(
                records[2].payload["decision"]
            )
            authority = ApprovalAuthority.model_validate(
                records[2].payload["authority"]
            )
            decision_transition = TransitionRecord.model_validate(
                records[2].payload["transition"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"integration inputs cannot be reconstructed: {exc}",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )

        package = disposition.merge_review_package
        if (
            task.task_id != task_id
            or package is None
            or outcome.patch is None
            or outcome.verification_report is None
            or decision.selected_option_id != "approve"
            or authority.actor_id != decision.actor_id
            or decision_transition.to_state is not TaskState.INTEGRATING
            or len(history) < 2
            or history[-2] != decision_transition
        ):
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "decision evidence does not authorize the completed integration",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )

        artifact_root = directory / "artifacts"
        if (
            not artifact_root.is_dir()
            or artifact_root.is_symlink()
        ):
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "run artifact registry is missing or unsafe",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )
        artifacts = LocalArtifactRegistry(artifact_root)
        try:
            manifests = self._authoritative_manifests(
                outcome=outcome,
                disposition=disposition,
                decision_payload=records[2].payload,
            )
            manifests = (
                *manifests,
                *self._execution_persistence_manifests(
                    artifacts=artifacts,
                    execution_payload=records[0].payload,
                    outcome=outcome,
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"persisted artifact manifests cannot be reconstructed: {exc}",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )
        if not all(artifacts.verify(manifest) for manifest in manifests):
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "persisted run artifacts fail content-addressed verification",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )

        completed_transition = history[-1]
        try:
            result, integration_artifact = (
                self.coordinator._completed_integration_evidence(
                    artifacts=artifacts,
                    evidence_ids=completed_transition.evidence_ids,
                    task_id=task.task_id,
                    run_id=records[0].run_id,
                )
            )
        except LocalRunError as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                str(exc),
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )
        required_evidence = {
            decision.decision_id,
            authority.authorization_id,
            integration_artifact.artifact_id,
            package.verification_report.report_id,
            result.integration_id,
        }
        if (
            completed_transition.from_state is not TaskState.INTEGRATING
            or completed_transition.actor_type is not ActorType.HUMAN
            or completed_transition.actor_id != decision.actor_id
            or not required_evidence.issubset(completed_transition.evidence_ids)
        ):
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "completed transition does not bind the approved integration evidence",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )

        try:
            workspace_manager = GitWorktreeManager(
                outcome.workspace.repository_root,
                Path(outcome.workspace.path).parent,
            )
            LocalGitIntegrator(workspace_manager).verify_existing_integration(
                task=task,
                workspace=outcome.workspace,
                reviewed_patch=package.patch,
                target_ref=target_ref,
                approved_by=decision.actor_id,
                commit_message=f"JobSlayer: {task.title}",
                result=result,
            )
        except (LocalGitIntegrationError, OSError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"completed workflow is not supported by current Git facts: {exc}",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )

        return self._assessment(
            directory,
            RecoveryStatus.RECOVERABLE,
            "Git integration and Completed transition are authoritative but the source-integration run record is missing",
            repair_action=self._RESUME_SOURCE_INTEGRATION_RECORD,
            run_stage=records[-1].stage.value,
            workflow_state=TaskState.COMPLETED.value,
        )

    def _assess_workspace_cleanup_gap(
        self,
        directory: Path,
        records: tuple[Any, ...],
    ) -> RecoveryAssessment | None:
        if len(records) != 4:
            return None
        task_id = records[0].task_id
        try:
            history = JsonlAuditJournal(
                directory / "workflow.jsonl"
            ).records_for(task_id)
        except (AuditIntegrityError, OSError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"workflow journal integrity failed: {exc}",
                run_stage=records[-1].stage.value,
            )
        if not history or history[-1].to_state is not TaskState.COMPLETED:
            return None

        try:
            outcome = TaskExecutionOutcome.model_validate(
                records[0].payload["outcome"]
            )
            disposition = ReviewDisposition.model_validate(
                records[1].payload["disposition"]
            )
            result = SourceIntegrationResult.model_validate(
                records[3].payload["integration_result"]
            )
            integration_artifact = ArtifactManifest.model_validate(
                records[3].payload["integration_artifact"]
            )
            integration_transition = TransitionRecord.model_validate(
                records[3].payload["transition"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"cleanup inputs cannot be reconstructed: {exc}",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )
        workspace_path = Path(outcome.workspace.path)
        if workspace_path.exists():
            return None
        if workspace_path.is_symlink():
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "removed workspace path was replaced by a symbolic link",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )
        if (
            result.task_id != task_id
            or result.workspace_id != outcome.workspace.workspace_id
            or history[-1] != integration_transition
            or integration_transition.to_state is not TaskState.COMPLETED
            or result.integration_id not in integration_transition.evidence_ids
        ):
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "completed integration evidence does not bind the removed workspace",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )

        artifact_root = directory / "artifacts"
        if not artifact_root.is_dir() or artifact_root.is_symlink():
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "run artifact registry is missing or unsafe",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )
        artifacts = LocalArtifactRegistry(artifact_root)
        try:
            manifests = (
                *self._authoritative_manifests(
                    outcome=outcome,
                    disposition=disposition,
                    decision_payload=records[2].payload,
                ),
                *self._execution_persistence_manifests(
                    artifacts=artifacts,
                    execution_payload=records[0].payload,
                    outcome=outcome,
                ),
                integration_artifact,
            )
            persisted_result = SourceIntegrationResult.model_validate_json(
                artifacts.read(integration_artifact)
            )
        except (ArtifactRegistryError, KeyError, OSError, TypeError, ValueError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"cleanup artifact evidence cannot be reconstructed: {exc}",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )
        if persisted_result != result or not all(
            artifacts.verify(manifest) for manifest in manifests
        ):
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "cleanup depends on invalid or mismatched artifact evidence",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )

        try:
            removal = GitWorktreeManager(
                outcome.workspace.repository_root,
                workspace_path.parent,
            ).inspect_removal(
                outcome.workspace,
                expected_commit=result.commit,
            )
        except (OSError, ValueError, WorkspaceError) as exc:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                f"workspace removal facts cannot be verified: {exc}",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )
        if not removal.safely_removed:
            return self._assessment(
                directory,
                RecoveryStatus.INVALID_EVIDENCE,
                "workspace is absent but its Git registration or preserved branch drifted",
                run_stage=records[-1].stage.value,
                workflow_state=TaskState.COMPLETED.value,
            )
        return self._assessment(
            directory,
            RecoveryStatus.RECOVERABLE,
            "workspace removal is authoritative but the cleanup run record is missing",
            repair_action=self._RESUME_WORKSPACE_CLEANUP_RECORD,
            run_stage=records[-1].stage.value,
            workflow_state=TaskState.COMPLETED.value,
        )

    @staticmethod
    def _authoritative_manifests(
        *,
        outcome: TaskExecutionOutcome,
        disposition: ReviewDisposition,
        decision_payload: dict[str, Any],
    ) -> tuple[ArtifactManifest, ...]:
        manifests = LocalRunRecoveryManager._pre_decision_manifests(
            outcome=outcome,
            disposition=disposition,
        )
        return (
            *manifests,
            ArtifactManifest.model_validate(decision_payload["decision_artifact"]),
            ArtifactManifest.model_validate(decision_payload["authority_artifact"]),
        )

    @staticmethod
    def _pre_decision_manifests(
        *,
        outcome: TaskExecutionOutcome,
        disposition: ReviewDisposition,
    ) -> tuple[ArtifactManifest, ...]:
        return tuple(
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
                disposition.review_artifact,
                (
                    disposition.merge_review_package.decision_card_artifact
                    if disposition.merge_review_package is not None
                    else None
                ),
            )
            if artifact is not None
        )

    @staticmethod
    def _execution_persistence_manifests(
        *,
        artifacts: LocalArtifactRegistry,
        execution_payload: dict[str, Any],
        outcome: TaskExecutionOutcome,
    ) -> tuple[ArtifactManifest, ...]:
        intent_payload = execution_payload.get("execution_intent_artifact")
        outcome_payload = execution_payload.get("execution_outcome_artifact")
        if intent_payload is None and outcome_payload is None:
            return ()
        if (intent_payload is None) != (outcome_payload is None):
            raise ValueError("execution persistence evidence is incomplete")
        intent_manifest = ArtifactManifest.model_validate(intent_payload)
        outcome_manifest = ArtifactManifest.model_validate(outcome_payload)
        intent = LocalExecutionIntentEnvelope.model_validate_json(
            artifacts.read(intent_manifest)
        )
        persisted_outcome = TaskExecutionOutcome.model_validate_json(
            artifacts.read(outcome_manifest)
        )
        if (
            intent.context_payload() != execution_payload["context"]
            or persisted_outcome != outcome
            or intent_manifest.task_id != outcome.task_id
            or outcome_manifest.task_id != outcome.task_id
            or intent_manifest.run_id != outcome_manifest.run_id
            or intent.intent.run_id != intent_manifest.run_id
            or outcome.agent_run.run_id != intent.intent.run_id
        ):
            raise ValueError("execution persistence evidence binding is invalid")
        return intent_manifest, outcome_manifest

    def _resolve_run_directory(self, run_directory: str | Path) -> Path:
        directory = Path(run_directory)
        if not directory.is_absolute():
            directory = self.coordinator.repository_root / directory
        directory = directory.resolve(strict=True)
        expected_root = (self.coordinator.state_root / "runs").resolve(strict=False)
        if not directory.is_dir() or not directory.is_relative_to(expected_root):
            raise RecoveryError("run directory must be inside the configured state root")
        return directory

    @staticmethod
    def _expected_decision_card(records: tuple[Any, ...]) -> DecisionCard | None:
        if len(records) < 2:
            return None
        disposition = ReviewDisposition.model_validate(
            records[1].payload["disposition"]
        )
        package = disposition.merge_review_package
        return None if package is None else package.decision_card

    @staticmethod
    def _write_all(descriptor: int, content: bytes) -> None:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("could not persist the complete recovered projection")
            offset += written

    @staticmethod
    def _assessment(
        directory: Path,
        status: RecoveryStatus,
        reason: str,
        *,
        repair_action: str | None = None,
        run_stage: str | None = None,
        workflow_state: str | None = None,
    ) -> RecoveryAssessment:
        return RecoveryAssessment(
            run_id=directory.name,
            run_directory=str(directory),
            status=status,
            reason=reason,
            repair_action=repair_action,
            run_stage=run_stage,
            workflow_state=workflow_state,
        )
