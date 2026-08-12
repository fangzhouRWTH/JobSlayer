"""Deterministically derive an execution budget from source-controlled contracts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from jobslayer.domain.models import AgentRunSpec, TaskSpec
from jobslayer.governance import BudgetError, ExecutionBudget


def execution_budget_from_contracts(
    task: TaskSpec,
    run_spec: AgentRunSpec,
) -> ExecutionBudget:
    if run_spec.task_id != task.task_id:
        raise BudgetError("run budget contracts belong to different tasks")
    if (
        run_spec.maximum_input_tokens is None
        or run_spec.maximum_output_tokens is None
        or task.max_cost_usd is None
    ):
        raise BudgetError(
            "execution requires explicit input, output, and cost limits"
        )
    try:
        microusd = int(
            (Decimal(str(task.max_cost_usd)) * Decimal(1_000_000)).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
    except (InvalidOperation, ValueError) as exc:
        raise BudgetError("task cost limit cannot be represented") from exc
    return ExecutionBudget(
        budget_id=f"budget-{run_spec.run_id}",
        task_id=task.task_id,
        run_id=run_spec.run_id,
        maximum_input_tokens=run_spec.maximum_input_tokens,
        maximum_output_tokens=run_spec.maximum_output_tokens,
        maximum_cost_microusd=microusd,
        maximum_duration_ms=run_spec.timeout_seconds * 1000,
        maximum_attempts=run_spec.max_attempts,
        maximum_repairs=run_spec.max_repairs,
    )


__all__ = ["execution_budget_from_contracts"]
