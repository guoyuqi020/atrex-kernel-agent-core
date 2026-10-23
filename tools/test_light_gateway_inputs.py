"""Regression tests for Light gateway command input discovery."""

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aka.light.evaluation.gateway import _referenced_workspace_inputs  # noqa: E402


class CommandInputDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        (self.workspace / "scratch").mkdir()
        (self.workspace / "scratch" / "bench.py").write_text("pass\n")
        (self.workspace / "scratch" / "configs.json").write_text("[]\n")

    def test_long_inline_json_is_not_treated_as_a_filename(self):
        payload = '[{"name":"variant","value":"' + "x" * 512 + '"}]'
        for argument in (f"--cfg={payload}", payload):
            with self.subTest(argument=argument[:16]):
                inputs = _referenced_workspace_inputs(
                    self.workspace,
                    ["python3", "scratch/bench.py", argument],
                )
                self.assertEqual(inputs, {"scratch/bench.py"})

    def test_option_assignment_can_reference_a_workspace_file(self):
        inputs = _referenced_workspace_inputs(
            self.workspace,
            ["python3", "scratch/bench.py", "--config=scratch/configs.json"],
        )
        self.assertEqual(inputs, {"scratch/bench.py", "scratch/configs.json"})


if __name__ == "__main__":
    unittest.main()
