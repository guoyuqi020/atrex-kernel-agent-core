"""Regression tests for Runtime-owned AKA-Light correctness coverage."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aka.contracts.evaluation import derived_evaluation_timeout
from aka.legacy.task.problem.layout import agent_visible_operator_files
from aka.legacy.task.problem.native_harness import _parser as native_harness_parser
from aka.light.acceptance.provider import candidate_acceptance_argv
from aka.light.acceptance.recorded_verifier import RecordedABBAValidator
from aka.light.acceptance.source_policy import optimization_mode_directive
from aka.light.evaluation.gateway import (
    _run_typed_gateway,
    build_parser,
    build_typed_request,
    evaluation_policy,
)
from aka.light.evaluation.evaluation_failures import (
    embedded_candidate_wall_clock_timeouts,
)
from aka.light.evaluation.policy import BENCH_ITERS, CORRECTNESS_CASES
from aka.light.evaluation.shapes import (
    effective_shape_batch_size,
    shape_request_timeout,
)
from aka.light.execution.gateway_jobs import (
    DEFAULT_MAX_INFRA_RETRIES,
    TERMINAL_VALIDATION_ENV,
    configured_max_infra_retries,
    execute_job,
    retry_kind,
)
from aka.light.runtime.server import RuntimeConfig, SupervisorRuntime
from aka.light.runtime.request_feedback import gateway_schema
from aka.light.workflow.bootstrap import Campaign, framework_baseline_validation_command
from aka.light.workflow.context import session_execution_policy
from aka.light.workflow.plugin import Defaults as WORKFLOW_PLUGIN_DEFAULTS
from aka.light.workflow.provider import WorkflowOptions
from aka.light.workflow.session_io import _evaluation_performance_timeout


class RuntimeOwnedCorrectnessPolicyTests(unittest.TestCase):
    def test_one_runtime_step_timeout_drives_gateway_request(self):
        self.assertEqual(derived_evaluation_timeout(), 1230)
        command = RuntimeConfig("L20D").command(Path("/tmp/workspace"))
        self.assertEqual(
            command[command.index("--evaluation-step-timeout") + 1], "300"
        )
        parser = build_parser()
        arguments = parser.parse_args(["--hardware", "L20D"])
        self.assertEqual(arguments.evaluation_step_timeout, 300)

    def test_eval_timeout_scales_with_actual_shape_count(self):
        # Only the sequential performance phase repeats.  Fixed candidate,
        # correctness, queue and cleanup budgets remain singletons.
        self.assertEqual(shape_request_timeout(1230, 5), 2430)
        self.assertEqual(shape_request_timeout(1230, 3), 1830)
        self.assertEqual(effective_shape_batch_size(1230, 5, 120), 5)
        self.assertEqual(effective_shape_batch_size(3000, 5, 120), 5)
        self.assertEqual(
            effective_shape_batch_size(
                3000, 5, 120, performance_timeout=3000
            ),
            3,
        )

    def test_eval_timeout_rejects_one_shape_above_gateway_limit(self):
        with self.assertRaisesRegex(ValueError, "One Shape exceeds"):
            effective_shape_batch_size(10681, 5, 120)

    def test_only_request_performance_work_scales_by_shape(self):
        self.assertEqual(
            _evaluation_performance_timeout(["python3", "test_kernel.py"], 300),
            300,
        )
        self.assertEqual(
            _evaluation_performance_timeout(
                ["python3", "test_kernel.py", "--version", "v0"], 300
            ),
            300,
        )
        self.assertEqual(
            _evaluation_performance_timeout(
                ["python3", "test_kernel.py", "--baseline-path", "v0.py"],
                300,
            ),
            1200,
        )
        self.assertEqual(
            _evaluation_performance_timeout(
                [
                    "python3", "test_kernel.py", "--baseline-path", "v0.py",
                    "--comparison-repeats", "4",
                ],
                500,
            ),
            4000,
        )

    def test_eval_retries_candidate_wall_clock_timeout_once(self):
        payload = {
            "name": "timeout-retry",
            "spec": {"target_hardware": ["L20D"]},
            "candidate": "class Model: pass\n",
            "reference": {
                "operator": "example",
                "reference_py": "class Model: pass\n",
                "input_py": "def generate_input(*args, **kwargs): return ()\n",
                "shapes": {"0": {"x": 1}},
            },
            "options": {"num_correctness_cases": 1, "bench_iters": 1, "timeout_s": 600},
            "runner_overrides": {"shape_ids": ["0"]},
            "mode": "full",
            "lock_clocks": True,
            "env_vars": {},
        }
        timeout_job = subprocess.CompletedProcess(
            ["agate-sdk", "get"],
            1,
            json.dumps({
                "job_id": "ev_timeout",
                "status": "succeeded",
                "result": {
                    "performance": {
                        "shapes": {
                            "0": {
                                "error": "candidate exceeded 300.0s wall-clock timeout"
                            }
                        }
                    }
                },
            }),
            "",
        )
        final = subprocess.CompletedProcess(
            ["agate-sdk", "get"],
            1,
            json.dumps({
                "job_id": "ev_timeout_retry",
                "status": "succeeded",
                "result": {
                    "performance": {
                        "shapes": {
                            "0": {
                                "error": "candidate exceeded 300.0s wall-clock timeout"
                            }
                        }
                    }
                },
            }),
            "",
        )
        args = SimpleNamespace(
            hardware="L20D", timeout=600, env=[], profiler="auto",
            profile_level="basic", profile_counter=[], kernel_regex=None,
            top_kernels=None, kernel_name=None, profile_source=None,
            launch_skip=None, launch_count=None, profile_shape_id=None,
            requirement=[], deps_mode="auto", evaluation_input_path=None,
            evaluation_shapes_path=None, evaluation_mode=None, version=None,
            multi_seed=None, shape_id=[], timed_runs=None, shape_batch_size=5,
            evaluation_step_timeout=300,
            dry_run=False, url=None, gateway_profile="prod",
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("aka.light.evaluation.gateway._is_generalized_workspace", return_value=False),
            patch("aka.light.workspace.sources.policy_at", return_value=None),
            patch("aka.light.evaluation.gateway.build_typed_request", return_value=payload),
            patch(
                "aka.light.evaluation.source_transport.run",
                side_effect=[timeout_job, final],
            ) as sdk_run,
        ):
            result = _run_typed_gateway(
                args, Path(directory), ["python3", "test_kernel.py"],
                "run", [], 120,
            )

        self.assertEqual(result, 1)
        self.assertEqual(sdk_run.call_count, 2)
        self.assertNotIn("identity_extra", sdk_run.call_args_list[0].kwargs)
        self.assertEqual(
            sdk_run.call_args_list[1].kwargs["identity_extra"],
            {"eval_wall_clock_retry": 1},
        )
        self.assertEqual(
            embedded_candidate_wall_clock_timeouts(json.loads(final.stdout)),
            ["candidate exceeded 300.0s wall-clock timeout"],
        )

    @staticmethod
    def _deadline_job(*, max_deadline_retries, failures):
        submitted = []

        def submit(wire_key):
            submitted.append(wire_key)
            return subprocess.CompletedProcess(
                [], 0, json.dumps({"job_id": f"job-{len(submitted)}"}), ""
            )

        def poll(process, _remaining, *, recovery_probe=False):
            attempt = int(json.loads(process.stdout)["job_id"].split("-")[-1])
            failed = attempt <= failures
            job = {
                "job_id": f"job-{attempt}",
                "status": "failed" if failed else "succeeded",
            }
            if failed:
                job["error"] = {
                    "error_class": "timeout",
                    "reason": "timeout",
                    "message": "job deadline exceeded",
                }
            else:
                job["result"] = {"ok": True}
            return subprocess.CompletedProcess(
                [], 1 if failed else 0, json.dumps(job), ""
            )

        with patch("aka.light.execution.gateway_jobs.time.sleep"):
            process = execute_job(
                {"request": "deadline-test"},
                submit,
                poll,
                wait_budget=60,
                idempotent_submission=True,
                max_deadline_retries=max_deadline_retries,
            )
        return process, submitted

    @staticmethod
    def _infra_job(*, max_infra_retries, failures, status="failed"):
        submitted = []

        def submit(wire_key):
            submitted.append(wire_key)
            return subprocess.CompletedProcess(
                [], 0, json.dumps({"job_id": f"job-{len(submitted)}"}), ""
            )

        def poll(process, _remaining, *, recovery_probe=False):
            attempt = int(json.loads(process.stdout)["job_id"].split("-")[-1])
            failed = attempt <= failures
            job = {
                "job_id": f"job-{attempt}",
                "status": status if failed else "succeeded",
            }
            if failed:
                job["error"] = {
                    "error_class": "infra",
                    "reason": "backend_unavailable",
                    "message": "temporary infrastructure failure",
                }
            else:
                job["result"] = {"ok": True}
            return subprocess.CompletedProcess(
                [], 1 if failed else 0, json.dumps(job), ""
            )

        with patch("aka.light.execution.gateway_jobs.time.sleep"):
            process = execute_job(
                {"request": "infra-test", "limit": max_infra_retries},
                submit,
                poll,
                wait_budget=60,
                idempotent_submission=True,
                max_infra_retries=max_infra_retries,
            )
        return process, submitted

    def test_ordinary_gateway_deadline_has_no_retry_count_cap(self):
        process, submitted = self._deadline_job(
            max_deadline_retries=None,
            failures=3,
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(len(submitted), 4)

    def test_terminal_gateway_deadline_has_no_retry_count_cap(self):
        process, submitted = self._deadline_job(
            max_deadline_retries=None,
            failures=3,
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(len(submitted), 4)

    def test_exploration_infrastructure_retry_remains_bounded(self):
        process, submitted = self._infra_job(
            max_infra_retries=DEFAULT_MAX_INFRA_RETRIES,
            failures=3,
        )
        self.assertEqual(process.returncode, 1)
        self.assertEqual(len(submitted), 3)

    def test_terminal_infrastructure_retry_has_no_count_cap(self):
        process, submitted = self._infra_job(
            max_infra_retries=None,
            failures=3,
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(len(submitted), 4)

    def test_terminal_infrastructure_policy_is_selected_by_private_environment(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(TERMINAL_VALIDATION_ENV, None)
            self.assertEqual(
                configured_max_infra_retries(), DEFAULT_MAX_INFRA_RETRIES
            )
        with patch.dict(os.environ, {TERMINAL_VALIDATION_ENV: "1"}):
            self.assertIsNone(configured_max_infra_retries())

    def test_infrastructure_cancel_and_failure_origin_are_retryable(self):
        self.assertEqual(
            retry_kind({
                "status": "cancelled",
                "error": {"error_class": "infra", "message": "worker lost"},
            }),
            "infra",
        )
        self.assertEqual(
            retry_kind({
                "status": "failed",
                "failure_origin": "infrastructure",
                "error": {"message": "ray node exited"},
            }),
            "infra",
        )
        self.assertEqual(
            retry_kind({
                "status": "failed",
                "error": {
                    "message": "job supervisor exited",
                    "details": {"failure_origin": "infrastructure"},
                },
            }),
            "infra",
        )

    def test_episode_report_candidate_eval_is_terminal_validation(self):
        runtime = SupervisorRuntime.__new__(SupervisorRuntime)
        runtime.config = SimpleNamespace(
            atrex_bench_root=Path("/private/atrex-bench"),
            optimization_mode="production",
        )
        runtime.numerical_validator = None
        runtime.measurements = SimpleNamespace(read=lambda _: {})
        runtime.journal_factory = SimpleNamespace(
            research_records=False,
            experiments_enabled=False,
        )
        runtime._live = lambda _: True
        captured = {}

        def validate_candidate(context, source):
            context.measure(["--kind", "run"], source, True)
            return {"status": "ok"}

        runtime.acceptance = SimpleNamespace(validate_candidate=validate_candidate)
        runtime.execute = lambda *args, **kwargs: captured.update(kwargs) or {
            "exit_code": 0, "stdout": "", "stderr": "",
        }
        capability = SimpleNamespace()
        self.assertEqual(
            runtime._validate_candidate_correctness(capability, b"candidate"),
            {"status": "ok"},
        )
        self.assertIs(captured["terminal_validation"], True)

    def test_output_limit_recovery_defaults_to_five_continuations(self):
        self.assertEqual(WorkflowOptions().output_limit_recovery_retries, 5)
        self.assertEqual(WORKFLOW_PLUGIN_DEFAULTS["output_limit_recovery_retries"], 5)
        campaign = SimpleNamespace(module_options=SimpleNamespace())
        self.assertEqual(
            session_execution_policy(campaign)["output_limit_recovery_retries"],
            5,
        )

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
        self.assertNotIn("--evaluation-step-timeout", schema["cli_options"])

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

    def test_v0_and_candidates_share_300_second_performance_timeout(self):
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

            v0 = build_typed_request(
                workspace,
                "L20D",
                1800,
                [],
                ["python3", "test_kernel.py", "--version", "v0"],
                "run",
            )
            candidate = build_typed_request(
                workspace,
                "L20D",
                1800,
                [],
                ["python3", "test_kernel.py"],
                "run",
            )

        self.assertEqual(v0["runner_overrides"]["perf_timeout_s"], 300)
        self.assertEqual(candidate["runner_overrides"]["perf_timeout_s"], 300)

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
                patch("aka.light.workflow.bootstrap._sandbox_command", return_value=completed) as sandbox,
                patch.object(Campaign, "_print_v0_evaluator_output"),
            ):
                parsed, problem = campaign._validate_framework_baseline(1)
        self.assertIs(sandbox.call_args.kwargs["terminal_validation"], True)
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
