import tempfile
import unittest
from html.parser import HTMLParser

from jsondiff.diff import diff_shapes
from jsondiff.report import (esc_html, esc_md, esc_terminal, render_html,
                             render_md, render_terminal)
from jsondiff.shape import build_shape
from tests import jsondiff_fixtures as fx

RENDERERS = {"terminal": render_terminal, "md": render_md, "html": render_html}
ALLOWED_TAGS = {"html", "head", "meta", "title", "style", "body", "main", "header",
                "section", "h1", "h2", "p", "div", "table", "thead", "tbody", "tr",
                "th", "td", "code", "span", "ul", "li"}
RAW_CONTROLS = ("\x1b", "\u202e")


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = set()
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs.extend(attrs)


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def result(self, pair, *, values=False, keys=None):
        old, new = pair
        # values=False is left to the builder's default, so a default flip is caught.
        extra = {"values": True} if values else {}
        result = diff_shapes(build_shape(old, keys=keys, **extra),
                             build_shape(new, keys=keys, **extra), keys=keys)
        self.assertTrue(result["changes"])
        return result

    def render_all(self, result, **kwargs):
        outputs = {}
        for name, render in RENDERERS.items():
            text = render(result, **kwargs)
            self.assertTrue(text)
            outputs[name] = text
        return outputs


class TestEscapeHelpers(unittest.TestCase):
    def test_controls_and_bidi_made_visible(self):
        raw = "a\x1bb\u202ec\nd\te\u2066f\x7fg\x85h"
        for helper in (esc_terminal, esc_md, esc_html):
            out = helper(raw)
            self.assertTrue(out)
            for ch in ("\x1b", "\u202e", "\n", "\t", "\u2066", "\x7f", "\x85"):
                self.assertNotIn(ch, out)
            self.assertIn("\\x1b", out)
            self.assertIn("\\u202e", out)
            self.assertIn("\\x0a", out)
            self.assertIn("\\x09", out)
            self.assertIn("\\u2066", out)

    def test_md_escapes_table_and_markup_characters(self):
        out = esc_md('a|b`c<img src=x>[x](javascript:y)')
        self.assertTrue(out)
        self.assertIn("\\|", out)
        self.assertIn("\\`", out)
        self.assertNotIn("<img", out)
        self.assertIn("\\[x\\](javascript", out)
        self.assertEqual(out.count("]("), out.count("\\]("))

    def test_html_escape_quotes(self):
        out = esc_html('"><img onerror=x>\'')
        self.assertTrue(out)
        self.assertEqual(out, "&quot;&gt;&lt;img onerror=x&gt;&#x27;")


class TestReportEscaping(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.outputs = self.render_all(
            self.result(fx.hostile_pair(self.dir), keys=["esc\x1b\u202e<script>"]),
            show_all=True)

    def test_hostile_keys_escaped_in_all_formats(self):
        for name, text in self.outputs.items():
            for ch in RAW_CONTROLS:
                self.assertNotIn(ch, text, f"raw {ch!r} in {name}")
            self.assertIn("\\x1b", text, name)       # hostile file name / key field
            self.assertIn("\\u202e", text, name)

    def test_html_has_no_raw_markup_from_source(self):
        text = self.outputs["html"]
        self.assertNotIn("<img", text)
        self.assertNotIn("<script", text.lower())
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("&quot;&gt;&lt;img onerror=x&gt;", text)
        parser = _Tags()
        parser.feed(text)
        self.assertTrue(parser.tags)
        self.assertLessEqual(parser.tags, ALLOWED_TAGS)
        self.assertFalse([a for a in parser.attrs if a[0].startswith("on")])
        for needle in ("http://", "https://", "src=", "url(", "<link", "@import"):
            self.assertNotIn(needle, text)
        self.assertIn('<meta charset="utf-8">', text)

    def test_md_table_rows_keep_their_columns(self):
        text = self.outputs["md"]
        self.assertNotIn("<img", text)
        self.assertNotIn("<script", text)
        rows = [l for l in text.splitlines() if l.startswith("| ")]
        self.assertTrue(rows)
        changes = text.split("## Changes")[1].split("##")[0]
        widths = {l.replace("\\|", "").count("|") for l in changes.splitlines()
                  if l.startswith("|")}
        self.assertEqual(widths, {5})
        self.assertIn("a\\|b\\`c", text)


class TestReportPrivacy(ReportTestCase):
    def test_values_off_by_default_in_all_formats(self):
        result = self.result(fx.privacy_pair(self.dir))
        for name, text in self.render_all(result, show_all=True).items():
            for sentinel in fx.SENTINELS:
                self.assertNotIn(sentinel, text, f"{sentinel} leaked in {name}")
            self.assertIn("values: off", text)
            self.assertNotIn("values: on", text)

    def test_values_on_when_requested(self):
        result = self.result(fx.privacy_pair(self.dir), values=True)
        for name, text in self.render_all(result).items():
            for sentinel in fx.SENTINELS:
                self.assertIn(sentinel, text, f"{sentinel} missing in {name}")
            self.assertIn("values: on", text)


class TestReportContent(ReportTestCase):
    def test_header_summary_and_sections(self):
        result = self.result(fx.hostile_pair(self.dir))
        for name, text in self.render_all(result).items():
            self.assertIn("candidates for review, not proof of breakage", text)
            self.assertIn("array elements compared by merged shape, not position", text)
            self.assertIn("potentially breaking", text)
            self.assertIn("Warnings", text)
            self.assertIn("none", text.split("Warnings")[1], name)
            self.assertIn("new.json", text)
            self.assertNotIn(self.dir, text)

    def test_breaking_changes_listed_first(self):
        old = fx.write_json(self.dir, "o.json", {"a": 1, "z": 1})
        new = fx.write_json(self.dir, "n.json", {"a": 1, "b": 2})
        text = render_terminal(self.result((old, new)))
        self.assertTrue(text)
        changes = text.split("\nChanges\n")[1]
        self.assertLess(changes.index("  z "), changes.index("  b "))

    def test_jsonl_header_counts(self):
        bad = fx.invalid_jsonl(self.dir)
        good = fx.write_jsonl(self.dir, "good.jsonl", [{"a": 1, "b": 1}])
        text = render_terminal(self.result((good, bad)))
        self.assertTrue(text)
        self.assertIn("JSONL, 4 records, 4 invalid lines (first: 2, 5, 7)", text)
        self.assertIn("JSONL, 1 record, 0 invalid lines", text)

    def test_key_matches_section(self):
        old = fx.keyed_orders(self.dir, "o.json", [{"id": 1, "a": 1}, {"id": 2}])
        new = fx.keyed_orders(self.dir, "n.json", [{"id": 1, "a": "x"}, {"id": 3}])
        result = self.result((old, new), keys=["orders=id"])
        for text in self.render_all(result).values():
            self.assertIn("Key matches", text)
        self.assertIn("1 added, 1 removed, 1 changed",
                      render_terminal(result))

    def test_all_lists_unchanged_paths(self):
        result = self.result(fx.privacy_pair(self.dir))
        self.assertNotIn("Unchanged paths", render_terminal(result))
        text = render_terminal(result, show_all=True)
        self.assertTrue(text)
        self.assertIn("Unchanged paths\n  $\n", text)
