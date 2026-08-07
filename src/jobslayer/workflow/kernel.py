from __future__ import annotations

from collections.abc import Mapping

from jobslayer.domain.models import (
    ActorType,
    SourceIntegrationResult,
    TaskState,
    TransitionRecord,
    VerificationReport,
)
from jobslayer.workflow.journal import JsonlAuditJournal


class WorkflowError(RuntimeError):
    """Base class for rejected workflow commands."""


class IllegalTransitionError(WorkflowError):
    pass


class AuthorizationError(WorkflowError):
    pass


class VerificationGateError(WorkflowError):
    pass


ALLOWED_TRANSITIONS: Mapping[TaskState, frozenset[TaskState]] = {
    TaskState.DRAFT: frozenset({TaskState.PLANNED}),
    TaskState.PLANNED: frozenset(
        {TaskState.PLAN_REVIEW, TaskState.IMPLEMENTING}
    ),
    TaskState.PLAN_REVIEW: frozenset(
        {TaskState.PLANNED, TaskState.IMPLEMENTING, TaskState.CANCELLED}
    ),
    TaskState.IMPLEMENTING: frozenset(
        {TaskState.VERIFYING, TaskState.BLOCKED, TaskState.FAILED}
    ),
    TaskState.BLOCKED: frozenset(
        {TaskState.IMPLEMENTING, TaskState.CANCELLED}
    ),
    TaskState.FAILED: frozenset(
        {TaskState.IMPLEMENTING, TaskState.CANCELLED}
    ),
    TaskState.VERIFYING: frozenset(
        {TaskState.REPAIRING, TaskState.REVIEWING}
    ),
    TaskState.REPAIRING: frozenset({TaskState.VERIFYING}),
    TaskState.REVIEWING: frozenset(
        {TaskState.REPAIRING, TaskState.MERGE_REVIEW}
    ),
    TaskState.MERGE_REVIEW: frozenset(
        {TaskState.REPAIRING, TaskState.INTEGRATING, TaskState.CANCELLED}
    ),
    TaskState.INTEGRATING: frozenset(
        {TaskState.COMPLETED, TaskState.CANCELLED}
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class WorkflowKernel:
    """Owns task state and rejects transitions that lack authority or evidence."""

    def __init__(self, journal: JsonlAuditJournal):
        self.journal = journal

    def current_state(self, task_id: str) -> TaskState:
        records = self.journal.records_for(task_id)
        return records[-1].to_state if records else TaskState.DRAFT

    def history(self, task_id: str) -> tuple[TransitionRecord, ...]:
        return tuple(self.journal.records_for(task_id))

    def transition(
        self,
        *,
        task_id: str,
        to_state: TaskState,
        actor_type: ActorType,
        actor_id: str,
        reason: str,
        verification_report: VerificationReport | None = None,
        integration_result: SourceIntegrationResult | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> TransitionRecord:
        from_state = self.current_state(task_id)
        if to_state not in ALLOWED_TRANSITIONS[from_state]:
            raise IllegalTransitionError(
                f"transition {from_state.value} -> {to_state.value} is not allowed"
            )
        self._authorize(
            actor_type=actor_type,
            from_state=from_state,
            to_state=to_state,
        )
        evidence_ids = self._enforce_verification(
            task_id=task_id,
            from_state=from_state,
            to_state=to_state,
            report=verification_report,
            integration_result=integration_result,
            evidence_ids=evidence_ids,
        )
        return self.journal.append_transition(
            task_id=task_id,
            from_state=from_state,
            to_state=to_state,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence_ids=evidence_ids,
        )

    @staticmethod
    def _authorize(
        *,
        actor_type: ActorType,
        from_state: TaskState,
        to_state: TaskState,
    ) -> None:
        if from_state in {TaskState.PLAN_REVIEW, TaskState.MERGE_REVIEW} and actor_type not in {
            ActorType.HUMAN,
            ActorType.POLICY,
        }:
            raise AuthorizationError(
                f"leaving {from_state.value} requires a human or policy actor"
            )
        if to_state in {TaskState.COMPLETED, TaskState.CANCELLED} and actor_type not in {
            ActorType.HUMAN,
            ActorType.POLICY,
        }:
            raise AuthorizationError(
                f"{to_state.value} requires a human or policy actor"
            )
        if to_state is TaskState.IMPLEMENTING and actor_type is ActorType.AGENT:
            raise AuthorizationError(
                "an agent cannot approve its own entry into implementation"
            )

    @staticmethod
    def _enforce_verification(
        *,
        task_id: str,
        from_state: TaskState,
        to_state: TaskState,
        report: VerificationReport | None,
        integration_result: SourceIntegrationResult | None,
        evidence_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if to_state in {
            TaskState.REVIEWING,
            TaskState.INTEGRATING,
            TaskState.COMPLETED,
        }:
            if report is None:
                raise VerificationGateError(
                    f"{to_state.value} requires a verification report"
                )
            if report.task_id != task_id:
                raise VerificationGateError(
                    "verification report belongs to a different task"
                )
            if not report.passes_gate:
                raise VerificationGateError(
                    "verification report does not pass the completion gate"
                )
            evidence_ids = tuple(dict.fromkeys((*evidence_ids, report.report_id)))

        if to_state is TaskState.COMPLETED:
            if from_state is not TaskState.INTEGRATING:
                raise VerificationGateError(
                    "completion requires the integrating state"
                )
            if integration_result is None:
                raise VerificationGateError(
                    "completion requires a source integration result"
                )
            if integration_result.task_id != task_id:
                raise VerificationGateError(
                    "source integration result belongs to a different task"
                )
            assert report is not None
            if (
                report.source_patch_sha256 is None
                or report.source_patch_sha256
                != integration_result.source_patch_sha256
            ):
                raise VerificationGateError(
                    "source integration result does not match the verified patch"
                )
            evidence_ids = tuple(
                dict.fromkeys((*evidence_ids, integration_result.integration_id))
            )

        if from_state is TaskState.VERIFYING and to_state is TaskState.REPAIRING:
            if report is None or report.passes_gate:
                raise VerificationGateError(
                    "repairing requires a failing verification report"
                )
            if report.task_id != task_id:
                raise VerificationGateError(
                    "verification report belongs to a different task"
                )
            evidence_ids = tuple(dict.fromkeys((*evidence_ids, report.report_id)))
        return evidence_ids
