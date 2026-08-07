import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from jobslayer.cli import main
from jobslayer.domain.models import DecisionCard, HumanDecision
from jobslayer.supervision.decision import (
    DecisionError,
    create_human_decision,
    decision_card_hash,
    render_decision_card,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CARD = REPOSITORY_ROOT / "examples" / "decision-card.example.json"


class HumanSupervisionTests(unittest.TestCase):
    def load_card(self) -> DecisionCard:
        return DecisionCard.model_validate_json(
            EXAMPLE_CARD.read_text(encoding="utf-8")
        )

    def test_renders_required_context_and_recommendation(self) -> None:
        rendered = render_decision_card(self.load_card())

        self.assertIn("是否允许示例补丁进入合并提案", rendered)
        self.assertIn("为什么是现在", rendered)
        self.assertIn("verification-example-001", rendered)
        self.assertIn("approve: 批准合并提案（推荐/默认）", rendered)
        self.assertIn("卡片哈希", rendered)

    def test_human_decision_is_bound_to_the_card_and_evidence(self) -> None:
        card = self.load_card()
        decision = create_human_decision(
            card,
            actor_id="reviewer@example.invalid",
            selected_option_id="request_changes",
            rationale="The numerical tolerance needs justification.",
        )

        self.assertEqual(decision.card_sha256, decision_card_hash(card))
        self.assertEqual(decision.task_id, card.task_id)
        self.assertEqual(
            decision.evidence_ids,
            ("verification-example-001", "review-example-001"),
        )

    def test_rejects_an_option_that_was_not_presented(self) -> None:
        with self.assertRaises(DecisionError):
            create_human_decision(
                self.load_card(),
                actor_id="reviewer",
                selected_option_id="secret-fourth-option",
                rationale="Not shown on the card",
            )

    def test_card_requires_one_recommended_default(self) -> None:
        payload = self.load_card().model_dump(mode="json")
        payload["options"][1]["recommended"] = True

        with self.assertRaises(ValidationError):
            DecisionCard.model_validate(payload)

    def test_interactive_cli_writes_a_structured_decision_without_applying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "decision.json"
            stdout = io.StringIO()
            with patch(
                "builtins.input",
                side_effect=["request_changes", "Please add boundary evidence."],
            ), redirect_stdout(stdout):
                exit_code = main(
                    [
                        "review-decision",
                        str(EXAMPLE_CARD),
                        "--actor-id",
                        "human-reviewer",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            decision = HumanDecision.model_validate_json(
                output.read_text(encoding="utf-8")
            )
            self.assertEqual(decision.selected_option_id, "request_changes")
            self.assertIn("Please add boundary evidence", decision.rationale)
            self.assertIn("decision record written", stdout.getvalue())

    def test_cli_does_not_overwrite_an_existing_decision_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "decision.json"
            output.write_text("existing\n", encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "review-decision",
                        str(EXAMPLE_CARD),
                        "--actor-id",
                        "human-reviewer",
                        "--select",
                        "approve",
                        "--rationale",
                        "Evidence checked.",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing\n")
            self.assertIn("refusing to overwrite", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
