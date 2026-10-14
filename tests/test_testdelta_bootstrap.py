"""testdelta bootstrap: redact copy, console script, usage errors, ASCII."""
import contextlib
import io
import pathlib
import unittest

from testdelta.cli import EXIT_OK, EXIT_USAGE, main

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def ascii_files():
    files = sorted((ROOT / "testdelta").glob("*.py"))
    files += sorted((ROOT / "tests").glob("test_testdelta*.py"))
    files += sorted((ROOT / "tests").glob("testdelta_fixtures*.py"))
    return files


class TestBootstrap(unittest.TestCase):
    def test_redact_copy_identical(self):
        copy = (ROOT / "testdelta" / "_redact.py").read_bytes()
        original = (ROOT / "loglens" / "redact.py").read_bytes()
        self.assertTrue(copy)
        self.assertEqual(copy, original)

    def test_console_script_declared(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertTrue(text)
        self.assertIn('testdelta = "testdelta.cli:main"', text)
        packages = [line for line in text.splitlines() if line.startswith("packages = [")]
        self.assertEqual(len(packages), 1)
        self.assertIn('"testdelta"', packages[0])

    def test_unknown_option_is_usage_error(self):
        code, _, err = run(["a", "b", "--frobnicate"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)
        self.assertIn("unrecognized arguments", err)

    def test_missing_arguments_is_usage_error(self):
        code, _, err = run([])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)

    def test_bad_numbers_are_usage_errors(self):
        for argv in (["--slow-ratio", "0"], ["--slow-min", "-1"],
                     ["--slow-ratio", "nan"], ["--max-bytes", "0"],
                     ["--format", "xml"], ["--input-format", "tap"]):
            with self.subTest(argv=argv):
                code, _, err = run(["a", "b"] + argv)
                self.assertEqual(code, EXIT_USAGE)
                self.assertTrue(err)

    def test_help_lists_exit_codes(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(out)
        self.assertIn("exit codes:", out)
        self.assertIn("never runs tests", out)

    def test_sources_are_ascii(self):
        files = ascii_files()
        self.assertGreater(len(files), 3)
        for path in files:
            data = path.read_bytes()
            self.assertTrue(data or path.name == "__init__.py", path)
            self.assertTrue(data.isascii(), f"{path} is not pure ASCII")


if __name__ == "__main__":
    unittest.main()
