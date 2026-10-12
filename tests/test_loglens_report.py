import contextlib
import io
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from loglens.cli import main
from loglens.readers import ERROR, WARNING, Entry, read_logs
from loglens.redact import REDACTED, redact
from loglens.report import analyse, escape_html, escape_md, render
from loglens.timeline import BUCKETS, build_timeline, choose_bucket
from tests import loglens_fixtures as fx
from tests.test_loglens_group import PEM_SPLIT, SECRET_CASES

T0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
FORMATS = ("text", "md", "html")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def entry(dt, level=ERROR, message="x"):
    return Entry(dt, level, message)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()


class TestPrivacy(ReportTestCase):
    def _secret_log(self):
        lines = [f"{iso(T0)} ERROR payment gateway down"] * 3
        for i, (word, text, _) in enumerate(SECRET_CASES):
            lines.append(f"{iso(T0 + timedelta(seconds=i + 1))} ERROR {word} {text}")
        lines += [f"{iso(T0 + timedelta(seconds=40))} ERROR tango key dump follows",
                  *PEM_SPLIT]
        return fx.write_lines(self.dir, "app.log", lines)

    def _sentinels(self):
        return [s for _, _, s in SECRET_CASES] + ["SNTpemSplitTango"]

    def test_privacy_all_formats(self):
        path = self._secret_log()
        source = path.read_text(encoding="utf-8")
        for sentinel in self._sentinels():
            self.assertIn(sentinel, source)
        for fmt in FORMATS:
            for extra in ([], ["--raw"]):
                with self.subTest(fmt=fmt, extra=extra):
                    code, out, _ = run([path, "--format", fmt, "--context", "3", *extra])
                    self.assertTrue(out.strip(), "report is empty")
                    self.assertIn("ERROR payment gateway down", out)
                    for sentinel in self._sentinels():
                        self.assertNotIn(sentinel, out, f"{sentinel} leaked")
                    if extra:
                        self.assertIn("tango key dump follows", out)
                        # userinfo redacted by the URL rule, host kept
                        self.assertIn("@db.example.test/x", out)
                    if fmt == "html":
                        self.assertTrue(out.endswith("</html>\n"))
                        self.assertNotIn(REDACTED, out)       # never a raw tag
                        self.assertIn("&lt;redacted&gt;", out)

    def test_final_redaction_pass_is_idempotent_on_escaped_output(self):
        for _, text, _ in SECRET_CASES:
            once = redact(text)
            self.assertTrue(once)
            for escape in (escape_html, escape_md):
                rendered = escape(once)
                self.assertEqual(redact(rendered), rendered, text)
                self.assertEqual(redact(rendered + '" x1 (first -)'),
                                 rendered + '" x1 (first -)', text)

    def test_marker_in_input_does_not_shield_a_secret(self):
        cases = [f"Authorization: {REDACTED} SNTauthA",
                 f'token="{REDACTED} SNTtokB"',
                 f"token={REDACTED}SNTtokC",
                 f"Authorization: &lt;redacted&gt; SNTauthD"]
        for text in cases:
            out = redact(text)
            self.assertTrue(out)
            self.assertNotIn("SNT", out, text)


class TestEscaping(ReportTestCase):
    MESSAGE = ("ERROR render <script>alert(1)</script> img \" onerror=steal() "
               "bidi \u202e gnp.exe ansi \x1b[31mred pipe a|b tick `cmd`")

    def test_escaping_html_md_text(self):
        path = fx.write_lines(self.dir, "app.log", [f"{iso(T0)} {self.MESSAGE}"])
        for fmt in FORMATS:
            for extra in ([], ["--raw"]):
                with self.subTest(fmt=fmt, extra=extra):
                    _, out, _ = run([path, "--format", fmt, *extra])
                    self.assertTrue(out.strip())
                    self.assertIn("render", out)
                    self.assertNotIn("\u202e", out)
                    self.assertNotIn("\x1b", out)
                    self.assertIn("\\u202e", out)
                    self.assertIn("\\x1b\\[" if fmt == "md" else "\\x1b[", out)
                    if fmt == "text":
                        self.assertIn("<script>", out)          # plain text, escaped controls
                        self.assertIn("a|b", out)
                    if fmt == "md":
                        self.assertNotIn("<script", out.lower())
                        self.assertIn("&lt;script&gt;", out)
                        self.assertIn("a\\|b", out)
                        self.assertIn("\\`cmd\\`", out)
                        self.assertNotIn(" `cmd`", out)
                    if fmt == "html":
                        low = out.lower()
                        self.assertNotIn("<script", low)
                        self.assertIn("&lt;script&gt;", out)
                        self.assertIsNone(re.search(r"<[^>]*\bon\w+\s*=", low))
                        self.assertNotIn("href", low)
                        self.assertNotIn("://", low)
                        self.assertNotIn("url(", low)
                        self.assertNotIn("<img", low)
                        self.assertIn("<svg", low)
                    if extra:
                        self.assertIn("alert(1)", out)

    def test_md_table_row_stays_one_row(self):
        path = fx.write_lines(self.dir, "app.log", [f"{iso(T0)} ERROR x | y | z"])
        _, out, _ = run([path, "--format", "md"])
        self.assertTrue(out)
        rows = [line for line in out.splitlines() if line.startswith("| ERROR |")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(re.findall(r"(?<!\\)\|", rows[0])), 5)


class TestTimeline(ReportTestCase):
    def test_spike_detected(self):
        entries = []
        for minute in range(20):
            entries.append(entry(T0 + timedelta(minutes=minute)))
            entries.append(entry(T0 + timedelta(minutes=minute, seconds=5), WARNING))
        spike_at = T0 + timedelta(minutes=12)
        entries += [entry(spike_at + timedelta(seconds=10 + i)) for i in range(30)]
        entries.sort(key=lambda e: e.ts)
        tl = build_timeline(entries, "1m")
        self.assertEqual(len(tl.buckets), 20)
        self.assertTrue(tl.spikes)
        self.assertEqual([b.start for b in tl.spikes], [int(spike_at.timestamp())])
        self.assertEqual(tl.spikes[0].total, 32)
        self.assertEqual(tl.threshold, 2 + 3 * 1)

        lines = [f"{iso(e.ts)} {e.level} job {i} failed" for i, e in enumerate(entries)]
        path = fx.write_lines(self.dir, "app.log", lines)
        _, out, _ = run([path])
        self.assertTrue(out)
        self.assertIn(f"1 spike at {iso(spike_at)} (32 events)", out)

    def test_no_spike_when_flat(self):
        entries = [entry(T0 + timedelta(minutes=m)) for m in range(15)]
        tl = build_timeline(entries, "1m")
        self.assertEqual(tl.spikes, [])

    def test_no_spikes_under_10_buckets(self):
        entries = [entry(T0 + timedelta(minutes=m)) for m in range(9)]
        entries += [entry(T0 + timedelta(minutes=4, seconds=s)) for s in range(1, 50)]
        entries.sort(key=lambda e: e.ts)
        tl = build_timeline(entries, "1m")
        self.assertEqual(len(tl.buckets), 9)
        self.assertIsNone(tl.spikes)
        path = fx.write_lines(self.dir, "app.log",
                              [f"{iso(e.ts)} ERROR boom" for e in entries])
        for fmt in FORMATS:
            _, out, _ = run([path, "--format", fmt])
            self.assertTrue(out)
            self.assertIn("spike detection needs &gt;= 10 buckets" if fmt != "text"
                          else "spike detection needs >= 10 buckets", out)

    def test_bucket_auto(self):
        def pick(span):
            return choose_bucket([entry(T0), entry(T0 + span)])
        self.assertEqual(pick(timedelta(minutes=199)), ("1m", 60))
        self.assertEqual(pick(timedelta(minutes=200)), ("5m", 300))
        self.assertEqual(pick(timedelta(hours=4)), ("5m", 300))
        self.assertEqual(pick(timedelta(hours=40)), ("15m", 900))
        self.assertEqual(pick(timedelta(days=30)), ("6h", 21600))
        self.assertEqual(pick(timedelta(days=400)), ("1d", 86400))
        self.assertEqual(pick(timedelta(days=4000)), ("1d", 86400))

    def test_buckets_aligned_and_untimed_excluded(self):
        start = T0 + timedelta(minutes=7, seconds=13)
        lines = ["ERROR untimed preamble",
                 f"{iso(start)} ERROR a", f"{iso(start + timedelta(hours=3))} WARN b"]
        path = fx.write_lines(self.dir, "app.log", lines)
        result = read_logs([path])
        self.assertEqual(len(result.untimed), 1)
        tl = build_timeline(result.entries)
        self.assertEqual(tl.name, "1m")
        self.assertTrue(tl.buckets)
        self.assertTrue(all(b.start % BUCKETS["1m"] == 0 for b in tl.buckets))
        self.assertEqual(tl.buckets[0].start, int((T0 + timedelta(minutes=7)).timestamp()))
        self.assertEqual(sum(b.errors for b in tl.buckets), 1)
        self.assertEqual(sum(b.warnings for b in tl.buckets), 1)
        tl = build_timeline(result.entries, "1h")
        self.assertEqual([b.start % 3600 for b in tl.buckets], [0] * 4)
        self.assertIn("untimed entries (not on the timeline): 1", render(analyse(result)))


class TestIncidents(ReportTestCase):
    def _log(self):
        lines, t = [], [T0]

        def add(text):
            lines.append(f"{iso(t[0])} {text}")
            t[0] += timedelta(seconds=1)
        for i in range(50):
            add(f"INFO tick {i}")
        add("INFO before-bravo 777")
        add("ERROR bravo failed")
        add("WARN after-bravo 888")
        add("ERROR bravo failed")
        add("ERROR alpha failed")
        add("ERROR alpha failed")
        for i in range(5):
            add("ERROR charlie failed")
        for i in range(9):
            add(f"WARN delta slow {i}")
        add("CRITICAL echo once")
        add("DEBUG foxtrot")
        return fx.write_lines(self.dir, "app.log", lines)

    def test_incident_order_and_context(self):
        result = read_logs([self._log()])
        rep = analyse(result, context=1)
        order = [(i.group.level, i.group.signature) for i in rep.incidents]
        self.assertEqual(order, [
            ("ERROR", "ERROR charlie failed"),
            ("ERROR", "ERROR alpha failed"),
            ("ERROR", "ERROR bravo failed"),
            ("ERROR", "CRITICAL echo once"),
            ("WARNING", "WARN delta slow <n>"),
        ])
        bravo = rep.incidents[2]
        self.assertEqual([(c.level, c.signature) for c in bravo.before],
                         [("INFO", "INFO before-bravo <n>")])
        self.assertEqual([(c.level, c.signature) for c in bravo.after],
                         [("WARNING", "WARN after-bravo <n>")])
        self.assertIsNone(bravo.before[0].message)

        for fmt in FORMATS:
            _, out, _ = run([self._log(), "--context", "1", "--format", fmt])
            self.assertTrue(out)
            self.assertIn("before-bravo", out)
            self.assertNotIn("777", out)
            self.assertNotIn("888", out)
            _, out, _ = run([self._log(), "--context", "1", "--format", fmt, "--raw"])
            self.assertTrue(out)
            self.assertIn("INFO before-bravo 777", out)
            self.assertIn("WARN after-bravo 888", out)

    def test_context_default_zero_and_negative_refused(self):
        result = read_logs([self._log()])
        rep = analyse(result)
        self.assertTrue(rep.incidents)
        self.assertTrue(all(not i.before and not i.after for i in rep.incidents))
        code, _, err = run([self._log(), "--context", "-1"])
        self.assertEqual(code, 2)
        self.assertTrue(err)


class TestHeaderAndSummary(ReportTestCase):
    def test_header_fields_and_summary(self):
        log = fx.write_lines(self.dir, "a.log", [
            "Mar  1 10:00:00 host app ERROR disk 1 full",
            "Mar  1 10:00:05 host app ERROR disk 2 full",
            "Mar  1 10:01:00 host app WARN slow"])
        bad = fx.write_bytes(self.dir, "b.jsonl",
                             b'{"ts": "2026-03-01T10:02:00Z", "level": "warn", "msg": "caf\xe9"}\n'
                             b"not json\n{]\n[1]\nnope\n")
        _, out, _ = run([log, bad, "--year", "2026"])
        self.assertTrue(out)
        for needle in ("lines read: 8\n", "entries: 4\n", "invalid JSONL lines: 4 (first: ",
                       "b.jsonl:2, ", "b.jsonl:4)\n", "replaced non-UTF-8 bytes: 1\n",
                       "sampled: no\n", "syslog year: 2026\n", "bucket: 1m (auto)\n",
                       "raw lines: off\n", "redaction: on\n", " bytes  "):
            self.assertIn(needle, out)
        self.assertIn('3 groups at WARNING or above in 2 files; worst: ERROR '
                      '"host app ERROR disk <n> full" x2 (first 2026-03-01T10:00:00Z, '
                      'last 2026-03-01T10:00:05Z); spike detection needs >= 10 buckets.',
                      out)
        for fmt, row in (("md", "- syslog year: 2026\n"),
                         ("html", "<tr><th>syslog year\n</th><td>2026\n</td></tr>")):
            _, out, _ = run([log, bad, "--year", "2026", "--format", fmt])
            self.assertTrue(out)
            self.assertIn(row, out)
            self.assertIn("ERROR disk &lt;n&gt; full", out)

    def test_no_clock_time_in_output(self):
        log = fx.write_lines(self.dir, "a.log", [f"{iso(T0)} ERROR x"])
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for fmt in FORMATS:
            _, out, _ = run([log, "--format", fmt])
            self.assertTrue(out)
            self.assertNotIn(today, out)
            self.assertIn("syslog year: not used" if fmt != "html" else "not used", out)


if __name__ == "__main__":
    unittest.main()
