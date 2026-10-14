"""testdelta readers: JUnit XML and unittest -v text, refusals and hostile
inputs. Every input is synthetic, written to a temp dir at test time."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from testdelta import readers
from testdelta.readers import InputError, load, load_pair, parse_junit, parse_unittest
from tests import testdelta_fixtures as fx

W = "pkg.mod.Class"


class _TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, content):
        return fx.write(self.dir, name, content)

    def refused(self, path, fragment, **kwargs):
        with self.assertRaises(InputError) as ctx:
            load(path, **kwargs)
        message = str(ctx.exception)
        self.assertTrue(message)
        self.assertIn(fragment, message)
        return message


class TestJUnit(unittest.TestCase):
    def test_pytest_style_statuses_times_ids(self):
        xml = fx.junit_xml([
            fx.junit_case("test_ok", time="0.250"),
            fx.junit_case("test_fail", kind="fail", message="assert 1 == 2\nmore",
                          text="def test_fail():\n>   assert 1 == 2\nE   assert 1 == 2"),
            fx.junit_case("test_err", kind="error", message="RuntimeError: boom",
                          text="Traceback\nRuntimeError: boom"),
            fx.junit_case("test_skip", kind="skip"),
            fx.junit_case("test_xf", kind="xfail"),
            fx.junit_case("test_xp", kind="fail", message="[XPASS(strict)] reason"),
            fx.junit_case("test_out", system_out="captured out", system_err="captured err"),
        ])
        run = parse_junit(xml)
        self.assertEqual(run.format, "junit")
        got = {k: v.status for k, v in run.results.items()}
        self.assertEqual(got, {
            f"{W}::test_ok": "pass", f"{W}::test_fail": "fail", f"{W}::test_err": "error",
            f"{W}::test_skip": "skip", f"{W}::test_xf": "xfail", f"{W}::test_xp": "xpass",
            f"{W}::test_out": "pass"})
        self.assertEqual(run.results[f"{W}::test_ok"].time, 0.25)
        fail = run.results[f"{W}::test_fail"]
        self.assertEqual(fail.message, "assert 1 == 2")
        self.assertEqual(fail.traceback[-1], "E   assert 1 == 2")
        self.assertEqual(len(fail.traceback), 3)
        self.assertEqual(run.duplicates, ())

    def test_system_out_and_skip_reasons_never_stored(self):
        xml = fx.junit_xml([fx.junit_case("t", kind="skip", message="why-skip",
                                          system_out="out-text", system_err="err-text")])
        result = parse_junit(xml).results[f"{W}::t"]
        self.assertEqual(result.status, "skip")
        self.assertIsNone(result.message)
        self.assertEqual(result.traceback, ())
        self.assertNotIn("out-text", repr(result))
        self.assertNotIn("why-skip", repr(result))

    def test_minimal_report_ids(self):
        xml = ('<testsuite>\n<testcase name="a"/>\n'
               '<testcase classname="" file="tests/test_x.py" name="b"/>\n'
               '<testcase classname="" name="c"/>\n</testsuite>\n')
        run = parse_junit(xml)
        self.assertEqual(sorted(run.results), ["a", "c", "tests/test_x.py::b"])
        self.assertTrue(all(r.status == "pass" and r.time is None
                            for r in run.results.values()))

    def test_nested_suites(self):
        xml = ("<testsuites><testsuite name='outer'><testsuite name='inner'>"
               "<testcase classname='a.B' name='t'/></testsuite></testsuite></testsuites>")
        self.assertEqual(list(parse_junit(xml).results), ["a.B::t"])

    def test_message_from_text_when_no_attribute(self):
        xml = fx.junit_xml([fx.junit_case("t", kind="fail", text="\nfirst line\nsecond")])
        self.assertEqual(parse_junit(xml).results[f"{W}::t"].message, "first line")

    def test_bad_times_are_na_never_zero(self):
        cases = [fx.junit_case(f"t{i}", time=value)
                 for i, value in enumerate(["nan", "-1", "abc", "inf", None, "0", "1.5"])]
        times = {k.split("::")[1]: v.time for k, v in parse_junit(fx.junit_xml(cases)).results.items()}
        self.assertEqual(times, {"t0": None, "t1": None, "t2": None, "t3": None,
                                 "t4": None, "t5": 0.0, "t6": 1.5})

    def test_failure_and_error_children_keep_worst(self):
        case = ('<testcase classname="a.B" name="t"><failure message="f">x</failure>'
                '<error message="teardown">y</error></testcase>')
        result = parse_junit(fx.junit_xml([case])).results["a.B::t"]
        self.assertEqual(result.status, "error")
        self.assertEqual(result.message, "teardown")

    def test_duplicate_ids_keep_worst_status(self):
        xml = fx.junit_xml([fx.junit_case("t", time="1.0"),
                            fx.junit_case("t", kind="fail", message="m", time="2.0"),
                            fx.junit_case("t", kind="skip", time="3.0"),
                            fx.junit_case("u")])
        run = parse_junit(xml)
        self.assertEqual(run.duplicates, (f"{W}::t",))
        self.assertEqual(run.results[f"{W}::t"].status, "fail")
        self.assertEqual(run.results[f"{W}::t"].time, 2.0)
        self.assertEqual(len(run.results), 2)

    def test_empty_suite_is_a_valid_run(self):
        self.assertEqual(parse_junit("<testsuites></testsuites>").results, {})

    def test_wrong_root_refused(self):
        with self.assertRaises(InputError) as ctx:
            parse_junit("<html><testcase name='t'/></html>")
        self.assertIn("not a JUnit report", str(ctx.exception))
        self.assertNotIn("html", str(ctx.exception))


class TestUnittest(unittest.TestCase):
    def test_long_form_all_statuses(self):
        names = [("test_ok", "pass"), ("test_fail", "fail"), ("test_err", "error"),
                 ("test_skip", "skip"), ("test_xf", "xfail"), ("test_xp", "xpass")]
        run = parse_unittest(fx.ut_simple(names))
        self.assertEqual(run.format, "unittest")
        self.assertEqual({k: v.status for k, v in run.results.items()},
                         {f"{W}.{n}": s for n, s in names})
        self.assertTrue(all(r.time is None for r in run.results.values()))
        self.assertFalse(run.has_durations)
        fail = run.results[f"{W}.test_fail"]
        self.assertEqual(fail.message, "AssertionError: synthetic failure")
        self.assertEqual(fail.traceback[0], "Traceback (most recent call last):")
        self.assertIsNone(run.results[f"{W}.test_skip"].message)

    def test_short_form_older_python(self):
        names = [("test_ok", "pass"), ("test_fail", "fail")]
        run = parse_unittest(fx.ut_simple(names, form="short"))
        self.assertEqual({k: v.status for k, v in run.results.items()},
                         {f"{W}.test_ok": "pass", f"{W}.test_fail": "fail"})
        self.assertIsNotNone(run.results[f"{W}.test_fail"].message)

    def test_docstring_variant(self):
        lines = (fx.ut_line(W, "test_doc", "pass", doc="Check the (thing) works.")
                 + fx.ut_line(W, "test_doc2", "fail", doc="Second ... described test.")
                 + fx.ut_line(W, "test_plain", "skip"))
        blocks = [fx.ut_block("fail", W, "test_doc2")]
        run = parse_unittest(fx.unittest_log(lines, blocks, count=3))
        self.assertEqual({k: v.status for k, v in run.results.items()},
                         {f"{W}.test_doc": "pass", f"{W}.test_doc2": "fail",
                          f"{W}.test_plain": "skip"})

    def test_output_between_head_and_status(self):
        lines = [f"test_noisy ({W}.test_noisy) ... printed by the test", "ok",
                 f"test_next ({W}.test_next) ... ok"]
        run = parse_unittest(fx.unittest_log(lines, count=2))
        self.assertEqual(run.results[f"{W}.test_noisy"].status, "pass")
        self.assertEqual(run.results[f"{W}.test_next"].status, "pass")

    def test_subtests_fold_into_parent(self):
        lines = [f"test_sub ({W}.test_sub) ... ",
                 f"  test_sub ({W}.test_sub) (i=1) ... FAIL",
                 f"  test_sub ({W}.test_sub) (i=2) ... FAIL",
                 f"test_after ({W}.test_after) ... ok"]
        blocks = [fx.ut_block("fail", W, "test_sub", message="AssertionError: 1 != 0",
                              params="i=1"),
                  fx.ut_block("fail", W, "test_sub", message="AssertionError: 2 != 0",
                              params="i=2")]
        run = parse_unittest(fx.unittest_log(lines, blocks, count=2))
        self.assertEqual(sorted(run.results), [f"{W}.test_after", f"{W}.test_sub"])
        sub = run.results[f"{W}.test_sub"]
        self.assertEqual(sub.status, "fail")
        self.assertEqual(sub.message, "AssertionError: 1 != 0")
        self.assertEqual(run.duplicates, ())

    def test_subtest_failure_only_in_blocks(self):
        lines = [f"test_sub ({W}.test_sub) ... "]
        blocks = [fx.ut_block("error", W, "test_sub", message="KeyError: 'k'", params="k=1")]
        run = parse_unittest(fx.unittest_log(lines, blocks, count=1))
        self.assertEqual(run.results[f"{W}.test_sub"].status, "error")
        self.assertEqual(run.unresolved, ())

    def test_class_and_module_fixtures(self):
        lines = ["setUpClass (pkg.mod.D) ... ERROR", "setUpModule (pkg.other) ... ERROR"]
        blocks = [fx.ut_block("error", "pkg.mod.D", "setUpClass", form="short"),
                  fx.ut_block("error", "pkg.other", "setUpModule", form="short")]
        run = parse_unittest(fx.unittest_log(lines, blocks, count=0))
        self.assertEqual({k: v.status for k, v in run.results.items()},
                         {"pkg.mod.D.setUpClass": "error", "pkg.other.setUpModule": "error"})

    def test_durations_block_times_only_for_listed(self):
        names = [("test_a", "pass"), ("test_b", "fail"), ("test_c", "pass")]
        lines, blocks = [], [fx.ut_block("fail", W, "test_b")]
        for name, status in names:
            lines += fx.ut_line(W, name, status)
        log = fx.unittest_log(lines, blocks, durations=[(W, "test_a", 1.25), (W, "test_b", 0.0)])
        run = parse_unittest(log)
        self.assertTrue(run.has_durations)
        self.assertEqual(run.results[f"{W}.test_a"].time, 1.25)
        self.assertEqual(run.results[f"{W}.test_b"].time, 0.0)
        self.assertIsNone(run.results[f"{W}.test_c"].time)
        self.assertEqual(run.results[f"{W}.test_b"].status, "fail")

    def test_durations_hidden_note_skipped(self):
        log = fx.unittest_log(fx.ut_line(W, "test_a", "pass") + fx.ut_line(W, "test_b", "pass"))
        log = log.replace(fx.SEP_DASH + "\nRan", "\n".join([
            "Slowest test durations", fx.SEP_DASH, f"0.700s     test_b ({W}.test_b)",
            "", "(durations < 0.001s were hidden; use -v to show these)", "",
            fx.SEP_DASH, "Ran"]))
        run = parse_unittest(log)
        self.assertEqual(run.results[f"{W}.test_b"].time, 0.7)
        self.assertIsNone(run.results[f"{W}.test_a"].time)

    def test_captured_output_never_stored(self):
        lines = fx.ut_line(W, "test_b", "fail")
        blocks = [fx.ut_block("fail", W, "test_b", stdout="buffered-stdout-text",
                              stderr="buffered-stderr-text")]
        result = parse_unittest(fx.unittest_log(lines, blocks)).results[f"{W}.test_b"]
        self.assertEqual(result.message, "AssertionError: synthetic failure")
        self.assertTrue(result.traceback)
        self.assertNotIn("buffered", repr(result))
        self.assertNotIn("Stdout:", result.traceback)

    def test_chained_traceback_message_is_last_exception(self):
        message = "\n".join(["KeyError: 'a'", "",
                             "During handling of the above exception, another exception occurred:",
                             "", "Traceback (most recent call last):",
                             '  File "/synthetic/x.py", line 9, in test_b', "    raise",
                             "ValueError: second"])
        lines = fx.ut_line(W, "test_b", "error")
        blocks = [fx.ut_block("error", W, "test_b", message=message)]
        result = parse_unittest(fx.unittest_log(lines, blocks)).results[f"{W}.test_b"]
        self.assertEqual(result.message, "ValueError: second")

    def test_unexpected_success_block_does_not_eat_footer(self):
        lines = fx.ut_line(W, "test_xp", "xpass") + fx.ut_line(W, "test_f", "fail")
        blocks = [fx.ut_block("fail", W, "test_f"), fx.ut_block("xpass", W, "test_xp")]
        run = parse_unittest(fx.unittest_log(lines, blocks))
        self.assertEqual(run.results[f"{W}.test_xp"].status, "xpass")
        self.assertIsNone(run.results[f"{W}.test_xp"].message)

    def test_duplicate_ids_keep_worst(self):
        lines = fx.ut_line(W, "test_a", "pass") + fx.ut_line(W, "test_a", "error")
        run = parse_unittest(fx.unittest_log(lines, [fx.ut_block("error", W, "test_a")]))
        self.assertEqual(run.duplicates, (f"{W}.test_a",))
        self.assertEqual(run.results[f"{W}.test_a"].status, "error")

    def test_crlf_log(self):
        lines = fx.ut_line(W, "test_a", "pass") + fx.ut_line(W, "test_b", "fail")
        log = fx.unittest_log(lines, [fx.ut_block("fail", W, "test_b")], newline="\r\n")
        run = parse_unittest(log)
        self.assertEqual({k: v.status for k, v in run.results.items()},
                         {f"{W}.test_a": "pass", f"{W}.test_b": "fail"})
        self.assertFalse(run.results[f"{W}.test_b"].message.endswith("\r"))

    def test_started_without_status_is_unresolved(self):
        lines = [f"test_cut ({W}.test_cut) ... "] + fx.ut_line(W, "test_a", "pass")
        run = parse_unittest(fx.unittest_log(lines))
        self.assertEqual(run.unresolved, (f"{W}.test_cut",))
        self.assertNotIn(f"{W}.test_cut", run.results)

    def test_zero_tests_run_is_valid(self):
        run = parse_unittest("\n" + fx.SEP_DASH + "\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n")
        self.assertEqual(run.results, {})

    def test_stray_text_is_not_a_result(self):
        with self.assertRaises(InputError):
            parse_unittest("hello (world) ... ok\nok\nsome text\n")


class TestRealUnittestOutput(unittest.TestCase):
    """Guards the parser against format drift: a synthetic suite is run by
    this interpreter's unittest and its -v output parsed (testdelta itself
    never runs tests)."""

    SUITE = "\n".join([
        "import unittest",
        "class C(unittest.TestCase):",
        "    def test_ok(self): pass",
        "    def test_doc(self):",
        '        """Described test."""',
        "    def test_fail(self): self.fail('boom')",
        "    def test_err(self): raise ValueError('x')",
        "    @unittest.skip('why')",
        "    def test_skip(self): pass",
        "    @unittest.expectedFailure",
        "    def test_xf(self): self.fail()",
        "    @unittest.expectedFailure",
        "    def test_xp(self): pass",
        "    def test_sub(self):",
        "        for i in range(3):",
        "            with self.subTest(i=i):",
        "                self.assertEqual(i, 0)",
        ""])

    def test_parses_this_interpreters_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx.write(tmp, "test_synthetic.py", self.SUITE)
            argv = [sys.executable, "-I", "-m", "unittest", "discover", "-v",
                    "-s", tmp, "-t", tmp]
            if sys.version_info >= (3, 12):
                argv += ["--durations", "0"]
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                                  cwd=tmp)
        self.assertTrue(proc.stderr)
        run = parse_unittest(proc.stderr)
        w = "test_synthetic.C"
        self.assertEqual({k: v.status for k, v in run.results.items()}, {
            f"{w}.test_ok": "pass", f"{w}.test_doc": "pass", f"{w}.test_fail": "fail",
            f"{w}.test_err": "error", f"{w}.test_skip": "skip", f"{w}.test_xf": "xfail",
            f"{w}.test_xp": "xpass", f"{w}.test_sub": "fail"})
        self.assertEqual(run.duplicates, ())
        self.assertEqual(run.unresolved, ())
        self.assertEqual(run.results[f"{w}.test_fail"].message, "AssertionError: boom")
        self.assertEqual(run.results[f"{w}.test_err"].message, "ValueError: x")
        self.assertEqual(run.has_durations, sys.version_info >= (3, 12))
        if run.has_durations:
            self.assertIsNotNone(run.results[f"{w}.test_ok"].time)
            self.assertIsNone(run.results[f"{w}.test_skip"].time)


class TestLoad(_TempDir):
    def test_detects_both_formats(self):
        xml = self.write("a.xml", fx.junit_simple([("t", "pass")]))
        log = self.write("a.txt", fx.ut_simple([("t", "pass")]))
        self.assertEqual(load(xml).format, "junit")
        self.assertEqual(load(log).format, "unittest")

    def test_bom_and_leading_whitespace(self):
        path = self.write("a.xml", b"\xef\xbb\xbf\n  " + fx.junit_simple([("t", "pass")]).encode())
        self.assertEqual(load(path).format, "junit")

    def test_empty_and_blank_refused(self):
        self.refused(self.write("e.txt", b""), "empty file")
        self.refused(self.write("b.txt", b" \n\t\n"), "empty file")
        self.refused(self.write("bom.txt", b"\xef\xbb\xbf"), "empty file")

    def test_unrecognised_text_refused(self):
        self.refused(self.write("r.txt", "just some notes\nnothing here\n"),
                     "not JUnit XML or unittest -v output")

    def test_not_utf8_refused(self):
        self.refused(self.write("l.txt", b"test_a (m.C.test_a) ... ok\n\xff\xfe\n"),
                     "not UTF-8")

    def test_input_format_override(self):
        xml = self.write("a.xml", fx.junit_simple([("t", "pass")]))
        log = self.write("a.txt", fx.ut_simple([("t", "pass")]))
        self.refused(xml, "not JUnit XML or unittest", input_format="unittest")
        self.refused(log, "malformed XML", input_format="junit")
        self.assertEqual(load(log, input_format="unittest").format, "unittest")

    def test_missing_directory_symlink_refused(self):
        self.refused(os.path.join(self.dir, "nope.xml"), "file not found")
        self.refused(self.dir, "is a directory")
        target = self.write("real.xml", fx.junit_simple([("t", "pass")]))
        link = os.path.join(self.dir, "link.xml")
        os.symlink(target, link)
        self.refused(link, "symlink")

    def test_oversized_refused_before_open(self):
        path = self.write("big.xml", fx.junit_simple([("t", "pass")]))
        size = os.path.getsize(path)
        with mock.patch.object(readers.os, "open", side_effect=AssertionError("opened")):
            self.refused(path, "larger than --max-bytes", max_bytes=size - 1)
        self.assertEqual(load(path, max_bytes=size).format, "junit")

    def test_default_cap_is_50_mb(self):
        self.assertEqual(readers.DEFAULT_MAX_BYTES, 50 * 1024 * 1024)

    def test_mixed_formats_refused(self):
        xml = self.write("a.xml", fx.junit_simple([("t", "pass")]))
        log = self.write("b.txt", fx.ut_simple([("t", "pass")]))
        with self.assertRaises(InputError) as ctx:
            load_pair(xml, log)
        self.assertIn("different formats", str(ctx.exception))
        with self.assertRaises(InputError) as ctx:
            load_pair(log, xml)
        self.assertIn("before: unittest, after: junit", str(ctx.exception))

    def test_pair_names_the_side(self):
        good = self.write("a.xml", fx.junit_simple([("t", "pass")]))
        bad = self.write("b.xml", b"")
        with self.assertRaises(InputError) as ctx:
            load_pair(good, bad)
        self.assertEqual(str(ctx.exception), "after file: empty file")
        before, after = load_pair(good, good)
        self.assertEqual(before.results, after.results)


class TestHostileXML(_TempDir):
    def assert_refused_unparsed(self, content, fragment):
        path = self.write("hostile.xml", content)
        with mock.patch.object(readers.ET, "fromstring",
                               side_effect=AssertionError("parsed")) as parser:
            message = self.refused(path, fragment)
        parser.assert_not_called()
        return message

    def test_billion_laughs_refused_without_parsing(self):
        bomb = fx.billion_laughs()
        self.assertIn("<!DOCTYPE", bomb)
        self.assert_refused_unparsed(bomb, "DOCTYPE or ENTITY")

    def test_external_entity_refused_without_parsing(self):
        message = self.assert_refused_unparsed(fx.external_entity(), "DOCTYPE or ENTITY")
        self.assertNotIn("passwd", message)

    def test_case_and_spacing_variants_refused(self):
        for content in (fx.billion_laughs(doctype="doctype"),
                        fx.billion_laughs(doctype="DocType"),
                        "<testsuite><!-- <! ENTITY x 'y'> --></testsuite>",
                        '<testsuite><testcase name="t"><failure><![CDATA[<!ENTITY a "b">]]>'
                        "</failure></testcase></testsuite>"):
            with self.subTest(content=content[:40]):
                self.assert_refused_unparsed(content, "DOCTYPE or ENTITY")

    def test_entity_shape_refused_even_with_unittest_override(self):
        # Only the junit reader parses XML; a forced unittest read never does.
        path = self.write("x.xml", fx.external_entity())
        with mock.patch.object(readers.ET, "fromstring",
                               side_effect=AssertionError("parsed")) as parser:
            self.refused(path, "not JUnit XML or unittest", input_format="unittest")
        parser.assert_not_called()

    def test_utf16_doctype_refused_without_parsing(self):
        data = fx.billion_laughs().replace('version="1.0"', 'version="1.0" encoding="UTF-16"')
        self.assert_refused_unparsed(data.encode("utf-16"), "not UTF-8")

    def test_other_declared_encoding_refused_without_parsing(self):
        xml = fx.junit_simple([("t", "pass")]).replace("utf-8", "ISO-8859-1")
        self.assert_refused_unparsed(xml, "must be UTF-8")
        ok = fx.junit_simple([("t", "pass")]).replace("utf-8", "US-ASCII")
        self.assertEqual(load(self.write("ok.xml", ok)).format, "junit")

    def test_malformed_xml_refused(self):
        for content in ("<testsuite><testcase name='t'></testsuite>",
                        "<testsuite>\x01</testsuite>",
                        '<testsuite><testcase name="test_\x1b[31m"/></testsuite>',
                        "<testsuite><testcase name='&undefined;'/></testsuite>",
                        "<"):
            with self.subTest(content=content):
                message = self.refused(self.write("bad.xml", content), "malformed XML")
                self.assertNotIn("testcase", message)

    def test_hostile_names_survive_raw_in_the_model(self):
        names = [fx.HTML_NAME, fx.BIDI_NAME, fx.NEWLINE_NAME]
        xml = fx.junit_xml([fx.junit_case(n, classname="evil.<b>C</b>") for n in names])
        self.assertIn("&#10;", xml)
        run = load(self.write("names.xml", xml))
        self.assertEqual(sorted(run.results), sorted(f"evil.<b>C</b>::{n}" for n in names))

    def test_hostile_names_in_unittest_text(self):
        names = [fx.ANSI_NAME, fx.BIDI_NAME, fx.HTML_NAME]
        lines = []
        for name in names:
            lines += fx.ut_line(W, name, "pass")
        run = load(self.write("names.txt", fx.unittest_log(lines)))
        self.assertEqual(sorted(run.results), sorted(f"{W}.{n}" for n in names))
        self.assertIn("\x1b", "".join(run.results))
        self.assertIn("\u202e", "".join(run.results))


if __name__ == "__main__":
    unittest.main()
