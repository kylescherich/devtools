"""testdelta CLI end to end: every exit code and its precedence,
--fail-on-slow, --output guards, byte-identical reruns in all formats,
inputs left unchanged, and python -I -m testdelta. Inputs are synthetic."""
import contextlib
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from testdelta.cli import EXIT_INPUT, EXIT_OK, EXIT_REGRESSIONS, EXIT_USAGE, main
from testdelta.report import FORMATS
from tests import secret_shapes as ss
from tests import testdelta_fixtures as fx

ROOT = pathlib.Path(__file__).resolve().parent.parent
GH = ss.GHP + "A1b2C3d4E5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class _Files(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self._out = tempfile.TemporaryDirectory()
        self.outdir = self._out.name

    def tearDown(self):
        self._tmp.cleanup()
        self._out.cleanup()

    def junit(self, name, statuses, times=None):
        return fx.write(self.dir, name, fx.junit_simple(statuses, times=times))

    def pair(self, before, after, times_before=None, times_after=None):
        return (self.junit("before.xml", before, times_before),
                self.junit("after.xml", after, times_after))

    def assert_code(self, argv, expected):
        code, out, err = run(argv)
        self.assertEqual(code, expected, err)
        return out, err


class TestExitCodes(_Files):
    def test_identical_inputs_exit_0(self):
        b, a = self.pair([("t1", "pass"), ("t2", "fail")], [("t1", "pass"), ("t2", "fail")])
        out, err = self.assert_code([b, a], EXIT_OK)
        self.assertTrue(out)
        self.assertIn("RESULT no regressions", out)
        self.assertEqual(err, "")

    def test_improvements_only_exit_0(self):
        b, a = self.pair([("t1", "fail"), ("t2", "skip")],
                         [("t1", "pass"), ("t2", "pass"), ("t3", "pass")])
        self.assert_code([b, a], EXIT_OK)

    def test_newly_failing_exit_1(self):
        for after in ("fail", "error", "xpass"):
            with self.subTest(after=after):
                b, a = self.pair([("t1", "pass")], [("t1", after)])
                out, _ = self.assert_code([b, a], EXIT_REGRESSIONS)
                self.assertIn("RESULT WORSE", out)

    def test_removed_only_exit_1(self):
        b, a = self.pair([("t1", "pass"), ("t2", "pass")], [("t1", "pass")])
        out, _ = self.assert_code([b, a], EXIT_REGRESSIONS)
        self.assertTrue(out)
        self.assertIn("REMOVED (1)", out)

    def test_newly_skipped_only_exit_1(self):
        b, a = self.pair([("t1", "pass")], [("t1", "skip")])
        self.assert_code([b, a], EXIT_REGRESSIONS)

    def test_still_failing_and_other_changes_exit_0(self):
        b, a = self.pair([("t1", "fail"), ("t2", "xpass")], [("t1", "error"), ("t2", "pass")])
        self.assert_code([b, a], EXIT_OK)

    def test_slower_exit_1_only_with_fail_on_slow(self):
        b, a = self.pair([("t1", "pass")], [("t1", "pass")],
                         times_before={"t1": "1.0"}, times_after={"t1": "3.0"})
        out, _ = self.assert_code([b, a], EXIT_OK)
        self.assertIn("SLOWER (1)", out)
        out, _ = self.assert_code([b, a, "--fail-on-slow"], EXIT_REGRESSIONS)
        self.assertIn("1 slower", out)

    def test_slow_rule_options(self):
        b, a = self.pair([("t1", "pass")], [("t1", "pass")],
                         times_before={"t1": "1.0"}, times_after={"t1": "1.4"})
        self.assert_code([b, a, "--fail-on-slow"], EXIT_OK)
        self.assert_code([b, a, "--fail-on-slow", "--slow-ratio", "1.3",
                          "--slow-min", "0.3"], EXIT_REGRESSIONS)

    def test_na_times_never_slow_through_cli(self):
        b, a = self.pair([("t1", "pass")], [("t1", "pass")], times_after={"t1": "99.0"})
        out, _ = self.assert_code([b, a, "--fail-on-slow"], EXIT_OK)
        self.assertIn("SLOWER (0)", out)

    def test_faster_never_exit_1(self):
        b, a = self.pair([("t1", "pass")], [("t1", "pass")],
                         times_before={"t1": "3.0"}, times_after={"t1": "1.0"})
        out, _ = self.assert_code([b, a, "--fail-on-slow"], EXIT_OK)
        self.assertIn("FASTER (1)", out)

    def test_unittest_logs_compared(self):
        b = fx.write(self.dir, "b.txt", fx.ut_simple([("test_a", "pass"), ("test_b", "pass")]))
        a = fx.write(self.dir, "a.txt", fx.ut_simple([("test_a", "fail"), ("test_b", "pass")]))
        out, _ = self.assert_code([b, a], EXIT_REGRESSIONS)
        self.assertIn("pkg.mod.Class.test_a", out)
        self.assertIn("input format = unittest", out)

    def test_usage_errors_exit_2(self):
        b, a = self.pair([("t", "pass")], [("t", "pass")])
        for argv in ([b], [b, a, "--frobnicate"], [b, a, "--force"],
                     [b, a, "--format", "xml"], [b, a, "--slow-ratio", "0"],
                     [b, a, "--slow-min", "-1"], [b, a, "--max-bytes", "0"],
                     [b, a, "--input-format", "tap"]):
            with self.subTest(argv=argv[2:]):
                out, err = self.assert_code(argv, EXIT_USAGE)
                self.assertEqual(out, "")
                self.assertTrue(err)

    def test_input_errors_exit_4(self):
        good = self.junit("good.xml", [("t", "pass")])
        log = fx.write(self.dir, "log.txt", fx.ut_simple([("t", "pass")]))
        empty = fx.write(self.dir, "empty.xml", b"")
        bomb = fx.write(self.dir, "bomb.xml", fx.billion_laughs())
        xxe = fx.write(self.dir, "xxe.xml", fx.external_entity())
        bad = fx.write(self.dir, "bad.xml", "<testsuite><testcase></testsuite>")
        link = os.path.join(self.dir, "link.xml")
        os.symlink(good, link)
        cases = {
            "missing": ([good, os.path.join(self.dir, "nope.xml")], "after file: file not found"),
            "empty": ([empty, good], "before file: empty file"),
            "mixed formats": ([good, log], "different formats"),
            "billion laughs": ([bomb, good], "DOCTYPE or ENTITY"),
            "external entity": ([good, xxe], "DOCTYPE or ENTITY"),
            "malformed": ([good, bad], "malformed XML"),
            "symlink": ([link, good], "symlink"),
            "directory": ([self.dir, good], "is a directory"),
            "oversized": ([good, good, "--max-bytes", "10"], "larger than --max-bytes"),
            "forced format": ([log, log, "--input-format", "junit"], "malformed XML"),
        }
        for label, (argv, fragment) in cases.items():
            with self.subTest(label=label):
                out, err = self.assert_code(argv, EXIT_INPUT)
                self.assertEqual(out, "")
                self.assertTrue(err)
                self.assertTrue(err.startswith("testdelta: "))
                self.assertIn(fragment, err)

    def test_input_error_messages_quote_no_content(self):
        hostile = fx.external_entity().replace('name="&xxe;"', f'name="{GH}"')
        path = fx.write(self.dir, "x.xml", hostile)
        good = self.junit("good.xml", [("t", "pass")])
        _, err = self.assert_code([good, path], EXIT_INPUT)
        self.assertTrue(err)
        self.assertNotIn(GH, err)
        self.assertNotIn("passwd", err)

    def test_precedence_usage_over_input_over_regressions(self):
        good = self.junit("good.xml", [("t", "pass")])
        failing = self.junit("failing.xml", [("t", "fail")])
        missing = os.path.join(self.dir, "nope.xml")
        existing = fx.write(self.outdir, "exists.txt", "x")
        self.assert_code([missing, good, "--output", existing], EXIT_USAGE)
        self.assert_code([good, missing, "--force"], EXIT_USAGE)
        self.assert_code([good, missing], EXIT_INPUT)
        self.assert_code([good, failing], EXIT_REGRESSIONS)

    def test_help_exit_0(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(out)
        for line in ("0     no regressions", "1     regressions", "2     usage error", "4     input"):
            self.assertIn(line, out)


class TestOutput(_Files):
    def test_output_written_equals_stdout(self):
        b, a = self.pair([("t", "pass")], [("t", "fail")])
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                target = os.path.join(self.outdir, f"report.{fmt}")
                code, out, err = run([b, a, "--format", fmt, "--output", target])
                self.assertEqual(code, EXIT_REGRESSIONS, err)
                self.assertEqual(out, "")
                data = pathlib.Path(target).read_text(encoding="utf-8")
                self.assertTrue(data)
                _, stdout, _ = run([b, a, "--format", fmt])
                self.assertEqual(data, stdout)

    def test_existing_output_refused_without_force(self):
        b, a = self.pair([("t", "pass")], [("t", "pass")])
        target = fx.write(self.outdir, "report.txt", "keep me")
        _, err = self.assert_code([b, a, "--output", target], EXIT_USAGE)
        self.assertIn("already exists (use --force", err)
        self.assertEqual(pathlib.Path(target).read_text(), "keep me")
        self.assert_code([b, a, "--output", target, "--force"], EXIT_OK)
        self.assertIn("testdelta report", pathlib.Path(target).read_text())

    def test_symlink_output_refused_even_with_force(self):
        b, a = self.pair([("t", "pass")], [("t", "pass")])
        victim = fx.write(self.outdir, "victim.txt", "untouched")
        link = os.path.join(self.outdir, "report.txt")
        os.symlink(victim, link)
        dangling = os.path.join(self.outdir, "dangling.txt")
        os.symlink(os.path.join(self.outdir, "nowhere.txt"), dangling)
        for argv in ([b, a, "--output", link], [b, a, "--output", link, "--force"],
                     [b, a, "--output", dangling, "--force"]):
            with self.subTest(argv=argv[2:]):
                _, err = self.assert_code(argv, EXIT_USAGE)
                self.assertIn("symlink", err)
        self.assertEqual(pathlib.Path(victim).read_text(), "untouched")
        self.assertFalse(os.path.exists(os.path.join(self.outdir, "nowhere.txt")))

    def test_output_that_is_an_input_refused_even_with_force(self):
        b, a = self.pair([("t", "pass")], [("t", "fail")])
        digest = sha256(a)
        for target, label in ((a, "after"), (b, "before")):
            with self.subTest(label=label):
                _, err = self.assert_code([b, a, "--output", target, "--force"], EXIT_USAGE)
                self.assertIn(f"is the {label} input", err)
        same = os.path.join(self.dir, ".", "after.xml")
        self.assert_code([b, a, "--output", same, "--force"], EXIT_USAGE)
        self.assertEqual(sha256(a), digest)

    def test_output_directory_or_missing_parent_refused(self):
        b, a = self.pair([("t", "pass")], [("t", "pass")])
        self.assert_code([b, a, "--output", self.outdir, "--force"], EXIT_USAGE)
        missing = os.path.join(self.outdir, "no", "such", "dir", "r.txt")
        _, err = self.assert_code([b, a, "--output", missing], EXIT_USAGE)
        self.assertIn("cannot write", err)


class TestReproducibleAndReadOnly(_Files):
    def setUp(self):
        super().setUp()
        statuses_b = [("t_pass", "pass"), ("t_fail", "fail"), ("t_gone", "pass"),
                      ("t_slow", "pass"), (fx.HTML_NAME, "pass"), (fx.BIDI_NAME, "skip")]
        statuses_a = [("t_pass", "error"), ("t_fail", "pass"), ("t_new", "pass"),
                      ("t_slow", "pass"), (fx.HTML_NAME, "fail"), (fx.BIDI_NAME, "pass")]
        self.b, self.a = self.pair(statuses_b, statuses_a,
                                   times_before={"t_slow": "1.0", "t_pass": "0.2"},
                                   times_after={"t_slow": "4.0", "t_pass": "0.3"})

    def test_two_runs_byte_identical_stdout(self):
        for fmt in FORMATS:
            for extra in ([], ["--details"], ["--fail-on-slow"]):
                with self.subTest(fmt=fmt, extra=extra):
                    first = run([self.b, self.a, "--format", fmt] + extra)
                    second = run([self.b, self.a, "--format", fmt] + extra)
                    self.assertTrue(first[1])
                    self.assertEqual(first, second)

    def test_two_runs_byte_identical_output_files(self):
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                paths = [os.path.join(self.outdir, f"{fmt}-{n}.out") for n in (1, 2)]
                for path in paths:
                    self.assert_code([self.b, self.a, "--format", fmt, "--output", path],
                                     EXIT_REGRESSIONS)
                first, second = (pathlib.Path(p).read_bytes() for p in paths)
                self.assertTrue(first)
                self.assertEqual(first, second)

    def test_json_output_parses(self):
        out, _ = self.assert_code([self.b, self.a, "--format", "json"], EXIT_REGRESSIONS)
        doc = json.loads(out)
        self.assertEqual(doc["schema"], "testdelta/1")
        self.assertEqual(doc["inputs"], {"before": "before.xml", "after": "after.xml"})
        self.assertEqual(doc["summary"]["regressions"], 3)

    def test_inputs_unchanged_and_nothing_created(self):
        digests = (sha256(self.b), sha256(self.a))
        listing = sorted(os.listdir(self.dir))
        for fmt in FORMATS:
            run([self.b, self.a, "--format", fmt, "--details"])
            run([self.b, self.a, "--format", fmt, "--output",
                 os.path.join(self.outdir, f"r.{fmt}")])
        self.assertEqual((sha256(self.b), sha256(self.a)), digests)
        self.assertEqual(sorted(os.listdir(self.dir)), listing)

    def test_report_names_are_basenames(self):
        out, _ = self.assert_code([self.b, self.a], EXIT_REGRESSIONS)
        self.assertTrue(out)
        self.assertIn("before = before.xml", out)
        self.assertNotIn(self.dir, out)


class TestPrivacyThroughCli(_Files):
    def test_details_flag_controls_failure_text(self):
        before = fx.junit_xml([fx.junit_case("t")])
        after = fx.junit_xml([fx.junit_case("t", kind="fail", message="CLIMSG " + GH,
                                            text="CLITB line", system_out="CLIOUT " + GH)])
        b = fx.write(self.dir, "b.xml", before)
        a = fx.write(self.dir, "a.xml", after)
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                out, _ = self.assert_code([b, a, "--format", fmt], EXIT_REGRESSIONS)
                self.assertTrue(out)
                for value in ("CLIMSG", "CLITB", "CLIOUT", GH):
                    self.assertNotIn(value, out)
                out, _ = self.assert_code([b, a, "--format", fmt, "--details"],
                                          EXIT_REGRESSIONS)
                self.assertTrue(out)
                self.assertIn("CLIMSG", out)
                self.assertIn("CLITB", out)
                self.assertNotIn("CLIOUT", out)
                self.assertNotIn(GH, out)


# python -I -m testdelta, with the repository on sys.path (-I drops the
# current directory, and the package need not be installed).
MODULE = [sys.executable, "-I", "-c",
          "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); "
          "runpy.run_module('testdelta', run_name='__main__', alter_sys=True)", str(ROOT)]


class TestModuleEntryPoint(_Files):
    def test_python_dash_m(self):
        b, a = self.pair([("t", "pass")], [("t", "fail")])
        proc = subprocess.run(MODULE + [b, a, "--format", "json"], capture_output=True,
                              text=True, timeout=60, cwd=self.outdir)
        self.assertEqual(proc.returncode, EXIT_REGRESSIONS, proc.stderr)
        self.assertTrue(proc.stdout)
        self.assertEqual(json.loads(proc.stdout)["summary"]["regressions"], 1)
        proc = subprocess.run(MODULE + [b, os.path.join(self.dir, "nope.xml")],
                              capture_output=True, text=True, timeout=60, cwd=self.outdir)
        self.assertEqual(proc.returncode, EXIT_INPUT)
        self.assertEqual(proc.stdout, "")
        self.assertIn("testdelta: after file: file not found", proc.stderr)


if __name__ == "__main__":
    unittest.main()
