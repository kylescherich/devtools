import contextlib
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsondiff.cli import main
from tests import jsondiff_fixtures as fx

REPO_ROOT = Path(__file__).resolve().parent.parent


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._in = tempfile.TemporaryDirectory()
        self._out = tempfile.TemporaryDirectory()
        self.indir = self._in.name
        self.outdir = self._out.name

    def tearDown(self):
        self._in.cleanup()
        self._out.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def assert_error(self, expected_code, *argv):
        code, out, err = self.run_cli(*argv)
        self.assertTrue(err)
        self.assertEqual(code, expected_code, err)
        self.assertTrue(err.startswith("jsondiff: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertEqual(out, "")
        return err

    def pair(self, old, new, suffix=".json"):
        writer = fx.write_jsonl if suffix == ".jsonl" else fx.write_json
        return (writer(self.indir, "old" + suffix, old),
                writer(self.indir, "new" + suffix, new))

    def out(self, name):
        return Path(self.outdir) / name


class TestExitCodes(CliTestCase):
    def test_exit_0_no_structural_change(self):
        old, new = self.pair({"a": 1, "b": ["x"]}, {"a": 2, "b": ["y", "z"]})
        code, out, err = self.run_cli(old, new)
        self.assertTrue(out)
        self.assertEqual(code, 0, err)
        self.assertIn("jsondiff report", out)

    def test_exit_1_changes_none_breaking(self):
        old, new = self.pair({"a": 1}, {"a": 1, "b": 2})
        code, out, err = self.run_cli(old, new)
        self.assertTrue(out)
        self.assertEqual(code, 1, err)

    def test_exit_1_keyed_element_shape_change(self):
        old, new = self.pair([{"id": 1, "a": 1}, {"id": 2, "b": "x"}],
                             [{"id": 1, "b": "x"}, {"id": 2, "a": 1}])
        code, out, _ = self.run_cli(old, new)
        self.assertTrue(out)
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli(old, new, "--key", "id")
        self.assertTrue(out)
        self.assertEqual(code, 1)

    def test_exit_4_potentially_breaking(self):
        old, new = self.pair({"a": 1, "b": 2}, {"a": 1})
        code, out, err = self.run_cli(old, new)
        self.assertTrue(out)
        self.assertEqual(code, 4, err)
        self.assertIn("potentially breaking — review", out)

    def test_exit_2_usage_errors(self):
        old, new = self.pair({"a": 1}, {"a": 1})
        self.assert_error(2)
        self.assert_error(2, old, new, "--no-such-flag")
        self.assert_error(2, old, new, "--key", "orders=")
        self.assert_error(2, old, new, "--key", "id", "--key", "id")
        self.assert_error(2, old, new, "--max-depth", "0")
        self.assert_error(2, old, new, "--format", "md")
        jold, jnew = self.pair([{"id": 1}], [{"id": 1}], suffix=".jsonl")
        err = self.assert_error(2, jold, jnew, "--key", "id")
        self.assertIn("JSONL", err)
        self.assert_error(2, old, new, "--jsonl", "--key", "id")

    def test_exit_2_takes_precedence_over_3(self):
        missing = Path(self.indir) / "missing.jsonl"
        self.assert_error(2, missing, missing, "--key", "id")

    def test_exit_3_missing_file(self):
        old, _ = self.pair({"a": 1}, {"a": 1})
        err = self.assert_error(3, old, Path(self.indir) / "nope.json")
        self.assertIn("new file not found", err)

    def test_exit_3_invalid_json(self):
        old, _ = self.pair({"a": 1}, {"a": 1})
        bad = fx.write_text(self.indir, "bad.json", '{"a": 1,')
        err = self.assert_error(3, old, bad)
        self.assertIn("not valid JSON", err)

    def test_exit_3_not_utf8(self):
        old, _ = self.pair({"a": 1}, {"a": 1})
        latin1 = Path(self.indir) / "latin1.json"
        latin1.write_bytes(b'{"a": "caf\xe9"}')
        err = self.assert_error(3, old, latin1)
        self.assertIn("not UTF-8", err)
        utf16 = Path(self.indir) / "utf16.json"
        utf16.write_bytes('{"a": 1}'.encode("utf-16"))
        self.assert_error(3, old, utf16)

    def test_exit_3_directory_symlink_too_large(self):
        old, new = self.pair({"a": 1}, {"a": 1})
        self.assert_error(3, old, self.indir)
        link = Path(self.indir) / "link.json"
        try:
            os.symlink(new, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        self.assertIn("symlink", self.assert_error(3, old, link))
        self.assertIn("too large", self.assert_error(3, old, new, "--max-bytes", "3"))


class TestOutput(CliTestCase):
    def test_format_inference(self):
        old, new = self.pair({"a": 1}, {"a": "1"})
        md, html, forced = self.out("r.md"), self.out("r.html"), self.out("r.txt")
        self.assertEqual(self.run_cli(old, new, "--output", md)[0], 4)
        self.assertEqual(self.run_cli(old, new, "--output", html)[0], 4)
        self.assertEqual(self.run_cli(old, new, "--output", forced, "--format", "md")[0], 4)
        md_text, html_text = md.read_text("utf-8"), html.read_text("utf-8")
        self.assertTrue(md_text and html_text)
        self.assertTrue(md_text.startswith("# jsondiff report"))
        self.assertTrue(html_text.startswith("<!DOCTYPE html>"))
        self.assertEqual(forced.read_text("utf-8"), md_text)

    def test_refuses_existing_output_without_force(self):
        old, new = self.pair({"a": 1}, {"a": 1, "b": 2})
        target = self.out("report.html")
        target.write_text("keep me", encoding="utf-8")
        self.assertIn("--force", self.assert_error(2, old, new, "--output", target))
        self.assertEqual(target.read_text("utf-8"), "keep me")
        code, _, err = self.run_cli(old, new, "--output", target, "--force")
        self.assertEqual(code, 1, err)
        written = target.read_text("utf-8")
        self.assertTrue(written)
        self.assertIn("<!DOCTYPE html>", written)

    def test_refuses_output_inside_an_input_directory(self):
        old, new = self.pair({"a": 1}, {"a": 1, "b": 2})
        before = sorted(os.listdir(self.indir))
        self.assertTrue(before)
        for target in (Path(self.indir) / "report.html",
                       Path(self.indir) / "sub" / "report.md",
                       Path(self.indir)):
            err = self.assert_error(2, old, new, "--output", target, "--force")
            self.assertIn("inside the directory of an input", err)
        self.assertEqual(sorted(os.listdir(self.indir)), before)


class TestDeterminismAndReadOnly(CliTestCase):
    def test_byte_identical_across_runs(self):
        old, new = self.pair({"a": 1, "items": [{"x": 1}]},
                             {"a": "1", "items": [{"x": None, "y": 2}]})
        first, second = self.run_cli(old, new, "--all"), self.run_cli(old, new, "--all")
        self.assertTrue(first[1])
        self.assertEqual(first, second)
        for ext in ("md", "html"):
            a, b = self.out(f"a.{ext}"), self.out(f"b.{ext}")
            self.run_cli(old, new, "--output", a)
            self.run_cli(old, new, "--output", b)
            self.assertTrue(a.read_bytes())
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_inputs_unchanged_and_nothing_written_next_to_them(self):
        old, new = fx.privacy_pair(self.indir)
        jold = fx.null_vs_missing(self.indir)
        jnew = fx.invalid_jsonl(self.indir)
        inputs = [old, new, jold, jnew]
        before = {p: sha256(p) for p in inputs}
        listing = sorted(os.listdir(self.indir))
        self.assertTrue(before and listing)
        self.run_cli(old, new)
        self.run_cli(old, new, "--values", "--all")
        self.run_cli(jold, jnew)
        self.run_cli(old, new, "--output", self.out("r.html"))
        self.run_cli(old, new, "--output", self.out("r.md"), "--values")
        self.assertEqual(before, {p: sha256(p) for p in inputs})
        self.assertEqual(sorted(os.listdir(self.indir)), listing)


class TestPrivacyCli(CliTestCase):
    def test_values_off_by_default_on_with_flag(self):
        old, new = fx.privacy_pair(self.indir)
        default = [self.run_cli(old, new)[1]]
        for name in ("d.md", "d.html"):
            self.run_cli(old, new, "--output", self.out(name))
            default.append(self.out(name).read_text("utf-8"))
        for text in default:
            self.assertTrue(text)
            self.assertIn("values: off", text)
            for sentinel in fx.SENTINELS:
                self.assertNotIn(sentinel, text)
        shown = self.run_cli(old, new, "--values")[1]
        self.assertTrue(shown)
        self.assertIn("values: on", shown)
        for sentinel in fx.SENTINELS:
            self.assertIn(sentinel, shown)


class TestSubprocess(CliTestCase):
    def test_python_m_jsondiff(self):
        old, new = self.pair({"a": 1}, {"a": 1, "b": 2})
        proc = subprocess.run([sys.executable, "-m", "jsondiff", str(old), str(new)],
                              cwd=REPO_ROOT, capture_output=True, timeout=60)
        self.assertTrue(proc.stdout)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn(b"jsondiff report", proc.stdout)
        self.assertEqual(proc.stderr, b"")
