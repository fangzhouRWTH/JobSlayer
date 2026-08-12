"""Long-running task execution with atomic control-plane metadata commits."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import time
from typing import Any, Callable
from uuid import uuid4

from pydantic import Field

from jobslayer.agents.executor import AgentExecutor
from jobslayer.application.controller import TaskExecutionController
from jobslayer.application.run_records import RunRecord, RunRecordStage
from jobslayer.artifacts.registry import ArtifactRegistry
from jobslayer.domain.models import (
    AgentInvocation,
    ActorType,
    ArtifactManifest,
    ApprovalAuthority,
    DomainModel,
    HumanDecision,
    ReviewDisposition,
    ReviewReport,
    TaskExecutionAuthorization,
    TaskExecutionIntent,
    TaskExecutionOutcome,
    TaskSpec,
    TaskState,
    ValidationProfile,
    TransitionRecord,
    SourceIntegrationResult,
    WorkspaceRemovalInspection,
)
from jobslayer.persistence import ControlPlaneStore, OutboxEvent, StateIntegrityError
from jobslayer.persistence.transactional_journal import TransactionalAuditJournal
from jobslayer.verification.engine import VerificationEngine
from jobslayer.workflow.kernel import WorkflowKernel
from jobslayer.workspace.manager import WorkspaceManager
from jobslayer.supervision.application import DecisionApplicationService
from jobslayer.integration import SourceIntegrator
from jobslayer.identity import (
    AuthorizationAction,
    AuthorizationVerdict,
    require_authorized,
)
from jobslayer.observability import NoopTelemetrySink, TelemetrySink


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class TransactionalExecutionResult(DomainModel):
    schema_version: str = "1.0"
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    intent_artifact: ArtifactManifest
    outcome_artifact: ArtifactManifest
    run_record: RunRecord
    outcome: TaskExecutionOutcome
    committed_task_sequence: int = Field(ge=1)
    committed_run_sequence: int = Field(ge=1)


class TransactionalExecutionCoordinator:
    """Execute without holding a DB transaction, then atomically publish truth.

    Intent and its immutable input artifacts are committed before the worker is
    launched. Workflow transitions are generated only through ``WorkflowKernel``
    in a hash-linked buffer. Once the long-running action is terminal, the exact
    buffered transitions, execution run record, artifact metadata, and outbox
    event are committed together using optimistic sequences.
    """

    def __init__(
        self,
        *,
        store: ControlPlaneStore,
        workspace_manager: WorkspaceManager,
        agent_executor: AgentExecutor,
        verification_engine: VerificationEngine,
        artifact_registry: ArtifactRegistry,
        source_integrator: SourceIntegrator | None = None,
        telemetry: TelemetrySink | None = None,
        poll_interval_seconds: float = 0.05,
        collection_grace_seconds: float = 2.0,
    ):
        if verification_engine.artifact_registry is not artifact_registry:
            raise ValueError(
                "verification engine and coordinator must share an artifact registry"
            )
        self.store = store
        self.workspace_manager = workspace_manager
        self.agent_executor = agent_executor
        self.verification_engine = verification_engine
        self.artifact_registry = artifact_registry
        self.source_integrator = source_integrator
        self.telemetry = telemetry or NoopTelemetrySink()
        self.poll_interval_seconds = poll_interval_seconds
        self.collection_grace_seconds = collection_grace_seconds

    def execute(
        self,
        *,
        task: TaskSpec,
        invocation: AgentInvocation,
        validation_profile: ValidationProfile,
        authorization: TaskExecutionAuthorization,
        governance_evidence: dict[str, Any] | Callable[[], dict[str, Any]],
        now: datetime | None = None,
    ) -> TransactionalExecutionResult:
        started = time.perf_counter()
        prepared_at = now or datetime.now(UTC)
        run_id = invocation.run_spec.run_id
        if self.store.task_history(task.task_id) or self.store.run_history(run_id):
            raise StateIntegrityError(
                "transactional execution requires unused task and run identities"
            )
        intent = TaskExecutionIntent(
            intent_id=f"execution-intent-{run_id}",
            run_id=run_id,
            task=task,
            invocation=invocation,
            validation_profile=validation_profile,
            authorization=authorization,
            prepared_at=prepared_at,
        )
        intent_artifact = self.artifact_registry.register_bytes(
            task_id=task.task_id,
            run_id=run_id,
            artifact_type="task-execution-intent",
            producer="transactional-execution-coordinator",
            content=_canonical(intent.model_dump(mode="json")),
        )
        preexisting_manifests = self.artifact_registry.list_manifests(
            task_id=task.task_id,
            run_id=run_id,
        )
        with self.store.transaction(
            task_id=task.task_id,
            run_id=run_id,
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            for manifest in preexisting_manifests:
                transaction.add_artifact(manifest)
            transaction.enqueue(
                OutboxEvent(
                    event_id=f"intent-{uuid4().hex}",
                    topic="execution.intent.recorded",
                    task_id=task.task_id,
                    run_id=run_id,
                    payload={
                        "intent_artifact_id": intent_artifact.artifact_id,
                        "authorization_id": authorization.authorization_id,
                    },
                )
            )
            transaction.commit()
        self.telemetry.record(
            "jobslayer.execution.intent_committed",
            {
                "jobslayer.task.id": task.task_id,
                "jobslayer.run.id": run_id,
                "jobslayer.authorization.actor": authorization.actor_id,
            },
        )

        buffered_journal = TransactionalAuditJournal(task.task_id, ())
        controller = TaskExecutionController(
            kernel=WorkflowKernel(buffered_journal),
            workspace_manager=self.workspace_manager,
            agent_executor=self.agent_executor,
            verification_engine=self.verification_engine,
            artifact_registry=self.artifact_registry,
            poll_interval_seconds=self.poll_interval_seconds,
            collection_grace_seconds=self.collection_grace_seconds,
        )
        outcome = controller.execute_implementation(
            task=task,
            invocation=invocation,
            validation_profile=validation_profile,
            authorization=authorization,
            now=prepared_at,
        )
        outcome_artifact = self.artifact_registry.register_bytes(
            task_id=task.task_id,
            run_id=run_id,
            artifact_type="task-execution-outcome",
            producer="transactional-execution-coordinator",
            content=_canonical(outcome.model_dump(mode="json")),
        )
        evidence_payload = (
            governance_evidence()
            if callable(governance_evidence)
            else governance_evidence
        )
        payload = {
            "intent": intent.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
            "governance": json.loads(_canonical(evidence_payload)),
            "execution_intent_artifact": intent_artifact.model_dump(mode="json"),
            "execution_outcome_artifact": outcome_artifact.model_dump(mode="json"),
        }
        already_committed_ids = {
            manifest.artifact_id for manifest in preexisting_manifests
        }
        outcome_manifests = tuple(
            manifest
            for manifest in self.artifact_registry.list_manifests(
                task_id=task.task_id,
                run_id=run_id,
            )
            if manifest.artifact_id not in already_committed_ids
        )
        with self.store.transaction(
            task_id=task.task_id,
            run_id=run_id,
            expected_task_sequence=0,
            expected_run_sequence=0,
        ) as transaction:
            for transition in buffered_journal.staged:
                transaction.append_transition_record(transition)
            run_record = transaction.append_run_record(
                stage=RunRecordStage.EXECUTION,
                payload=payload,
            )
            for manifest in outcome_manifests:
                transaction.add_artifact(manifest)
            transaction.enqueue(
                OutboxEvent(
                    event_id=f"outcome-{uuid4().hex}",
                    topic="execution.outcome.committed",
                    task_id=task.task_id,
                    run_id=run_id,
                    payload={
                        "state": outcome.state.value,
                        "run_record_hash": run_record.record_hash,
                        "outcome_artifact_id": outcome_artifact.artifact_id,
                    },
                )
            )
            transaction.commit()
        result = TransactionalExecutionResult(
            task_id=task.task_id,
            run_id=run_id,
            intent_artifact=intent_artifact,
            outcome_artifact=outcome_artifact,
            run_record=run_record,
            outcome=outcome,
            committed_task_sequence=len(buffered_journal.staged),
            committed_run_sequence=run_record.sequence,
        )
        self.telemetry.record(
            "jobslayer.execution.outcome_committed",
            {
                "jobslayer.task.id": task.task_id,
                "jobslayer.run.id": run_id,
                "jobslayer.task.state": outcome.state.value,
                "jobslayer.execution.duration_ms": int(
                    (time.perf_counter() - started) * 1000
                ),
            },
        )
        return result

    def prepare_review(
        self,
        *,
        run_id: str,
        review_report: ReviewReport,
    ) -> tuple[ReviewDisposition, RunRecord]:
        records = self.store.run_history(run_id)
        if len(records) != 1:
            raise StateIntegrityError(
                "transactional run is not awaiting implementation review"
            )
        try:
            intent = TaskExecutionIntent.model_validate(records[0].payload["intent"])
            outcome = TaskExecutionOutcome.model_validate(records[0].payload["outcome"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StateIntegrityError("execution record cannot reconstruct review") from exc
        task = intent.task
        if review_report.task_id != task.task_id:
            raise StateIntegrityError("review report belongs to another task")
        history = self.store.task_history(task.task_id)
        buffered = TransactionalAuditJournal(task.task_id, history)
        existing_ids = {
            item.artifact_id for item in self.store.artifacts_for_run(run_id)
        }
        controller = TaskExecutionController(
            kernel=WorkflowKernel(buffered),
            workspace_manager=self.workspace_manager,
            agent_executor=self.agent_executor,
            verification_engine=self.verification_engine,
            artifact_registry=self.artifact_registry,
            poll_interval_seconds=self.poll_interval_seconds,
            collection_grace_seconds=self.collection_grace_seconds,
        )
        disposition = controller.prepare_merge_review(
            task=task,
            outcome=outcome,
            review_report=review_report,
        )
        payload = {
            "review_report": review_report.model_dump(mode="json"),
            "disposition": disposition.model_dump(mode="json"),
        }
        record = self._commit_stage(
            task_id=task.task_id,
            run_id=run_id,
            expected_task_sequence=len(history),
            expected_run_sequence=len(records),
            buffered=buffered,
            stage=RunRecordStage.IMPLEMENTATION_REVIEW,
            payload=payload,
            existing_artifact_ids=existing_ids,
            topic="review.outcome.committed",
            event_payload={
                "review_id": review_report.review_id,
                "state": disposition.state.value,
            },
        )
        return disposition, record

    def apply_decision(
        self,
        *,
        run_id: str,
        decision: HumanDecision,
        authority: ApprovalAuthority,
        authority_verifier: Callable[[ApprovalAuthority, datetime], ApprovalAuthority],
        now: datetime | None = None,
    ) -> tuple[TransitionRecord, RunRecord]:
        when = now or datetime.now(UTC)
        records = self.store.run_history(run_id)
        if len(records) != 2:
            raise StateIntegrityError(
                "transactional run is not awaiting decision application"
            )
        try:
            outcome = TaskExecutionOutcome.model_validate(records[0].payload["outcome"])
            disposition = ReviewDisposition.model_validate(
                records[1].payload["disposition"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateIntegrityError(
                "run records cannot reconstruct decision evidence"
            ) from exc
        package = disposition.merge_review_package
        if package is None:
            raise StateIntegrityError("run has no merge review package")
        if authority.proof is None:
            raise StateIntegrityError("decision authority has no verifiable proof")
        verified = authority_verifier(authority, when)
        if verified != authority:
            raise StateIntegrityError("authority verifier changed the authority")
        history = self.store.task_history(package.task_id)
        buffered = TransactionalAuditJournal(package.task_id, history)
        existing_ids = {
            item.artifact_id for item in self.store.artifacts_for_run(run_id)
        }
        decision_artifact = self.artifact_registry.register_bytes(
            task_id=package.task_id,
            run_id=run_id,
            artifact_type="human-decision",
            producer=decision.actor_id,
            content=_canonical(decision.model_dump(mode="json")),
        )
        authority_artifact = self.artifact_registry.register_bytes(
            task_id=package.task_id,
            run_id=run_id,
            artifact_type="approval-authority",
            producer="authority-provider",
            content=_canonical(authority.model_dump(mode="json")),
        )
        transition = DecisionApplicationService(WorkflowKernel(buffered)).apply(
            card=package.decision_card,
            decision=decision,
            authority=authority,
            verification_report=package.verification_report,
            additional_evidence_ids=(
                decision_artifact.artifact_id,
                authority_artifact.artifact_id,
            ),
            now=when,
        )
        payload = {
            "decision": decision.model_dump(mode="json"),
            "authority": authority.model_dump(mode="json"),
            "decision_artifact": decision_artifact.model_dump(mode="json"),
            "authority_artifact": authority_artifact.model_dump(mode="json"),
            "transition": transition.model_dump(mode="json"),
            "applied": True,
        }
        record = self._commit_stage(
            task_id=package.task_id,
            run_id=run_id,
            expected_task_sequence=len(history),
            expected_run_sequence=len(records),
            buffered=buffered,
            stage=RunRecordStage.DECISION_APPLICATION,
            payload=payload,
            existing_artifact_ids=existing_ids,
            topic="decision.application.committed",
            event_payload={
                "decision_id": decision.decision_id,
                "transition_hash": transition.record_hash,
                "state": transition.to_state.value,
            },
        )
        return transition, record

    def _commit_stage(
        self,
        *,
        task_id: str,
        run_id: str,
        expected_task_sequence: int,
        expected_run_sequence: int,
        buffered: TransactionalAuditJournal,
        stage: RunRecordStage,
        payload: dict[str, Any],
        existing_artifact_ids: set[str],
        topic: str,
        event_payload: dict[str, Any],
    ) -> RunRecord:
        new_manifests = tuple(
            item
            for item in self.artifact_registry.list_manifests(
                task_id=task_id,
                run_id=run_id,
            )
            if item.artifact_id not in existing_artifact_ids
        )
        with self.store.transaction(
            task_id=task_id,
            run_id=run_id,
            expected_task_sequence=expected_task_sequence,
            expected_run_sequence=expected_run_sequence,
        ) as transaction:
            for transition in buffered.staged:
                transaction.append_transition_record(transition)
            record = transaction.append_run_record(stage=stage, payload=payload)
            for manifest in new_manifests:
                transaction.add_artifact(manifest)
            transaction.enqueue(
                OutboxEvent(
                    event_id=f"stage-{uuid4().hex}",
                    topic=topic,
                    task_id=task_id,
                    run_id=run_id,
                    payload={**event_payload, "run_record_hash": record.record_hash},
                )
            )
            transaction.commit()
        self.telemetry.record(
            "jobslayer.control_plane.stage_committed",
            {
                "jobslayer.task.id": task_id,
                "jobslayer.run.id": run_id,
                "jobslayer.run.stage": stage.value,
                "jobslayer.outbox.topic": topic,
            },
        )
        return record

    def integrate(
        self,
        *,
        run_id: str,
        target_ref: str,
        authorization: AuthorizationVerdict,
    ) -> tuple[SourceIntegrationResult, TransitionRecord, RunRecord]:
        if self.source_integrator is None:
            raise StateIntegrityError("source integration adapter is not configured")
        records = self.store.run_history(run_id)
        if len(records) != 3:
            raise StateIntegrityError("transactional run is not awaiting integration")
        try:
            intent = TaskExecutionIntent.model_validate(records[0].payload["intent"])
            outcome = TaskExecutionOutcome.model_validate(records[0].payload["outcome"])
            disposition = ReviewDisposition.model_validate(
                records[1].payload["disposition"]
            )
            decision = HumanDecision.model_validate(records[2].payload["decision"])
            authority = ApprovalAuthority.model_validate(records[2].payload["authority"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StateIntegrityError(
                "run records cannot reconstruct integration evidence"
            ) from exc
        package = disposition.merge_review_package
        if (
            package is None
            or outcome.patch is None
            or outcome.verification_report is None
            or decision.selected_option_id != "approve"
        ):
            raise StateIntegrityError("run has no approved verified patch")
        self._require_action(
            authorization,
            AuthorizationAction.INTEGRATE_SOURCE,
            expected_subject=decision.actor_id,
        )
        if authority.actor_id != decision.actor_id:
            raise StateIntegrityError("integration approval actor binding is invalid")
        history = self.store.task_history(intent.task.task_id)
        buffered = TransactionalAuditJournal(intent.task.task_id, history)
        if WorkflowKernel(buffered).current_state(intent.task.task_id).value != "integrating":
            raise StateIntegrityError("workflow is not in the integrating state")
        existing_ids = {
            item.artifact_id for item in self.store.artifacts_for_run(run_id)
        }
        result = self.source_integrator.integrate(
            task=intent.task,
            workspace=outcome.workspace,
            reviewed_patch=package.patch,
            target_ref=target_ref,
            approved_by=decision.actor_id,
            commit_message=f"JobSlayer: {intent.task.title}",
        )
        integration_artifact = self.artifact_registry.register_bytes(
            task_id=intent.task.task_id,
            run_id=run_id,
            artifact_type="source-integration-result",
            producer="source-integrator",
            content=_canonical(result.model_dump(mode="json")),
        )
        transition = WorkflowKernel(buffered).transition(
            task_id=intent.task.task_id,
            to_state=TaskState.COMPLETED,
            actor_type=ActorType.HUMAN,
            actor_id=decision.actor_id,
            reason=(
                f"approved patch {package.patch.sha256} was integrated into "
                f"{target_ref}"
            ),
            verification_report=package.verification_report,
            integration_result=result,
            evidence_ids=(
                decision.decision_id,
                authority.authorization_id,
                integration_artifact.artifact_id,
                *authorization.evidence_ids,
            ),
        )
        record = self._commit_stage(
            task_id=intent.task.task_id,
            run_id=run_id,
            expected_task_sequence=len(history),
            expected_run_sequence=len(records),
            buffered=buffered,
            stage=RunRecordStage.SOURCE_INTEGRATION,
            payload={
                "integration_result": result.model_dump(mode="json"),
                "integration_artifact": integration_artifact.model_dump(mode="json"),
                "authorization": authorization.model_dump(mode="json"),
                "transition": transition.model_dump(mode="json"),
            },
            existing_artifact_ids=existing_ids,
            topic="source.integration.committed",
            event_payload={
                "integration_id": result.integration_id,
                "target_commit": result.target_commit,
                "state": transition.to_state.value,
            },
        )
        return result, transition, record

    def cleanup(
        self,
        *,
        run_id: str,
        authorization: AuthorizationVerdict,
    ) -> tuple[WorkspaceRemovalInspection, RunRecord]:
        records = self.store.run_history(run_id)
        if len(records) != 4:
            raise StateIntegrityError("transactional run is not awaiting cleanup")
        try:
            outcome = TaskExecutionOutcome.model_validate(records[0].payload["outcome"])
            result = SourceIntegrationResult.model_validate(
                records[3].payload["integration_result"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateIntegrityError("run records cannot reconstruct cleanup") from exc
        self._require_action(
            authorization,
            AuthorizationAction.CLEANUP_WORKSPACE,
        )
        history = self.store.task_history(result.task_id)
        if not history or history[-1].to_state is not TaskState.COMPLETED:
            raise StateIntegrityError("only a completed run workspace may be removed")
        existing_ids = {
            item.artifact_id for item in self.store.artifacts_for_run(run_id)
        }
        inspection = self.workspace_manager.inspect_removal(
            outcome.workspace,
            expected_commit=result.commit,
        )
        if not inspection.safely_removed:
            self.workspace_manager.remove(outcome.workspace)
            inspection = self.workspace_manager.inspect_removal(
                outcome.workspace,
                expected_commit=result.commit,
            )
        if not inspection.safely_removed:
            raise StateIntegrityError("workspace removal evidence did not pass")
        artifact = self.artifact_registry.register_bytes(
            task_id=result.task_id,
            run_id=run_id,
            artifact_type="workspace-removal-inspection",
            producer="workspace-manager",
            content=_canonical(inspection.model_dump(mode="json")),
        )
        buffered = TransactionalAuditJournal(result.task_id, history)
        record = self._commit_stage(
            task_id=result.task_id,
            run_id=run_id,
            expected_task_sequence=len(history),
            expected_run_sequence=len(records),
            buffered=buffered,
            stage=RunRecordStage.WORKSPACE_CLEANUP,
            payload={
                "removal_inspection": inspection.model_dump(mode="json"),
                "removal_artifact": artifact.model_dump(mode="json"),
                "authorization": authorization.model_dump(mode="json"),
            },
            existing_artifact_ids=existing_ids,
            topic="workspace.cleanup.committed",
            event_payload={
                "workspace_id": outcome.workspace.workspace_id,
                "safely_removed": True,
            },
        )
        return inspection, record

    @staticmethod
    def _require_action(
        verdict: AuthorizationVerdict,
        action: AuthorizationAction,
        *,
        expected_subject: str | None = None,
    ) -> None:
        require_authorized(verdict)
        if verdict.action is not action:
            raise StateIntegrityError("authorization verdict is for another action")
        if expected_subject is not None and verdict.subject_id != expected_subject:
            raise StateIntegrityError("authorization verdict belongs to another subject")


__all__ = ["TransactionalExecutionCoordinator", "TransactionalExecutionResult"]
