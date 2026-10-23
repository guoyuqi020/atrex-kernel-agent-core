"""Regression tests for public Dev results behind source-tree operations."""

from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aka.light.evaluation.gateway import public_dev_output  # noqa: E402
from aka.light.evaluation.projection import project_response  # noqa: E402
from aka.light.evaluation.source_diagnostics import DIAGNOSTIC_PREFIX  # noqa: E402
from aka.light.evaluation.source_transport import DIAGNOSTIC, run  # noqa: E402


class FakeGateway:
    public_dev_output = staticmethod(public_dev_output)

    @staticmethod
    def find_agate():
        return None

    @staticmethod
    def _resolved_gateway_url(*_args, **_kwargs):
        return "https://gateway.invalid"

    @staticmethod
    def parse_job_response(stdout):
        return json.loads(stdout)

    def __init__(self, job):
        self.job = job

    def run_direct_job(self, **_kwargs):
        return subprocess.CompletedProcess(
            args=["agate", "dev"], returncode=0, stdout=json.dumps(self.job), stderr=""
        )


def _args():
    return SimpleNamespace(url="", gateway_profile=None, timeout=600)


class SourceTreeDevResultTests(unittest.TestCase):
    def test_public_dev_result_is_attached_to_logical_diagnostic(self):
        diagnostic = {"operation": "check", "passed": False, "error": "compile failed"}
        execution = {
            "stdout": DIAGNOSTIC_PREFIX + json.dumps(diagnostic) + "\ncompiler detail\n",
            "stderr": "worker warning",
            "exit_code": 0,
            "output_bytes": 128,
            "output_truncated": False,
        }
        request = {
            DIAGNOSTIC: {"command": "python3 __atrex_diagnostic.py request.json"}
        }

        process = run(
            FakeGateway({"job_id": "dv_1", "status": "succeeded", "result": execution}),
            _args(), request, "check", 60,
        )
        job = json.loads(process.stdout)

        self.assertEqual(job["source_tree_execution"], execution)
        self.assertEqual(job["result"]["dev_result"], execution)

    def test_private_dev_result_is_not_attached(self):
        diagnostic = {"operation": "profile", "passed": True, "kernels": []}
        execution = {
            "stdout": DIAGNOSTIC_PREFIX + json.dumps(diagnostic) + "\n",
            "stderr": "",
            "exit_code": 0,
        }
        request = {DIAGNOSTIC: {"command": "python3 profile_driver.py"}}

        process = run(
            FakeGateway({"job_id": "dv_2", "status": "succeeded", "result": execution}),
            _args(), request, "profile", 60,
        )
        job = json.loads(process.stdout)

        self.assertNotIn("dev_result", job["result"])

    def test_all_source_tree_operations_preserve_projected_dev_fields(self):
        dev_result = {
            "stdout": "candidate output secret-value",
            "stderr": "candidate warning",
            "exit_code": 7,
            "output_bytes": 1234,
            "output_truncated": True,
            "private_future_field": "must not pass",
        }
        cases = (
            ("profile", "[sandbox] PROFILE_JSON=", {"passed": False, "kernels": []}),
            ("check", "[sandbox] CHECK_JSON=", {"passed": False}),
            ("disassemble", "[sandbox] DISASSEMBLE_JSON=", {"passed": False}),
        )
        for operation, prefix, result in cases:
            with self.subTest(operation=operation):
                payload = {**result, "dev_result": dev_result}
                process = subprocess.CompletedProcess(
                    args=["sandbox"], returncode=1,
                    stdout=prefix + json.dumps(payload) + "\n", stderr="",
                )
                response = project_response(
                    process, operation=operation, private_values=("secret-value",)
                )
                projected = json.loads(response["stdout"].split("=", 1)[1])

                self.assertEqual(projected["dev_result"]["exit_code"], 7)
                self.assertEqual(projected["dev_result"]["output_bytes"], 1234)
                self.assertTrue(projected["dev_result"]["output_truncated"])
                self.assertIn("<redacted>", projected["dev_result"]["stdout"])
                self.assertNotIn("private_future_field", projected["dev_result"])


if __name__ == "__main__":
    unittest.main()
