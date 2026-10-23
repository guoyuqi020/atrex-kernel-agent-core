"""Regression tests for non-duplicated Agent recovery guidance."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aka.light.evaluation.projection import (  # noqa: E402
    gateway_error_result,
    source_error_result,
)
from aka.light.runtime.request_errors import error_response  # noqa: E402


class RecoveryProjectionTests(unittest.TestCase):
    def test_gateway_error_has_one_prose_recovery_instruction(self):
        result = gateway_error_result(
            {
                "operation": "evaluate",
                "status": "failed",
                "error": {"error_class": "code", "message": "compile failed"},
            }
        )

        self.assertIn("next_action", result)
        self.assertNotIn("recovery", result)

    def test_request_error_uses_error_next_action_without_duplicate_recovery(self):
        result = error_response("missing field", next_action="Add the field and retry.")

        self.assertEqual(result["error"]["next_action"], "Add the field and retry.")
        self.assertNotIn("recovery", result)

    def test_source_rejection_does_not_repeat_its_instruction(self):
        result = source_error_result(
            {
                "error": {
                    "code": "candidate_source_rejected",
                    "job_submitted": False,
                    "violations": ["Blocked import: os"],
                }
            }
        )

        self.assertIn("next_action", result["error"])
        self.assertNotIn("recovery", result)

    def test_explicit_structured_recovery_is_preserved(self):
        recovery = [
            {
                "tool": "record-read",
                "request": {"record_id": "gateway-123"},
                "instruction": "Read the completed result.",
            }
        ]

        result = error_response(
            "already completed",
            next_action="Reuse the completed result.",
            recovery=recovery,
        )

        self.assertEqual(result["recovery"], recovery)


if __name__ == "__main__":
    unittest.main()
