import contextlib
import io
import unittest

from loglens.cli import EXIT_USAGE, main


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestLoglensCliStub(unittest.TestCase):
    def test_no_arguments_is_usage_error(self):
        code, _, err = run([])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)
        self.assertIn("loglens:", err)

    def test_unknown_option_is_usage_error(self):
        code, _, err = run(["--follow", "x.log"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)
        self.assertIn("--follow", err)

    def test_help_exits_zero(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, 0)
        self.assertTrue(out)
        self.assertIn("--raw", out)
