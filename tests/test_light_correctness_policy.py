"""Regression tests for Runtime-owned AKA-Light correctness coverage."""

from contextlib import ExitStack
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from aka.contracts.evaluation import derived_evaluation_timeout
from aka.contracts.workflow import WorkflowSettings
from aka.contracts.workspace import CandidateHandle
from aka.contracts.workflow_launch import decode_launch
from aka.legacy.task.problem.layout import agent_visible_operator_files
from aka.legacy.task.problem.native_harness import _parser as native_harness_parser
from aka.light.acceptance.numerical import NumericalCancellation
from aka.light.acceptance.provider import (
    CandidateValidation,
    RecordedAcceptance,
    candidate_acceptance_argv,
)
from aka.light.acceptance.recorded_verifier import RecordedABBAValidator
from aka.light.acceptance.source_policy import (
    install_workspace_policy,
    optimization_mode_directive,
    production_structure_violations,
    workspace_policy_block,
)
from aka.light.evaluation.gateway import (
    _run_typed_gateway,
    build_parser,
    build_typed_request,
    evaluation_policy,
)
from aka.light.evaluation.hardware import hardware_directive
from aka.light.evaluation.evaluation_failures import (
    embedded_candidate_wall_clock_timeouts,
)
from aka.light.evaluation.policy import BENCH_ITERS, CORRECTNESS_CASES
from aka.light.evaluation.projection import gateway_error_from_process
from aka.light.evaluation.shapes import (
    effective_shape_batch_size,
    shape_request_timeout,
)
from aka.light.execution.gateway_jobs import (
    DEFAULT_MAX_DEADLINE_RETRIES,
    DEFAULT_MAX_INFRA_RETRIES,
    TERMINAL_VALIDATION_ENV,
    annotate_deadline_timeout,
    configured_max_deadline_retries,
    configured_max_infra_retries,
    execute_job,
    retry_kind,
)
from aka.light.runtime.server import RuntimeConfig, SupervisorRuntime
from aka.light.runtime.request_errors import AgentRequestError
from aka.light.runtime.request_feedback import gateway_schema
from aka.light.workflow.bootstrap import Campaign, framework_baseline_validation_command
from aka.light.workflow.context import conversion_required, session_execution_policy
from aka.light.workflow.plugin import Defaults as WORKFLOW_PLUGIN_DEFAULTS
from aka.light.workflow.provider import WorkflowOptions
from aka.light.workflow.episode import LongHorizonCampaign
from aka.light.workflow.search import SearchOptions
from aka.light.workflow.session_io import _evaluation_performance_timeout
from aka.light.workspace.sources import default_tree, install, pack
from aka.light.workspace.episode import _claude_instructions
from aka.legacy.workflow.cli_conversion import adapt_argv


class RuntimeOwnedCorrectnessPolicyTests(unittest.TestCase):
    def test_episode_claude_instructions_include_current_mode_policy(self):
        production = workspace_policy_block(
            "production",
            "Cuda",
            source_checks_enabled=True,
            specify_dsl=True,
        )
        projected = _claude_instructions(production).decode()
        self.assertIn("# Kernel engineering constraints", projected)
        self.assertIn("Optimization mode: production (hard gate)", projected)
        self.assertIn("submitted candidate's implementation framework is exactly **Cuda**", projected)
        self.assertIn("During development, you may use any language or DSL", projected)
        self.assertIn("final-candidate constraint, not a development-method restriction", projected)
        self.assertEqual(projected.count("ATREX_OPTIMIZATION_MODE_POLICY_BEGIN"), 1)

        leaderboard = _claude_instructions(
            workspace_policy_block(
                "leaderboard",
                "Cuda",
                source_checks_enabled=True,
                specify_dsl=True,
            )
        ).decode()
        self.assertIn("Optimization mode: leaderboard", leaderboard)
        self.assertNotIn("production (hard gate)", leaderboard)

        open_dsl = _claude_instructions(
            workspace_policy_block(
                "production",
                "Cuda",
                source_checks_enabled=True,
                specify_dsl=False,
            )
        ).decode()
        self.assertIn("Optimization mode: production (open DSL)", open_dsl)
        self.assertNotIn("submitted candidate's implementation framework is exactly **Cuda**", open_dsl)

        with self.assertRaisesRegex(TypeError, "workspace_policy must be text"):
            RuntimeConfig("L20D", workspace_policy=1)

    def test_search_role_specialization_defaults_off_and_can_be_enabled(self):
        self.assertFalse(SearchOptions().role_specialization)
        self.assertTrue(SearchOptions(role_specialization=True).role_specialization)
        with self.assertRaisesRegex(TypeError, "role_specialization must be a boolean"):
            SearchOptions(role_specialization=1)

        for enabled in (True, False):
            with tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                base = SimpleNamespace(
                    workspace=workspace,
                    platform="L20D",
                    arch="sm_103",
                    notes="none",
                    research_records=False,
                    research_modules=(),
                    search_coordinate={
                        "epoch": 1,
                        "trajectory": 1,
                        "episode": 1,
                        "role_specialization": enabled,
                    },
                    module_options=SimpleNamespace(goal_episodes=False),
                    specify_dsl=True,
                    framework="Cuda",
                    dsl_context="- DSL: `Cuda`",
                    plugin_directive=lambda stage: "",
                    research_board=lambda: {},
                    tool_runtime=SimpleNamespace(
                        pause_agent_during_requests=True
                    ),
                )
                prompt = LongHorizonCampaign(base)._prompt(
                    episode=1,
                    version=1,
                    worktree=CandidateHandle(
                        1, "a" * 40, "work", workspace
                    ),
                    conversion_pending=False,
                )
            self.assertIn(
                "Search location: Epoch 1, Trajectory 1, Episode 1", prompt
            )
            self.assertEqual(
                "T1 role: bounded exploitation" in prompt,
                enabled,
            )

    def test_episode_prompt_has_one_workflow_and_modular_contracts(self):
        prompt_dir = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "aka"
            / "light"
            / "workflow"
            / "prompts"
        )
        episode = (prompt_dir / "episode.md").read_text(encoding="utf-8")
        self.assertEqual(episode.count("## Workflow"), 1)
        self.assertIn("{{RESEARCH_MODULE_RULES}}", episode)
        self.assertIn("{{HANDOFF_CONTRACT}}", episode)
        self.assertIn("{{RUNTIME_WAIT_POLICY}}", episode)
        for removed in (
            "{{MODE_POLICY}}",
            "{{EVALUATOR}}",
            "{{SANDBOX}}",
            "{{AGENT_RUNTIME}}",
            "{{WIKI_DIRECTIVE}}",
            "{{REPORT_SELECTION}}",
        ):
            self.assertNotIn(removed, episode)
        for name in (
            "episode_unrecorded.md",
            "episode_experiments.md",
            "episode_directions.md",
            "episode_research.md",
        ):
            fragment = (prompt_dir / name).read_text(encoding="utf-8")
            self.assertNotIn("## Workflow", fragment)
            self.assertNotIn("## Handoff", fragment)

    def test_optional_dsl_settings_and_cli_normalization(self):
        settings = WorkflowSettings(platform="L20D", framework="Cuda")
        self.assertTrue(settings.specify_dsl)
        with self.assertRaises(TypeError):
            WorkflowSettings(platform="L20D", framework="Cuda", specify_dsl=1)

        launch = decode_launch(
            adapt_argv(
                [
                    "--op-dir",
                    str(Path.cwd()),
                    "--platform",
                    "L20D",
                    "--framework",
                    "Cuda",
                    "--no-specify-dsl",
                ]
            )
        )
        self.assertFalse(launch.request.settings.specify_dsl)
        self.assertEqual(launch.request.settings.framework, "")
        self.assertIn("specify_dsl", launch.provided_settings)

    def test_open_dsl_policy_is_neutral_and_disables_conversion(self):
        directive = optimization_mode_directive(
            "production", "Cuda", specify_dsl=False
        )
        self.assertIn("No DSL is prescribed", directive)
        self.assertNotIn("`Cuda`", directive)
        campaign = SimpleNamespace(
            source_checks_enabled=True,
            specify_dsl=False,
            framework="Triton",
            convert_after=0,
        )
        self.assertFalse(conversion_required(campaign, 99, Path(".")))

    def test_open_dsl_structure_and_workspace_policy_do_not_pin_framework(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            workspace = Path(directory)
            policy, files = default_tree(
                {"kernel.py": b"def run(*args):\n    return args\n"}
            )
            install(workspace, pack(files, policy))
            self.assertEqual(
                production_structure_violations(
                    workspace, "", specify_dsl=False
                ),
                [],
            )
            self.assertIn(
                "unsupported production framework",
                production_structure_violations(
                    workspace, "", specify_dsl=True
                )[0],
            )

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            workspace = Path(directory)
            (workspace / "kernel.py").write_text(
                "def run(*args):\n    return args\n", encoding="utf-8"
            )
            self.assertEqual(
                production_structure_violations(
                    workspace, "", specify_dsl=False
                ),
                [],
            )

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            workspace = Path(directory)
            install_workspace_policy(
                workspace,
                "production",
                "Cuda",
                specify_dsl=False,
                update_tracked_files=False,
            )
            state = json.loads(
                (workspace / ".orchestrator_mode.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["framework"], "")
            self.assertFalse(state["specify_dsl"])

    def test_open_dsl_framework_baseline_prompt_is_neutral(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            campaign = Campaign(
                name="op",
                kernel_demo=str(Path(directory) / "reference.py"),
                platform="L20D",
                framework="Cuda",
                specify_dsl=False,
                work_dir=directory,
            )
            campaign.tool_runtime = SimpleNamespace(
                wiki=False,
                pause_agent_during_requests=True,
            )
            campaign.plugin_directive = lambda _phase: ""
            campaign._framework_baseline_correctness_guidance_text = lambda: ""
            campaign._mode_directive = lambda: optimization_mode_directive(
                "production", "", specify_dsl=False
            )
            prompt = campaign._framework_baseline_prompt(1)
            self.assertIn("Choose a supported DSL", prompt)
            self.assertNotIn("- DSL: `Cuda`", prompt)
            self.assertNotIn("yourself in `Cuda`", prompt)
            self.assertNotIn("{{", prompt)

    def test_disabled_framework_correctness_review_has_no_prompt_section(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            campaign = Campaign(
                name="op",
                kernel_demo="reference.py",
                platform="L20D",
                framework="Cuda",
                work_dir=directory,
            )
            self.assertEqual(
                campaign._framework_baseline_correctness_guidance_text(), ""
            )

    def test_hardware_prompt_contains_only_dynamic_runtime_arch(self):
        directive = hardware_directive("masked-product", "vendor_arch_987")

        self.assertEqual(directive, "Hardware: **vendor_arch_987**\n")
        self.assertNotIn("masked-product", directive)
        self.assertNotIn("sm_103", directive)
        self.assertNotIn("tcgen05", directive)

    def _framework_baseline_campaign(self, directory):
        return Campaign(
            name="operator",
            kernel_demo=str(Path(directory) / "reference.py"),
            platform="cuda",
            framework="cuda",
            work_dir=directory,
            optimization_mode="leaderboard",
            framework_baseline="auto",
        )

    def _framework_baseline_decision_patches(
        self, directory, *, reference_blob, implementation_blob
    ):
        def committed_blob(_workspace, _revision, path):
            if path == "reference.py":
                return reference_blob
            if path == "kernel.py":
                return implementation_blob
            return ""

        return (
            patch("aka.light.workflow.bootstrap.latest_version", return_value=0),
            patch("aka.light.workflow.bootstrap.git_head", return_value="head"),
            patch(
                "aka.light.workflow.bootstrap.resolve_framework_baseline_commit",
                return_value=(None, None),
            ),
            patch(
                "aka.light.workflow.bootstrap.head_kernel_is_initial_baseline",
                return_value=True,
            ),
            patch(
                "aka.light.workflow.bootstrap.framework_baseline_progress_path",
                return_value=Path(directory) / "missing-progress.json",
            ),
            patch.object(
                Campaign,
                "source_checks_enabled",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "aka.light.workflow.bootstrap.production_structure_violations",
                return_value=[],
            ),
            patch.object(Campaign, "_v0_baseline_commit", return_value="v0"),
            patch("aka.light.workspace.sources.revision_contains_reference",
                  return_value=reference_blob == implementation_blob),
            patch("aka.light.workspace.sources.layout_at",
                  return_value=SimpleNamespace(replaceable_baseline=True)),
            patch("aka.light.workspace.sources.read",
                  return_value=SimpleNamespace(snapshot=b"source-tree")),
            patch("aka.light.workspace.sources.from_revision",
                  return_value=SimpleNamespace(snapshot=b"source-tree")),
            patch(
                "aka.light.workflow.bootstrap.git_path_blob",
                side_effect=committed_blob,
            ),
            patch("aka.light.workflow.bootstrap.is_sol_op", return_value=False),
        )

    def test_known_v0_reference_wrapper_skips_production_review(self):
        with tempfile.TemporaryDirectory() as directory:
            campaign = self._framework_baseline_campaign(directory)
            reviewer = patch.object(
                Campaign,
                "_production_kernel_violations",
                side_effect=AssertionError("known V0 wrapper must not be reviewed"),
            )
            patches = self._framework_baseline_decision_patches(
                directory,
                reference_blob="same-blob",
                implementation_blob="same-blob",
            )
            with ExitStack() as stack:
                stack.enter_context(reviewer)
                for current in patches:
                    stack.enter_context(current)
                action, reason = campaign._framework_baseline_decision()

        self.assertEqual(action, "run")
        self.assertIn("PyTorch reference wrapper", reason)

    def test_distinct_v0_implementation_still_uses_production_review(self):
        with tempfile.TemporaryDirectory() as directory:
            campaign = self._framework_baseline_campaign(directory)
            patches = self._framework_baseline_decision_patches(
                directory,
                reference_blob="reference-blob",
                implementation_blob="implementation-blob",
            )
            reviewer_patch = patch.object(
                    Campaign, "_production_kernel_violations", return_value=[]
                )
            with ExitStack() as stack:
                reviewer = stack.enter_context(reviewer_patch)
                for current in patches:
                    stack.enter_context(current)
                action, reason = campaign._framework_baseline_decision()

        self.assertEqual(action, "pin")
        self.assertIn("already a compliant framework implementation", reason)
        reviewer.assert_called_once_with()

    def test_real_git_v0_reference_wrapper_skips_production_review(self):
        with tempfile.TemporaryDirectory() as directory:
            campaign = self._framework_baseline_campaign(directory)
            workspace = campaign.workspace
            (workspace / "implementation").mkdir(parents=True)
            source = "import torch\n\ndef run(*args, **kwargs):\n    return torch.zeros(1)\n"
            (workspace / "kernel.py").write_text(source, encoding="utf-8")
            (workspace / "reference.py").write_text(source, encoding="utf-8")
            (workspace / "implementation" / "__init__.py").write_text(
                "", encoding="utf-8"
            )
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            subprocess.run(
                ["git", "config", "user.name", "AKA Test"], cwd=workspace, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "aka-test@atrex.local"],
                cwd=workspace,
                check=True,
            )
            subprocess.run(["git", "add", "."], cwd=workspace, check=True)
            subprocess.run(
                ["git", "-c", "commit.gpgSign=false", "commit", "-qm", "V0"],
                cwd=workspace,
                check=True,
            )
            from aka.light.workspace.sources import from_git

            committed_source = from_git(workspace, "HEAD")

            with (
                patch("aka.light.workflow.bootstrap.latest_version", return_value=0),
                patch(
                    "aka.light.workflow.bootstrap.resolve_framework_baseline_commit",
                    return_value=(None, None),
                ),
                patch.object(
                    Campaign,
                    "source_checks_enabled",
                    new_callable=PropertyMock,
                    return_value=True,
                ),
                patch(
                    "aka.light.workflow.bootstrap.production_structure_violations",
                    return_value=[],
                ),
                patch(
                    "aka.light.workspace.sources.snapshot",
                    return_value=committed_source,
                ),
                patch("aka.light.workflow.bootstrap.is_sol_op", return_value=False),
                patch.object(
                    Campaign,
                    "_production_kernel_violations",
                    side_effect=AssertionError(
                        "known V0 wrapper must not be reviewed"
                    ),
                ),
            ):
                action, reason = campaign._framework_baseline_decision()

        self.assertEqual(action, "run")
        self.assertIn("PyTorch reference wrapper", reason)

    def test_unlimited_numerical_review_keeps_nested_request_timeout(self):
        runtime = SimpleNamespace(closed=False, _live=lambda _parent: True)
        cancel = NumericalCancellation(
            runtime, SimpleNamespace(deadline=float("inf"))
        )

        with patch(
            "aka.light.acceptance.numerical.time.monotonic", return_value=100.0
        ):
            self.assertEqual(cancel.timeout(600), 600)

    def test_finite_numerical_review_caps_nested_request_timeout(self):
        runtime = SimpleNamespace(closed=False, _live=lambda _parent: True)
        cancel = NumericalCancellation(runtime, SimpleNamespace(deadline=150.9))

        with patch(
            "aka.light.acceptance.numerical.time.monotonic", return_value=100.0
        ):
            self.assertEqual(cancel.timeout(600), 50)

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
            "__atrex_eval_archives": {
                "candidate": {"kernel.py": "class Model: pass\n"}
            },
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

    def test_ordinary_gateway_deadline_stops_after_two_retries(self):
        process, submitted = self._deadline_job(
            max_deadline_retries=DEFAULT_MAX_DEADLINE_RETRIES,
            failures=3,
        )
        self.assertEqual(process.returncode, 1)
        self.assertEqual(len(submitted), 3)
        self.assertEqual(
            json.loads(process.stdout)["error"],
            {
                "error_class": "timeout",
                "reason": "timeout",
                "message": "job deadline exceeded",
            },
        )
        self.assertEqual(
            gateway_error_from_process(process, "evaluate")["error"],
            {
                "error_class": "timeout",
                "reason": "timeout",
                "message": "job deadline exceeded",
            },
        )

    def test_terminal_gateway_deadline_has_no_retry_count_cap(self):
        process, submitted = self._deadline_job(
            max_deadline_retries=None,
            failures=3,
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(len(submitted), 4)

    def test_deadline_timeout_is_added_to_agent_visible_error(self):
        process, submitted = self._deadline_job(
            max_deadline_retries=DEFAULT_MAX_DEADLINE_RETRIES,
            failures=3,
        )
        annotated = annotate_deadline_timeout(process, 600)

        self.assertEqual(len(submitted), 3)
        self.assertEqual(json.loads(annotated.stdout)["error"]["timeout_s"], 600)
        projected = gateway_error_from_process(annotated, "dev")
        self.assertEqual(projected["operation"], "dev")
        self.assertEqual(projected["error"]["timeout_s"], 600)
        self.assertIn("configured 600-second deadline", projected["next_action"])
        self.assertIn("command is too slow", projected["next_action"])
        self.assertIn("Do not resubmit the unchanged", projected["next_action"])
        self.assertEqual(
            projected["recovery"],
            [{"instruction": projected["next_action"]}],
        )

    def test_only_terminal_eval_selects_unbounded_deadline_retries(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(TERMINAL_VALIDATION_ENV, None)
            self.assertEqual(
                configured_max_deadline_retries(terminal_evaluation=True),
                DEFAULT_MAX_DEADLINE_RETRIES,
            )
        with patch.dict(os.environ, {TERMINAL_VALIDATION_ENV: "1"}):
            self.assertIsNone(
                configured_max_deadline_retries(terminal_evaluation=True)
            )
            self.assertEqual(
                configured_max_deadline_retries(terminal_evaluation=False),
                DEFAULT_MAX_DEADLINE_RETRIES,
            )

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
        policy_calls = []
        runtime.production_policy_validator = lambda workspace, source, **options: (
            policy_calls.append((workspace, source, options)) or []
        )
        runtime.measurements = SimpleNamespace(read=lambda _: {})
        runtime.journal_factory = SimpleNamespace(
            research_records=False,
            experiments_enabled=False,
        )
        runtime._live = lambda _: True
        captured = {}

        def validate_candidate(context, source):
            context.measure(["--kind", "run"], source, True)
            self.assertEqual(context.production_policy(source), [])
            return {"status": "ok"}

        runtime.acceptance = SimpleNamespace(validate_candidate=validate_candidate)
        runtime.execute = lambda *args, **kwargs: captured.update(kwargs) or {
            "exit_code": 0, "stdout": "", "stderr": "",
        }
        capability = SimpleNamespace(
            journal=SimpleNamespace(
                service=SimpleNamespace(git_workspace=Path("/candidate")),
                require_gluon=True,
            )
        )
        self.assertEqual(
            runtime._validate_candidate_correctness(capability, b"candidate"),
            {"status": "ok"},
        )
        self.assertIs(captured["terminal_validation"], True)
        self.assertEqual(policy_calls[0][0], Path("/candidate"))
        self.assertEqual(policy_calls[0][1], b"candidate")
        self.assertIs(policy_calls[0][2]["parent"], capability)
        self.assertIs(policy_calls[0][2]["require_gluon"], True)

    def test_production_review_rejection_is_returned_by_episode_report(self):
        source = b"candidate"
        numerical_calls = []
        context = CandidateValidation(
            atrex_bench=True,
            production=True,
            measure=lambda *_args, **_kwargs: {
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
            },
            read_record=lambda _record_id: {},
            ensure_live=lambda: None,
            production_policy=lambda _source: [
                "production candidate rejected by independent reviewer: "
                "compute_provenance: PyTorch performs the core computation"
            ],
            supplemental=lambda _source, _correctness_passed: (
                numerical_calls.append(True) or ""
            ),
        )
        result = {
            "all_pass": True,
            "gateway_record_id": "gateway-" + "a" * 32,
        }
        with (
            patch(
                "aka.light.evaluation.provider.result_from_response",
                return_value=result,
            ),
            patch(
                "aka.light.acceptance.provider.authoritative_candidate_record",
                return_value=True,
            ),
            self.assertRaises(AgentRequestError) as raised,
        ):
            RecordedAcceptance().validate_candidate(context, source)

        response = raised.exception.response
        self.assertEqual(
            response["error"]["code"],
            "candidate_production_policy_failed",
        )
        self.assertIn("PyTorch performs", response["error"]["violations"][0])
        self.assertIn("resubmit episode-report", response["error"]["next_action"])
        self.assertEqual(numerical_calls, [True])
        self.assertEqual(response["checks"]["correctness"]["status"], "passed")
        self.assertEqual(response["checks"]["production_review"]["status"], "failed")
        self.assertEqual(response["checks"]["numerical"]["status"], "passed")

    def test_production_acceptance_gates_start_concurrently(self):
        acceptance = RecordedAcceptance()
        barrier = threading.Barrier(3)

        def gate(*_args):
            barrier.wait(timeout=2)
            return {"status": "passed"}

        context = CandidateValidation(
            atrex_bench=True,
            production=True,
            measure=lambda *_args, **_kwargs: {},
            read_record=lambda _record_id: {},
            ensure_live=lambda: None,
        )
        with (
            patch.object(acceptance, "_validate_correctness", side_effect=gate),
            patch.object(acceptance, "_validate_policy", side_effect=gate),
            patch.object(acceptance, "_validate_numerical", side_effect=gate),
        ):
            result = acceptance.validate_candidate(context, b"candidate")
        self.assertEqual(result["checks"]["correctness"]["status"], "passed")
        self.assertEqual(result["checks"]["production_review"]["status"], "passed")
        self.assertEqual(result["checks"]["numerical"]["status"], "passed")

    def test_multiple_acceptance_failures_are_returned_together(self):
        acceptance = RecordedAcceptance()
        context = CandidateValidation(
            atrex_bench=True,
            production=True,
            measure=lambda *_args, **_kwargs: {},
            read_record=lambda _record_id: {},
            ensure_live=lambda: None,
        )
        with (
            patch.object(
                acceptance,
                "_validate_correctness",
                side_effect=AgentRequestError(
                    "correctness failed",
                    code="candidate_correctness_failed",
                    next_action="Repair correctness.",
                ),
            ),
            patch.object(
                acceptance,
                "_validate_policy",
                side_effect=AgentRequestError(
                    "policy failed",
                    code="candidate_production_policy_failed",
                    next_action="Repair final source.",
                ),
            ),
            patch.object(
                acceptance,
                "_validate_numerical",
                return_value={"status": "passed"},
            ),
            self.assertRaises(AgentRequestError) as raised,
        ):
            acceptance.validate_candidate(context, b"candidate")

        response = raised.exception.response
        self.assertEqual(response["error"]["code"], "candidate_acceptance_failed")
        self.assertEqual(
            response["error"]["failed_checks"],
            ["correctness", "production_review"],
        )
        self.assertEqual(response["error"]["checks"]["numerical"]["status"], "passed")

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

    def test_framework_baseline_prompt_delegates_runtime_contract_to_claude_and_skills(self):
        template = (
            Path(__file__).resolve().parents[1]
            / "src/aka/light/workflow/prompts/framework_baseline.md"
        ).read_text(encoding="utf-8")
        self.assertIn("{{RUNTIME_WAIT_POLICY}}", template)
        for removed in ("{{AGENT_RUNTIME}}", "{{SANDBOX}}", "{{EVALUATOR}}"):
            self.assertNotIn(removed, template)

    def test_light_policy_defers_correctness_rules_to_task_configuration(self):
        directive = optimization_mode_directive("production", "CUDA")
        self.assertNotIn("relative L2 error <= 0.2 for NVFP4", directive)
        self.assertNotIn("elementwise atol/rtol", directive)
        self.assertNotIn("mismatch-fraction", directive)
        self.assertIn("read workspace `metadata.json`", directive)
        self.assertIn("benchmark correctness contract is authoritative", directive)
        self.assertIn("Do not assume, invent, or override", directive)
        self.assertIn("one passing check never overrides another reported failure", directive)

    def test_pinned_production_dsl_only_constrains_submitted_candidate(self):
        cuda = optimization_mode_directive("production", "Cuda")
        self.assertIn(
            "submitted candidate's implementation framework is exactly **Cuda**",
            cuda,
        )
        self.assertIn("During development, you may use any language or DSL", cuda)
        self.assertIn("PyTorch/ATen compute", cuda)
        self.assertIn("third-party compute libraries", cuda)
        self.assertIn("dynamically loaded external implementations", cuda)
        self.assertIn("temporary prototype", cuda)
        self.assertIn("remove them from the submitted Kernel source tree", cuda)
        self.assertIn("wrapping another DSL as the selected framework", cuda)

        triton = optimization_mode_directive("production", "Triton")
        self.assertIn("submitted candidate's implementation framework is exactly **Triton**", triton)
        self.assertIn("Do not submit a candidate that switches early", triton)

    def test_dsl_context_distinguishes_final_gate_from_development_tools(self):
        production = object.__new__(Campaign)
        production.specify_dsl = True
        production.optimization_mode = "production"
        production.framework = "Cuda"
        self.assertIn("Required final candidate DSL: `Cuda`", production.dsl_context)
        self.assertIn("prototypes may use any language or DSL", production.dsl_context)

        leaderboard = object.__new__(Campaign)
        leaderboard.specify_dsl = True
        leaderboard.optimization_mode = "leaderboard"
        leaderboard.framework = "Cuda"
        self.assertEqual(
            leaderboard.dsl_context,
            "- Recommended candidate DSL: `Cuda`",
        )

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

    def test_framework_baseline_gates_run_concurrently_and_return_together(self):
        barrier = threading.Barrier(3)

        def policy_gate():
            barrier.wait(timeout=2)
            return ["review rejected final dependency"]

        def validation_gate(_version):
            barrier.wait(timeout=2)
            return {"all_pass": False}, "shape 7 failed correctness"

        def numerical_gate(_workspace, **options):
            barrier.wait(timeout=2)
            self.assertFalse(options["standard_correctness_passed"]())
            return "supplemental probe found an unstable case"

        with tempfile.TemporaryDirectory() as directory:
            campaign = Campaign(
                name="operator",
                kernel_demo="reference.py",
                platform="cuda",
                framework="cuda",
                work_dir=directory,
            )
            with (
                patch.object(
                    Campaign,
                    "source_checks_enabled",
                    new_callable=PropertyMock,
                    return_value=True,
                ),
                patch.object(campaign, "_production_kernel_violations", side_effect=policy_gate),
                patch.object(campaign, "_validate_framework_baseline", side_effect=validation_gate),
                patch.object(campaign, "supplemental_numerical_feedback", side_effect=numerical_gate),
            ):
                result, problem = campaign._framework_baseline_external_gates(1)

        self.assertEqual(result, {"all_pass": False})
        self.assertIn("review rejected final dependency", problem)
        self.assertIn("shape 7 failed correctness", problem)
        self.assertIn("supplemental probe found an unstable case", problem)
        self.assertIn("- production_review: failed", problem)
        self.assertIn("- correctness_performance: failed", problem)
        self.assertIn("- numerical: failed", problem)

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
