import contextlib
import io
import tempfile
import unittest

from loglens.cli import EXIT_ERROR, main
from loglens.group import group_entries, normalise, traceback_signature
from loglens.readers import INFO, read_logs
from loglens.redact import REDACTED, redact
from loglens.report import analyse, render
from tests import loglens_fixtures as fx

TS = "2026-03-01T10:00:{:02d}Z"

# One sentinel per redaction pattern. Each case has a distinct letter word so
# it lands in its own group and its --raw example is rendered.
SECRET_CASES = [
    ("alpha", "auth with Bearer SNTbearerAlpha.x-y", "SNTbearerAlpha"),
    ("bravo", "header Authorization: Basic SNTauthBravo==", "SNTauthBravo"),
    ("charlie", "login password=SNTpwCharlie ok", "SNTpwCharlie"),
    ("delta", "login passwd=SNTpwdDelta ok", "SNTpwdDelta"),
    ("echo", "vault secret=SNTsecretEcho ok", "SNTsecretEcho"),
    ("foxtrot", "session token=SNTtokenFoxtrot ok", "SNTtokenFoxtrot"),
    ("golf", "client api_key=SNTapikeyGolf ok", "SNTapikeyGolf"),
    ("hotel", "config password: SNTcolonHotel ok", "SNTcolonHotel"),
    ("india", 'payload {"api_key": "SNT quoted India"} sent', "SNT quoted India"),
    ("juliet", "payload {'token': 'SNTsingleJuliet'} sent", "SNTsingleJuliet"),
    ("kilo", "push with ghp_SNTghpKilo0123456789abcd", "SNTghpKilo"),
    ("lima", "push with github_pat_SNTpatLima_0123456789", "SNTpatLima"),
    ("mike", "model key sk-SNTskMike0123456789", "SNTskMike"),
    ("november", "aws key AKIASNTNOVEMBER01234 used", "SNTNOVEMBER"),
    ("oscar", "cookie eyJSNTjwtOscar.eyJzdWIiOiJ4In0.c2lnbmF0dXJl", "SNTjwtOscar"),
    ("papa", "inline -----BEGIN PRIVATE KEY-----SNTpemPapa-----END PRIVATE KEY-----",
     "SNTpemPapa"),
    ("quebec", "card 4111111111111111 charged", "4111111111111111"),
    ("romeo", "card 4000 1234 5678 9010 charged", "4000 1234 5678 9010"),
    ("sierra", "card 5500-0000-0000-0004 charged", "5500-0000-0000-0004"),
]
PEM_SPLIT = ["-----BEGIN RSA PRIVATE KEY-----", "SNTpemSplitTango0123",
             "SNTpemSplitTango4567", "-----END RSA PRIVATE KEY-----"]


class GroupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()


class TestNormalise(unittest.TestCase):
    def test_placeholders(self):
        cases = {
            "GET https://api.example.test/v1/users?id=5 done": "GET <url> done",
            "mail to ann.lee+x@example.test sent": "mail to <email> sent",
            "id 123e4567-e89b-12d3-a456-426614174000 x": "id <uuid> x",
            "from 10.1.2.3 and fe80::1 and 2001:db8::8a2e:370:7334": "from <ip> and <ip> and <ip>",
            "open /var/log/app/x.log failed": "open <path> failed",
            "open C:\\Users\\svc\\app.ini failed": "open <path> failed",
            "commit 9f3a7c21e0b4 merged": "commit <hex> merged",
            "user 'ann' said \"hi there\"": "user <str> said <str>",
            "took 12.5 ms after 3 tries": "took <n> ms after <n> tries",
        }
        for message, expected in cases.items():
            self.assertEqual(normalise(message), expected, message)

    def test_non_ip_and_short_tokens_untouched(self):
        self.assertEqual(normalise("at 10:00:01 see /health and deadbeefcafe"),
                         "at <n>:<n>:<n> see /health and deadbeefcafe")
        self.assertEqual(normalise("can't stop, won't stop"), "can't stop, won't stop")

    def test_redaction_runs_before_normalising(self):
        entry_file = None
        with tempfile.TemporaryDirectory() as d:
            entry_file = fx.write_lines(d, "a.log", [
                f"{TS.format(0)} ERROR card 4111111111111111 declined"])
            groups = group_entries(read_logs([entry_file]).entries)
        self.assertTrue(groups)
        # Redacted first: the card becomes <redacted>, not <n>.
        self.assertEqual(groups[0].signature, f"ERROR card {REDACTED} declined")


class TestGrouping(GroupTestCase):
    def test_grouping_numbers_normalised(self):
        path = fx.write_lines(self.dir, "app.log", [
            f"{TS.format(1)} WARN user 42 retry 3 from 10.0.0.1 req 7f3a9c2e1b",
            f"{TS.format(2)} INFO started worker",
            f"{TS.format(3)} WARN user 98765 retry 11 from 192.168.20.7 req 0a1b2c3d4e5f",
            f"{TS.format(4)} WARN user 7 retry 1 from 2001:db8::1 req abcdef0123",
        ])
        groups = group_entries(read_logs([path]).entries)
        self.assertTrue(groups)
        self.assertEqual(len(groups), 2)
        top = groups[0]
        self.assertEqual(top.signature, "WARN user <n> retry <n> from <ip> req <hex>")
        self.assertEqual(top.count, 3)
        self.assertEqual((top.first_ts.second, top.last_ts.second), (1, 4))
        self.assertEqual(top.files, [str(path)])
        self.assertEqual(top.examples, [])          # only kept for --raw
        self.assertEqual((groups[1].level, groups[1].count), (INFO, 1))

    def test_level_is_part_of_key(self):
        path = fx.write_lines(self.dir, "app.log", [f"{TS.format(1)} ERROR disk 1 full",
                                                    f"{TS.format(2)} disk 2 full"])
        groups = group_entries(read_logs([path]).entries)
        self.assertEqual(len(groups), 2)

    def test_examples_capped_at_two(self):
        path = fx.write_lines(self.dir, "app.log",
                              [f"{TS.format(i)} INFO tick {i}" for i in range(5)])
        groups = group_entries(read_logs([path]).entries, keep_examples=True)
        self.assertTrue(groups)
        self.assertEqual(groups[0].examples, ["INFO tick 0", "INFO tick 1"])

    def test_groups_span_files_and_untimed(self):
        a = fx.write_lines(self.dir, "a.log", [f"{TS.format(1)} INFO hello 1"])
        b = fx.write_jsonl(self.dir, "b.jsonl", [{"level": "info", "msg": "INFO hello 2"}])
        result = read_logs([a, b])
        groups = group_entries(result.entries + result.untimed)
        self.assertTrue(groups)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].files, [str(a), str(b)])
        self.assertEqual(groups[0].first_ts, groups[0].last_ts)


class TestTraceback(GroupTestCase):
    def test_traceback_continuations_grouped(self):
        lines = []
        for i, value in enumerate(["u-17", "u-90210", "admin"]):
            lines += [f"{TS.format(i)} ERROR request {i * 7} failed for {value}",
                      *fx.traceback_lines(value)]
        lines += [f"{TS.format(9)} ERROR request 5 failed",
                  *fx.traceback_lines("x", func="save_user", line=88)]
        path = fx.write_lines(self.dir, "app.log", lines)
        entries = read_logs([path]).entries
        self.assertEqual(len(entries), 4)
        # Continuations are attached to their entry, not separate entries.
        self.assertEqual(entries[0].continuation_lines, fx.traceback_lines("u-17"))
        groups = group_entries(entries)
        self.assertTrue(groups)
        self.assertEqual([(g.signature, g.count) for g in groups], [
            ("KeyError at users.py:42 in load_user", 3),
            ("KeyError at users.py:88 in save_user", 1)])

    def test_signature_uses_last_frame_and_final_exception(self):
        lines = ["Traceback (most recent call last):",
                 '  File "C:\\app\\a.py", line 1, in outer',
                 "ValueError: first",
                 "",
                 "During handling of the above exception, another exception occurred:",
                 "",
                 "Traceback (most recent call last):",
                 '  File "C:\\app\\b.py", line 7, in inner',
                 "    raise RuntimeError(x)",
                 "    ^^^^^^^^^^^^^^^^^^^^^",
                 "app.errors.RuntimeError: second"]
        self.assertEqual(traceback_signature(lines),
                         "app.errors.RuntimeError at b.py:7 in inner")

    def test_no_traceback_uses_normalised_message(self):
        self.assertIsNone(traceback_signature(["  just detail 1", "  more"]))


class TestRedaction(GroupTestCase):
    def _write_secret_log(self):
        lines = []
        for i, (word, text, _) in enumerate(SECRET_CASES):
            lines.append(f"{TS.format(i)} ERROR {word} {text}")
        lines += [f"{TS.format(40)} ERROR tango key dump follows", *PEM_SPLIT,
                  f"{TS.format(41)} INFO uniform CANARYvisible ok"]
        return fx.write_lines(self.dir, "secrets.log", lines)

    def _sentinels(self):
        return [s for _, _, s in SECRET_CASES] + ["SNTpemSplitTango"]

    def test_redaction_all_patterns(self):
        path = self._write_secret_log()
        source = path.read_text(encoding="utf-8")
        for sentinel in self._sentinels():          # the test data really holds them
            self.assertIn(sentinel, source)

        result = read_logs([path])
        for raw in (False, True):
            text = render(analyse(result, raw=raw))
            self.assertTrue(text.strip(), "rendered report is empty")
            for sentinel in self._sentinels():
                self.assertNotIn(sentinel, text, f"raw={raw}: {sentinel} leaked")
            if raw:
                # Every case's example was rendered, so the check above bites.
                self.assertIn("CANARYvisible", text)
                for word, _, _ in SECRET_CASES:
                    self.assertIn(f"{word} ", text)
                self.assertIn(REDACTED, text)
                self.assertIn("tango key dump follows", text)

    def test_redaction_all_patterns_via_cli(self):
        path = self._write_secret_log()
        for extra in ([], ["--raw"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main([str(path), *extra])
            text = out.getvalue()
            self.assertEqual(code, EXIT_ERROR)
            self.assertTrue(text.strip(), "CLI output is empty")
            for sentinel in self._sentinels():
                self.assertNotIn(sentinel, text, f"{extra}: {sentinel} leaked")

    def test_redact_keeps_labels_and_is_idempotent(self):
        text = 'token=abc123 "password": "p w" Authorization: Bearer xyz'
        once = redact(text)
        self.assertTrue(once)
        self.assertEqual(once, f'token={REDACTED} "password": "{REDACTED}" '
                               f'Authorization: {REDACTED}')
        self.assertEqual(redact(once), once)

    def test_unterminated_pem_redacted_to_end(self):
        self.assertEqual(redact("x -----BEGIN PRIVATE KEY-----\nSNTabc"), f"x {REDACTED}")

    def test_ordinary_text_untouched(self):
        text = "user 42 logged in from 10.0.0.1 after 1234 ms; tokens: 5"
        self.assertEqual(redact(text), text)


class TestDefaultOutput(GroupTestCase):
    def test_default_output_has_no_raw_line(self):
        raw_line = f"{TS.format(1)} ERROR payment 31337 failed for order 99"
        path = fx.write_lines(self.dir, "app.log", [raw_line])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([str(path)])
        text = out.getvalue()
        self.assertEqual(code, EXIT_ERROR)
        self.assertTrue(text.strip())
        self.assertIn("ERROR payment <n> failed for order <n>", text)
        self.assertNotIn(raw_line, text)
        self.assertNotIn("31337", text)
        self.assertNotIn("example:", text)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main([str(path), "--raw"])
        self.assertIn("ERROR payment 31337 failed for order 99", out.getvalue())


if __name__ == "__main__":
    unittest.main()
