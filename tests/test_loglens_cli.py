import contextlib
import io
import tempfile
import unittest

from loglens.cli import EXIT_USAGE, main
from tests import loglens_fixtures as fx


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

    def test_input_format_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fx.write_lines(tmp, "app.log", [
                '{"ts": "2026-03-01T10:00:00Z", "level": "info", "msg": "jsonl body"}'])
            code, out, _ = run(["--input-format", "jsonl", str(path)])
            self.assertEqual(code, 0)
            self.assertTrue(out)
            self.assertIn("INFO", out)
            self.assertIn("jsonl body", out)
            self.assertNotIn("invalid JSON", out)

    def test_format_no_longer_selects_input_format(self):
        code, _, err = run(["--format", "jsonl", "x.log"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)
        self.assertIn("--format", err)
