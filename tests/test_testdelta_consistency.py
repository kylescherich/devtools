"""testdelta report-level consistency (BRIEF-DEVWS-026 task 2): a report
that says the run failed must name a failing test, else exit 4; other
declared-vs-parsed mismatches and truncated logs are loud warnings; a
failing added test is a regression (Hub DEC-233). Inputs are synthetic, written to temp dirs at test time."""
import contextlib
import io
import json
import tempfile
import unittest

from testdelta.cli import EXIT_INPUT, EXIT_OK, EXIT_REGRESSIONS, main
from testdelta.readers import REFUSED_FAILED, InputError, load
from tests import secret_shapes as ss
from tests import testdelta_fixtures as fx

W = "pkg.mod.Class"


NO_TIMES = ["before: no test times; slower/faster need both times",
            "after: no test times; slower/faster need both times"]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def hub_junit(errors="1", suite_error=True, failures="0", root_attrs=""):
    """The Hub's reproducer: a suite-level <error> and errors="1", while the
    one testcase passed."""
    loose = '<error message="setup failed">boom</error>' if suite_error else ""
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            f"<testsuites{root_attrs}>"
            f'<testsuite name="pytest" errors="{errors}" failures="{failures}" '
            'skipped="0" tests="1" time="0.100">'
            f'<testcase classname="a.B" name="t" time="0.010" />{loose}'
            "</testsuite></testsuites>\n")


class _Files(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, content):
        return fx.write(self.dir, name, content)

    def report(self, before, after, expected, fmt="text"):
        code, out, err = run([before, after, "--format", fmt])
        self.assertEqual(code, expected, err)
        self.assertTrue(out)
        return out

    def refused(self, before, after, side):
        code, out, err = run([before, after])
        self.assertEqual(code, EXIT_INPUT, out)
        self.assertTrue(err)
        self.assertEqual(out, "")
        self.assertEqual(err, f"testdelta: {side} file: {REFUSED_FAILED}\n")
        self.assertNotIn("no regressions", out + err)


class TestRefusal(_Files):
    def test_hub_junit_reproducer_exit_4_either_side(self):
        clean = self.write("clean.xml", fx.junit_simple([("t", "pass")], classname="a.B"))
        failed = self.write("failed.xml", hub_junit())
        self.refused(clean, failed, "after")
        self.refused(failed, clean, "before")

    def test_each_junit_signal_alone_refused(self):
        clean = self.write("clean.xml", fx.junit_simple([("t", "pass")], classname="a.B"))
        variants = {
            "errors attribute": hub_junit(errors="1", suite_error=False),
            "failures attribute": hub_junit(errors="0", failures="2", suite_error=False),
            "suite-level element": hub_junit(errors="0", suite_error=True),
            "testsuites attribute": hub_junit(errors="0", suite_error=False,
                                              root_attrs=' tests="1" failures="1"'),
        }
        for label, xml in variants.items():
            with self.subTest(label=label):
                self.refused(clean, self.write("v.xml", xml), "after")

    def test_suite_level_element_next_to_a_real_failure_warns(self):
        xml = hub_junit(errors="1", failures="1").replace(
            'name="t" time="0.010" />', 'name="t" time="0.010"><failure message="m">x'
            "</failure></testcase>")
        run_ = load(self.write("f.xml", xml))
        self.assertEqual(run_.results["a.B::t"].status, "fail")
        self.assertEqual(run_.checks, (
            "report declares tests=1 failures=1 errors=1; parsed tests=1 failures=1 "
            "errors=0",
            "report has 1 suite-level error/failure element not tied to any test"))
        clean = self.write("clean.xml", fx.junit_simple([("t", "pass")], classname="a.B"))
        out = self.report(clean, self.write("f2.xml", xml), EXIT_REGRESSIONS)
        self.assertIn("!!! after: report has 1 suite-level error/failure element", out)

    def test_unittest_failed_footer_without_failing_test_refused(self):
        clean = self.write("b.txt", fx.ut_simple([("test_a", "pass")]))
        # The setUpModule line was glued to printed output and its block is
        # gone: nothing failing can be identified, but the footer says so.
        lines = ["setUpModule (test_mod) ... noiseERROR"] + fx.ut_line(W, "test_a", "pass")
        after = self.write("a.txt", fx.unittest_log(lines, count=1,
                                                    footer="FAILED (errors=1)"))
        self.refused(clean, after, "after")
        self.refused(after, clean, "before")
        with self.assertRaises(InputError):
            load(after)

    def test_failed_footer_with_unexpected_success_is_fine(self):
        lines = fx.ut_line(W, "test_x", "xpass")
        log = fx.unittest_log(lines, [fx.ut_block("xpass", W, "test_x")])
        self.assertIn("FAILED (unexpected successes=1)", log)
        run_ = load(self.write("a.txt", log))
        self.assertEqual(run_.results[f"{W}.test_x"].status, "xpass")
        self.assertEqual(run_.checks, ())


class TestUnittestFooter(_Files):
    def test_setupmodule_error_real_shape(self):
        # As 3.11 and 3.12 print it: the fixture is not counted in "Ran N".
        before = self.write("b.txt", fx.unittest_log(
            fx.ut_line("test_mod.C", "test_a", "pass")))
        lines = ["setUpModule (test_mod) ... ERROR"]
        blocks = [fx.ut_block("error", "test_mod", "setUpModule", form="short")]
        log = fx.unittest_log(lines, blocks, count=0)
        self.assertIn("Ran 0 tests", log)
        self.assertIn("FAILED (errors=1)", log)
        after = self.write("a.txt", log)
        run_ = load(after)
        self.assertEqual(run_.results["test_mod.setUpModule"].status, "error")
        self.assertTrue(run_.failed_signal)
        self.assertTrue(run_.summary)
        self.assertEqual(run_.checks, ())
        out = self.report(before, after, EXIT_REGRESSIONS)
        self.assertIn("REMOVED (1)", out)
        self.assertNotIn("report declares", out)
        self.assertNotIn("no summary line", out)

    def test_truncated_log_warns_after_unresolved(self):
        before = self.write("b.txt", fx.ut_simple([("test_a", "pass"), ("test_b", "pass")]))
        lines = fx.ut_line(W, "test_a", "pass") + [f"test_b ({W}.test_b) ... "]
        after = self.write("a.txt", fx.unittest_log(lines, footer=False))
        self.assertFalse(load(after).summary)
        out = self.report(before, after, EXIT_REGRESSIONS)
        unresolved = out.index("after: 1 test started without a status")
        truncated = out.index("!!! after: no summary line: the log may be truncated")
        self.assertLess(unresolved, truncated)

    def test_truncated_log_alone_is_a_loud_warning(self):
        before = self.write("b.txt", fx.ut_simple([("test_a", "pass")]))
        after = self.write("a.txt", fx.unittest_log(fx.ut_line(W, "test_a", "pass"),
                                                    footer=False))
        out = self.report(before, after, EXIT_OK)
        self.assertIn("  !!! after: no summary line: the log may be truncated\n", out)
        self.assertNotIn("before: no summary line", out)

    def test_ran_line_without_final_line_is_truncated(self):
        log = fx.unittest_log(fx.ut_line(W, "test_a", "pass"))
        cut = log[:log.index("Ran 1 test")] + "Ran 1 test in 0.123s\n"
        self.assertFalse(load(self.write("a.txt", cut)).summary)

    def test_subtest_failures_counted_separately_warn_not_refuse(self):
        before = self.write("b.txt", fx.ut_simple([("test_sub", "pass")]))
        lines = [f"test_sub ({W}.test_sub) ... ",
                 f"  test_sub ({W}.test_sub) (i=1) ... FAIL",
                 f"  test_sub ({W}.test_sub) (i=2) ... FAIL"]
        blocks = [fx.ut_block("fail", W, "test_sub", params="i=1"),
                  fx.ut_block("fail", W, "test_sub", params="i=2")]
        log = fx.unittest_log(lines, blocks, count=1)
        self.assertIn("FAILED (failures=2)", log)
        after = self.write("a.txt", log)
        out = self.report(before, after, EXIT_REGRESSIONS)
        self.assertIn("!!! after: report declares tests=1 failures=2; parsed tests=1 "
                      "failures=1", out)
        self.assertIn("NEWLY FAILING (1)", out)

    def test_real_unittest_failing_run_is_consistent(self):
        log = fx.real_unittest_log(self.dir, True)
        self.assertTrue(log)
        run_ = load(self.write("a.txt", log))
        self.assertTrue(run_.summary)
        self.assertTrue(run_.failed_signal)
        self.assertEqual(run_.checks, ())


class TestCleanAndMismatch(_Files):
    def test_clean_runs_have_no_warnings(self):
        statuses = [("test_a", "pass"), ("test_b", "fail"), ("test_c", "error"),
                    ("test_d", "skip"), ("test_e", "xfail"), ("test_f", "xpass")]
        times = {name: "0.100" for name, _ in statuses}
        pairs = {"junit": (fx.junit_simple(statuses, times=times), "x.xml", []),
                 "unittest": (fx.ut_simple(statuses), "x.txt", NO_TIMES)}
        for label, (content, name, expected) in pairs.items():
            with self.subTest(fmt=label):
                path = self.write(name, content)
                run_ = load(path)
                self.assertEqual(run_.checks, ())
                self.assertTrue(run_.summary)
                out = self.report(path, path, EXIT_OK)
                self.assertNotIn("!!!", out)
                if not expected:
                    self.assertIn("WARNINGS none", out)
                doc = json.loads(self.report(path, path, EXIT_OK, "json"))
                self.assertEqual(doc["warnings"], expected)
                self.assertEqual(doc["schema"], "testdelta/1")

    def test_junit_skip_mismatch_warns_exit_unchanged(self):
        before = self.write("b.xml", fx.junit_simple([("t", "pass"), ("u", "skip")]))
        xml = fx.junit_simple([("t", "pass"), ("u", "skip")]).replace(
            'skipped="1"', 'skipped="2"')
        after = self.write("a.xml", xml)
        out = self.report(before, after, EXIT_OK)
        self.assertIn("!!! after: report declares tests=2 skipped=2; parsed tests=2 "
                      "skipped=1", out)

    def test_loud_in_md_and_html(self):
        before = self.write("b.txt", fx.ut_simple([("test_a", "pass")]))
        after = self.write("a.txt", fx.unittest_log(fx.ut_line(W, "test_a", "pass"),
                                                    footer=False))
        md = self.report(before, after, EXIT_OK, "md")
        self.assertIn("- **WARNING: after: no summary line: the log may be truncated**", md)
        html = self.report(before, after, EXIT_OK, "html")
        self.assertIn('<li class="loud">WARNING: after: no summary line', html)


class TestAddedFailing(_Files):
    """Hub DEC-233: a test absent before and fail/error/xpass after is a
    regression ("added failing", exit 1), listed right after newly failing;
    added tests that pass stay "added" improvements. The former warning
    "N added tests fail" is gone (the category replaces it)."""

    def check_one(self, before, after, test_id, status):
        out = self.report(before, after, EXIT_REGRESSIONS)
        self.assertIn("RESULT WORSE: 1 regression (0 newly failing, 1 added failing, "
                      "0 removed, 0 newly skipped); 0 improvements.", out)
        self.assertIn(f"ADDED FAILING (1)\n      - -> {status:<5}", out)
        self.assertIn("ADDED (0)", out)
        self.assertLess(out.index("NEWLY FAILING ("), out.index("ADDED FAILING ("))
        self.assertLess(out.index("ADDED FAILING ("), out.index("REMOVED ("))
        self.assertNotIn("added test", out)
        doc = json.loads(self.report(before, after, EXIT_REGRESSIONS, "json"))
        self.assertEqual(doc["added_failing"], [{
            "id": test_id, "before": None, "after": status,
            "before_time": None, "after_time": None}])
        self.assertEqual(doc["added"], [])
        self.assertEqual(doc["summary"], {"regressions": 1, "improvements": 0})
        self.assertEqual(doc["warnings"], NO_TIMES)
        self.assertEqual(doc["schema"], "testdelta/1")
        keys = list(doc)
        self.assertEqual(keys[keys.index("newly_failing") + 1], "added_failing")

    def test_junit_added_fail_error_xpass(self):
        for status in ("fail", "error", "xpass"):
            with self.subTest(status=status):
                before = self.write("b.xml", fx.junit_simple([("t1", "pass")]))
                after = self.write("a.xml", fx.junit_simple([("t1", "pass"), ("t2", status)]))
                self.check_one(before, after, "pkg.mod.Class::t2", status)

    def test_unittest_added_fail_error_xpass(self):
        for status in ("fail", "error", "xpass"):
            with self.subTest(status=status):
                before = self.write("b.txt", fx.ut_simple([("test_a", "pass")]))
                after = self.write("a.txt", fx.ut_simple([("test_a", "pass"),
                                                          ("test_b", status)]))
                self.check_one(before, after, "pkg.mod.Class.test_b", status)

    def test_added_passing_and_failing_split(self):
        before = self.write("b.xml", fx.junit_simple([("t1", "pass")]))
        after = self.write("a.xml", fx.junit_simple(
            [("t1", "pass"), ("t2", "fail"), ("t3", "error"), ("t4", "pass")]))
        out = self.report(before, after, EXIT_REGRESSIONS)
        self.assertIn("ADDED FAILING (2)", out)
        self.assertIn("ADDED (1)", out)
        self.assertIn("RESULT WORSE: 2 regressions", out)
        doc = json.loads(self.report(before, after, EXIT_REGRESSIONS, "json"))
        self.assertEqual([i["id"] for i in doc["added_failing"]],
                         ["pkg.mod.Class::t2", "pkg.mod.Class::t3"])
        self.assertEqual([i["id"] for i in doc["added"]], ["pkg.mod.Class::t4"])
        self.assertEqual(doc["summary"], {"regressions": 2, "improvements": 1})
        self.assertEqual(doc["warnings"], NO_TIMES)

    def test_added_passing_tests_stay_improvements_exit_0(self):
        before = self.write("b.xml", fx.junit_simple([("t1", "pass")]))
        after = self.write("a.xml", fx.junit_simple(
            [("t1", "pass"), ("t2", "pass"), ("t3", "skip")]))
        out = self.report(before, after, EXIT_OK)
        self.assertIn("RESULT no regressions; 2 improvements.", out)
        self.assertIn("ADDED FAILING (0)", out)
        self.assertIn("ADDED (2)", out)
        self.assertNotIn("added test", out)
        self.assertNotIn("!!!", out)
        doc = json.loads(self.report(before, after, EXIT_OK, "json"))
        self.assertEqual(doc["added_failing"], [])
        self.assertEqual(doc["warnings"], NO_TIMES)

    def test_details_for_added_failing(self):
        token = ss.GHP + "A1b2C3d4E5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        before = self.write("b.xml", fx.junit_simple([("t1", "pass")]))
        after = self.write("a.xml", fx.junit_xml([
            fx.junit_case("t1"),
            fx.junit_case("t2", kind="fail", message="ADDMSG " + token + "\nADDSECOND",
                          text="ADDTB line", system_out="ADDOUT " + token)]))
        code, out, err = run([before, after])
        self.assertEqual(code, EXIT_REGRESSIONS, err)
        self.assertTrue(out)
        for value in ("ADDMSG", "ADDTB", "ADDOUT", token):
            self.assertNotIn(value, out)
        code, out, err = run([before, after, "--details"])
        self.assertEqual(code, EXIT_REGRESSIONS, err)
        self.assertTrue(out)
        self.assertIn("      message  ADDMSG <redacted>\n", out)
        self.assertIn("      | ADDTB line\n", out)
        for value in ("ADDSECOND", "ADDOUT", token):
            self.assertNotIn(value, out)
        code, out, err = run([before, after, "--details", "--format", "json"])
        self.assertTrue(out)
        item = json.loads(out)["added_failing"][0]
        self.assertEqual(item["message"], "ADDMSG <redacted>")
        self.assertEqual(item["traceback"], ["ADDTB line"])
        self.assertEqual(item["traceback_omitted"], 0)


if __name__ == "__main__":
    unittest.main()
