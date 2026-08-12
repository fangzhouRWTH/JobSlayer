from __future__ import annotations

from pydantic import Field, model_validator

from jobslayer.domain.models import (
    AgentInvocation,
    DomainModel,
    TaskExecutionAuthorization,
    TaskExecutionIntent,
    TaskSpec,
    TestbedInspection,
    TestbedSpec,
    ValidationProfile,
)


class LocalExecutionContext(DomainModel):
    """Normalized local context projection stored in a Phase 0 run record.

    Parsing the projection through current typed contracts applies compatible
    defaults before comparison. This preserves old hash-linked records when a
    later schema revision adds an optional/defaulted field without weakening
    validation or mutating historical evidence.
    """

    runbook_path: str = Field(min_length=1)
    runbook_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    testbed: TestbedSpec
    testbed_inspection: TestbedInspection
    task: TaskSpec
    invocation: AgentInvocation
    validation_profile: ValidationProfile
    authorization: TaskExecutionAuthorization

    @model_validator(mode="after")
    def validate_bindings(self) -> LocalExecutionContext:
        if self.testbed.testbed_id != self.testbed_inspection.testbed_id:
            raise ValueError("execution context inspection belongs elsewhere")
        if not self.testbed_inspection.valid_local_baseline:
            raise ValueError("execution context requires a valid local baseline")
        if self.task.project_id != self.testbed.testbed_id:
            raise ValueError("execution context task belongs to another testbed")
        if self.task.task_id != self.invocation.run_spec.task_id:
            raise ValueError("execution context invocation belongs to another task")
        if self.authorization.task_id != self.task.task_id:
            raise ValueError("execution context authorization belongs to another task")
        if self.validation_profile.profile_id != self.task.validation_profile:
            raise ValueError("execution context validation profile does not match task")
        return self


class LocalExecutionIntentEnvelope(DomainModel):
    """Local source binding around a provider-neutral execution intent."""

    schema_version: str = "1.0"
    intent: TaskExecutionIntent
    runbook_path: str = Field(min_length=1)
    runbook_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    testbed: TestbedSpec
    testbed_inspection: TestbedInspection

    @model_validator(mode="after")
    def validate_testbed_binding(self) -> LocalExecutionIntentEnvelope:
        if self.testbed.testbed_id != self.testbed_inspection.testbed_id:
            raise ValueError("execution intent testbed inspection belongs elsewhere")
        if not self.testbed_inspection.valid_local_baseline:
            raise ValueError("execution intent requires a valid local testbed baseline")
        if self.intent.task.project_id != self.testbed.testbed_id:
            raise ValueError("execution intent task belongs to another testbed")
        return self

    def execution_context(self) -> LocalExecutionContext:
        return LocalExecutionContext(
            runbook_path=self.runbook_path,
            runbook_sha256=self.runbook_sha256,
            testbed=self.testbed,
            testbed_inspection=self.testbed_inspection,
            task=self.intent.task,
            invocation=self.intent.invocation,
            validation_profile=self.intent.validation_profile,
            authorization=self.intent.authorization,
        )

    def context_payload(self) -> dict[str, object]:
        return self.execution_context().model_dump(mode="json")


__all__ = ["LocalExecutionContext", "LocalExecutionIntentEnvelope"]
