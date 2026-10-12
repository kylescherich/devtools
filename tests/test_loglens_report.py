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

    def test_backslash_unambiguous(self):
        # one line: a typed backslash-x1b, then a real ESC
        path = fx.write_lines(self.dir, "app.log",
                              [f"{iso(T0)} ERROR lit \\x1b real \x1b end"])
        self.assertIn(b"lit \\x1b real \x1b end", path.read_bytes())
        # (format, --raw example, default signature); md escapes the doubled
        # pair once more, so it renders as two backslashes.
        cases = (("text", r"lit \\x1b real \x1b end", r"lit \\x<n>b real \x1b end"),
                 ("md", r"lit \\\\x1b real \x1b end",
                  r"lit \\\\x&lt;n&gt;b real \x1b end"))
        for fmt, example, signature in cases:
            with self.subTest(fmt=fmt):
                _, out, _ = run([path, "--format", fmt, "--raw"])
                self.assertTrue(out.strip(), "report is empty")
                self.assertNotIn("\x1b", out)                  # no real ESC
                self.assertIn(example, out)
                _, out, _ = run([path, "--format", fmt])
                self.assertTrue(out.strip(), "report is empty")
                self.assertIn(signature, out)
        _, out, _ = run([path, "--format", "html", "--raw"])
        self.assertTrue(out.strip(), "report is empty")
        self.assertIn(r"lit \x1b real \x1b end", out)        # HTML unchanged

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


class TestTimelineBounds(ReportTestCase):
    def test_bucket_cap_exit_2(self):
        lines = [f"{iso(T0 + timedelta(hours=h))} ERROR job failed" for h in range(73)]
        path = fx.write_lines(self.dir, "app.log", lines)       # 3-day span
        code, out, err = run([path, "--bucket", "1m"])
        self.assertTrue(err.strip(), "no error message")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--bucket 1m gives 4321 buckets (limit 2000)", err)
        self.assertIn("use --bucket 5m or larger", err)
        code, out, _ = run([path, "--bucket", "5m"])
        self.assertTrue(out.strip())
        self.assertEqual(code, 4)
        self.assertIn("bucket: 5m\n", out)
        _, out, _ = run([path])
        self.assertTrue(out.strip())
        self.assertIn("bucket: 1h (auto)\n", out)              # auto stays <= 200
        code, out, err = run(["--help"])
        self.assertTrue(out.strip())
        self.assertIn("2000", out)

    def test_outlier_timestamp_excluded(self):
        lines = ["1970-01-01T00:00:00Z ERROR job 0 failed"]
        lines += [f"{iso(T0 + timedelta(minutes=15 * i))} ERROR job {i} failed"
                  for i in range(1, 97)]                        # 1-day span
        path = fx.write_lines(self.dir, "app.log", lines)
        result = read_logs([path])
        tl = build_timeline(result.entries)
        self.assertEqual(tl.name, "15m")
        self.assertEqual([e.ts.year for e in tl.outliers], [1970])
        self.assertEqual(tl.buckets[0].start, int((T0 + timedelta(minutes=15)).timestamp()))
        self.assertEqual(sum(b.errors for b in tl.buckets), 96)
        self.assertEqual(analyse(result).groups[0].count, 97)
        warning = ("1 entry with outlying timestamps (first 1970-01-01T00:00:00Z)")
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                _, out, _ = run([path, "--format", fmt])
                self.assertTrue(out.strip(), "report is empty")
                self.assertIn(warning, out)
                self.assertIn("15m (auto)", out)
                self.assertIn("x97", out)                       # group count includes it
                self.assertNotIn("1970-01-01T00:00:00Z  E", out)  # not a bucket
                low = out.lower()
                self.assertLess(low.index("summary"), low.index("warnings"))
                self.assertLess(low.index("warnings"), low.index("groups ("))

        clean = fx.write_lines(self.dir, "clean.log", lines[1:])
        for fmt in FORMATS:
            _, out, _ = run([clean, "--format", fmt])
            self.assertTrue(out.strip())
            self.assertNotIn("outlying", out)
            section = out[out.lower().index("warnings"):out.lower().index("groups (")]
            self.assertIn("none", section)

    def test_empty_bucket_runs_collapsed(self):
        minutes = [0, 1, 12, 13, 18]          # 10 empty (2..11), then 4 empty (14..17)
        lines = [f"{iso(T0 + timedelta(minutes=m))} ERROR boom" for m in minutes]
        path = fx.write_lines(self.dir, "app.log", lines)
        tl = build_timeline(read_logs([path]).entries, "1m")
        self.assertEqual(len(tl.buckets), 19)                   # spikes see all buckets
        empty_row = "E      0  W      0"
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                _, out, _ = run([path, "--format", fmt, "--bucket", "1m"])
                self.assertTrue(out.strip(), "report is empty")
                self.assertEqual(out.count("... 10 empty buckets ..."), 1)
                self.assertNotIn("... 4 empty buckets ...", out)
                if fmt == "html":
                    self.assertIn('<tr><td colspan="3">... 10 empty buckets ...</td></tr>',
                                  out)
                    self.assertEqual(out.count('<td class="n">0</td><td class="n">0</td>'), 4)
                else:
                    self.assertEqual(out.count(empty_row), 4)
                    self.assertIn(f"{iso(T0 + timedelta(minutes=1))}  E      1", out)
                    self.assertIn(f"{iso(T0 + timedelta(minutes=14))}  {empty_row}", out)
                self.assertIn("threshold", out)                 # 19 >= 10 buckets


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
        # WARNING+ only, count desc; ties by first occurrence (bravo before
        # alpha, after-bravo before echo), INFO tick and DEBUG never listed.
        self.assertEqual(order, [
            ("WARNING", "WARN delta slow <n>"),
            ("ERROR", "ERROR charlie failed"),
            ("ERROR", "ERROR bravo failed"),
            ("ERROR", "ERROR alpha failed"),
            ("WARNING", "WARN after-bravo <n>"),
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

    @staticmethod
    def _incident_section(out, fmt):
        start = out.lower().index("incident view (top 5)")
        end = {"text": "\n\n", "md": "\n\n## ", "html": "</ol>"}[fmt]
        stop = out.find(end, start + 25)
        return out[start:stop if stop != -1 else len(out)]

    @staticmethod
    def _incident_count(section, fmt):
        if fmt == "html":
            return section.count("<li><p>")
        return len(re.findall(r"(?m)^\d+\. ", section))

    def test_incident_view_warning_plus_only(self):
        lines, t = [], [T0]

        def add(text, times=1):
            for _ in range(times):
                lines.append(f"{iso(t[0])} {text}")
                t[0] += timedelta(seconds=1)
        add("INFO request served", 300)
        add("DEBUG cache probe", 200)
        add("ERROR upstream timeout", 4)
        add("INFO heartbeat ok", 150)
        add("WARN queue backlog", 2)
        add("CRITICAL disk failure")
        add("DEBUG gc pass", 120)
        path = fx.write_lines(self.dir, "app.log", lines)
        rep = analyse(read_logs([path]))
        self.assertEqual([(i.group.level, i.group.signature, i.group.count)
                          for i in rep.incidents],
                         [("ERROR", "ERROR upstream timeout", 4),
                          ("WARNING", "WARN queue backlog", 2),
                          ("ERROR", "CRITICAL disk failure", 1)])
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                _, out, _ = run([path, "--format", fmt])
                self.assertTrue(out.strip(), "report is empty")
                section = self._incident_section(out, fmt)
                self.assertTrue(section.strip())
                self.assertEqual(self._incident_count(section, fmt), 3, section)
                for quiet in ("request served", "cache probe", "heartbeat ok", "gc pass"):
                    self.assertNotIn(quiet, section)
                    self.assertIn(quiet, out)            # still in the groups table
                self.assertIn("upstream timeout", section)
                self.assertIn("disk failure", section)
                self.assertNotIn("no warning or error groups", section)

        quiet = fx.write_lines(self.dir, "quiet.log",
                               [f"{iso(T0)} INFO fine", f"{iso(T0)} DEBUG probe"])
        self.assertEqual(analyse(read_logs([quiet])).incidents, [])
        for fmt in FORMATS:
            with self.subTest(fmt=fmt, case="none"):
                _, out, _ = run([quiet, "--format", fmt])
                self.assertTrue(out.strip(), "report is empty")
                section = self._incident_section(out, fmt)
                self.assertIn("no warning or error groups", section)
                self.assertEqual(self._incident_count(section, fmt), 0)
                self.assertNotIn("fine", section)

    def test_context_default_zero_and_negative_refused(self):
        result = read_logs([self._log()])
        rep = analyse(result)
        self.assertTrue(rep.incidents)
        self.assertTrue(all(not i.before and not i.after for i in rep.incidents))
        code, _, err = run([self._log(), "--context", "-1"])
        self.assertEqual(code, 2)
        self.assertTrue(err)


class TestSyslogOrigin(ReportTestCase):
    def test_syslog_hosts_merge_into_one_group(self):
        lines = ["Mar  1 10:00:00 web1 nginx[123]: ERROR upstream timed out",
                 "Mar  1 10:00:05 web2 nginx[456]: ERROR upstream timed out",
                 "Mar  1 10:00:09 web2 nginx: ERROR upstream timed out"]
        lines += [f"Mar  1 10:01:0{i} db{i} postgres[9{i}]: WARN slow query"
                  for i in (4, 1, 3, 2)]
        lines.append("Mar  1 10:02:00 myhost ERROR: not a header")
        path = fx.write_lines(self.dir, "syslog", lines)
        result = read_logs([path], year=2026)
        first = result.entries[0]
        self.assertEqual((first.host, first.app, first.message),
                         ("web1", "nginx", "ERROR upstream timed out"))
        self.assertEqual((result.entries[-1].host, result.entries[-1].message),
                         (None, "myhost ERROR: not a header"))
        groups = {g.signature: g for g in analyse(result).groups}
        upstream = groups["ERROR upstream timed out"]
        self.assertEqual(upstream.count, 3)
        self.assertEqual((upstream.hosts, upstream.apps), ({"web1", "web2"}, {"nginx"}))
        self.assertEqual(groups["WARN slow query"].count, 4)
        self.assertEqual(len([s for s in groups if "upstream" in s]), 1)
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                _, out, _ = run([path, "--year", "2026", "--format", fmt])
                self.assertTrue(out.strip(), "report is empty")
                incident = out[out.lower().index("incident view"):]
                self.assertIn("hosts: 2 (web1, web2)  apps: 1 (nginx)", incident)
                self.assertIn("hosts: 4 (db1, db2, db3)  apps: 1 (postgres)", incident)
                self.assertNotIn("db4", out)
                self.assertNotIn("web1 nginx", out)

    def test_syslog_origin_names_redacted(self):
        path = fx.write_lines(self.dir, "syslog", [
            "Mar  1 10:00:00 token=SNThostTok app: ERROR boom"])
        for fmt in FORMATS:
            _, out, _ = run([path, "--year", "2026", "--format", fmt])
            self.assertTrue(out.strip(), "report is empty")
            self.assertIn("hosts: 1 (token=", out)
            self.assertNotIn("SNThostTok", out)


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
