"""gitarch reports: privacy defaults, --messages, --diffs, --authors,
escaping and HTML safety (BRIEF-DEVWS-022 task 4)."""
import contextlib
import io
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from gitarch.cli import EXIT_OK, main
from gitarch.history import DIFF_COMMITS, DIFF_LINES, SUBJECT_CHARS
from gitarch.report import FORMATS
from tests import gitarch_fixtures as fx
from tests import secret_shapes as ss

AUTHORS = [("Zorblax Quintessa", "zorblax.q@example.test"),
           ("Mirelda Fenwhistle", "mirelda.f@example.test")]
SUBJECTS = ["SUBJmarkerAlpha add files", "SUBJmarkerBravo tune", "SUBJmarkerCharlie more"]
AWS = ss.AKIA + "SNTGITARCHREPORT"          # 20 characters: a full AWS key id shape


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def report(self, *argv):
        code, out, err = run(argv)
        self.assertEqual(code, EXIT_OK, err)
        self.assertTrue(out.strip(), "report is empty")
        return out

    def marked_repo(self):
        """Commit subjects and author names are unique markers; bodies carry
        secret sentinels."""
        repo = fx.init_repo(self.dir)
        for i, subject in enumerate(SUBJECTS):
            fx.write(repo, "app.py", f"v = {i}\n")
            body = (f"{subject}\n\nbody line BODYmarker{i}\naws key {AWS}\n"
                    f"password=SNTreport words {i}")
            fx.commit(repo, body, f"2026-01-0{5 + i}T10:00:00Z",
                      author=AUTHORS[i % 2])
        return repo


class TestPrivacyDefaults(ReportTestCase):
    def test_default_output_has_no_messages_or_authors(self):
        repo = self.marked_repo()
        log = fx.git(repo, "log", "--format=%an %ae %B").decode()
        for word in ("SUBJmarkerAlpha", "Zorblax", "mirelda.f@", AWS, "SNTreport"):
            self.assertIn(word, log)                     # the data is really there
        forbidden = ([s.split()[0] for s in SUBJECTS] + [n for n, _ in AUTHORS]
                     + [e for _, e in AUTHORS] + ["Zorblax", "Fenwhistle", "@example.test",
                                                  "BODYmarker", AWS, "SNTreport"])
        for fmt in FORMATS:
            for extra in ([], ["--path", "app.py"]):
                with self.subTest(fmt=fmt, extra=extra):
                    out = self.report(repo, "--format", fmt, *extra)
                    self.assertIn("messages: off" if fmt != "html" else "messages", out)
                    for word in forbidden:
                        self.assertNotIn(word, out)

    def test_messages_shows_subjects_redacted(self):
        repo = self.marked_repo()
        fx.write(repo, "app.py", "v = 9\n")
        long_subject = "SUBJlong " + "x" * 200
        fx.commit(repo, f"{long_subject}\n\nBODYmarkerLong", "2026-01-09T10:00:00Z")
        fx.write(repo, "app.py", "v = 10\n")
        fx.commit(repo, f"rotate key {AWS} and password=SNTsubj words", "2026-01-10T10:00:00Z")
        for fmt in FORMATS:
            for extra in ([], ["--path", "app.py"]):
                with self.subTest(fmt=fmt, extra=extra):
                    out = self.report(repo, "--messages", "--format", fmt, *extra)
                    for subject in SUBJECTS:
                        self.assertIn(subject, out)
                    self.assertIn("rotate key", out)
                    for word in (AWS, "SNTsubj", "BODYmarker", "x" * (SUBJECT_CHARS - 8)):
                        self.assertNotIn(word, out)
                    self.assertIn("SUBJlong " + "x" * (SUBJECT_CHARS - 9), out)
                    self.assertNotIn("Zorblax", out)

    def test_messages_token_glued_after_underscore_redacted(self):
        # BRIEF-DEVWS-025: a subject with name_<token>, default format.
        token = ss.GHP + "SNTgluedGitarchToken"
        repo = fx.init_repo(self.dir)
        fx.write(repo, "app.py", "v = 1\n")
        fx.commit(repo, "rotate name_" + token, "2026-01-05T10:00:00Z")
        self.assertIn(token, fx.git(repo, "log", "--format=%s").decode())
        out = self.report(repo, "--messages")
        self.assertTrue(out)
        self.assertIn("rotate name_<redacted>", out)
        self.assertNotIn("SNTgluedGitarchToken", out)

    def test_authors_names_only_no_email(self):
        repo = self.marked_repo()
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                out = self.report(repo, "--authors", "--format", fmt)
                self.assertIn("Zorblax Quintessa", out)
                self.assertIn("Mirelda Fenwhistle", out)
                for _, email in AUTHORS:
                    self.assertNotIn(email, out)
                    self.assertNotIn(email.split("@")[0], out)
                self.assertNotIn("@example.test", out)
                self.assertNotIn("<email>", out)
                self.assertNotIn("SUBJmarker", out)


class TestDiffs(ReportTestCase):
    def test_diffs_redacted_and_capped(self):
        repo = fx.init_repo(self.dir)
        fx.write(repo, "data/notes.txt", "start\n")
        fx.commit(repo, "c0", "2026-01-01T10:00:00Z")
        for i in range(1, 11):          # 11 commits in all touching data/
            if i == 5:
                fx.write(repo, "data/notes.txt",
                         "start\n" + "".join(f"line {n}\n" for n in range(300)))
            elif i == 7:
                fx.write(repo, "data/blob.bin", b"\x00\x01\x02" * 10)
            elif i == 9:
                fx.write(repo, "data/notes.txt", f"start\nconfig aws_key={AWS}\n"
                                                 f"token {ss.GHP}SNTdiffToken0123456789\n")
            else:
                fx.write(repo, "data/notes.txt", f"start\nedit {i}\n")
            fx.commit(repo, f"c{i}", f"2026-01-{1 + i:02d}T10:00:00Z")
        oldest = fx.head(repo, "HEAD~10")[:10]
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                out = self.report(repo, "--path", "data", "--diffs", "--format", fmt)
                diff_part = out.split("iffs (newest")[1]
                self.assertEqual(diff_part.count("commit "), DIFF_COMMITS)
                self.assertNotIn(oldest, diff_part)          # 11th commit left out
                self.assertIn("line 0", out)
                self.assertIn(f"line {DIFF_LINES - 10}", out)
                self.assertNotIn("line 299", out)
                self.assertIn("more lines", out)
                self.assertIn("binary file diff", out)
                self.assertNotIn("\x00", out)
                self.assertNotIn(AWS, out)
                self.assertNotIn("SNTdiffToken", out)
                self.assertIn("aws_key=", out)


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [name for name, _ in attrs]

    handle_startendtag = handle_starttag


class TestEscaping(ReportTestCase):
    def hostile_repo(self):
        repo = fx.init_repo(self.dir)
        fx.write(repo, "<img onerror>.py", "x = 1\n")
        fx.write(repo, "bidi\u202egnp.py", "y = 1\n")
        fx.write(repo, 'back\\slash" onclick=alert(1).py', "z = 1\n")
        fx.commit(repo, "\x1b[31mred subject <script>alert(1)</script> | `x` [l](h)",
                  "2026-01-05T10:00:00Z")
        return repo

    def test_escaping_html_md_text(self):
        repo = self.hostile_repo()
        outs = {fmt: self.report(repo, "--messages", "--format", fmt) for fmt in FORMATS}
        for fmt, out in outs.items():
            with self.subTest(fmt=fmt):
                self.assertNotIn("\x1b", out)
                red = "\\x1b\\[31mred" if fmt == "md" else "\\x1b[31mred"
                self.assertIn(red, out)
                self.assertNotIn("\u202e", out)
                self.assertIn("\\u202e", out)
        self.assertIn("<img onerror>.py", outs["text"])
        self.assertIn("back\\\\slash", outs["text"])                 # doubled
        self.assertIn("&lt;img onerror&gt;.py", outs["md"])
        self.assertIn("\\| \\`x\\` \\[l\\](h)", outs["md"])
        self.assertIn("&lt;img onerror&gt;.py", outs["html"])
        self.assertIn("&lt;script&gt;", outs["html"])
        self.assertNotIn("<script", outs["html"].lower())
        self.assertNotIn("<img", outs["html"].lower())

    def test_html_tags_and_attributes(self):
        repo = self.hostile_repo()
        for extra in ([], ["--messages", "--authors"],
                      ["--path", "<img onerror>.py", "--messages", "--diffs"]):
            with self.subTest(extra=extra):
                out = self.report(repo, "--format", "html", *extra)
                parser = _TagCollector()
                parser.feed(out)
                parser.close()
                self.assertTrue(parser.tags)
                for tag in ("script", "img", "a", "iframe", "object", "embed", "link",
                            "form", "base"):
                    self.assertNotIn(tag, parser.tags)
                self.assertTrue(parser.attrs)
                for name in parser.attrs:
                    self.assertFalse(name.startswith("on"), name)
                    self.assertNotIn(name, ("src", "href", "srcset", "action",
                                            "formaction", "xlink:href", "style"))
                self.assertIn("Content-Security-Policy", out)
                self.assertIn("default-src 'none'", out)
