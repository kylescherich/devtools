"""testdelta privacy: by default only ids, statuses and times. Failure
messages, tracebacks, captured output and skip/xfail reasons stay out of every
format; --details shows, for newly failing tests only, the redacted first
message line and at most 20 traceback lines. Every test asserts non-empty
output before checking for absence (DEC-071). Sentinels are built at runtime
from tests/secret_shapes.py (DEC-195)."""
import json
import re
import tempfile
import unittest

from testdelta.compare import compare
from testdelta.readers import load_pair
from testdelta.report import FORMATS, render
from tests import secret_shapes as ss
from tests import testdelta_fixtures as fx

GH = ss.GHP + "A1b2C3d4E5F6g7H8i9J0k1L2m3N4o5P6q7R8"
AWS = ss.AKIA + "QWERTYUIOPASDFGH"
PW = "hunter2-sentinel-pw"
PEM_BODY = "MIIEsentinelPEMbodyQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo"
SECRETS = (GH, AWS, PW, PEM_BODY)

# Plain markers: private text that must not appear unless --details allows it.
MSG = "MSGMARK"              # newly failing message: shown only with --details
TB = "TBMARK"                # newly failing traceback: shown only with --details
HIDDEN = ("MSGSECONDLINE", "STILLMARK", "ADDEDMARK", "SKIPMARK", "XFAILMARK",
          "SYSOUTMARK", "SYSERRMARK", "BUFOUTMARK", "BUFERRMARK", "OTHERMARK")


def pem_lines():
    return [ss.pem("BEGIN", "RSA "), PEM_BODY, ss.pem("END", "RSA ")]


def failure_text():
    return "\n".join([f"{TB} frame one", f"    connect(password={PW})",
                      f"    header = 'Bearer {GH}'"] + pem_lines()
                     + [f"E   {MSG} key {AWS} rejected"])


def junit_pair():
    W = "pkg.mod.Class"
    before = fx.junit_xml([
        fx.junit_case("test_new_fail", W),
        fx.junit_case("test_still", W, kind="fail", message="STILLMARK one",
                      text="STILLMARK tb"),
        fx.junit_case("test_skip_reason", W),
        fx.junit_case("test_xfail_reason", W),
        fx.junit_case("test_skip_same", W, kind="skip", message="SKIPMARK same " + GH),
        fx.junit_case("test_out", W, system_out="SYSOUTMARK " + GH,
                      system_err="SYSERRMARK " + PW),
        fx.junit_case("test_other", W, kind="error", message="OTHERMARK", text="OTHERMARK"),
    ])
    after = fx.junit_xml([
        fx.junit_case("test_new_fail", W, kind="fail",
                      message=f"{MSG} key {AWS} rejected\nMSGSECONDLINE",
                      text=failure_text(), system_out="SYSOUTMARK " + GH,
                      system_err="SYSERRMARK " + PW),
        fx.junit_case("test_still", W, kind="fail", message="STILLMARK two",
                      text="STILLMARK tb2"),
        fx.junit_case("test_skip_reason", W, kind="skip", message="SKIPMARK " + GH),
        fx.junit_case("test_xfail_reason", W, kind="xfail", message="XFAILMARK " + AWS),
        fx.junit_case("test_skip_same", W, kind="skip", message="SKIPMARK same " + GH),
        fx.junit_case("test_out", W, system_out="SYSOUTMARK " + GH,
                      system_err="SYSERRMARK " + PW),
        fx.junit_case("test_added", W, kind="error", message="ADDEDMARK " + GH,
                      text="ADDEDMARK tb"),
        fx.junit_case("test_newly_skipped_src", W, kind="skip", message="SKIPMARK 2"),
        fx.junit_case("test_other", W, kind="skip", message="SKIPMARK other"),
    ])
    before = before.replace("</testsuite>", fx.junit_case("test_newly_skipped_src", W)
                            + "\n</testsuite>")
    return before, after


def unittest_pair():
    W = "pkg.mod.Class"
    lines_b = (fx.ut_line(W, "test_new_fail", "pass") + fx.ut_line(W, "test_still", "fail")
               + fx.ut_line(W, "test_skip_reason", "pass")
               + fx.ut_line(W, "test_xfail_reason", "pass")
               + [f"test_skip_same ({W}.test_skip_same) ... skipped 'SKIPMARK same {GH}'"])
    blocks_b = [fx.ut_block("fail", W, "test_still", message="AssertionError: STILLMARK",
                            stdout="BUFOUTMARK " + GH, stderr="BUFERRMARK " + PW)]
    lines_a = (fx.ut_line(W, "test_new_fail", "fail") + fx.ut_line(W, "test_still", "fail")
               + [f"test_skip_reason ({W}.test_skip_reason) ... skipped 'SKIPMARK {GH}'"]
               + fx.ut_line(W, "test_xfail_reason", "xfail")
               + fx.ut_line(W, "test_added", "error")
               + [f"test_skip_same ({W}.test_skip_same) ... skipped 'SKIPMARK same {GH}'"])
    message = "\n".join(["    " + line for line in pem_lines()] + [f"    login(password={PW})",
                                       f"AssertionError: {MSG} key {AWS} rejected"])
    blocks_a = [fx.ut_block("fail", W, "test_new_fail", message=message,
                            stdout="BUFOUTMARK " + GH, stderr="BUFERRMARK " + PW),
                fx.ut_block("fail", W, "test_still", message="AssertionError: STILLMARK",
                            stdout="BUFOUTMARK " + GH),
                fx.ut_block("error", W, "test_added", message="ValueError: ADDEDMARK " + GH)]
    # Traceback frames say TBMARK so the traceback marker is present too.
    blocks_a[0] = [line.replace("step_0()", f"step_0()  # {TB}") for line in blocks_a[0]]
    return fx.unittest_log(lines_b, blocks_b), fx.unittest_log(lines_a, blocks_a)


class _Pipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def delta(self, pair, suffix):
        before, after = pair
        b = fx.write(self.dir, "before" + suffix, before)
        a = fx.write(self.dir, "after" + suffix, after)
        return compare(*load_pair(b, a))

    def outputs(self, details):
        """(label, output) for both input formats in all four report formats."""
        out = []
        for label, pair, suffix in (("junit", junit_pair(), ".xml"),
                                    ("unittest", unittest_pair(), ".txt")):
            delta = self.delta(pair, suffix)
            for fmt in FORMATS:
                out.append((f"{label}/{fmt}", render(delta, fmt, details=details)))
        return out

    def assert_absent(self, out, values, label):
        self.assertTrue(out, f"{label}: empty output")
        self.assertIn("test_new_fail", out, f"{label}: report lacks the test")
        for value in values:
            self.assertNotIn(value, out, f"{label}: {value[:12]}... leaked")


class TestDefaultPrivacy(_Pipeline):
    def test_default_has_no_message_traceback_or_output(self):
        for label, out in self.outputs(details=False):
            with self.subTest(label=label):
                self.assert_absent(out, SECRETS + HIDDEN + (MSG, TB), label)

    def test_inputs_really_contain_the_sentinels(self):
        for before, after in (junit_pair(), unittest_pair()):
            text = before + after
            self.assertTrue(text)
            for value in SECRETS + (MSG, TB, "SKIPMARK", "STILLMARK", "ADDEDMARK"):
                self.assertIn(value, text)
        self.assertIn("SYSOUTMARK", junit_pair()[1])
        self.assertIn("XFAILMARK", junit_pair()[1])
        self.assertIn("BUFOUTMARK", unittest_pair()[1])

    def test_json_has_no_detail_keys_by_default(self):
        delta = self.delta(junit_pair(), ".xml")
        out = render(delta, "json")
        self.assertTrue(out)
        doc = json.loads(out)
        self.assertTrue(doc["newly_failing"])
        keys = set()

        def walk(node):
            if isinstance(node, dict):
                keys.update(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(doc)
        self.assertTrue(keys)
        for key in ("message", "traceback", "traceback_omitted"):
            self.assertNotIn(key, keys)

    def test_html_has_csp_and_no_active_content(self):
        allowed_tags = {"!doctype", "html", "head", "meta", "title", "style", "body", "h1",
                        "h2", "table", "tr", "th", "td", "p", "strong", "ul", "li", "pre"}
        allowed_attrs = {"lang", "charset", "http-equiv", "content", "class", "colspan"}
        hostile = ['<img src=x onerror=alert(1)>', '<a href="https://evil.example.test">x</a>',
                   "<script>alert(1)</script>", '" onmouseover="x', "javascript:alert(1)"]
        before = fx.junit_xml([fx.junit_case(n, "evil") for n in hostile]
                              + [fx.junit_case("test_new_fail", "pkg")])
        after = fx.junit_xml([fx.junit_case(n, "evil", kind="fail", message="<img src=y>",
                                            text="<script>x</script>") for n in hostile]
                             + [fx.junit_case("test_new_fail", "pkg", kind="fail",
                                              message=MSG, text=TB)])
        for details in (False, True):
            page = render(self.delta((before, after), ".xml"), "html", details=details)
            with self.subTest(details=details):
                self.assertTrue(page)
                self.assertIn('<meta http-equiv="Content-Security-Policy" '
                              "content=\"default-src 'none'; style-src 'unsafe-inline'\">",
                              page)
                tags = re.findall(r"<(/?)([A-Za-z!][A-Za-z0-9-]*)([^>]*)>", page)
                self.assertTrue(tags)
                for _, name, attrs in tags:
                    self.assertIn(name.lower(), allowed_tags)
                    for attr in re.findall(r"([A-Za-z-]+)=", attrs):
                        self.assertIn(attr.lower(), allowed_attrs)
                        self.assertFalse(attr.lower().startswith("on"))
                lowered = page.lower()
                for bad in ("<script", "<img", "<a ", "<a>", "<link", "<iframe",
                            "<object", "<embed", "<form", "<base", "url("):
                    self.assertNotIn(bad, lowered)
                style = page[page.index("<style>"):page.index("</style>")]
                self.assertNotIn("http", style)
                for _, _, attrs in tags:
                    self.assertNotIn("http", attrs.replace("http-equiv", ""))
                    self.assertNotIn("//", attrs)


class TestReasonsNeverShown(_Pipeline):
    def test_skip_and_xfail_reasons_absent_default_and_details(self):
        for details in (False, True):
            for label, out in self.outputs(details=details):
                with self.subTest(label=label, details=details):
                    self.assert_absent(out, ("SKIPMARK", "XFAILMARK"), label)
                    self.assertIn("test_skip_reason", out)
                    self.assertIn("test_xfail_reason", out)


class TestSecretInTestName(_Pipeline):
    def test_secret_in_name_redacted_in_all_formats(self):
        names = [f"test_auth[{GH}]", f"test_key[{AWS}]", f"test_pw[password={PW}]",
                 f"test_header[Bearer {GH}]"]
        before = fx.junit_xml([fx.junit_case(n) for n in names]
                              + [fx.junit_case("test_new_fail")])
        after = fx.junit_xml([fx.junit_case(n, kind="fail", message="m", text="t")
                              for n in names] + [fx.junit_case("test_new_fail", kind="fail")]
                             + [fx.junit_case(names[0])])
        delta = self.delta((before, after), ".xml")
        self.assertEqual(len(delta.categories["newly_failing"]), len(names) + 1)
        self.assertTrue(delta.duplicates_after)
        for fmt in FORMATS:
            for details in (False, True):
                out = render(delta, fmt, details=details)
                with self.subTest(fmt=fmt, details=details):
                    self.assert_absent(out, SECRETS, fmt)
                    self.assertIn("test_auth", out)
                    self.assertIn("redacted", out)
        doc = json.loads(render(delta, "json"))
        ids = [i["id"] for i in doc["newly_failing"]] + doc["duplicates"]["after"]
        self.assertTrue(ids)
        self.assertFalse(any(s in i for s in SECRETS for i in ids))


class TestDetails(_Pipeline):
    def test_details_shows_redacted_message_first_line(self):
        for label, out in self.outputs(details=True):
            with self.subTest(label=label):
                self.assert_absent(out, SECRETS, label)
                self.assertIn(MSG, out)
                self.assertIn(TB, out)
                self.assertIn("redacted", out)
                self.assertNotIn("MSGSECONDLINE", out)

    def test_details_only_for_newly_failing(self):
        for label, out in self.outputs(details=True):
            with self.subTest(label=label):
                self.assert_absent(out, ("STILLMARK", "ADDEDMARK", "OTHERMARK"), label)

    def test_captured_output_absent_even_with_details(self):
        for label, out in self.outputs(details=True):
            with self.subTest(label=label):
                self.assert_absent(out, ("SYSOUTMARK", "SYSERRMARK", "BUFOUTMARK",
                                         "BUFERRMARK"), label)

    def test_traceback_capped_at_last_20_lines(self):
        text = "\n".join(f"TBLINE-{n:02d};" for n in range(1, 51))
        before = fx.junit_xml([fx.junit_case("test_new_fail")])
        after = fx.junit_xml([fx.junit_case("test_new_fail", kind="fail", message="m",
                                            text=text)])
        delta = self.delta((before, after), ".xml")
        for fmt in FORMATS:
            out = render(delta, fmt, details=True)
            with self.subTest(fmt=fmt):
                self.assertTrue(out)
                self.assertEqual(out.count("TBLINE-"), 20)
                self.assertIn("TBLINE-31;", out)
                self.assertIn("TBLINE-50;", out)
                self.assertNotIn("TBLINE-30;", out)
                self.assertNotIn("TBLINE-01;", out)
                if fmt != "json":
                    self.assertIn("30 earlier traceback lines omitted", out)
        text_out = render(delta, "text", details=True)
        self.assertEqual(sum(1 for line in text_out.split("\n")
                             if line.startswith("      | ")), 20)
        doc = json.loads(render(delta, "json", details=True))
        item = doc["newly_failing"][0]
        self.assertEqual(len(item["traceback"]), 20)
        self.assertEqual(item["traceback_omitted"], 30)
        self.assertEqual(item["message"], "m")

    def test_unittest_traceback_capped(self):
        W = "pkg.mod.Class"
        before = fx.unittest_log(fx.ut_line(W, "test_new_fail", "pass"))
        after = fx.unittest_log(fx.ut_line(W, "test_new_fail", "fail"),
                                [fx.ut_block("fail", W, "test_new_fail", frames=30)])
        out = render(self.delta((before, after), ".txt"), "text", details=True)
        self.assertTrue(out)
        self.assertEqual(sum(1 for line in out.split("\n") if line.startswith("      | ")), 20)
        self.assertIn("earlier traceback lines omitted", out)
        self.assertIn("AssertionError: synthetic failure", out)

    def test_pem_cut_by_the_cap_still_redacted(self):
        # BEGIN line falls before the last 20 lines, the body inside them.
        lines = pem_lines()[:1] + [f"filler {n}" for n in range(15)] + [PEM_BODY] * 3 \
            + [f"tail {n}" for n in range(10)] + pem_lines()[2:]
        before = fx.junit_xml([fx.junit_case("test_new_fail")])
        after = fx.junit_xml([fx.junit_case("test_new_fail", kind="fail", message="m",
                                            text="\n".join(lines))])
        delta = self.delta((before, after), ".xml")
        for fmt in FORMATS:
            out = render(delta, fmt, details=True)
            with self.subTest(fmt=fmt):
                self.assertTrue(out)
                self.assertIn("test_new_fail", out)
                self.assertNotIn(PEM_BODY, out)

    def test_details_json_keys_only_on_newly_failing(self):
        delta = self.delta(junit_pair(), ".xml")
        out = render(delta, "json", details=True)
        self.assertTrue(out)
        doc = json.loads(out)
        self.assertTrue(doc["newly_failing"])
        for item in doc["newly_failing"]:
            self.assertEqual(list(item), ["id", "before", "after", "before_time",
                                          "after_time", "message", "traceback",
                                          "traceback_omitted"])
            self.assertLessEqual(len(item["traceback"]), 20)
        for name in ("still_failing", "added", "other_changes", "removed"):
            for item in doc[name]:
                self.assertNotIn("message", item)
                self.assertNotIn("traceback", item)


if __name__ == "__main__":
    unittest.main()
