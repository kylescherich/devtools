"""askdata query tz(): ISO input forms, local output, and the built-in US
Central fallback checked against zoneinfo (DEC-427)."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from askdata import sqlfuncs

CHICAGO = ZoneInfo("America/Chicago")


class TestTz(unittest.TestCase):
    def setUp(self):
        self.f = sqlfuncs.SqlFunctions()

    def test_input_forms(self):
        cases = {
            "2026-07-04T17:00:00Z": "2026-07-04 12:00:00",
            "2026-07-04 17:00:00Z": "2026-07-04 12:00:00",
            "2026-07-04T17:00:00+00:00": "2026-07-04 12:00:00",
            "2026-01-15T18:00:00.123456+00:00": "2026-01-15 12:00:00",
            "2026-01-15 18:00:00.5Z": "2026-01-15 12:00:00",
            "2026-01-15T20:00:00+02:00": "2026-01-15 12:00:00",
            "2026-01-15T13:00:00-05:00": "2026-01-15 12:00:00",
            "2026-01-15T18:00Z": "2026-01-15 12:00:00",
            "2026-10-06T02:30:00Z": "2026-10-05 21:30:00",
        }
        for ts, want in cases.items():
            with self.subTest(ts=ts):
                got = self.f.tz(ts)
                self.assertTrue(got)
                self.assertEqual(got, want)

    def test_other_zone(self):
        got = self.f.tz("2026-07-04T17:00:00Z", "Europe/Berlin")
        self.assertTrue(got)
        self.assertEqual(got, "2026-07-04 19:00:00")

    def test_naive_and_date_only_unchanged(self):
        for ts in ("2026-07-04 17:00:00", "2026-07-04T17:00:00", "2026-07-04"):
            with self.subTest(ts=ts):
                got = self.f.tz(ts)
                self.assertTrue(got)
                self.assertEqual(got, ts)

    def test_null_and_unparsable(self):
        self.assertIsNone(self.f.tz(None))
        self.assertIsNone(self.f.tz("soon"))
        self.assertIsNone(self.f.tz("2026-13-40T99:00:00Z"))
        self.assertEqual(self.f.counts["tz_unparsable"], 2)

    def test_fallback_used_when_tz_database_missing(self):
        with mock.patch("askdata.sqlfuncs.ZoneInfo", side_effect=ZoneInfoNotFoundError("x")):
            f = sqlfuncs.SqlFunctions()
            cases = {
                "2026-03-08T07:59:59Z": "2026-03-08 01:59:59",
                "2026-03-08T08:00:00Z": "2026-03-08 03:00:00",
                "2026-11-01T06:59:59Z": "2026-11-01 01:59:59",
                "2026-11-01T07:00:00Z": "2026-11-01 01:00:00",
                "2026-10-06T02:30:00Z": "2026-10-05 21:30:00",
            }
            for ts, want in cases.items():
                with self.subTest(ts=ts):
                    got = f.tz(ts)
                    self.assertTrue(got)
                    self.assertEqual(got, want)
            self.assertIsNone(f.tz("2006-06-01T00:00:00Z"))
            self.assertEqual(f.counts["tz_before_2007"], 1)
            self.assertIn("before 2007", " ".join(f.warnings()))


class TestCentralFallback(unittest.TestCase):
    def test_every_hour_2007_to_2030_matches_zoneinfo(self):
        start = datetime(2007, 1, 1, 6, tzinfo=timezone.utc)
        end = datetime(2031, 1, 1, 6, tzinfo=timezone.utc)
        u = start
        checked = 0
        mismatches = []
        while u < end:
            want = u.astimezone(CHICAGO).replace(tzinfo=None)
            got = sqlfuncs.central_fallback(u)
            if got != want:
                mismatches.append((u.isoformat(), got, want))
            checked += 1
            u += timedelta(hours=1)
        self.assertGreater(checked, 24 * 365 * 24)
        self.assertEqual(mismatches[:5], [])

    def test_every_quarter_hour_around_each_transition(self):
        # Both transitions fall on day 1-14 of March/November.
        checked = 0
        for year in range(2007, 2031):
            for month in (3, 11):
                day = datetime(year, month, 1, tzinfo=timezone.utc)
                for minute in range(0, 15 * 1440, 15):
                    u = day + timedelta(minutes=minute)
                    want = u.astimezone(CHICAGO).replace(tzinfo=None)
                    self.assertEqual(sqlfuncs.central_fallback(u), want, u.isoformat())
                    checked += 1
        self.assertGreater(checked, 0)

    def test_before_2007_is_none(self):
        self.assertIsNone(sqlfuncs.central_fallback(datetime(2006, 12, 31, 12,
                                                             tzinfo=timezone.utc)))
        self.assertIsNone(sqlfuncs.central_fallback(datetime(2007, 1, 1, 5, 59,
                                                             tzinfo=timezone.utc)))
        got = sqlfuncs.central_fallback(datetime(2007, 1, 1, 6, tzinfo=timezone.utc))
        self.assertIsNotNone(got)
        self.assertEqual(got, datetime(2007, 1, 1, 0, 0))


if __name__ == "__main__":
    unittest.main()
