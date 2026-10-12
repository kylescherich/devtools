import subprocess
import sys
import unittest
from pathlib import Path


class TestAskdataCLI(unittest.TestCase):
    def test_no_arguments_shows_usage(self):
        repo_root = Path(__file__).parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "askdata"],
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: askdata", result.stderr)
    
    def test_inspect_help_shows_options(self):
        repo_root = Path(__file__).parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "askdata", "inspect", "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        
        self.assertNotEqual(result.stdout, "")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--max-bytes", result.stdout)
        self.assertIn("--max-rows", result.stdout)
        self.assertIn("--timeout", result.stdout)
        self.assertIn("--force", result.stdout)
