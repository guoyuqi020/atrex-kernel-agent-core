"""Accept recorded, policy-matched ABBA evidence for Full Episodes."""
from __future__ import annotations

import tempfile
from pathlib import Path

from aka.light.evaluation.provider import result_from_response
from aka.light.experiment.store import EvidenceUnavailable
from aka.light.workspace.files import read_input, publish
from aka.light.workspace.episode import PUBLIC_FILES
from aka.light.execution.retry import InfrastructureUnavailable
from aka.light.runtime.errors import GatewayExecutionUnavailable, RequestDispatchTimeout

from aka.light.workspace.git import git_blob, warn_kernel_mismatch
from aka.light.workflow.models import VerificationResult
from aka.light.acceptance.verifier import (
    GatewayABBAValidator,
    score_verification_payload,
    verification_schedule,
)


class RecordedABBAValidator(GatewayABBAValidator):
    def __init__(self, *, execute, **kwargs):
        super().__init__(**kwargs)
        self.execute = execute

    def request_argv(self, baseline_kernel_id: str) -> list[str]:
        """One canonical request for trusted acceptance and the Agent prompt."""
        return [
            "--kind", "run", "--mode", "full", "--no-sync",
            "--baseline-commit", baseline_kernel_id,
            "--comparison-repeats", str(self.repeats),
            "--comparison-run-timeout", str(self.per_run_timeout),
        ]

    def agent_instructions(self, *, baseline_kernel_id: str, measurement_repetitions: int) -> str:
        import shlex
        measure = shlex.join(
            ["python3", "tools/sandbox.py", *self.request_argv(baseline_kernel_id)]
        )
        return f"""## This Episode's acceptance request

ABBA is optional during exploration. To make a comparison eligible for acceptance reuse,
commit the complete candidate, verify `git status` is clean, and run this exact command:

```bash
{measure}
```

`--baseline-commit` names this Episode's committed incumbent, not the current draft. Runtime
exports that exact Git source into private request staging before submission. The command matches
the Supervisor's configured ABBA repeats and per-run timeout.
Runtime applies the Campaign's Shape batch size; the separate candidate correctness gate owns its
Runtime-defined coverage. Do not add
command/input/Shape/seed/benchmark/dependency overrides.
The Runtime supplies the evaluator, full workload and target; private inputs stay private.

Supervisor measurement repetitions: **{measurement_repetitions}** per request (one result if 1;
per-Shape median if 3). The enclosing allocation is derived from the per-run limits above.
These are Runtime-owned settings, already applied to this request; do not loop the command
or set environment variables to reproduce them.

Reuse requires the same candidate, incumbent, inputs, evaluator and complete task policy.
A duplicate-task error names an existing Record: read `records/<ID>.json` and do not make another
GPU submission.
Acceptance reuses a completed matching Record or measures when none exists; it is not a
zero-submission guarantee. ABBA does not replace the standard full Evaluate required by
`episode-report`, and the Supervisor still applies policy and performance gates.
"""

    def verify(self, workspace: Path, *, base_commit: str, candidate_commit: str,
               changed_paths: list[str], candidate_kernel_id: str | None = None,
               baseline_kernel_id: str | None = None) -> VerificationResult:
        try:
            from aka.light.workspace.sources import from_git, Artifact, install
            candidate = from_git(workspace, candidate_commit)
            baseline = from_git(workspace, base_commit)
            if not Artifact(baseline).allows_changes(changed_paths):
                raise ValueError("Recorded acceptance requires a kernel-only candidate")
            warn_kernel_mismatch(workspace, candidate)
            with tempfile.TemporaryDirectory(prefix="aka-acceptance-") as temporary:
                staged = Path(temporary).resolve()
                for name in PUBLIC_FILES:
                    try:
                        content = read_input(workspace, name)
                    except FileNotFoundError:
                        continue
                    publish(staged, name, content)
                install(staged, candidate)
                response = self.execute(
                    staged,
                    self.request_argv(baseline_kernel_id or base_commit),
                    kernel_ids={
                        "candidate": candidate_kernel_id or candidate_commit,
                        "baseline": baseline_kernel_id or base_commit,
                    },
                )
            result = result_from_response(response, "same_allocation_abba")
            if not result or not result.get("gateway_record_id"):
                raise ValueError("No authoritative ABBA record: " + response.get("stderr", "")[-1000:])
            if result.get("comparison") != {"method": "abba", "repeats": self.repeats}:
                raise ValueError("ABBA record does not match the acceptance schedule")
            from aka.light.evaluation.native_abba import is_terminal_abba_timeout
            if is_terminal_abba_timeout(result):
                error = result["error"]
                verdict = VerificationResult(
                    "FAIL",
                    None,
                    None,
                    None,
                    artifact=result["gateway_record_id"],
                    error=(
                        "ABBA run exceeded its wall-clock limit on the initial request and one retry: "
                        + str(error.get("message") or error.get("reason"))
                    ),
                )
                verdict.gateway_record_id = result["gateway_record_id"]
                verdict.reused = response.get("reused") is True
                return verdict
            # Gateway validates every schedule row and Shape. Promotion uses
            # latency reduction, never the reference-relative performance score.
            schedule = verification_schedule(1)
            rows = []
            for step, side in zip(schedule, ("baseline", "candidate"), strict=True):
                metrics = result[side]
                rows.append(dict(step, exit_code=0, result=dict(
                    metrics, all_pass=result.get("correct") is True and metrics.get("correct") is True)))
            scored = score_verification_payload(
                {"schema_version": 1, "runs": rows}, schedule=schedule, repeats=1,
                min_improvement_pct=self.min_improvement_pct, artifact=result["gateway_record_id"],
            )
            scored.gateway_record_id = result["gateway_record_id"]
            scored.reused = response.get("reused") is True
            return scored
        except InfrastructureUnavailable:
            raise
        except (GatewayExecutionUnavailable, RequestDispatchTimeout) as error:
            raise InfrastructureUnavailable(str(error)) from error
        except EvidenceUnavailable as error:
            raise InfrastructureUnavailable(str(error)) from error
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            return VerificationResult("ERROR", None, None, None, error=f"Recorded ABBA verification failed: {error}")
