from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from jobslayer.adapters.source_ui_designs import (
    SourceControlledUIDesignRegistry,
    UIDesignCatalogError,
)
from jobslayer.domain.models import ActorType
from jobslayer.ui_design import (
    ActiveUIDesign,
    SemanticUIDesign,
    UIDesignAgentDraft,
    UIDesignAgentRequest,
    UIDesignActiveBinding,
    UIDesignCatalog,
    UIDesignDifference,
    UIDesignExecutionAction,
    UIDesignIntentState,
    UIDesignObservation,
    UIDesignObservationSet,
    UIDesignStableChangeAuthorization,
    UIDesignStatusCounts,
    assess_ui_design_execution,
    canonical_ui_design_sha256,
    validate_ui_design_agent_draft,
    validate_ui_design_revision,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPOSITORY_ROOT / "ui-designs" / "catalog.json"


class SemanticUIDesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SourceControlledUIDesignRegistry(CATALOG_PATH)
        self.active = self.registry.get_active("task-manager")

    def _revision_payload(self) -> dict:
        previous = self.active.description
        payload = previous.model_dump(mode="json")
        payload.update(
            {
                "revision": previous.revision + 1,
                "parent_revision_sha256": canonical_ui_design_sha256(previous),
                "authorship": {
                    "actor_type": "agent",
                    "actor_id": "ui-design-agent",
                    "agent_adapter": "fixture-ui-agent",
                    "created_at": datetime.now(UTC).isoformat(),
                },
                "stable_change_authorizations": [],
                "change_summary": "Agent repaired one planned semantic requirement.",
            }
        )
        return payload

    def test_source_catalog_resolves_one_exact_active_revision(self) -> None:
        self.assertEqual(len(self.registry.list_references()), 8)
        self.assertEqual(self.active.binding.scheme_id, "focused-task-graph")
        self.assertEqual(self.active.binding.revision, 8)
        self.assertEqual(self.active.state_counts.dirty, 0)
        self.assertEqual(self.active.state_counts.planned, 63)
        self.assertEqual(self.active.state_counts.stable, 13)
        self.assertTrue(
            {
                "region.view-rail",
                "region.home-view",
                "region.agent-status-view",
                "region.control-view",
                "region.orchestration-view",
                "region.execution-view",
                "region.quick-agent-capacity",
                "region.quick-agent-console",
                "region.quick-agent-mode",
                "region.quick-agent-model-controls",
                "region.execution-coordinator",
                "region.human-action-guidance",
                "region.human-decision-control",
                "region.human-action-assistant",
            }.issubset({item.unit_id for item in self.active.description.regions})
        )
        self.assertIn(
            "adr-0057",
            self.active.binding.evidence_ids,
        )
        self.assertTrue(
            {
                "requirement.calm-ops-style",
                "requirement.readable-type",
                "requirement.information-restraint",
                "requirement.quick-agent-independence",
                "requirement.provider-capacity-truth",
                "requirement.quick-agent-permission-mode",
                "requirement.quick-agent-streaming",
                "requirement.provider-model-catalog-truth",
                "requirement.serial-coordinator-truth",
                "requirement.human-action-guidance",
                "requirement.formal-human-decision-gate",
                "requirement.human-feedback-append-only",
                "requirement.human-assistant-non-authority",
            }.issubset(
                {item.unit_id for item in self.active.description.requirements}
            )
        )
        self.assertEqual(
            self.active.binding.descriptor_sha256,
            canonical_ui_design_sha256(self.active.description),
        )
        observations = UIDesignObservationSet.model_validate_json(
            (
                REPOSITORY_ROOT
                / "examples"
                / "ui-design-observations.example.json"
            ).read_text(encoding="utf-8")
        )
        plan = assess_ui_design_execution(self.active, observations)
        self.assertEqual(
            next(
                item.action
                for item in plan.decisions
                if item.unit_id == "requirement.design-status-projection"
            ),
            UIDesignExecutionAction.VERIFY_ONLY,
        )

    def test_catalog_rejects_descriptor_content_drift(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "ui-designs"
            shutil.copytree(REPOSITORY_ROOT / "ui-designs", root)
            descriptor = root / "task-manager" / "focused-task-graph" / "v1.json"
            payload = json.loads(descriptor.read_text(encoding="utf-8"))
            payload["title"] = "tampered after activation"
            descriptor.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(UIDesignCatalogError, "hash mismatch"):
                SourceControlledUIDesignRegistry(root / "catalog.json")

    def test_catalog_rejects_more_than_one_active_scheme_for_a_page(self) -> None:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        payload["active_bindings"].append(dict(payload["active_bindings"][0]))

        with self.assertRaises(ValidationError):
            UIDesignCatalog.model_validate(payload)

    def test_catalog_requires_an_active_binding_and_rejects_agent_activation(self) -> None:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        payload["active_bindings"] = []
        with self.assertRaises(ValidationError):
            UIDesignCatalog.model_validate(payload)

        binding = self.active.binding.model_dump(mode="json")
        binding["activated_by_actor_type"] = "agent"
        with self.assertRaises(ValidationError):
            UIDesignActiveBinding.model_validate(binding)

    def test_stable_unit_change_requires_explicit_non_agent_authorization(self) -> None:
        payload = self._revision_payload()
        payload["regions"][0]["intent"] = "Materially changed stable graph intent."
        changed = SemanticUIDesign.model_validate(payload)

        with self.assertRaisesRegex(ValueError, "missing authorization"):
            validate_ui_design_revision(self.active.description, changed)

        payload["stable_change_authorizations"] = [
            UIDesignStableChangeAuthorization(
                unit_id="region.task-graph",
                actor_type=ActorType.HUMAN,
                actor_id="product-owner",
                reason="The active product direction explicitly changed.",
                evidence_ids=("design-decision-002",),
            ).model_dump(mode="json")
        ]
        authorized = SemanticUIDesign.model_validate(payload)
        validate_ui_design_revision(self.active.description, authorized)

    def test_active_v2_material_change_is_bound_to_human_authorization(self) -> None:
        root = REPOSITORY_ROOT / "ui-designs" / "task-manager" / "focused-task-graph"
        previous = SemanticUIDesign.model_validate_json(
            (root / "v1.json").read_text(encoding="utf-8")
        )
        current_payload = json.loads((root / "v2.json").read_text(encoding="utf-8"))
        current = SemanticUIDesign.model_validate(current_payload)

        validate_ui_design_revision(previous, current)
        current_payload["stable_change_authorizations"] = []
        unauthorized = SemanticUIDesign.model_validate(current_payload)
        with self.assertRaisesRegex(ValueError, "requirement.single-screen"):
            validate_ui_design_revision(previous, unauthorized)

    def test_active_v3_calm_ops_preserves_all_stable_units(self) -> None:
        root = REPOSITORY_ROOT / "ui-designs" / "task-manager" / "focused-task-graph"
        previous = SemanticUIDesign.model_validate_json(
            (root / "v2.json").read_text(encoding="utf-8")
        )
        current = SemanticUIDesign.model_validate_json(
            (root / "v3.json").read_text(encoding="utf-8")
        )

        validate_ui_design_revision(previous, current)
        self.assertEqual(current.stable_change_authorizations, ())
        previous_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in previous.units()
            if item.state == UIDesignIntentState.STABLE
        }
        current_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in current.units()
            if item.state == UIDesignIntentState.STABLE
        }
        self.assertEqual(current_stable, previous_stable)

    def test_active_v4_quick_agent_preserves_all_stable_units(self) -> None:
        root = REPOSITORY_ROOT / "ui-designs" / "task-manager" / "focused-task-graph"
        previous = SemanticUIDesign.model_validate_json(
            (root / "v3.json").read_text(encoding="utf-8")
        )
        current = SemanticUIDesign.model_validate_json(
            (root / "v4.json").read_text(encoding="utf-8")
        )

        validate_ui_design_revision(previous, current)
        self.assertEqual(current.stable_change_authorizations, ())
        previous_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in previous.units()
            if item.state == UIDesignIntentState.STABLE
        }
        current_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in current.units()
            if item.state == UIDesignIntentState.STABLE
        }
        self.assertEqual(current_stable, previous_stable)

    def test_active_v5_model_controls_preserve_all_stable_units(self) -> None:
        root = REPOSITORY_ROOT / "ui-designs" / "task-manager" / "focused-task-graph"
        previous = SemanticUIDesign.model_validate_json(
            (root / "v4.json").read_text(encoding="utf-8")
        )
        current = SemanticUIDesign.model_validate_json(
            (root / "v5.json").read_text(encoding="utf-8")
        )

        validate_ui_design_revision(previous, current)
        self.assertEqual(current.stable_change_authorizations, ())
        previous_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in previous.units()
            if item.state == UIDesignIntentState.STABLE
        }
        current_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in current.units()
            if item.state == UIDesignIntentState.STABLE
        }
        self.assertEqual(current_stable, previous_stable)

    def test_active_v6_coordinator_preserves_all_stable_units(self) -> None:
        root = REPOSITORY_ROOT / "ui-designs" / "task-manager" / "focused-task-graph"
        previous = SemanticUIDesign.model_validate_json(
            (root / "v5.json").read_text(encoding="utf-8")
        )
        current = SemanticUIDesign.model_validate_json(
            (root / "v6.json").read_text(encoding="utf-8")
        )

        validate_ui_design_revision(previous, current)
        self.assertEqual(current.stable_change_authorizations, ())
        previous_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in previous.units()
            if item.state == UIDesignIntentState.STABLE
        }
        current_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in current.units()
            if item.state == UIDesignIntentState.STABLE
        }
        self.assertEqual(current_stable, previous_stable)

    def test_active_v7_human_guidance_preserves_all_stable_units(self) -> None:
        root = REPOSITORY_ROOT / "ui-designs" / "task-manager" / "focused-task-graph"
        previous = SemanticUIDesign.model_validate_json(
            (root / "v6.json").read_text(encoding="utf-8")
        )
        current = SemanticUIDesign.model_validate_json(
            (root / "v7.json").read_text(encoding="utf-8")
        )

        validate_ui_design_revision(previous, current)
        self.assertEqual(current.stable_change_authorizations, ())
        previous_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in previous.units()
            if item.state == UIDesignIntentState.STABLE
        }
        current_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in current.units()
            if item.state == UIDesignIntentState.STABLE
        }
        self.assertEqual(current_stable, previous_stable)

    def test_active_v8_human_controls_preserve_all_stable_units(self) -> None:
        root = REPOSITORY_ROOT / "ui-designs" / "task-manager" / "focused-task-graph"
        previous = SemanticUIDesign.model_validate_json(
            (root / "v7.json").read_text(encoding="utf-8")
        )
        current = SemanticUIDesign.model_validate_json(
            (root / "v8.json").read_text(encoding="utf-8")
        )

        validate_ui_design_revision(previous, current)
        self.assertEqual(current.stable_change_authorizations, ())
        previous_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in previous.units()
            if item.state == UIDesignIntentState.STABLE
        }
        current_stable = {
            item.unit_id: item.model_dump(mode="json")
            for item in current.units()
            if item.state == UIDesignIntentState.STABLE
        }
        self.assertEqual(current_stable, previous_stable)

    def test_agent_draft_is_bound_to_the_active_hash_and_may_repair_planned_intent(self) -> None:
        payload = self._revision_payload()
        planned = next(
            item
            for item in payload["requirements"]
            if item["unit_id"] == "requirement.design-status-projection"
        )
        planned["intent"] = "Clarify the planned backend-owned design status projection."
        description = SemanticUIDesign.model_validate(payload)
        request = UIDesignAgentRequest(
            active_design=self.active,
            agent_adapter="fixture-ui-agent",
            instruction="Repair the planned design status description without touching stable units.",
            advisory_evidence_artifact_ids=("ui-advice-evidence-1",),
        )
        draft = UIDesignAgentDraft(
            based_on_descriptor_sha256=self.active.binding.descriptor_sha256,
            summary="Clarified one planned unit.",
            description=description,
            evidence_artifact_ids=("ui-advice-evidence-1",),
        )

        validate_ui_design_agent_draft(request, draft)
        without_advice = draft.model_copy(update={"evidence_artifact_ids": ()})
        with self.assertRaisesRegex(ValueError, "omitted requested advisory evidence"):
            validate_ui_design_agent_draft(request, without_advice)
        stale = draft.model_copy(update={"based_on_descriptor_sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "stale"):
            validate_ui_design_agent_draft(request, stale)

        promoted_payload = self._revision_payload()
        promoted_payload["requirements"][2].update(
            {
                "state": "stable",
                "stability_evidence_ids": ["agent-self-claim"],
            }
        )
        promoted = draft.model_copy(
            update={
                "description": SemanticUIDesign.model_validate(promoted_payload)
            }
        )
        with self.assertRaisesRegex(ValueError, "cannot promote"):
            validate_ui_design_agent_draft(request, promoted)

    def test_elastic_reconciliation_avoids_churn_and_protects_stable_intent(self) -> None:
        observations = UIDesignObservationSet(
            page_id=self.active.binding.page_id,
            scheme_id=self.active.binding.scheme_id,
            revision=self.active.binding.revision,
            descriptor_sha256=self.active.binding.descriptor_sha256,
            observations=(
                UIDesignObservation(
                    unit_id="requirement.design-status-projection",
                    difference=UIDesignDifference.MINOR,
                    summary="The status is present with equivalent compact wording.",
                    evidence_ids=("dom-snapshot-1",),
                ),
                UIDesignObservation(
                    unit_id="region.task-graph",
                    difference=UIDesignDifference.MATERIAL,
                    summary="The graph no longer occupies the primary area.",
                    evidence_ids=("screenshot-1",),
                ),
            ),
        )
        plan = assess_ui_design_execution(self.active, observations)
        decisions = {item.unit_id: item.action for item in plan.decisions}

        self.assertEqual(
            decisions["requirement.design-status-projection"],
            UIDesignExecutionAction.VERIFY_ONLY,
        )
        self.assertEqual(
            decisions["region.task-graph"],
            UIDesignExecutionAction.CLARIFY,
        )
        self.assertFalse(plan.implementation_required)
        self.assertTrue(plan.clarification_required)

        material = observations.model_copy(
            update={
                "observations": (
                    UIDesignObservation(
                        unit_id="requirement.design-status-projection",
                        difference=UIDesignDifference.MATERIAL,
                        summary="The backend design status is not shown.",
                        evidence_ids=("dom-snapshot-2",),
                    ),
                )
            }
        )
        implementation = assess_ui_design_execution(self.active, material)
        self.assertTrue(implementation.implementation_required)
        self.assertEqual(
            next(
                item.action
                for item in implementation.decisions
                if item.unit_id == "requirement.design-status-projection"
            ),
            UIDesignExecutionAction.IMPLEMENT,
        )

    def test_dirty_intent_is_repaired_before_code_execution(self) -> None:
        payload = self.active.description.model_dump(mode="json")
        payload["requirements"][2]["state"] = UIDesignIntentState.DIRTY.value
        dirty = SemanticUIDesign.model_validate(payload)
        digest = canonical_ui_design_sha256(dirty)
        active = ActiveUIDesign(
            binding=self.active.binding.model_copy(
                update={"descriptor_sha256": digest}
            ),
            description=dirty,
            state_counts=UIDesignStatusCounts(dirty=1, planned=28, stable=13),
        )

        plan = assess_ui_design_execution(active)
        decision = next(
            item
            for item in plan.decisions
            if item.unit_id == "requirement.design-status-projection"
        )
        self.assertEqual(decision.action, UIDesignExecutionAction.REFINE_DESCRIPTION)
        self.assertTrue(plan.clarification_required)


if __name__ == "__main__":
    unittest.main()
