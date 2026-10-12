import subprocess
import sys
import unittest
from pathlib import Path


class TestCLI(unittest.TestCase):
    def test_no_arguments_shows_usage(self):
        repo_root = Path(__file__).parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "repo_xray"],
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        
        self.assertNotEqual(result.stderr, "")
        self.assertIn("usage: repo-xray", result.stderr)
        self.assertEqual(result.returncode, 2)
