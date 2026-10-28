"""Regression tests for Runtime-owned AKA-Light correctness coverage."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aka.legacy.task.problem.layout import agent_visible_operator_files
from aka.legacy.task.problem.native_harness import _parser as native_harness_parser
from aka.light.acceptance.provider import candidate_acceptance_argv
from aka.light.acceptance.recorded_verifier import RecordedABBAValidator
from aka.light.acceptance.source_policy import optimization_mode_directive
from aka.light.evaluation.gateway import (
    build_parser,
    build_typed_request,
    evaluation_policy,
)
from aka.light.evaluation.policy import BENCH_ITERS, CORRECTNESS_CASES
from aka.light.runtime.request_feedback import gateway_schema
from aka.light.workflow.bootstrap import Campaign, framework_baseline_validation_command
from aka.light.workflow.context import session_execution_policy


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

    def test_framework_baseline_uses_episode_evaluator_policy(self):
        native = framework_baseline_validation_command(1, atrex_bench=True)
        sol = framework_baseline_validation_command(1, atrex_bench=False)
        self.assertEqual(
            native[native.index("--timed-runs") + 1], str(BENCH_ITERS)
        )
        self.assertEqual(
            sol[sol.index("--multi-seed") + 1], str(CORRECTNESS_CASES - 1)
        )

    def test_framework_baseline_and_episode_share_session_execution_policy(self):
        campaign = SimpleNamespace(
            module_options=SimpleNamespace(
                episode_timeout_seconds=None,
                output_limit_recovery_retries=2,
                dependency_policy_recovery_retries=2,
            )
        )
        self.assertEqual(
            session_execution_policy(campaign),
            {
                "episode_timeout_seconds": None,
                "reasoning_effort": "max",
                "output_limit_recovery_retries": 2,
                "dependency_policy_recovery_retries": 2,
            },
        )

    def test_framework_baseline_uses_full_episode_sandbox_contract(self):
        campaign = Campaign(
            name="operator",
            kernel_demo="reference.py",
            platform="cuda",
            framework="cuda",
            sandbox_hardware="L20D",
        )
        self.assertEqual(
            campaign._framework_baseline_sandbox_directive(),
            campaign._sandbox_directive(),
        )

    def test_light_policy_defers_correctness_rules_to_task_configuration(self):
        directive = optimization_mode_directive("production", "CUDA")
        self.assertNotIn("relative L2 error <= 0.2 for NVFP4", directive)
        self.assertNotIn("elementwise atol/rtol", directive)
        self.assertNotIn("mismatch-fraction", directive)
        self.assertIn("read workspace `metadata.json`", directive)
        self.assertIn("benchmark correctness contract is authoritative", directive)
        self.assertIn("Do not assume, invent, or override", directive)
        self.assertIn("one passing check never overrides another reported failure", directive)

    def test_generalized_agent_workspace_exposes_metadata_but_not_exact_shapes(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            operator = Path(directory)
            visible = agent_visible_operator_files(operator, generalized=True)
        self.assertIn("metadata.json", visible)
        self.assertNotIn("shapes.json", visible)

    def test_generalized_workspace_privacy_guard_allows_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            operator = root / "operator"
            operator.mkdir()
            (operator / "reference.py").write_text("class Model: pass\n")
            (operator / "shapes.json").write_text(
                json.dumps({"0": {"init_kwargs": {}, "input_kwargs": {"x": 1}}})
            )
            public_problem = {
                "schema_version": "atrex.agent_problem.v1",
                "objective": "Optimize the operator.",
                "evaluation": {
                    "exact_cases": "private",
                    "development_cases_are_evaluation_cases": False,
                },
                "operator_contract": {},
                "shape_domain": {},
                "invariants": [],
                "coverage_regimes": [],
            }
            (operator / "agent_problem.json").write_text(json.dumps(public_problem))
            campaign = Campaign(
                name="operator",
                kernel_demo=str(operator / "reference.py"),
                platform="L20D",
                framework="Cuda",
                atrex_bench_root=str(root),
                work_dir=str(root / "work"),
            )
            campaign.workspace.mkdir(parents=True)
            (campaign.workspace / "agent_problem.json").write_text(
                json.dumps(public_problem)
            )
            (campaign.workspace / "metadata.json").write_text(
                json.dumps({"benchmark_contract": {}})
            )

            campaign._assert_generalized_inputs_are_private()

            (campaign.workspace / "shapes.json").write_text(
                (operator / "shapes.json").read_text()
            )
            with self.assertRaisesRegex(RuntimeError, "shapes.json"):
                campaign._assert_generalized_inputs_are_private()

    def test_typed_eval_forwards_metadata_without_light_tolerance_defaults(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            workspace = Path(directory)
            (workspace / "kernel.py").write_text("class Model: pass\n")
            (workspace / "reference.py").write_text("class Model: pass\n")
            (workspace / "input.py").write_text(
                "def generate_input(*args, **kwargs): return ()\n"
            )
            (workspace / "shapes.json").write_text(
                json.dumps({"0": {"init_kwargs": {}, "input_kwargs": {}}})
            )
            metadata = {
                "benchmark_contract": {
                    "correctness_tolerances": {
                        "default": {
                            "atol": 0.125,
                            "rtol": 0.25,
                            "max_rel_l2": 0.5,
                        }
                    }
                }
            }
            (workspace / "metadata.json").write_text(json.dumps(metadata))

            request = build_typed_request(
                workspace,
                "L20D",
                600,
                [],
                ["python3", "test_kernel.py"],
                "run",
            )

        self.assertEqual(request["reference"]["metadata"], metadata)
        self.assertNotIn("atol", request["options"])
        self.assertNotIn("rtol", request["options"])
        self.assertNotIn("correctness_max_rel_l2", request["options"])

    def test_native_harness_has_no_light_owned_tolerance_switches(self):
        args = native_harness_parser().parse_args([])
        self.assertFalse(hasattr(args, "atol"))
        self.assertFalse(hasattr(args, "rtol"))
        self.assertFalse(hasattr(args, "correctness_max_rel_l2"))

    def test_framework_baseline_preserves_structured_failure_diagnostics(self):
        result = {
            "all_pass": False,
            "failures": ["shape 6 failed correctness"],
            "actionable_diagnostics": [
                {
                    "stage": "correctness",
                    "shape_id": "6",
                    "message": "output mismatch",
                }
            ],
            "diagnostics": [
                {
                    "stage": "correctness",
                    "shape_id": "7",
                    "case_index": 4,
                    "check": "outputs",
                    "name": "output",
                    "error": "elementwise tolerance failed",
                    "max_elementwise_rel_diff": 0.125,
                    "relative_l2": 0.01,
                }
            ],
        }
        completed = subprocess.CompletedProcess(
            ["python", "test_kernel.py"],
            1,
            "[test_kernel] RESULT_JSON=" + json.dumps(result) + "\n",
            "",
        )
        with tempfile.TemporaryDirectory() as directory:
            campaign = Campaign(
                name="operator",
                kernel_demo="reference.py",
                platform="cuda",
                framework="cuda",
                work_dir=directory,
            )
            with (
                patch("aka.light.workflow.bootstrap._sandbox_command", return_value=completed),
                patch.object(Campaign, "_print_v0_evaluator_output"),
            ):
                parsed, problem = campaign._validate_framework_baseline(1)
        self.assertEqual(parsed, result)
        self.assertIn("shape 6 failed correctness", problem)
        self.assertIn('\"shape_id\": \"6\"', problem)
        self.assertIn('\"shape_id\": \"7\"', problem)
        self.assertIn("elementwise tolerance failed", problem)
        self.assertIn('\"max_elementwise_rel_diff\": 0.125', problem)
        self.assertNotEqual(problem, "combined validation command failed (exit=1)")

    def test_framework_baseline_keeps_bounded_transport_error_without_result(self):
        completed = subprocess.CompletedProcess(
            ["python", "test_kernel.py"],
            1,
            "",
            "gateway rejected request: shape contract is invalid",
        )
        with tempfile.TemporaryDirectory() as directory:
            campaign = Campaign(
                name="operator",
                kernel_demo="reference.py",
                platform="cuda",
                framework="cuda",
                work_dir=directory,
            )
            with (
                patch("aka.light.workflow.bootstrap._sandbox_command", return_value=completed),
                patch.object(Campaign, "_print_v0_evaluator_output"),
            ):
                parsed, problem = campaign._validate_framework_baseline(1)
        self.assertIsNone(parsed)
        self.assertIn("shape contract is invalid", problem)


if __name__ == "__main__":
    unittest.main()
