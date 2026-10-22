"""devtools checks its own tool packages with repo-xray --boundaries (BRIEF-DEVWS-035)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent

PACKAGES = ["repo_xray", "askdata", "jsondiff", "loglens", "gitarch", "testdelta",
            "envcontract", "keycheck"]


class TestXrayAdoption(unittest.TestCase):
    def test_tool_packages_independent(self):
        """The [tool.repo-xray] list in pyproject.toml has zero violations."""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, "-m", "repo_xray", str(REPO_ROOT),
                 "--boundaries", "--format", "json"],
                cwd=cwd, capture_output=True, text=True, env=env)
        self.assertNotEqual(result.stdout, "", result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["independent"]["packages"], PACKAGES)
        self.assertEqual(data["independent"]["not_found"], [])
        self.assertEqual(data["independent"]["violations"], [])
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
