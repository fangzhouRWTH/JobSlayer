"""Provider-neutral contracts for semantic, elastic UI design descriptions."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath
from typing import Protocol

from pydantic import Field, field_validator, model_validator

from jobslayer.domain.models import ActorType, DomainModel


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class UIDesignIntentState(str, Enum):
    """How an implementation agent must treat one semantic design unit."""

    DIRTY = "dirty"
    PLANNED = "planned"
    STABLE = "stable"


class UIDesignRelationKind(str, Enum):
    CONTAINS = "contains"
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    ABOVE = "above"
    BELOW = "below"
    ADJACENT_TO = "adjacent_to"
    OVERLAYS = "overlays"


class UIDesignRequirementStrength(str, Enum):
    MUST = "must"
    SHOULD = "should"
    MAY = "may"
    MUST_NOT = "must_not"


class UIDesignDifference(str, Enum):
    NONE = "none"
    MINOR = "minor"
    MATERIAL = "material"
    UNKNOWN = "unknown"


class UIDesignExecutionAction(str, Enum):
    REFERENCE_ONLY = "reference_only"
    REFINE_DESCRIPTION = "refine_description"
    INSPECT = "inspect"
    VERIFY_ONLY = "verify_only"
    IMPLEMENT = "implement"
    CLARIFY = "clarify"


def _bounded_strings(values: tuple[str, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    if any(not value.strip() or len(value) > 1_000 for value in values):
        raise ValueError(f"{field_name} needs non-blank bounded strings")


class UIDesignAuthorship(DomainModel):
    actor_type: ActorType
    actor_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    agent_adapter: str | None = Field(default=None, max_length=160)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_authorship(self) -> UIDesignAuthorship:
        if self.created_at.tzinfo is None:
            raise ValueError("UI design authorship timestamp needs a timezone")
        if self.actor_type == ActorType.AGENT:
            if self.agent_adapter is None or not self.agent_adapter.strip():
                raise ValueError("agent-authored UI design needs an adapter id")
        elif self.agent_adapter is not None:
            raise ValueError("only agent-authored UI design may name an agent adapter")
        return self


class UIDesignTrackedUnit(DomainModel):
    unit_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    state: UIDesignIntentState
    intent: str = Field(min_length=1, max_length=8_000)
    stability_evidence_ids: tuple[str, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def validate_tracking(self) -> UIDesignTrackedUnit:
        if not self.intent.strip():
            raise ValueError("UI design unit intent must not be blank")
        _bounded_strings(self.stability_evidence_ids, "stability evidence ids")
        if self.state == UIDesignIntentState.STABLE and not self.stability_evidence_ids:
            raise ValueError("stable UI design units require evidence ids")
        return self


class UIDesignRegion(UIDesignTrackedUnit):
    label: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=240)
    responsibilities: tuple[str, ...] = Field(default=(), max_length=24)
    content: tuple[str, ...] = Field(default=(), max_length=24)
    interactions: tuple[str, ...] = Field(default=(), max_length=24)
    implementation_anchors: tuple[str, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def validate_region(self) -> UIDesignRegion:
        if not self.label.strip() or not self.role.strip():
            raise ValueError("UI design region label and role must not be blank")
        for name, values in (
            ("region responsibilities", self.responsibilities),
            ("region content", self.content),
            ("region interactions", self.interactions),
            ("implementation anchors", self.implementation_anchors),
        ):
            _bounded_strings(values, name)
        for anchor in self.implementation_anchors:
            path = PurePosixPath(anchor)
            if (
                path.is_absolute()
                or ".." in path.parts
                or anchor.startswith("./")
                or "\\" in anchor
            ):
                raise ValueError(
                    "implementation anchors must be normalized repository-relative paths"
                )
        return self


class UIDesignRelation(UIDesignTrackedUnit):
    source_region_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    target_region_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    relation: UIDesignRelationKind
    preferred_ratio: str | None = Field(
        default=None,
        pattern=r"^[1-9][0-9]{0,2}:[1-9][0-9]{0,2}$",
    )
    constraints: tuple[str, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def validate_relation(self) -> UIDesignRelation:
        if self.source_region_id == self.target_region_id:
            raise ValueError("UI design relation cannot reference the same region")
        _bounded_strings(self.constraints, "relation constraints")
        if self.preferred_ratio is not None and self.relation in {
            UIDesignRelationKind.CONTAINS,
            UIDesignRelationKind.OVERLAYS,
        }:
            raise ValueError("containment and overlay relations cannot declare a ratio")
        return self


class UIDesignJourneyStep(DomainModel):
    step_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    actor: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=2_000)
    region_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    expected_feedback: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_step(self) -> UIDesignJourneyStep:
        if any(
            not value.strip()
            for value in (self.actor, self.action, self.expected_feedback)
        ):
            raise ValueError("UI design journey step text must not be blank")
        return self


class UIDesignJourney(UIDesignTrackedUnit):
    label: str = Field(min_length=1, max_length=200)
    trigger: str = Field(min_length=1, max_length=2_000)
    steps: tuple[UIDesignJourneyStep, ...] = Field(min_length=1, max_length=32)
    completion_signal: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_journey(self) -> UIDesignJourney:
        if any(
            not value.strip()
            for value in (self.label, self.trigger, self.completion_signal)
        ):
            raise ValueError("UI design journey text must not be blank")
        step_ids = tuple(step.step_id for step in self.steps)
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("UI design journey step ids must be unique")
        return self


class UIDesignRequirement(UIDesignTrackedUnit):
    strength: UIDesignRequirementStrength
    statement: str = Field(min_length=1, max_length=4_000)
    verification_hint: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_requirement(self) -> UIDesignRequirement:
        if not self.statement.strip() or not self.verification_hint.strip():
            raise ValueError("UI design requirement text must not be blank")
        return self


class UIDesignStableChangeAuthorization(DomainModel):
    unit_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    actor_type: ActorType
    actor_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def validate_authorization(self) -> UIDesignStableChangeAuthorization:
        if self.actor_type not in {ActorType.HUMAN, ActorType.POLICY}:
            raise ValueError("stable UI changes require a human or policy actor")
        if not self.reason.strip():
            raise ValueError("stable UI change reason must not be blank")
        _bounded_strings(self.evidence_ids, "stable change evidence ids")
        return self


class SemanticUIDesign(DomainModel):
    """One self-contained semantic description revision for one page scheme."""

    schema_version: str = "1.0"
    page_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    scheme_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    revision: int = Field(ge=1)
    parent_revision_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    title: str = Field(min_length=1, max_length=240)
    language: str = Field(min_length=2, max_length=32)
    design_intent: str = Field(min_length=1, max_length=12_000)
    non_goals: tuple[str, ...] = Field(default=(), max_length=24)
    authorship: UIDesignAuthorship
    regions: tuple[UIDesignRegion, ...] = Field(min_length=1, max_length=64)
    relations: tuple[UIDesignRelation, ...] = Field(default=(), max_length=128)
    journeys: tuple[UIDesignJourney, ...] = Field(default=(), max_length=64)
    requirements: tuple[UIDesignRequirement, ...] = Field(default=(), max_length=128)
    stable_change_authorizations: tuple[
        UIDesignStableChangeAuthorization, ...
    ] = Field(default=(), max_length=64)
    change_summary: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def validate_description(self) -> SemanticUIDesign:
        if any(
            not value.strip()
            for value in (
                self.title,
                self.language,
                self.design_intent,
                self.change_summary,
            )
        ):
            raise ValueError("UI design description text must not be blank")
        _bounded_strings(self.non_goals, "UI design non-goals")
        if self.revision == 1:
            if self.parent_revision_sha256 is not None:
                raise ValueError("first UI design revision cannot have a parent hash")
            if self.stable_change_authorizations:
                raise ValueError("first UI design revision cannot authorize stable changes")
        elif self.parent_revision_sha256 is None:
            raise ValueError("later UI design revisions require a parent hash")

        units = self.units()
        if (
            self.revision == 1
            and self.authorship.actor_type == ActorType.AGENT
            and any(unit.state == UIDesignIntentState.STABLE for unit in units)
        ):
            raise ValueError("an Agent-authored first revision cannot declare stable units")
        unit_ids = tuple(unit.unit_id for unit in units)
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("UI design unit ids must be globally unique")
        region_ids = {region.unit_id for region in self.regions}
        for relation in self.relations:
            if (
                relation.source_region_id not in region_ids
                or relation.target_region_id not in region_ids
            ):
                raise ValueError("UI design relation references an unknown region")
        for journey in self.journeys:
            if any(step.region_id not in region_ids for step in journey.steps):
                raise ValueError("UI design journey references an unknown region")

        authorization_ids = tuple(
            authorization.unit_id
            for authorization in self.stable_change_authorizations
        )
        if len(authorization_ids) != len(set(authorization_ids)):
            raise ValueError("stable change authorization unit ids must be unique")
        self._validate_containment_graph(region_ids)
        return self

    def units(self) -> tuple[UIDesignTrackedUnit, ...]:
        return (*self.regions, *self.relations, *self.journeys, *self.requirements)

    def _validate_containment_graph(self, region_ids: set[str]) -> None:
        children: dict[str, list[str]] = {region_id: [] for region_id in region_ids}
        parents: dict[str, str] = {}
        for relation in self.relations:
            if relation.relation != UIDesignRelationKind.CONTAINS:
                continue
            child = relation.target_region_id
            if child in parents:
                raise ValueError("UI design region cannot have multiple containers")
            parents[child] = relation.source_region_id
            children[relation.source_region_id].append(child)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(region_id: str) -> None:
            if region_id in visiting:
                raise ValueError("UI design containment graph must be acyclic")
            if region_id in visited:
                return
            visiting.add(region_id)
            for child in children[region_id]:
                visit(child)
            visiting.remove(region_id)
            visited.add(region_id)

        for region_id in region_ids:
            visit(region_id)


def canonical_ui_design_sha256(description: SemanticUIDesign) -> str:
    content = json.dumps(
        description.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def validate_ui_design_revision(
    previous: SemanticUIDesign,
    current: SemanticUIDesign,
) -> None:
    if (previous.page_id, previous.scheme_id) != (
        current.page_id,
        current.scheme_id,
    ):
        raise ValueError("UI design revision changed page or scheme identity")
    if current.revision != previous.revision + 1:
        raise ValueError("UI design revisions must be contiguous")
    if current.parent_revision_sha256 != canonical_ui_design_sha256(previous):
        raise ValueError("UI design parent revision hash does not match")

    previous_units = {unit.unit_id: unit for unit in previous.units()}
    current_units = {unit.unit_id: unit for unit in current.units()}
    changed_stable_ids = {
        unit_id
        for unit_id, unit in previous_units.items()
        if unit.state == UIDesignIntentState.STABLE
        and (
            unit_id not in current_units
            or unit.model_dump(mode="json")
            != current_units[unit_id].model_dump(mode="json")
        )
    }
    authorized_ids = {
        authorization.unit_id
        for authorization in current.stable_change_authorizations
    }
    if changed_stable_ids != authorized_ids:
        missing = changed_stable_ids.difference(authorized_ids)
        unused = authorized_ids.difference(changed_stable_ids)
        details: list[str] = []
        if missing:
            details.append("missing authorization for " + ", ".join(sorted(missing)))
        if unused:
            details.append("unused authorization for " + ", ".join(sorted(unused)))
        raise ValueError("stable UI change authorization mismatch: " + "; ".join(details))
    if current.authorship.actor_type == ActorType.AGENT:
        promoted_ids = {
            unit_id
            for unit_id, unit in current_units.items()
            if unit.state == UIDesignIntentState.STABLE
            and (
                unit_id not in previous_units
                or previous_units[unit_id].state != UIDesignIntentState.STABLE
            )
        }
        if promoted_ids:
            raise ValueError(
                "an Agent cannot promote UI design units to stable: "
                + ", ".join(sorted(promoted_ids))
            )


class UIDesignDescriptorReference(DomainModel):
    page_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    scheme_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    revision: int = Field(ge=1)
    path: str = Field(min_length=1, max_length=512)
    descriptor_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or value.startswith("./")
            or "\\" in value
        ):
            raise ValueError("UI design descriptor path must be normalized and relative")
        return value


class UIDesignActiveBinding(DomainModel):
    page_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    scheme_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    revision: int = Field(ge=1)
    descriptor_sha256: str = Field(pattern=SHA256_PATTERN)
    activated_by_actor_type: ActorType
    activated_by: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    activated_at: datetime
    decision: str = Field(min_length=1, max_length=4_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def validate_binding(self) -> UIDesignActiveBinding:
        if self.activated_by_actor_type not in {ActorType.HUMAN, ActorType.POLICY}:
            raise ValueError("an Agent cannot activate a UI design scheme")
        if self.activated_at.tzinfo is None:
            raise ValueError("UI design activation timestamp needs a timezone")
        if not self.decision.strip():
            raise ValueError("UI design activation decision must not be blank")
        _bounded_strings(self.evidence_ids, "UI design activation evidence ids")
        return self


class UIDesignCatalog(DomainModel):
    schema_version: str = "1.0"
    descriptors: tuple[UIDesignDescriptorReference, ...] = Field(min_length=1)
    active_bindings: tuple[UIDesignActiveBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> UIDesignCatalog:
        descriptor_keys = tuple(
            (item.page_id, item.scheme_id, item.revision)
            for item in self.descriptors
        )
        if len(descriptor_keys) != len(set(descriptor_keys)):
            raise ValueError("UI design descriptor identities must be unique")
        paths = tuple(item.path for item in self.descriptors)
        if len(paths) != len(set(paths)):
            raise ValueError("UI design descriptor paths must be unique")
        active_pages = tuple(item.page_id for item in self.active_bindings)
        if len(active_pages) != len(set(active_pages)):
            raise ValueError("each page must have exactly one active UI design binding")
        registered_pages = {item.page_id for item in self.descriptors}
        if set(active_pages) != registered_pages:
            raise ValueError(
                "every registered UI design page needs exactly one active binding"
            )
        if descriptor_keys != tuple(sorted(descriptor_keys)):
            raise ValueError("UI design descriptor references must be sorted")
        if active_pages != tuple(sorted(active_pages)):
            raise ValueError("UI design active bindings must be sorted by page id")
        return self


class UIDesignStatusCounts(DomainModel):
    dirty: int = Field(ge=0)
    planned: int = Field(ge=0)
    stable: int = Field(ge=0)


class ActiveUIDesign(DomainModel):
    schema_version: str = "1.0"
    binding: UIDesignActiveBinding
    description: SemanticUIDesign
    state_counts: UIDesignStatusCounts


class UIDesignObservation(DomainModel):
    unit_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    difference: UIDesignDifference
    summary: str = Field(min_length=1, max_length=4_000)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def validate_observation(self) -> UIDesignObservation:
        if not self.summary.strip():
            raise ValueError("UI design observation summary must not be blank")
        _bounded_strings(self.evidence_ids, "UI design observation evidence ids")
        if self.difference != UIDesignDifference.UNKNOWN and not self.evidence_ids:
            raise ValueError("known UI differences require observation evidence")
        return self


class UIDesignObservationSet(DomainModel):
    schema_version: str = "1.0"
    page_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    scheme_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    revision: int = Field(ge=1)
    descriptor_sha256: str = Field(pattern=SHA256_PATTERN)
    observations: tuple[UIDesignObservation, ...]

    @model_validator(mode="after")
    def validate_observations(self) -> UIDesignObservationSet:
        unit_ids = tuple(item.unit_id for item in self.observations)
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("UI design observations must be unique by unit id")
        return self


class UIDesignUnitDecision(DomainModel):
    unit_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=128)
    state: UIDesignIntentState
    observed_difference: UIDesignDifference | None
    action: UIDesignExecutionAction
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_ids: tuple[str, ...] = ()


class UIDesignExecutionPlan(DomainModel):
    schema_version: str = "1.0"
    page_id: str
    scheme_id: str
    revision: int
    descriptor_sha256: str = Field(pattern=SHA256_PATTERN)
    decisions: tuple[UIDesignUnitDecision, ...]
    implementation_required: bool
    clarification_required: bool


def assess_ui_design_execution(
    active: ActiveUIDesign,
    observations: UIDesignObservationSet | None = None,
) -> UIDesignExecutionPlan:
    binding = active.binding
    if observations is not None and (
        observations.page_id,
        observations.scheme_id,
        observations.revision,
        observations.descriptor_sha256,
    ) != (
        binding.page_id,
        binding.scheme_id,
        binding.revision,
        binding.descriptor_sha256,
    ):
        raise ValueError("UI design observations do not bind the active revision")
    by_unit = {
        item.unit_id: item
        for item in (() if observations is None else observations.observations)
    }
    known_ids = {unit.unit_id for unit in active.description.units()}
    unknown_ids = set(by_unit).difference(known_ids)
    if unknown_ids:
        raise ValueError(
            "UI design observations reference unknown units: "
            + ", ".join(sorted(unknown_ids))
        )

    decisions: list[UIDesignUnitDecision] = []
    for unit in active.description.units():
        observation = by_unit.get(unit.unit_id)
        difference = observation.difference if observation else None
        evidence_ids = observation.evidence_ids if observation else ()
        action, reason = _execution_decision(unit.state, difference)
        decisions.append(
            UIDesignUnitDecision(
                unit_id=unit.unit_id,
                state=unit.state,
                observed_difference=difference,
                action=action,
                reason=reason,
                evidence_ids=evidence_ids,
            )
        )
    return UIDesignExecutionPlan(
        page_id=binding.page_id,
        scheme_id=binding.scheme_id,
        revision=binding.revision,
        descriptor_sha256=binding.descriptor_sha256,
        decisions=tuple(decisions),
        implementation_required=any(
            item.action == UIDesignExecutionAction.IMPLEMENT for item in decisions
        ),
        clarification_required=any(
            item.action
            in {
                UIDesignExecutionAction.CLARIFY,
                UIDesignExecutionAction.REFINE_DESCRIPTION,
            }
            for item in decisions
        ),
    )


def _execution_decision(
    state: UIDesignIntentState,
    difference: UIDesignDifference | None,
) -> tuple[UIDesignExecutionAction, str]:
    if state == UIDesignIntentState.DIRTY:
        return (
            UIDesignExecutionAction.REFINE_DESCRIPTION,
            "dirty intent must be clarified or repaired before implementation",
        )
    if state == UIDesignIntentState.STABLE:
        if difference in {UIDesignDifference.MATERIAL, UIDesignDifference.UNKNOWN}:
            return (
                UIDesignExecutionAction.CLARIFY,
                "stable intent is protected; material or uncertain drift needs explicit authorization",
            )
        return (
            UIDesignExecutionAction.REFERENCE_ONLY,
            "stable intent remains reference-only when no material drift is evidenced",
        )
    if difference is None or difference == UIDesignDifference.UNKNOWN:
        return (
            UIDesignExecutionAction.INSPECT,
            "planned intent needs an implementation observation before editing",
        )
    if difference == UIDesignDifference.MATERIAL:
        return (
            UIDesignExecutionAction.IMPLEMENT,
            "planned intent has evidenced material implementation drift",
        )
    return (
        UIDesignExecutionAction.VERIFY_ONLY,
        "planned intent is already aligned or differs only elastically; avoid code churn",
    )


class UIDesignAgentRequest(DomainModel):
    schema_version: str = "1.0"
    active_design: ActiveUIDesign
    agent_adapter: str = Field(min_length=1, max_length=160)
    instruction: str = Field(min_length=1, max_length=12_000)
    observations: UIDesignObservationSet | None = None
    advisory_evidence_artifact_ids: tuple[str, ...] = Field(
        default=(), max_length=24
    )

    @model_validator(mode="after")
    def validate_request(self) -> UIDesignAgentRequest:
        if not self.agent_adapter.strip() or not self.instruction.strip():
            raise ValueError("UI design Agent adapter and instruction must not be blank")
        _bounded_strings(
            self.advisory_evidence_artifact_ids,
            "UI design advisory evidence artifact ids",
        )
        if self.observations is not None:
            assess_ui_design_execution(self.active_design, self.observations)
        return self


class UIDesignAgentDraft(DomainModel):
    schema_version: str = "1.0"
    based_on_descriptor_sha256: str = Field(pattern=SHA256_PATTERN)
    summary: str = Field(min_length=1, max_length=4_000)
    description: SemanticUIDesign
    evidence_artifact_ids: tuple[str, ...] = Field(default=(), max_length=24)

    @model_validator(mode="after")
    def validate_draft(self) -> UIDesignAgentDraft:
        if not self.summary.strip():
            raise ValueError("UI design Agent draft summary must not be blank")
        _bounded_strings(self.evidence_artifact_ids, "Agent draft evidence ids")
        if self.description.authorship.actor_type != ActorType.AGENT:
            raise ValueError("UI design Agent draft must identify an Agent author")
        return self


def validate_ui_design_agent_draft(
    request: UIDesignAgentRequest,
    draft: UIDesignAgentDraft,
) -> None:
    active = request.active_design
    if canonical_ui_design_sha256(active.description) != active.binding.descriptor_sha256:
        raise ValueError("active UI design content does not match its binding")
    if draft.based_on_descriptor_sha256 != active.binding.descriptor_sha256:
        raise ValueError("UI design Agent draft is stale")
    if draft.description.authorship.agent_adapter != request.agent_adapter:
        raise ValueError("UI design Agent draft changed adapter identity")
    missing_advice = set(request.advisory_evidence_artifact_ids).difference(
        draft.evidence_artifact_ids
    )
    if missing_advice:
        raise ValueError(
            "UI design Agent draft omitted requested advisory evidence: "
            + ", ".join(sorted(missing_advice))
        )
    validate_ui_design_revision(active.description, draft.description)


class UIDesignQuery(Protocol):
    def get_active(self, page_id: str) -> ActiveUIDesign:
        """Return the exact backend-selected active description for one page."""


__all__ = [
    "ActiveUIDesign",
    "SemanticUIDesign",
    "UIDesignActiveBinding",
    "UIDesignAgentDraft",
    "UIDesignAgentRequest",
    "UIDesignAuthorship",
    "UIDesignCatalog",
    "UIDesignDescriptorReference",
    "UIDesignDifference",
    "UIDesignExecutionAction",
    "UIDesignExecutionPlan",
    "UIDesignIntentState",
    "UIDesignJourney",
    "UIDesignJourneyStep",
    "UIDesignObservation",
    "UIDesignObservationSet",
    "UIDesignQuery",
    "UIDesignRegion",
    "UIDesignRelation",
    "UIDesignRelationKind",
    "UIDesignRequirement",
    "UIDesignRequirementStrength",
    "UIDesignStableChangeAuthorization",
    "UIDesignStatusCounts",
    "UIDesignUnitDecision",
    "assess_ui_design_execution",
    "canonical_ui_design_sha256",
    "validate_ui_design_agent_draft",
    "validate_ui_design_revision",
]
