import hashlib
import unittest

from pydantic import ValidationError

from jobslayer.domain.models import (
    CheckResult,
    CheckStatus,
    RiskLevel,
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
