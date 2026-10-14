import contextlib
import io
import pathlib
import unittest

from gitarch.cli import EXIT_OK, EXIT_USAGE, main

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class TestBootstrap(unittest.TestCase):
    def test_redact_copy_identical(self):
        copy = (ROOT / "gitarch" / "_redact.py").read_bytes()
        original = (ROOT / "loglens" / "redact.py").read_bytes()
        self.assertTrue(copy)
        self.assertEqual(copy, original)

    def test_console_script_declared(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertTrue(text)
        self.assertIn('gitarch = "gitarch.cli:main"', text)
        packages = [line for line in text.splitlines() if line.startswith("packages = [")]
        self.assertEqual(len(packages), 1)
        self.assertIn('"gitarch"', packages[0])

    def test_unknown_option_is_usage_error(self):
        code, _, err = run(["--frobnicate"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)
        self.assertIn("gitarch:", err)
        self.assertIn("--frobnicate", err)

    def test_bad_timeout_is_usage_error(self):
        for value in ("0", "-1", "soon"):
            code, _, err = run(["--timeout", value])
            self.assertEqual(code, EXIT_USAGE, value)
            self.assertTrue(err)

    def test_rev_starting_with_dash_is_usage_error(self):
        code, out, err = run(["--rev=--output=/tmp/x"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)
        self.assertIn("must not start with '-'", err)
        self.assertEqual(out, "")

    def test_help_exits_zero(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(out)
        self.assertIn("--rev", out)
        self.assertIn("exit codes", out)
