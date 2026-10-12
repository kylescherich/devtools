import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone

from loglens.cli import EXIT_INPUT, EXIT_USAGE, main
from loglens.readers import (DEBUG, ERROR, INFO, MAX_CONTINUATION, TRUNCATED_MARKER,
                             UNKNOWN, WARNING, LEVEL_RANK, InputError, UsageError,
                             find_level, read_logs)
from tests import loglens_fixtures as fx


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class ReaderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def read_one(self, lines, name="app.log", **kw):
        path = fx.write_lines(self.dir, name, lines)
        result = read_logs([path], **kw)
        entries = result.entries + result.untimed
        self.assertTrue(entries, "reader returned no entries")
        return result, entries


class TestTextTimestamps(ReaderTestCase):
    def test_iso_variants(self):
        _, entries = self.read_one([
            "2026-03-01T10:00:00Z INFO a",
            "2026-03-01 10:00:01 INFO b",
            "2026-03-01T10:00:02.123456 INFO c",
            "2026-03-01 10:00:03,250 INFO d",
            "2026-03-01T12:00:04+02:00 INFO e",
            "2026-03-01T05:00:05.5-0500 INFO f",
        ])
        self.assertEqual([e.ts for e in entries], [
            utc(2026, 3, 1, 10, 0, 0), utc(2026, 3, 1, 10, 0, 1),
            utc(2026, 3, 1, 10, 0, 2, 123456), utc(2026, 3, 1, 10, 0, 3, 250000),
            utc(2026, 3, 1, 10, 0, 4), utc(2026, 3, 1, 10, 0, 5, 500000)])
        self.assertTrue(all(e.ts.tzinfo == timezone.utc for e in entries))
        self.assertEqual(entries[0].message, "INFO a")

    def test_syslog_uses_year_option(self):
        _, entries = self.read_one(["Mar  7 08:09:10 host sshd[1]: INFO ok"], year=2024)
        self.assertEqual(entries[0].ts, utc(2024, 3, 7, 8, 9, 10))

    def test_syslog_default_year_is_current_utc_year(self):
        _, entries = self.read_one(["Mar 17 08:09:10 host app: ok"])
        self.assertEqual(entries[0].ts.year, datetime.now(timezone.utc).year)

    def test_timestamp_must_be_at_line_start(self):
        result, entries = self.read_one(["host 2026-03-01T10:00:00Z INFO x"])
        self.assertEqual(result.entries, [])
        self.assertEqual(entries[0].ts, None)


class TestLevels(ReaderTestCase):
    def test_level_words_map_to_buckets(self):
        cases = {
            "ERROR": ERROR, "ERR": ERROR, "CRITICAL": ERROR, "FATAL": ERROR,
            "ALERT": ERROR, "EMERG": ERROR, "WARN": WARNING, "WARNING": WARNING,
            "INFO": INFO, "NOTICE": INFO, "DEBUG": DEBUG, "TRACE": DEBUG,
        }
        for word, level in cases.items():
            self.assertEqual(find_level(f"2026-03-01T10:00:00Z [{word}] x"), level, word)

    def test_no_level_is_unknown_below_warning(self):
        self.assertEqual(find_level("2026-03-01T10:00:00Z something happened"), UNKNOWN)
        self.assertLess(LEVEL_RANK[UNKNOWN], LEVEL_RANK[WARNING])

    def test_first_level_word_wins(self):
        self.assertEqual(find_level("2026-03-01T10:00:00Z WARN retry after ERROR"), WARNING)

    def test_level_only_within_first_64_chars(self):
        line = "2026-03-01T10:00:00Z " + "x" * 60 + " ERROR late"
        self.assertEqual(find_level(line), UNKNOWN)

    def test_level_word_must_be_whole_word(self):
        self.assertEqual(find_level("2026-03-01T10:00:00Z INFORMATION ERRORS"), UNKNOWN)

    def test_uppercase_word_counts(self):
        self.assertEqual(find_level("2026-03-01T10:00:00Z worker CRITICAL disk"), ERROR)
        self.assertEqual(find_level("Mar  1 10:00:00 host app NOTICE x"), INFO)

    def test_any_case_in_brackets(self):
        self.assertEqual(find_level("2026-03-01T10:00:00Z [info] started"), INFO)
        self.assertEqual(find_level("2026-03-01T10:00:00Z <Error> failed"), ERROR)
        self.assertEqual(find_level("2026-03-01T10:00:00Z [ warn ] slow"), WARNING)

    def test_any_case_after_level_key(self):
        self.assertEqual(find_level("2026-03-01T10:00:00Z level=error msg=x"), ERROR)
        self.assertEqual(find_level("2026-03-01T10:00:00Z lvl=Warn msg=x"), WARNING)
        self.assertEqual(find_level('2026-03-01T10:00:00Z severity="debug" x'), DEBUG)
        self.assertEqual(find_level("2026-03-01T10:00:00Z severity='fatal' x"), ERROR)

    def test_any_case_with_colon_as_first_word(self):
        self.assertEqual(find_level("2026-03-01T10:00:00Z error: disk full"), ERROR)
        self.assertEqual(find_level("Mar  1 10:00:00 Warning: low memory"), WARNING)
        self.assertEqual(find_level("info: no timestamp here"), INFO)

    def test_colon_word_must_be_first_after_timestamp(self):
        self.assertEqual(find_level("2026-03-01T10:00:00Z job error: x"), UNKNOWN)
        self.assertEqual(find_level("2026-03-01T10:00:00Z error : x"), UNKNOWN)

    def test_plain_lowercase_word_is_not_a_level(self):
        self.assertEqual(find_level("2026-03-01T10:00:00Z security alert raised"), UNKNOWN)
        self.assertEqual(find_level("2026-03-01T10:00:00Z no error"), UNKNOWN)
        self.assertEqual(find_level("2026-03-01T10:00:00Z Error in module"), UNKNOWN)
        self.assertEqual(find_level("2026-03-01T10:00:00Z loglevel=error x"), UNKNOWN)


class TestContinuations(ReaderTestCase):
    def test_lines_without_timestamp_attach_to_previous(self):
        tb = fx.traceback_lines("u1")
        _, entries = self.read_one(["2026-03-01T10:00:00Z ERROR boom", *tb,
                                    "2026-03-01T10:00:01Z INFO next"])
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].continuation_lines, tb)
        self.assertEqual(entries[1].continuation_lines, [])

    def test_continuations_capped_at_200_with_marker(self):
        extra = [f"  detail {i}" for i in range(MAX_CONTINUATION + 50)]
        _, entries = self.read_one(["2026-03-01T10:00:00Z ERROR big", *extra])
        lines = entries[0].continuation_lines
        self.assertEqual(len(lines), MAX_CONTINUATION + 1)
        self.assertEqual(lines[:MAX_CONTINUATION], extra[:MAX_CONTINUATION])
        self.assertEqual(lines[-1], TRUNCATED_MARKER)

    def test_leading_lines_without_timestamp_are_untimed(self):
        result, _ = self.read_one(["no stamp here", "  more", "2026-03-01T10:00:00Z INFO x"])
        self.assertEqual(len(result.untimed), 1)
        self.assertEqual(result.untimed[0].continuation_lines, ["  more"])
        self.assertEqual(len(result.entries), 1)


class TestJsonl(ReaderTestCase):
    def test_default_field_names(self):
        _, entries = self.read_one([
            '{"ts": "2026-03-01T10:00:00Z", "level": "error", "msg": "a"}',
            '{"time": "2026-03-01T10:00:01+01:00", "lvl": "WARN", "message": "b"}',
            '{"timestamp": 1772359202, "severity": "notice", "msg": "c"}',
            '{"@timestamp": "2026-03-01 10:00:03.5", "level": "trace", "msg": "d"}',
        ], name="app.jsonl")
        self.assertEqual([(e.level, e.message) for e in entries],
                         [(WARNING, "b"), (ERROR, "a"), (INFO, "c"), (DEBUG, "d")])
        self.assertEqual(entries[0].ts, utc(2026, 3, 1, 9, 0, 1))
        self.assertEqual(entries[3].ts, utc(2026, 3, 1, 10, 0, 3, 500000))

    def test_field_override(self):
        _, entries = self.read_one(['{"when": "2026-03-01T10:00:00Z", "sev": "FATAL", '
                                    '"text": "hi", "msg": "ignored"}'],
                                   name="app.jsonl",
                                   fields=["ts=when", "level=sev", "message=text"])
        self.assertEqual((entries[0].ts, entries[0].level, entries[0].message),
                         (utc(2026, 3, 1, 10), ERROR, "hi"))

    def test_bad_field_override_is_usage_error(self):
        path = fx.write_jsonl(self.dir, "a.jsonl", [{"msg": "x"}])
        for spec in ("nope=x", "ts", "ts="):
            with self.assertRaises(UsageError, msg=spec):
                read_logs([path], fields=[spec])

    def test_invalid_lines_counted_first_three_kept(self):
        good = '{"ts": "2026-03-01T10:00:00Z", "msg": "ok"}'
        result, _ = self.read_one([good, "{bad", good, "[1,2]", "nope", "", "{x", good],
                                  name="app.jsonl")
        self.assertEqual(result.invalid_json, 4)
        self.assertEqual(list(result.invalid_json_lines.values()), [[2, 4, 5]])
        self.assertEqual(len(result.entries), 3)

    def test_multiline_message_becomes_continuations(self):
        _, entries = self.read_one(['{"ts": "2026-03-01T10:00:00Z", "msg": "a\\nb\\nc"}'],
                                   name="app.jsonl")
        self.assertEqual((entries[0].message, entries[0].continuation_lines), ("a", ["b", "c"]))


class TestFiles(ReaderTestCase):
    def test_gzip_text_and_jsonl(self):
        a = fx.write_gz(self.dir, "a.log.gz", "2026-03-01T10:00:00Z ERROR gz text\n")
        b = fx.write_gz(self.dir, "b.jsonl.gz",
                        '{"ts": "2026-03-01T10:00:01Z", "msg": "gz json"}\n')
        result = read_logs([a, b])
        self.assertTrue(result.entries)
        self.assertEqual([e.message for e in result.entries], ["ERROR gz text", "gz json"])

    def test_non_utf8_replaced_and_counted(self):
        path = fx.write_bytes(self.dir, "bin.log",
                              b"2026-03-01T10:00:00Z INFO caf\xe9\n"
                              b"2026-03-01T10:00:01Z INFO fine\n")
        result = read_logs([path])
        self.assertTrue(result.entries)
        self.assertEqual(result.decode_errors, 1)
        self.assertEqual(result.entries[0].message, "INFO caf\ufffd")

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks")
    def test_symlink_refused(self):
        target = fx.write_lines(self.dir, "real.log", ["2026-03-01T10:00:00Z INFO x"])
        link = fx.symlink_to(self.dir, "link.log", target)
        with self.assertRaises(InputError) as ctx:
            read_logs([link])
        self.assertIn("symlink", str(ctx.exception))

    def test_missing_file_is_input_error(self):
        with self.assertRaises(InputError):
            read_logs([os.path.join(self.dir, "missing.log")])

    def test_max_bytes_exceeded(self):
        path = fx.write_lines(self.dir, "a.log", ["2026-03-01T10:00:00Z INFO x"] * 10)
        with self.assertRaises(InputError):
            read_logs([path], max_bytes=50)

    def test_max_bytes_counts_decompressed_gzip(self):
        path = fx.write_gz(self.dir, "a.log.gz", "2026-03-01T10:00:00Z INFO x\n" * 1000)
        self.assertLess(os.path.getsize(path), 1000)
        with self.assertRaises(InputError):
            read_logs([path], max_bytes=1000)

    def test_max_lines_samples_first_n_across_files(self):
        a = fx.write_lines(self.dir, "a.log", [f"2026-03-01T10:00:0{i}Z INFO a{i}"
                                               for i in range(3)])
        b = fx.write_lines(self.dir, "b.log", ["2026-03-01T09:00:00Z INFO b0"])
        result = read_logs([a, b], max_lines=3)
        self.assertTrue(result.entries)
        self.assertTrue(result.sampled)
        self.assertEqual(result.sampled_note, "sampled: first 3 lines")
        self.assertEqual(result.lines_read, 3)
        self.assertEqual([e.message for e in result.entries], ["INFO a0", "INFO a1", "INFO a2"])

    def test_exactly_max_lines_is_not_sampled(self):
        a = fx.write_lines(self.dir, "a.log", ["2026-03-01T10:00:00Z INFO a"] * 3)
        result = read_logs([a], max_lines=3)
        self.assertTrue(result.entries)
        self.assertFalse(result.sampled)


class TestMerge(ReaderTestCase):
    def test_merge_by_ts_then_file_order_then_line(self):
        a = fx.write_lines(self.dir, "a.log", ["2026-03-01T10:00:02Z INFO a1",
                                               "2026-03-01T10:00:00Z INFO a2",
                                               "2026-03-01T10:00:01Z INFO a3"])
        b = fx.write_lines(self.dir, "b.log", ["2026-03-01T10:00:01Z INFO b1",
                                               "2026-03-01T11:00:00+01:00 INFO b2"])
        result = read_logs([a, b])
        self.assertTrue(result.entries)
        self.assertEqual([e.message for e in result.entries],
                         ["INFO a2", "INFO b2", "INFO a3", "INFO b1", "INFO a1"])

    def test_untimed_kept_counted_and_excluded_from_timeline(self):
        path = fx.write_jsonl(self.dir, "a.jsonl", [
            {"ts": "2026-03-01T10:00:00Z", "msg": "timed"},
            {"msg": "no ts"}, {"ts": "not a date", "msg": "bad ts"}])
        result = read_logs([path])
        self.assertTrue(result.entries)
        self.assertEqual([e.message for e in result.entries], ["timed"])
        self.assertEqual([e.message for e in result.untimed], ["no ts", "bad ts"])


class TestCliReaders(ReaderTestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def test_max_bytes_exit_3(self):
        path = fx.write_lines(self.dir, "a.log", ["2026-03-01T10:00:00Z INFO x"] * 10)
        code, _, err = self.run_cli([path, "--max-bytes", "50"])
        self.assertEqual(code, EXIT_INPUT)
        self.assertTrue(err)
        self.assertIn("--max-bytes", err)

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks")
    def test_symlink_exit_3(self):
        target = fx.write_lines(self.dir, "real.log", ["2026-03-01T10:00:00Z INFO x"])
        code, _, err = self.run_cli([fx.symlink_to(self.dir, "l.log", target)])
        self.assertEqual(code, EXIT_INPUT)
        self.assertTrue(err)

    def test_bad_field_exit_2(self):
        path = fx.write_jsonl(self.dir, "a.jsonl", [{"msg": "x"}])
        code, _, err = self.run_cli([path, "--field", "bogus"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)

    def test_sampled_note_printed(self):
        path = fx.write_lines(self.dir, "a.log", ["2026-03-01T10:00:00Z INFO x"] * 5)
        code, out, _ = self.run_cli([path, "--max-lines", "2"])
        self.assertEqual(code, 0)
        self.assertTrue(out)
        self.assertIn("sampled: first 2 lines", out)


if __name__ == "__main__":
    unittest.main()
