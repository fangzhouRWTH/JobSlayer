import hashlib
import unittest

from pydantic import ValidationError

from jobslayer.domain.models import (
    CheckResult,
    CheckStatus,
    RiskLevel,
    TestbedBaseline,
    TestbedInspection,
    TestbedSpec,
    TestbedStatus,
    TaskSpec,
    VerificationReport,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class TaskSpecTests(unittest.TestCase):
    def base_payload(self) -> dict:
        return {
            "task_id": "task-1",
            "project_id": "project-1",
            "title": "Fix a deterministic bug",
            "objective": "Make the existing failing test pass",
            "repository": "https://example.invalid/repo.git",
            "base_commit": "0123456789abcdef0123456789abcdef01234567",
            "allowed_paths": ["src/", "tests/"],
            "required_capabilities": ["python_bugfix"],
            "acceptance_criteria": ["regression test passes"],
            "validation_profile": "python_patch_v1",
            "risk": RiskLevel.LOW,
        }

    def test_accepts_a_scoped_task(self) -> None:
        task = TaskSpec.model_validate(self.base_payload())
        self.assertEqual(task.allowed_paths, ("src/", "tests/"))
        self.assertEqual(task.version, 1)

    def test_rejects_path_escape(self) -> None:
        payload = self.base_payload()
        payload["allowed_paths"] = ["../outside"]
        with self.assertRaises(ValidationError):
            TaskSpec.model_validate(payload)

    def test_rejects_unknown_contract_fields(self) -> None:
        payload = self.base_payload()
        payload["provider_agent"] = {"name": "vendor-specific"}
        with self.assertRaises(ValidationError):
            TaskSpec.model_validate(payload)


class TestbedSpecTests(unittest.TestCase):
    def test_accepts_external_project_registration(self) -> None:
        testbed = TestbedSpec.model_validate(
            {
                "testbed_id": "brave-new-world",
                "display_name": "BraveNewWorld",
                "purpose": "A deterministic robotics teaching testbed",
                "status": TestbedStatus.BOOTSTRAPPING,
                "repository": {
                    "clone_url": "https://github.com/example/project.git",
                    "alternative_clone_urls": ["git@github.com:example/project.git"],
                    "default_branch": "main",
                },
                "baseline": {
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "tag": "bnw-0",
                    "published": False,
                    "verification_command": ["./bnw", "check"],
                },
                "architecture_areas": ["simulation", "ui"],
                "capability_targets": ["python_bugfix", "ui_change"],
            }
        )
        self.assertEqual(testbed.repository.default_branch, "main")
        self.assertEqual(testbed.baseline.tag, "bnw-0")

    def test_rejects_unsafe_baseline_tag(self) -> None:
        with self.assertRaises(ValidationError):
            TestbedBaseline.model_validate(
                {
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "tag": "../escape",
                    "published": False,
                    "verification_command": ["./bnw", "check"],
                }
            )

    def test_rejects_an_inconsistent_inspection_match_claim(self) -> None:
        with self.assertRaises(ValidationError):
            TestbedInspection(
                testbed_id="testbed",
                checkout_path="/tmp/testbed",
                baseline_commit="0123456789abcdef0123456789abcdef01234567",
                head_commit="fedcba9876543210fedcba9876543210fedcba98",
                tag="bnw-0",
                tag_commit="0123456789abcdef0123456789abcdef01234567",
                origin_url="https://example.invalid/testbed.git",
                working_tree_clean=True,
                head_matches_baseline=True,
                tag_matches_baseline=True,
                origin_registered=True,
                baseline_published=False,
            )

    def test_rejects_duplicate_repository_urls(self) -> None:
        with self.assertRaises(ValidationError):
            TestbedSpec.model_validate(
                {
                    "testbed_id": "duplicate-url",
                    "display_name": "Duplicate URL",
                    "purpose": "Invalid registration",
                    "status": "planned",
                    "repository": {
                        "clone_url": "https://example.invalid/repo.git",
                        "alternative_clone_urls": [
                            "https://example.invalid/repo.git"
                        ],
                        "default_branch": "main",
                    },
                    "architecture_areas": ["simulation"],
                    "capability_targets": ["unit_test"],
                }
            )

class VerificationReportTests(unittest.TestCase):
    def test_requires_at_least_one_required_check(self) -> None:
        optional = CheckResult(
            check_id="informational",
            status=CheckStatus.PASSED,
            required=False,
            summary="informational check passed",
            evidence_hash=digest("informational log"),
        )
        with self.assertRaises(ValidationError):
            VerificationReport(
                report_id="report-optional-only",
                task_id="task-1",
                source_commit="0123456",
                checks=(optional,),
                required_checks_passed=True,
            )

    def test_rejects_a_false_passing_claim(self) -> None:
        failed = CheckResult(
            check_id="unit",
            status=CheckStatus.FAILED,
            summary="one test failed",
            evidence_hash=digest("failure log"),
        )
        with self.assertRaises(ValidationError):
            VerificationReport(
                report_id="report-1",
                task_id="task-1",
                source_commit="0123456",
                checks=(failed,),
                required_checks_passed=True,
            )

    def test_unresolved_risk_blocks_gate(self) -> None:
        passed = CheckResult(
            check_id="unit",
            status=CheckStatus.PASSED,
            summary="all tests passed",
            evidence_hash=digest("success log"),
        )
        report = VerificationReport(
            report_id="report-1",
            task_id="task-1",
            source_commit="0123456",
            checks=(passed,),
            required_checks_passed=True,
            unresolved_risks=("performance was not measured",),
        )
        self.assertFalse(report.passes_gate)


if __name__ == "__main__":
    unittest.main()
