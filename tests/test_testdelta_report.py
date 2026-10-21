"""testdelta reports: sections, totals, JSON schema, escaping of untrusted
test names in every format, and the final redact() pass being a no-op."""
import datetime
import json
import re
import tempfile
import unittest

from testdelta import report
from testdelta._redact import redact
from testdelta.compare import CATEGORIES, compare
from testdelta.readers import STATUSES, Result, Run, load_pair
from testdelta.report import FORMATS, TITLES, render
from tests import testdelta_fixtures as fx

ESC = chr(27)
BIDI = chr(0x202e)
BACKSLASH = chr(92)
MD_NAME = "test_a|b`c[d]e"
TYPED_ESCAPE_NAME = "test_typed_" + BACKSLASH + "x1b_text"
HOSTILE = [fx.HTML_NAME, fx.ANSI_NAME, fx.BIDI_NAME, fx.NEWLINE_NAME, MD_NAME,
           TYPED_ESCAPE_NAME, '<a href="https://evil.example.test/x">link</a>',
           "<script>alert(1)</script>"]


def run(tests, fmt="junit", duplicates=(), unresolved=()):
    results = {}
    for test_id, value in tests.items():
        status, time = (value, None) if isinstance(value, str) else value
        results[test_id] = Result(status, time)
    return Run(fmt, results, tuple(duplicates), tuple(unresolved))


def sample_delta():
    before = run({"pkg::t_new_fail": ("pass", 1.0), "pkg::t_removed": "pass",
                  "pkg::t_skipped": "pass", "pkg::t_fixed": "fail",
                  "pkg::t_unskipped": "skip", "pkg::t_still": "error",
                  "pkg::t_other": "xpass", "pkg::t_slow": ("pass", 1.0),
                  "pkg::t_fast": ("pass", 3.0), "pkg::t_gone2": "pass"})
    after = run({"pkg::t_new_fail": ("fail", 0.9), "pkg::t_skipped": "skip",
                 "pkg::t_fixed": "pass", "pkg::t_unskipped": "pass",
                 "pkg::t_still": "fail", "pkg::t_other": "pass",
                 "pkg::t_slow": ("pass", 2.5), "pkg::t_fast": ("pass", 1.0),
                 "pkg::t_added": "pass"}, duplicates=["pkg::t_fixed"])
    return compare(before, after)


def hostile_delta():
    before = {name: "pass" for name in HOSTILE}
    after = {name: "fail" for name in HOSTILE}
    after["added " + fx.ANSI_NAME] = "pass"
    return compare(run(before), run(after, duplicates=[fx.BIDI_NAME]))


class TestStructure(unittest.TestCase):
    def test_every_format_renders_non_empty(self):
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                out = render(sample_delta(), fmt)
                self.assertTrue(out)
                self.assertTrue(out.endswith("\n"))
                self.assertTrue(out.isascii())

    def test_sections_in_fixed_order(self):
        expected = ["newly_failing", "added_failing", "removed", "newly_skipped", "slower",
                    "fixed",
                    "unskipped", "added", "faster", "still_failing", "other_changes"]
        self.assertEqual(list(CATEGORIES), expected)
        for fmt in ("text", "md", "html"):
            with self.subTest(fmt=fmt):
                out = render(sample_delta(), fmt)
                self.assertTrue(out)
                titles = [TITLES[n].upper() if fmt == "text" else TITLES[n] for n in expected]
                positions = [out.index(title + " (") for title in titles]
                self.assertEqual(positions, sorted(positions))
                self.assertLess(out.index("arnings" if fmt != "text" else "WARNINGS"),
                                positions[0])

    def test_text_rows_and_summary(self):
        out = render(sample_delta(), "text")
        self.assertTrue(out)
        self.assertIn("RESULT WORSE: 4 regressions (1 newly failing, 0 added failing, "
                      "2 removed, 1 newly skipped); 3 improvements.", out)
        self.assertIn("\n   pass -> fail      1.000s -> 0.900s     pkg::t_new_fail\n", out)
        self.assertIn("\n   pass -> -            n/a -> n/a        pkg::t_removed\n", out)
        self.assertIn("\n   pass -> pass      1.000s -> 2.500s     pkg::t_slow\n", out)
        self.assertIn("\n      - -> pass         n/a -> n/a        pkg::t_added\n", out)
        self.assertIn("STILL FAILING (NOT NEW) (1)", out)
        self.assertIn("redaction = on", out)

    def test_fail_on_slow_counts_slower_in_summary(self):
        out = render(sample_delta(), "text", fail_on_slow=True)
        self.assertTrue(out)
        self.assertIn("RESULT WORSE: 5 regressions (1 newly failing, 0 added failing, "
                      "2 removed, 1 newly skipped, 1 slower)", out)

    def test_no_regressions_summary_and_empty_sections(self):
        delta = compare(run({"a": ("fail", 1.0)}), run({"a": ("pass", 1.0)}))
        out = render(delta, "text")
        self.assertTrue(out)
        self.assertIn("RESULT no regressions; 1 improvement.", out)
        self.assertIn("NEWLY FAILING (0)\n  none\n", out)
        self.assertIn("WARNINGS none", out)

    def test_totals_every_status(self):
        out = render(sample_delta(), "text")
        self.assertTrue(out)
        for status in STATUSES + ("total",):
            self.assertRegex(out, rf"\n  {status} +\d+ +\d+\n")
        self.assertIn("\n  total         10       9\n", out)

    def test_count_drop_warning_is_loud_everywhere(self):
        delta = sample_delta()
        self.assertEqual(delta.warnings[0], "total test count dropped from 10 to 9 (1 fewer)")
        text = render(delta, "text")
        md = render(delta, "md")
        page = render(delta, "html")
        doc = json.loads(render(delta, "json"))
        for out in (text, md, page):
            self.assertTrue(out)
        self.assertIn("  !!! total test count dropped from 10 to 9 (1 fewer)", text)
        self.assertIn("- **WARNING: total test count dropped from 10 to 9 (1 fewer)**", md)
        self.assertIn('<li class="loud">WARNING: total test count dropped', page)
        self.assertEqual(doc["warnings"][0], "total test count dropped from 10 to 9 (1 fewer)")

    def test_duplicate_ids_listed(self):
        out = render(sample_delta(), "text")
        self.assertTrue(out)
        self.assertIn("  - duplicate id in after  pkg::t_fixed", out)

    def test_html_is_one_self_contained_page(self):
        page = render(sample_delta(), "html")
        self.assertTrue(page)
        self.assertTrue(page.startswith("<!doctype html>\n"))
        self.assertTrue(page.endswith("</html>\n"))
        self.assertEqual(page.count("<html"), 1)

    def test_no_current_date_or_time(self):
        today = datetime.date.today()
        for fmt in FORMATS:
            out = render(sample_delta(), fmt)
            self.assertTrue(out)
            self.assertNotIn(today.isoformat(), out)
            self.assertNotIn(str(today.year), out)


class TestJSONSchema(unittest.TestCase):
    def test_top_level_keys_in_order(self):
        out = render(sample_delta(), "json")
        self.assertTrue(out)
        doc = json.loads(out)
        self.assertEqual(list(doc), ["schema", "inputs", "input_format", "slow_rule",
                                     "details", "fail_on_slow", "counts", "summary",
                                     "warnings"] + list(CATEGORIES)
                         + ["duplicates", "unresolved", "renamed_params"])
        self.assertEqual(doc["schema"], "testdelta/1")
        self.assertEqual(doc["summary"], {"regressions": 4, "improvements": 3})
        self.assertEqual(doc["slow_rule"], {"ratio": 2.0, "min_seconds": 0.5})

    def test_counts_are_status_objects(self):
        doc = json.loads(render(sample_delta(), "json"))
        self.assertTrue(doc["counts"])
        self.assertEqual([c["status"] for c in doc["counts"]], list(STATUSES) + ["total"])
        self.assertEqual(doc["counts"][-1], {"status": "total", "before": 10, "after": 9})

    def test_items_exact_keys_sorted_and_null_times(self):
        doc = json.loads(render(sample_delta(), "json"))
        self.assertTrue(doc["removed"])
        self.assertEqual([i["id"] for i in doc["removed"]], ["pkg::t_gone2", "pkg::t_removed"])
        for name in CATEGORIES:
            for item in doc[name]:
                self.assertEqual(list(item), ["id", "before", "after", "before_time",
                                              "after_time"])
        self.assertEqual(doc["removed"][0]["after"], None)
        self.assertEqual(doc["removed"][0]["before_time"], None)
        self.assertEqual(doc["slower"][0]["after_time"], 2.5)

    def test_byte_identical_reruns(self):
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                first = render(sample_delta(), fmt, details=True)
                second = render(sample_delta(), fmt, details=True)
                self.assertTrue(first)
                self.assertEqual(first, second)

    def test_byte_identical_from_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = fx.write(tmp, "b.xml", fx.junit_simple([("x", "pass"), ("y", "fail")]))
            a = fx.write(tmp, "a.xml", fx.junit_simple([("x", "fail"), ("z", "pass")]))
            outs = {fmt: [render(compare(*load_pair(b, a)), fmt, names=("b.xml", "a.xml"))
                          for _ in range(2)] for fmt in FORMATS}
        for fmt, (first, second) in outs.items():
            self.assertTrue(first)
            self.assertEqual(first, second, fmt)


class TestEscaping(unittest.TestCase):
    def test_text_makes_controls_and_bidi_visible(self):
        out = render(hostile_delta(), "text")
        self.assertTrue(out)
        self.assertNotIn(ESC, out)
        self.assertNotIn(BIDI, out)
        self.assertIn("test_" + BACKSLASH + "x1b[31mred" + BACKSLASH + "x1b[0m", out)
        self.assertIn("test_" + BACKSLASH + "u202eexe.txt", out)
        self.assertIn("test_line1" + BACKSLASH + "x0aline2", out)
        self.assertFalse(any(line.startswith("line2") for line in out.split("\n")))

    def test_text_and_md_double_typed_backslashes(self):
        # text: doubled; md: doubled, then each escaped (renders as two).
        for fmt, count in (("text", 2), ("md", 4)):
            out = render(hostile_delta(), fmt)
            self.assertTrue(out)
            self.assertIn("test_typed_" + BACKSLASH * count + "x1b_text", out)

    def test_md_escapes_markup(self):
        out = render(hostile_delta(), "md")
        self.assertTrue(out)
        self.assertNotIn("<img", out)
        self.assertNotIn("<script", out)
        self.assertNotIn("<a href", out)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", out)
        self.assertIn("test_a" + BACKSLASH + "|b" + BACKSLASH + "`c" + BACKSLASH + "[d"
                      + BACKSLASH + "]e", out)
        self.assertNotIn(ESC, out)
        self.assertNotIn(BIDI, out)

    def test_html_escapes_markup_in_text_nodes(self):
        out = render(hostile_delta(), "html")
        self.assertTrue(out)
        self.assertNotIn("<img", out)
        self.assertNotIn("<script", out)
        self.assertNotIn("<a ", out)
        self.assertIn("test_&lt;img src=x onerror=alert(1)&gt;", out)
        self.assertNotIn(ESC, out)
        self.assertNotIn(BIDI, out)

    def test_json_is_ascii_and_round_trips_ids(self):
        out = render(hostile_delta(), "json")
        self.assertTrue(out)
        self.assertTrue(out.isascii())
        self.assertNotIn(ESC, out)
        doc = json.loads(out)
        ids = sorted(item["id"] for item in doc["newly_failing"])
        self.assertEqual(ids, sorted(HOSTILE))
        self.assertEqual(doc["duplicates"]["after"], [fx.BIDI_NAME])

    def test_header_names_escaped(self):
        names = ("before" + ESC + "[2J.xml", "<b>after</b>.xml")
        for fmt in FORMATS:
            out = render(sample_delta(), fmt, names=names)
            self.assertTrue(out)
            self.assertNotIn(ESC, out)
            if fmt in ("md", "html"):
                self.assertNotIn("<b>", out)

    def test_final_redact_pass_is_a_no_op(self):
        delta = hostile_delta()
        for fmt in FORMATS:
            for details in (False, True):
                with self.subTest(fmt=fmt, details=details):
                    raw = report._RENDERERS[fmt](delta, ("before.xml", "after.xml"),
                                                 details, False)
                    self.assertTrue(raw)
                    self.assertEqual(redact(raw), raw)
                    self.assertEqual(render(delta, fmt, details, ("before.xml", "after.xml")),
                                     raw)
        json.loads(render(sample_delta(), "json"))


if __name__ == "__main__":
    unittest.main()
