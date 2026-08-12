"""In-memory journal staging used inside transactional state adapters."""

from __future__ import annotations

from jobslayer.domain.models import ActorType, TaskState, TransitionRecord
from jobslayer.persistence import StateIntegrityError
from jobslayer.workflow.journal import (
    AuditIntegrityError,
    AuditJournal,
    build_transition_record,
    verify_transition_sequence,
)


class TransactionalAuditJournal(AuditJournal):
    """Stage hash-linked transitions until the owning transaction commits."""

    def __init__(self, task_id: str, records: tuple[TransitionRecord, ...]):
        self.task_id = task_id
        self._records = list(records)
        self.initial_count = len(records)

    def records_for(self, task_id: str) -> list[TransitionRecord]:
        if task_id != self.task_id:
            return []
        return list(self._records)

    def append_transition(
        self,
        *,
        task_id: str,
        from_state: TaskState,
        to_state: TaskState,
        actor_type: ActorType,
        actor_id: str,
        reason: str,
        evidence_ids: tuple[str, ...] = (),
    ) -> TransitionRecord:
        if task_id != self.task_id:
            raise StateIntegrityError("transaction journal is bound to another task")
        record = build_transition_record(
            self._records,
            task_id=task_id,
            from_state=from_state,
            to_state=to_state,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            evidence_ids=evidence_ids,
        )
        self._records.append(record)
        return record

    @property
    def staged(self) -> tuple[TransitionRecord, ...]:
        return tuple(self._records[self.initial_count :])

    def stage_record(self, record: TransitionRecord) -> None:
        """Stage an exact transition previously produced through WorkflowKernel."""

        if record.task_id != self.task_id:
            raise StateIntegrityError("transition belongs to another task")
        expected_sequence = len(self._records) + 1
        if record.sequence != expected_sequence:
            raise StateIntegrityError("transition sequence is not the next sequence")
        expected_state = self._records[-1].to_state if self._records else TaskState.DRAFT
        if record.from_state is not expected_state:
            raise StateIntegrityError("transition source state does not match history")
        try:
            verify_transition_sequence([*self._records, record])
        except AuditIntegrityError as exc:
            raise StateIntegrityError(
                "transition record failed hash-chain verification"
            ) from exc
        self._records.append(record)


__all__ = ["TransactionalAuditJournal"]
