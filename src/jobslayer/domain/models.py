from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import hashlib
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainModel(BaseModel):
    """Strict and immutable base for records crossing system boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskState(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    PLAN_REVIEW = "plan_review"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    REVIEWING = "reviewing"
    MERGE_REVIEW = "merge_review"
    INTEGRATING = "integrating"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ActorType(str, Enum):
    AGENT = "agent"
    HUMAN = "human"
    POLICY = "policy"
    SYSTEM = "system"


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class TestbedStatus(str, Enum):
    PLANNED = "planned"
    BOOTSTRAPPING = "bootstrapping"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class CommandStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class DecisionKind(str, Enum):
    PLAN_REVIEW = "plan_review"
    MERGE_REVIEW = "merge_review"
    PERMISSION_ELEVATION = "permission_elevation"
    CANCELLATION = "cancellation"
    RISK_ACCEPTANCE = "risk_acceptance"
    CLARIFICATION = "clarification"


class AgentRunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ReviewStatus(str, Enum):
    ACCEPTED = "accepted"
    CHANGES_REQUESTED = "changes_requested"


class TaskExecutionStatus(str, Enum):
    AWAITING_REVIEW = "awaiting_review"
    REPAIR_REQUIRED = "repair_required"
    FAILED = "failed"


class ReviewDispositionStatus(str, Enum):
    AWAITING_MERGE_DECISION = "awaiting_merge_decision"
    REPAIR_REQUIRED = "repair_required"


def _validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value.strip() or path.is_absolute() or ".." in path.parts:
        raise ValueError("paths must be non-empty repository-relative POSIX paths")
    if value.startswith("./"):
        raise ValueError("paths must be normalized and must not start with './'")
    return value


class TaskSpec(DomainModel):
    schema_version: str = "1.0"
    task_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    base_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    allowed_paths: tuple[str, ...] = Field(min_length=1)
    forbidden_paths: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    validation_profile: str = Field(min_length=1)
    risk: RiskLevel
    deadline: datetime | None = None
    max_cost_usd: float | None = Field(default=None, ge=0)

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def validate_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_validate_relative_path(path) for path in paths)
        if len(normalized) != len(set(normalized)):
            raise ValueError("paths must not contain duplicates")
        return normalized

    @field_validator("required_capabilities", "acceptance_criteria")
    @classmethod
    def validate_non_empty_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("list items must not be blank")
        return values


class RepositoryLocation(DomainModel):
    clone_url: str = Field(min_length=1)
    alternative_clone_urls: tuple[str, ...] = ()
    default_branch: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_urls(self) -> RepositoryLocation:
        urls = (self.clone_url, *self.alternative_clone_urls)
        if len(urls) != len(set(urls)):
            raise ValueError("repository clone URLs must be unique")
        return self


class TestbedBaseline(DomainModel):
    """A fixed external repository revision available to governed tasks."""

    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    tag: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )
    published: bool
    verification_command: tuple[str, ...] = Field(min_length=1)

    @field_validator("tag")
    @classmethod
    def validate_git_tag(cls, value: str) -> str:
        components = value.split("/")
        if (
            ".." in value
            or any(
                not component
                or component.startswith(".")
                or component.endswith((".", ".lock"))
                for component in components
            )
        ):
            raise ValueError("tag must be a normalized safe Git ref name")
        return value

    @field_validator("verification_command")
    @classmethod
    def validate_verification_command(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not argument.strip() for argument in value):
            raise ValueError("verification command arguments must not be blank")
        return value


class TestbedSpec(DomainModel):
    """Registration of an external project used for governed experiments."""

    schema_version: str = "1.0"
    testbed_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    status: TestbedStatus
    repository: RepositoryLocation
    baseline: TestbedBaseline | None = None
    local_checkout_hint: str | None = None
    architecture_areas: tuple[str, ...] = Field(min_length=1)
    capability_targets: tuple[str, ...] = Field(min_length=1)

    @field_validator("architecture_areas", "capability_targets")
    @classmethod
    def validate_unique_non_empty_items(
        cls, values: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("list items must not be blank")
        if len(values) != len(set(values)):
            raise ValueError("list items must be unique")
        return values


class TestbedInspection(DomainModel):
    """Read-only facts observed from a local external testbed checkout."""

    schema_version: str = "1.0"
    testbed_id: str = Field(min_length=1)
    checkout_path: str = Field(min_length=1)
    baseline_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    tag: str = Field(min_length=1)
    tag_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    origin_url: str | None = None
    working_tree_clean: bool
    head_matches_baseline: bool
    tag_matches_baseline: bool
    origin_registered: bool
    baseline_published: bool

    @model_validator(mode="after")
    def validate_match_claims(self) -> TestbedInspection:
        if self.head_matches_baseline != (
            self.head_commit == self.baseline_commit
        ):
            raise ValueError("head_matches_baseline must match the observed commits")
        if self.tag_matches_baseline != (
            self.tag_commit == self.baseline_commit
        ):
            raise ValueError("tag_matches_baseline must match the observed commits")
        return self

    @property
    def valid_local_baseline(self) -> bool:
        return all(
            (
                self.working_tree_clean,
                self.head_matches_baseline,
                self.tag_matches_baseline,
                self.origin_registered,
            )
        )


class WorkspaceSpec(DomainModel):
    schema_version: str = "1.0"
    workspace_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    task_id: str = Field(min_length=1)
    base_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")

    @field_validator("workspace_id")
    @classmethod
    def validate_git_safe_workspace_id(cls, value: str) -> str:
        if ".." in value or value.endswith(".") or value.lower().endswith(".lock"):
            raise ValueError("workspace id must also be a safe Git branch component")
        return value


class WorkspaceManifest(DomainModel):
    schema_version: str = "1.0"
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    repository_root: str = Field(min_length=1)
    path: str = Field(min_length=1)
    requested_base_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    resolved_base_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    branch_name: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkspaceInspection(DomainModel):
    schema_version: str = "1.0"
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    head_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    branch_name: str = Field(min_length=1)
    changed_paths: tuple[str, ...] = ()
    working_tree_clean: bool

    @model_validator(mode="after")
    def validate_changed_paths(self) -> WorkspaceInspection:
        if len(self.changed_paths) != len(set(self.changed_paths)):
            raise ValueError("changed paths must be unique")
        for path in self.changed_paths:
            _validate_relative_path(path)
        return self


class WorkspaceRemovalInspection(DomainModel):
    """Read-only evidence that cleanup removed only the registered worktree."""

    schema_version: str = "1.0"
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    path_absent: bool
    registration_absent: bool
    branch_name: str = Field(min_length=1)
    branch_commit: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{40,64}$"
    )
    expected_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")

    @property
    def safely_removed(self) -> bool:
        return (
            self.path_absent
            and self.registration_absent
            and self.branch_commit == self.expected_commit
        )


class WorkspacePatch(DomainModel):
    schema_version: str = "1.0"
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    base_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    changed_paths: tuple[str, ...]
    patch_text: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("changed_paths")
    @classmethod
    def validate_patch_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        if len(paths) != len(set(paths)):
            raise ValueError("changed paths must be unique")
        for path in paths:
            _validate_relative_path(path)
        return paths

    @model_validator(mode="after")
    def validate_patch_hash(self) -> WorkspacePatch:
        calculated = hashlib.sha256(self.patch_text.encode("utf-8")).hexdigest()
        if self.sha256 != calculated:
            raise ValueError("workspace patch sha256 does not match patch_text")
        return self


class SourceIntegrationResult(DomainModel):
    """Evidence that one reviewed patch became the target source revision."""

    schema_version: str = "1.0"
    integration_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    repository_root: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    base_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    target_previous_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    target_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    source_patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_paths: tuple[str, ...] = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    integrated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("changed_paths")
    @classmethod
    def validate_integration_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        if len(paths) != len(set(paths)):
            raise ValueError("integration changed paths must be unique")
        for path in paths:
            _validate_relative_path(path)
        return paths

    @model_validator(mode="after")
    def validate_fast_forward_claim(self) -> SourceIntegrationResult:
        if self.target_previous_commit != self.base_commit:
            raise ValueError("integration must start from the reviewed base commit")
        if self.target_commit != self.commit:
            raise ValueError("target commit must be the integrated source commit")
        return self


class CommandRule(DomainModel):
    rule_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    argv_prefix: tuple[str, ...] = Field(min_length=1)
    allow_additional_arguments: bool = False
    accepted_exit_codes: tuple[int, ...] = (0,)
    max_timeout_seconds: float | None = Field(default=None, gt=0)

    @field_validator("argv_prefix")
    @classmethod
    def validate_rule_arguments(cls, arguments: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or "\0" in value for value in arguments):
            raise ValueError("command arguments must be non-empty and contain no NUL")
        return arguments

    @field_validator("accepted_exit_codes")
    @classmethod
    def validate_exit_codes(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if not values:
            raise ValueError("at least one accepted exit code is required")
        if len(values) != len(set(values)):
            raise ValueError("accepted exit codes must be unique")
        return values


class CommandPolicy(DomainModel):
    schema_version: str = "1.0"
    policy_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    rules: tuple[CommandRule, ...] = Field(min_length=1)
    max_timeout_seconds: float = Field(default=300, gt=0)
    max_output_bytes_per_stream: int = Field(default=1_000_000, ge=1)

    @model_validator(mode="after")
    def validate_unique_rules(self) -> CommandPolicy:
        rule_ids = tuple(rule.rule_id for rule in self.rules)
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("command rule ids must be unique")
        prefixes = tuple(rule.argv_prefix for rule in self.rules)
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("command rule prefixes must be unique")
        return self


class CommandRequest(DomainModel):
    schema_version: str = "1.0"
    command_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: float = Field(default=60, gt=0)

    @field_validator("argv")
    @classmethod
    def validate_arguments(cls, arguments: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or "\0" in value for value in arguments):
            raise ValueError("command arguments must be non-empty and contain no NUL")
        return arguments

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        return _validate_relative_path(value)


class CommandResult(DomainModel):
    schema_version: str = "1.0"
    command_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    argv: tuple[str, ...]
    cwd: str
    status: CommandStatus
    exit_code: int | None
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)
    stdout: str
    stderr: str
    stdout_bytes: int = Field(ge=0)
    stderr_bytes: int = Field(ge=0)
    stdout_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stderr_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stdout_truncated: bool
    stderr_truncated: bool

    @model_validator(mode="after")
    def validate_timing_and_status(self) -> CommandResult:
        if self.finished_at < self.started_at:
            raise ValueError("command finish time must not precede start time")
        if self.status is CommandStatus.TIMED_OUT and self.exit_code is not None:
            raise ValueError("timed out commands must not claim a normal exit code")
        if self.status is not CommandStatus.TIMED_OUT and self.exit_code is None:
            raise ValueError("completed commands require an exit code")
        return self


class ValidationCheckSpec(DomainModel):
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    title: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: float = Field(default=60, gt=0)
    required: bool = True

    @field_validator("argv")
    @classmethod
    def validate_check_arguments(cls, arguments: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or "\0" in value for value in arguments):
            raise ValueError("validation arguments must be non-empty and contain no NUL")
        return arguments

    @field_validator("cwd")
    @classmethod
    def validate_check_cwd(cls, value: str) -> str:
        return _validate_relative_path(value)


class ValidationProfile(DomainModel):
    schema_version: str = "1.0"
    profile_id: str = Field(min_length=1)
    command_policy: CommandPolicy
    checks: tuple[ValidationCheckSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_checks_against_policy(self) -> ValidationProfile:
        check_ids = tuple(check.check_id for check in self.checks)
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("validation check ids must be unique")
        if not any(check.required for check in self.checks):
            raise ValueError("a validation profile needs at least one required check")
        for check in self.checks:
            matching_rules = []
            for rule in self.command_policy.rules:
                prefix_length = len(rule.argv_prefix)
                prefix_matches = check.argv[:prefix_length] == rule.argv_prefix
                length_matches = (
                    rule.allow_additional_arguments
                    or len(check.argv) == prefix_length
                )
                if prefix_matches and length_matches:
                    matching_rules.append(rule)
            if not matching_rules:
                raise ValueError(
                    f"validation check {check.check_id} has no command policy rule"
                )
            rule = max(matching_rules, key=lambda item: len(item.argv_prefix))
            timeout_limit = min(
                self.command_policy.max_timeout_seconds,
                rule.max_timeout_seconds
                or self.command_policy.max_timeout_seconds,
            )
            if check.timeout_seconds > timeout_limit:
                raise ValueError(
                    f"validation check {check.check_id} exceeds its timeout policy"
                )
        return self


class EvidenceSummary(DomainModel):
    evidence_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class DecisionOption(DomainModel):
    option_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    consequences: str = Field(min_length=1)
    recommended: bool = False


class DecisionCard(DomainModel):
    schema_version: str = "1.0"
    card_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    decision_kind: DecisionKind
    title: str = Field(min_length=1)
    decision_required: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    risk: RiskLevel
    reversible: bool
    affected_artifact_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceSummary, ...] = Field(min_length=1)
    options: tuple[DecisionOption, ...] = Field(min_length=2)
    default_option_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_options(self) -> DecisionCard:
        option_ids = tuple(option.option_id for option in self.options)
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("decision option ids must be unique")
        if self.default_option_id not in option_ids:
            raise ValueError("default option must identify an available option")
        recommended = tuple(
            option.option_id for option in self.options if option.recommended
        )
        if recommended != (self.default_option_id,):
            raise ValueError("the default option must be the single recommendation")
        return self


class HumanDecision(DomainModel):
    schema_version: str = "1.0"
    decision_id: str = Field(min_length=1)
    card_id: str = Field(min_length=1)
    card_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    selected_option_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalCredentialProof(DomainModel):
    """Provider-neutral proof metadata attached by an authority issuer."""

    schema_version: str = "1.0"
    proof_type: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    issuer: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    subject_session_id: str = Field(min_length=1)
    authorization_policy_id: str = Field(min_length=1)
    authorized_action: str = Field(min_length=1)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApprovalAuthority(DomainModel):
    schema_version: str = "1.0"
    authorization_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    allowed_decision_kinds: tuple[DecisionKind, ...] = Field(min_length=1)
    issued_at: datetime
    valid_until: datetime
    proof: ApprovalCredentialProof | None = None

    @model_validator(mode="after")
    def validate_authority_window(self) -> ApprovalAuthority:
        if self.issued_at.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("authority timestamps must include a timezone")
        if self.valid_until <= self.issued_at:
            raise ValueError("authority must expire after it is issued")
        if len(self.allowed_decision_kinds) != len(set(self.allowed_decision_kinds)):
            raise ValueError("allowed decision kinds must be unique")
        return self


class ExecutionCredentialProof(DomainModel):
    """Provider-neutral signature metadata for one execution authority."""

    schema_version: str = "1.0"
    proof_type: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    issuer: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    subject_session_id: str = Field(min_length=1)
    authorization_policy_id: str = Field(min_length=1)
    authorized_action: str = Field(min_length=1)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskExecutionAuthorization(DomainModel):
    schema_version: str = "1.0"
    authorization_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str | None = None
    actor_type: ActorType
    actor_id: str = Field(min_length=1)
    maximum_risk: RiskLevel
    issued_at: datetime
    valid_until: datetime
    proof: ExecutionCredentialProof | None = None

    @model_validator(mode="after")
    def validate_execution_authority(self) -> TaskExecutionAuthorization:
        if self.actor_type not in {ActorType.HUMAN, ActorType.POLICY}:
            raise ValueError("execution authority requires a human or policy actor")
        if self.issued_at.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("authority timestamps must include a timezone")
        if self.valid_until <= self.issued_at:
            raise ValueError("authority must expire after it is issued")
        return self


class ReviewReport(DomainModel):
    schema_version: str = "1.0"
    review_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    reviewer_actor_type: ActorType
    reviewer_id: str = Field(min_length=1)
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ReviewStatus
    summary: str = Field(min_length=1)
    findings: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_reviewer(self) -> ReviewReport:
        if self.reviewer_actor_type not in {ActorType.AGENT, ActorType.HUMAN}:
            raise ValueError("reviewer must be an agent or human")
        return self


class AgentRunSpec(DomainModel):
    schema_version: str = "1.0"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    executor_type: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
    context_package_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    permission_profile: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    max_attempts: int = Field(default=1, ge=1)
    max_repairs: int = Field(default=0, ge=0)
    maximum_input_tokens: int | None = Field(default=None, gt=0)
    maximum_output_tokens: int | None = Field(default=None, gt=0)
    maximum_context_bytes: int | None = Field(default=None, gt=0)
    output_schema: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_repair_limit(self) -> AgentRunSpec:
        if self.max_repairs >= self.max_attempts:
            raise ValueError("max_repairs must be smaller than max_attempts")
        return self


class AgentInvocation(DomainModel):
    schema_version: str = "1.0"
    run_spec: AgentRunSpec
    prompt: str = Field(min_length=1, max_length=200_000)


class TaskExecutionIntent(DomainModel):
    """Immutable authorization and typed inputs for one execution attempt."""

    schema_version: str = "1.0"
    intent_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    task: TaskSpec
    invocation: AgentInvocation
    validation_profile: ValidationProfile
    authorization: TaskExecutionAuthorization
    prepared_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_execution_bindings(self) -> TaskExecutionIntent:
        if self.run_id != self.invocation.run_spec.run_id:
            raise ValueError("execution intent run id does not match invocation")
        if self.task.task_id != self.invocation.run_spec.task_id:
            raise ValueError("execution intent task does not match invocation")
        if self.authorization.task_id != self.task.task_id:
            raise ValueError("execution intent authorization belongs to another task")
        if self.validation_profile.profile_id != self.task.validation_profile:
            raise ValueError("execution intent validation profile does not match task")
        if (
            self.prepared_at < self.authorization.issued_at
            or self.prepared_at >= self.authorization.valid_until
        ):
            raise ValueError("execution intent was prepared outside authorization window")
        return self


class AgentRunHandle(DomainModel):
    schema_version: str = "1.0"
    run_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    executor_type: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    started_at: datetime


class AgentCancellationResult(DomainModel):
    schema_version: str = "1.0"
    run_id: str = Field(min_length=1)
    cancellation_requested: bool
    already_terminal: bool
    status: AgentRunStatus
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentRunResult(DomainModel):
    schema_version: str = "1.0"
    run_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    executor_type: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    status: AgentRunStatus
    exit_code: int | None
    event_count: int = Field(ge=0)
    final_message: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    raw_event_log_path: str = Field(min_length=1)
    raw_event_log_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stderr_log_path: str = Field(min_length=1)
    stderr_log_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    finished_at: datetime
    error_summary: str | None = None

    @model_validator(mode="after")
    def validate_agent_result(self) -> AgentRunResult:
        if self.finished_at < self.started_at:
            raise ValueError("agent run finish time must not precede start time")
        if self.status in {AgentRunStatus.CANCELLED, AgentRunStatus.TIMED_OUT}:
            if self.exit_code is not None:
                raise ValueError("cancelled or timed-out runs have no normal exit code")
        elif self.exit_code is None:
            raise ValueError("completed or failed runs require an exit code")
        return self


class RunEvent(DomainModel):
    schema_version: str = "1.0"
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ArtifactManifest(DomainModel):
    schema_version: str = "1.0"
    artifact_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str | None = None
    artifact_type: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    producer: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CheckResult(DomainModel):
    check_id: str = Field(min_length=1)
    status: CheckStatus
    required: bool = True
    command: tuple[str, ...] | None = None
    artifact_ids: tuple[str, ...] = ()
    summary: str = Field(min_length=1)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerificationReport(DomainModel):
    schema_version: str = "1.0"
    report_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    source_commit: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    source_patch_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    checks: tuple[CheckResult, ...] = Field(min_length=1)
    required_checks_passed: bool
    regressions_detected: bool = False
    unresolved_risks: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_required_check_claim(self) -> VerificationReport:
        if not any(check.required for check in self.checks):
            raise ValueError("a verification report must contain a required check")
        calculated = all(
            check.status is CheckStatus.PASSED
            for check in self.checks
            if check.required
        )
        if self.required_checks_passed != calculated:
            raise ValueError(
                "required_checks_passed must match the statuses of all required checks"
            )
        return self

    @property
    def passes_gate(self) -> bool:
        return (
            self.required_checks_passed
            and not self.regressions_detected
            and not self.unresolved_risks
        )


class TaskExecutionOutcome(DomainModel):
    schema_version: str = "1.0"
    task_id: str = Field(min_length=1)
    status: TaskExecutionStatus
    state: TaskState
    workspace: WorkspaceManifest
    task_artifact: ArtifactManifest
    authorization_artifact: ArtifactManifest
    validation_profile_artifact: ArtifactManifest
    agent_run: AgentRunResult
    agent_run_artifact: ArtifactManifest
    raw_event_artifact: ArtifactManifest
    stderr_artifact: ArtifactManifest
    patch: WorkspacePatch | None = None
    patch_artifact: ArtifactManifest | None = None
    verification_report: VerificationReport | None = None
    verification_artifact: ArtifactManifest | None = None
    failure_artifact: ArtifactManifest | None = None

    @model_validator(mode="after")
    def validate_execution_evidence(self) -> TaskExecutionOutcome:
        expected_state = {
            TaskExecutionStatus.AWAITING_REVIEW: TaskState.REVIEWING,
            TaskExecutionStatus.REPAIR_REQUIRED: TaskState.REPAIRING,
            TaskExecutionStatus.FAILED: TaskState.FAILED,
        }[self.status]
        if self.state is not expected_state:
            raise ValueError("execution status does not match its task state")
        if self.workspace.task_id != self.task_id:
            raise ValueError("workspace belongs to a different task")
        if self.agent_run.workspace_id != self.workspace.workspace_id:
            raise ValueError("agent result belongs to a different workspace")

        artifacts = (
            self.task_artifact,
            self.authorization_artifact,
            self.validation_profile_artifact,
            self.agent_run_artifact,
            self.raw_event_artifact,
            self.stderr_artifact,
            self.patch_artifact,
            self.verification_artifact,
            self.failure_artifact,
        )
        if any(
            artifact is not None and artifact.task_id != self.task_id
            for artifact in artifacts
        ):
            raise ValueError("execution artifact belongs to a different task")
        if (self.patch is None) != (self.patch_artifact is None):
            raise ValueError("patch and patch artifact must be present together")
        if (self.verification_report is None) != (
            self.verification_artifact is None
        ):
            raise ValueError(
                "verification report and artifact must be present together"
            )
        if self.patch is not None:
            if (
                self.patch.task_id != self.task_id
                or self.patch.workspace_id != self.workspace.workspace_id
            ):
                raise ValueError("patch belongs to a different task or workspace")
            assert self.patch_artifact is not None
            if self.patch_artifact.sha256 != self.patch.sha256:
                raise ValueError("patch artifact hash does not match the patch")
        if self.verification_report is not None:
            if self.patch is None:
                raise ValueError("verification report requires a patch")
            if self.verification_report.task_id != self.task_id:
                raise ValueError("verification report belongs to a different task")
            if self.verification_report.source_patch_sha256 != self.patch.sha256:
                raise ValueError("verification report evaluated a different patch")

        if self.status is TaskExecutionStatus.FAILED:
            if self.failure_artifact is None:
                raise ValueError("failed execution requires a failure artifact")
        else:
            if self.patch is None or self.verification_report is None:
                raise ValueError("review or repair outcome requires verification evidence")
            if self.failure_artifact is not None:
                raise ValueError("non-failed execution cannot contain a failure artifact")
            passes = self.verification_report.passes_gate
            if passes != (self.status is TaskExecutionStatus.AWAITING_REVIEW):
                raise ValueError("execution status contradicts its verification gate")
        return self


class MergeReviewPackage(DomainModel):
    schema_version: str = "1.0"
    task_id: str = Field(min_length=1)
    patch: WorkspacePatch
    verification_report: VerificationReport
    review_report: ReviewReport
    review_artifact: ArtifactManifest
    decision_card: DecisionCard
    decision_card_artifact: ArtifactManifest

    @model_validator(mode="after")
    def validate_merge_evidence(self) -> MergeReviewPackage:
        if any(
            item.task_id != self.task_id
            for item in (
                self.patch,
                self.verification_report,
                self.review_report,
                self.review_artifact,
                self.decision_card,
                self.decision_card_artifact,
            )
        ):
            raise ValueError("merge review evidence belongs to different tasks")
        if self.patch.sha256 != self.review_report.patch_sha256:
            raise ValueError("review report evaluated a different patch")
        if self.verification_report.source_patch_sha256 != self.patch.sha256:
            raise ValueError("verification report evaluated a different patch")
        if not self.verification_report.passes_gate:
            raise ValueError("merge review package requires passing verification")
        return self


class ReviewDisposition(DomainModel):
    schema_version: str = "1.0"
    task_id: str = Field(min_length=1)
    status: ReviewDispositionStatus
    state: TaskState
    review_report: ReviewReport
    review_artifact: ArtifactManifest
    merge_review_package: MergeReviewPackage | None = None

    @model_validator(mode="after")
    def validate_disposition(self) -> ReviewDisposition:
        expected_state = {
            ReviewDispositionStatus.AWAITING_MERGE_DECISION: TaskState.MERGE_REVIEW,
            ReviewDispositionStatus.REPAIR_REQUIRED: TaskState.REPAIRING,
        }[self.status]
        if self.state is not expected_state:
            raise ValueError("review disposition status does not match its task state")
        has_package = self.merge_review_package is not None
        if has_package != (
            self.status is ReviewDispositionStatus.AWAITING_MERGE_DECISION
        ):
            raise ValueError("only an accepted review may contain a merge package")
        return self


class TransitionRecord(DomainModel):
    schema_version: str = "1.0"
    task_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    from_state: TaskState
    to_state: TaskState
    actor_type: ActorType
    actor_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    previous_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
