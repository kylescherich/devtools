"""The nine M2a questions as regression cases (BRIEF-DEVWS-037).

Each expected answer is computed in plain Python from the records the
fixture generator wrote (zoneinfo for local time), not with the SQL under
test. Rows are compared exactly.
"""
import unittest
from collections import Counter
from datetime import date, timedelta

from tests.askdata_query_fixtures import (
    PULL_WINDOW, SENTINEL, QueryTestCase, make_arora, make_gate_logs, make_job_scans,
    make_pull_log, title_hash, to_local,
)


def _s(rows):
    return [["" if v is None else str(v) for v in row] for row in rows]


class TestJobQuestions(QueryTestCase):
    def setUp(self):
        super().setUp()
        _, self.rows = make_job_scans(self.src)
        self.jobs = f"jobs={self.src}/*Openings.csv"

    def _ask(self, sql):
        rows = self.query_ok([self.jobs], sql)
        self.assertNotIn(SENTINEL, repr(rows))
        return rows

    def test_q1_openings_per_state_per_week_with_trend(self):
        counts = Counter((r["State"], r["_file_date"]) for r in self.rows)
        want = []
        for state in sorted({s for s, _ in counts}):
            prev = None
            for week in sorted(w for s, w in counts if s == state):
                n = counts[(state, week)]
                if prev is None:
                    delta, trend = None, None
                else:
                    delta = n - prev
                    trend = "up" if n > prev else "down" if n < prev else "same"
                want.append([state, week, n, delta, trend])
                prev = n
        self.assertGreater(len(want), 6)
        rows = self._ask("""
            WITH w AS (SELECT State, _file_date AS week, count(*) AS n
                       FROM jobs GROUP BY State, _file_date)
            SELECT State, week, n, n - lag(n) OVER p AS delta,
                   CASE WHEN lag(n) OVER p IS NULL THEN NULL
                        WHEN n > lag(n) OVER p THEN 'up'
                        WHEN n < lag(n) OVER p THEN 'down' ELSE 'same' END AS trend
            FROM w WINDOW p AS (PARTITION BY State ORDER BY week)
            ORDER BY State, week""")
        self.assertEqual(rows[0], ["State", "week", "n", "delta", "trend"])
        self.assertEqual(rows[1:], _s(want))

    def test_q2_top_two_employers_per_state_last_28_days(self):
        latest = max(date.fromisoformat(r["_file_date"]) for r in self.rows)
        cutoff = latest - timedelta(days=28)
        window = [r for r in self.rows if date.fromisoformat(r["_file_date"]) > cutoff]
        self.assertLess(len(window), len(self.rows))  # the oldest week is outside
        counts = Counter((r["State"], r["Employer"]) for r in window)
        want = []
        for state in sorted({s for s, _ in counts}):
            ranked = sorted(((-n, e) for (s, e), n in counts.items() if s == state))
            want += [[state, e, -negn] for negn, e in ranked[:2]]
        rows = self._ask("""
            WITH win AS (SELECT State, Employer FROM jobs
                         WHERE _file_date > (SELECT date(max(_file_date), '-28 days') FROM jobs)),
                 c AS (SELECT State, Employer, count(*) AS n FROM win GROUP BY State, Employer),
                 r AS (SELECT State, Employer, n,
                              row_number() OVER (PARTITION BY State ORDER BY n DESC, Employer)
                              AS rk FROM c)
            SELECT State, Employer, n FROM r WHERE rk <= 2 ORDER BY State, rk""")
        self.assertEqual(rows[1:], _s(want))

    def test_q3_state_by_setting_and_by_schedule(self):
        for col in ("Setting", "Schedule"):
            with self.subTest(col=col):
                counts = Counter((r["State"], r[col]) for r in self.rows)
                want = [[s, v, n] for (s, v), n in sorted(counts.items())]
                rows = self._ask(f"SELECT State, {col}, count(*) AS n FROM jobs "
                                 f"GROUP BY State, {col} ORDER BY State, {col}")
                self.assertEqual(rows[1:], _s(want))

    def test_q4_stretch_first_last_seen_per_job(self):
        seen = {}
        for r in self.rows:
            key = (r["Employer"], r["City"], title_hash(r["Job Title"]))
            seen.setdefault(key, set()).add(r["_file_date"])
        want = []
        for key in sorted(seen):
            dates = sorted(seen[key])
            days = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
            want.append([*key, dates[0], dates[-1], days, len(dates)])
        self.assertTrue(any(row[6] == 3 for row in want))  # case/padding-normalised
        rows = self._ask("""
            SELECT Employer, City, job_title_h, min(_file_date) AS first_seen,
                   max(_file_date) AS last_seen,
                   CAST(julianday(max(_file_date)) - julianday(min(_file_date)) AS INTEGER)
                       AS days_listed,
                   count(DISTINCT _file_date) AS weeks_listed
            FROM jobs GROUP BY Employer, City, job_title_h
            ORDER BY Employer, City, job_title_h""")
        self.assertEqual(rows[1:], _s(want))


class TestGateQuestions(QueryTestCase):
    def setUp(self):
        super().setUp()
        _, self.events = make_gate_logs(self.src)
        self.gate = f"gate={self.src}/decisions-gate-*.jsonl.gz"
        for ev in self.events:
            ev["local"] = to_local(ev["ts"])

    def _ask(self, sql):
        rows = self.query_ok([self.gate], sql)
        self.assertNotIn(SENTINEL, repr(rows))
        return rows

    def test_q5_requests_per_principal_per_local_day(self):
        shifted = [ev for ev in self.events if ev["local"].date().isoformat() != ev["ts"][:10]]
        self.assertTrue(shifted)  # some UTC events are the previous day in Chicago
        counts = Counter((ev["principal"], ev["local"].date().isoformat()) for ev in self.events)
        want = [[p, d, n] for (p, d), n in sorted(counts.items())]
        rows = self._ask("SELECT principal, date(tz(ts)) AS day, count(*) AS n FROM gate "
                         "GROUP BY principal, day ORDER BY principal, day")
        self.assertEqual(rows[1:], _s(want))
        self.assertIn("2026-10-04", {r[1] for r in rows[1:]})

    def test_q5_busiest_local_weekday_hour(self):
        counts = Counter((ev["local"].isoweekday() % 7, ev["local"].hour) for ev in self.events)
        (wd, hour), n = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        rows = self._ask("SELECT CAST(strftime('%w', tz(ts)) AS INTEGER) AS wd, "
                         "CAST(strftime('%H', tz(ts)) AS INTEGER) AS hour, count(*) AS n "
                         "FROM gate GROUP BY wd, hour ORDER BY n DESC, wd, hour LIMIT 1")
        self.assertEqual(rows[1:], _s([[wd, hour, n]]))

    def test_q6_denies_by_rule_principal_local_week(self):
        denies = [ev for ev in self.events if ev["decision"] == "deny"]
        self.assertTrue(denies)
        counts = Counter((ev["rule_id"], ev["principal"], ev["local"].strftime("%Y-W%W"))
                         for ev in denies)
        self.assertGreater(len({k[2] for k in counts}), 1)  # spans a local week boundary
        want = [[r, p, w, n] for (r, p, w), n in sorted(counts.items())]
        rows = self._ask("SELECT rule_id, principal, strftime('%Y-W%W', tz(ts)) AS week, "
                         "count(*) AS n FROM gate WHERE decision = 'deny' "
                         "GROUP BY rule_id, principal, week ORDER BY rule_id, principal, week")
        self.assertEqual(rows[1:], _s(want))


class TestAroraQuestions(QueryTestCase):
    def setUp(self):
        super().setUp()
        self.path, data = make_arora(self.src)
        self.site = {u["url_id"]: u["site"] for u in data["urls"]}
        self.records = data["records"]

    def _ask(self, sql):
        rows = self.query_ok([f"arora={self.path}"], sql)
        self.assertNotIn(SENTINEL, repr(rows))
        return rows

    def test_q7_records_per_site_per_local_month(self):
        counts = Counter()
        for r in self.records:
            pub = r["published_at"]
            month = pub[:7] if len(pub) == 10 else to_local(pub).strftime("%Y-%m")
            counts[(self.site[r["url_id"]], month)] += 1
        want = [[s, m, n] for (s, m), n in sorted(counts.items())]
        rows = self._ask("""
            SELECT u.site, strftime('%Y-%m', tz(r.published_at)) AS month, count(*) AS n
            FROM arora.records r JOIN arora.urls u ON r.url_id = u.url_id
            GROUP BY u.site, month ORDER BY u.site, month""")
        self.assertEqual(rows[1:], _s(want))
        self.assertEqual(sum(int(r[2]) for r in rows[1:]), len(self.records))

    def test_q8_weekday_hour_per_site_second_precision(self):
        counts = Counter()
        for r in self.records:
            if r["published_precision"] != "second":
                continue
            local = to_local(r["published_at"])
            counts[(self.site[r["url_id"]], local.isoweekday() % 7, local.hour)] += 1
        want = [[s, wd, h, n] for (s, wd, h), n in sorted(counts.items())]
        rows = self._ask("""
            SELECT u.site, CAST(strftime('%w', tz(r.published_at)) AS INTEGER) AS wd,
                   CAST(strftime('%H', tz(r.published_at)) AS INTEGER) AS hour, count(*) AS n
            FROM arora.records r JOIN arora.urls u ON r.url_id = u.url_id
            WHERE r.published_precision = 'second'
            GROUP BY u.site, wd, hour ORDER BY u.site, wd, hour""")
        self.assertEqual(rows[1:], _s(want))


class TestPullLogQuestion(QueryTestCase):
    def test_q9_pull_outcome_per_local_day(self):
        path, events = make_pull_log(self.src)
        ok, failed = Counter(), Counter()
        for dt, kind in events:
            day = to_local(dt.strftime("%Y-%m-%dT%H:%M:%SZ")).date().isoformat()
            (failed if kind == "failed" else ok)[day] += 1
        first, last = (date.fromisoformat(d) for d in PULL_WINDOW)
        want = []
        d = first
        while d <= last:
            k = d.isoformat()
            want.append([k, ok[k], failed[k], int(ok[k] + failed[k] == 0)])
            d += timedelta(days=1)
        self.assertEqual(len(want), 14)
        self.assertTrue(any(r[3] for r in want) and any(r[2] for r in want))
        rows = self.query_ok([path], f"""
            WITH RECURSIVE cal(d) AS (
                SELECT '{PULL_WINDOW[0]}'
                UNION ALL SELECT date(d, '+1 day') FROM cal WHERE d < '{PULL_WINDOW[1]}'),
            ev AS (
                SELECT date(tz(regexp_extract(line,
                           '^(\\d{{4}}-\\d{{2}}-\\d{{2}} \\d{{2}}:\\d{{2}}:\\d{{2}}Z)', 1))) AS d,
                       line REGEXP ' (pulled arora|already have arora)' AS ok,
                       line REGEXP ' FAILED: ' AS bad
                FROM pull)
            SELECT cal.d AS day, coalesce(sum(ev.ok), 0) AS succeeded,
                   coalesce(sum(ev.bad), 0) AS failed,
                   CASE WHEN coalesce(sum(ev.ok), 0) + coalesce(sum(ev.bad), 0) = 0
                        THEN 1 ELSE 0 END AS missing
            FROM cal LEFT JOIN ev ON ev.d = cal.d
            GROUP BY cal.d ORDER BY cal.d""")
        self.assertEqual(rows[0], ["day", "succeeded", "failed", "missing"])
        self.assertEqual(rows[1:], _s(want))


if __name__ == "__main__":
    unittest.main()
