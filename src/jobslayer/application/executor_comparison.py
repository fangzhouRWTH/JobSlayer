"""Build deterministic comparisons from verified run-ledger evidence."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from jobslayer.application.run_records import LocalRunLedger, RunRecordStage
from jobslayer.domain.models import (
    ActorType,
    HumanDecision,
    ReviewDisposition,
    ReviewReport,
    TaskExecutionOutcome,
    TaskSpec,
    ValidationProfile,
)
from jobslayer.evaluation import (
    ExecutorAggregate,
    ExecutorComparisonError,
    ExecutorComparisonReport,
    ExecutorEvaluationSample,
)


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class ExecutorComparisonEvaluator:
    def sample(self, run_directory: str | Path) -> ExecutorEvaluationSample:
        directory = Path(run_directory).resolve(strict=True)
        if not directory.is_dir() or directory.is_symlink():
            raise ExecutorComparisonError("run directory is unsafe")
        try:
            records = LocalRunLedger(
                directory / "records.jsonl", run_id=directory.name
            ).read_all()
            if not records or records[0].stage is not RunRecordStage.EXECUTION:
                raise ExecutorComparisonError("run has no execution evidence")
            context = records[0].payload["context"]
            task = TaskSpec.model_validate(context["task"])
            profile = ValidationProfile.model_validate(context["validation_profile"])
            outcome = TaskExecutionOutcome.model_validate(records[0].payload["outcome"])
        except (KeyError, OSError, TypeError, ValueError) as exc:
            if isinstance(exc, ExecutorComparisonError):
                raise
            raise ExecutorComparisonError("run evaluation evidence is invalid") from exc
        verification = outcome.verification_report
        usage = outcome.agent_run.usage or {}
        duration = max(
            0,
            int(
                (outcome.agent_run.finished_at - outcome.agent_run.started_at)
                .total_seconds()
                * 1000
            ),
        )
        human_interventions = 0
        terminal_state = outcome.state.value
        for record in records[1:]:
            if record.stage is RunRecordStage.IMPLEMENTATION_REVIEW:
                review = ReviewReport.model_validate(record.payload["review_report"])
                disposition = ReviewDisposition.model_validate(
                    record.payload["disposition"]
                )
                terminal_state = disposition.state.value
                if review.reviewer_actor_type is ActorType.HUMAN:
                    human_interventions += 1
            elif record.stage is RunRecordStage.DECISION_APPLICATION:
                HumanDecision.model_validate(record.payload["decision"])
                terminal_state = str(record.payload["transition"]["to_state"])
                human_interventions += 1
            elif record.stage in {
                RunRecordStage.SOURCE_INTEGRATION,
                RunRecordStage.WORKSPACE_CLEANUP,
            }:
                terminal_state = "completed"
        return ExecutorEvaluationSample(
            run_id=outcome.agent_run.run_id,
            task_id=task.task_id,
            executor_type=outcome.agent_run.executor_type,
            task_contract_sha256=hashlib.sha256(
                _canonical(task.model_dump(mode="json"))
            ).hexdigest(),
            validation_contract_sha256=hashlib.sha256(
                _canonical(profile.model_dump(mode="json"))
            ).hexdigest(),
            terminal_state=terminal_state,
            verification_passed=bool(verification and verification.passes_gate),
            input_tokens=max(0, int(usage.get("input_tokens", 0))),
            cached_input_tokens=max(0, int(usage.get("cached_input_tokens", 0))),
            output_tokens=max(0, int(usage.get("output_tokens", 0))),
            cost_microusd=max(0, int(usage.get("cost_microusd", 0))),
            duration_ms=duration,
            human_interventions=human_interventions,
        )

    def evaluate(
        self,
        samples: tuple[ExecutorEvaluationSample, ...],
        *,
        now: datetime | None = None,
    ) -> ExecutorComparisonReport:
        if len(samples) < 2:
            raise ExecutorComparisonError("comparison needs at least two samples")
        ordered = tuple(sorted(samples, key=lambda item: (item.executor_type, item.run_id)))
        first = ordered[0]
        if len({item.executor_type for item in ordered}) < 2:
            raise ExecutorComparisonError("comparison needs at least two executor types")
        if any(
            item.task_id != first.task_id
            or item.task_contract_sha256 != first.task_contract_sha256
            or item.validation_contract_sha256 != first.validation_contract_sha256
            for item in ordered
        ):
            raise ExecutorComparisonError(
                "executor samples must share exact task and validation contracts"
            )
        aggregates = []
        for executor_type in sorted({item.executor_type for item in ordered}):
            group = tuple(item for item in ordered if item.executor_type == executor_type)
            aggregates.append(
                ExecutorAggregate(
                    executor_type=executor_type,
                    runs=len(group),
                    verified_successes=sum(
                        item.verification_passed
                        and item.terminal_state
                        not in {"failed", "repairing", "cancelled"}
                        for item in group
                    ),
                    total_input_tokens=sum(item.input_tokens for item in group),
                    total_cached_input_tokens=sum(
                        item.cached_input_tokens for item in group
                    ),
                    total_output_tokens=sum(item.output_tokens for item in group),
                    total_cost_microusd=sum(item.cost_microusd for item in group),
                    total_duration_ms=sum(item.duration_ms for item in group),
                    total_human_interventions=sum(
                        item.human_interventions for item in group
                    ),
                )
            )
        digest = hashlib.sha256(
            _canonical([item.model_dump(mode="json") for item in ordered])
        ).hexdigest()
        return ExecutorComparisonReport(
            comparison_id=f"comparison-{digest[:24]}",
            task_id=first.task_id,
            task_contract_sha256=first.task_contract_sha256,
            validation_contract_sha256=first.validation_contract_sha256,
            samples=ordered,
            aggregates=tuple(aggregates),
            generated_at=now or datetime.now(UTC),
        )

    def evaluate_runs(
        self,
        run_directories: tuple[str | Path, ...],
        *,
        now: datetime | None = None,
    ) -> ExecutorComparisonReport:
        return self.evaluate(
            tuple(self.sample(path) for path in run_directories),
            now=now,
        )


__all__ = ["ExecutorComparisonEvaluator"]
