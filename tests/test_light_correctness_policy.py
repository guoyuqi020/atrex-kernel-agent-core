"""Regression tests for Runtime-owned AKA-Light correctness coverage."""

import unittest

from aka.light.acceptance.provider import candidate_acceptance_argv
from aka.light.acceptance.recorded_verifier import RecordedABBAValidator
from aka.light.evaluation.gateway import build_parser, evaluation_policy
from aka.light.runtime.request_feedback import gateway_schema
from aka.light.workflow.bootstrap import framework_baseline_validation_command


class RuntimeOwnedCorrectnessPolicyTests(unittest.TestCase):
    def test_full_and_shape_selected_evaluates_use_five_cases(self):
        self.assertEqual(
            evaluation_policy(["python3", "test_kernel.py"])["num_correctness_cases"],
            5,
        )
        self.assertEqual(
            evaluation_policy(
                ["python3", "test_kernel.py", "--shape-id", "6"],
                "correctness_only",
            ),
            {
                "schema_version": 1,
                "num_correctness_cases": 5,
                "mode": "correctness_only",
            },
        )

    def test_agent_cannot_override_correctness_case_count(self):
        with self.assertRaisesRegex(ValueError, "Runtime-owned"):
            evaluation_policy(
                ["python3", "test_kernel.py", "--multi-seed", "0"]
            )
        parser = build_parser()
        schema = gateway_schema("run", parser)
        self.assertNotIn("--multi-seed", schema["cli_options"])

    def test_supervisor_gates_do_not_expose_multi_seed(self):
        framework = framework_baseline_validation_command(1, atrex_bench=True)
        candidate = candidate_acceptance_argv(atrex_bench=True)
        verifier = RecordedABBAValidator(execute=lambda *_: None, hardware="L20D")
        self.assertNotIn("--multi-seed", framework)
        self.assertNotIn("--multi-seed", candidate)
        self.assertNotIn("--multi-seed", verifier.request_argv())


if __name__ == "__main__":
    unittest.main()
