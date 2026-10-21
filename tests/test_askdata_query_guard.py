"""askdata query output guard (DEC-427): deny-list in the authorizer and the
aggregate-only default. Every test asserts non-empty output first (DEC-071)."""
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from askdata import profiles, query
from tests.askdata_query_fixtures import (
    ARORA_DENIED, GATE_DENIED, GATE_KEYS, JOB_DENIED, JOB_HEADER, SENTINEL,
    QueryTestCase, make_arora, make_gate_logs, make_job_scans, make_log, make_pull_log,
    title_hash,
)


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _shapes(table, col):
    """Query shapes that would read table.col; each is aggregate-shaped so that
    only the deny-list (not the aggregate rule) can refuse it."""
    c = _q(col)
    return {
        "select_star": f"SELECT count(*) FROM (SELECT * FROM {table})",
        "select_col": f"SELECT {c}, count(*) FROM {table} GROUP BY 1",
        "length": f"SELECT sum(length({c})) FROM {table}",
        "cte": f"WITH x AS (SELECT {c} AS v FROM {table}) SELECT count(v) FROM x",
        "subquery": f"SELECT count(*) FROM (SELECT {c} FROM {table}) s",
        "join": f"SELECT count(*) FROM {table} a JOIN {table} b ON a.{c} = b.{c}",
        "union": f"SELECT count(*) FROM (SELECT {c} AS v FROM {table} UNION SELECT 'x')",
        "where_like": f"SELECT count(*) FROM {table} WHERE {c} LIKE 'T%'",
    }


class TestDenyList(QueryTestCase):
    def setUp(self):
        super().setUp()
        self.arora, _ = make_arora(self.src)
        make_job_scans(self.src)
        make_gate_logs(self.src)
        self.jobs = f"jobs={self.src}/*Openings.csv"
        self.gate = f"gate={self.src}/decisions-gate-*.jsonl.gz"

    def _assert_denied(self, sources, table, col):
        for shape, sql in _shapes(table, col).items():
            with self.subTest(table=table, column=col, shape=shape):
                code, out, err = self.query(sources, sql)
                self.assert_refused(code, out, err, 2)
                self.assertIn("prohibited", err)
                if shape != "select_star":
                    # SELECT * stops at the table's first denied column.
                    self.assertIn(col.split()[0], err)

    def test_arora_denied_columns_every_shape(self):
        for table, col in ARORA_DENIED:
            self._assert_denied([f"arora={self.arora}"], f"arora.{table}", col)

    def test_job_scan_denied_columns_every_shape(self):
        for col in JOB_DENIED:
            self._assert_denied([self.jobs], "jobs", col)

    def test_gate_denied_columns_every_shape(self):
        for col in GATE_DENIED:
            self._assert_denied([self.gate], "gate", col)

    def test_unqualified_table_name_still_denied(self):
        code, out, err = self.query([f"arora={self.arora}"], "SELECT count(title) FROM records")
        self.assert_refused(code, out, err, 2)
        self.assertIn("records.title", err)

    def test_allowed_columns_work(self):
        rows = self.query_ok([f"arora={self.arora}"],
                             "SELECT site, count(*) FROM arora.urls GROUP BY site ORDER BY site")
        self.assertEqual(rows[0], ["site", "count(*)"])
        self.assertEqual([r[0] for r in rows[1:]], ["site-a", "site-b", "site-c", "site-d"])

    def test_job_title_h_allowed(self):
        paths = sorted(self.src.glob("*Openings.csv"))
        self.assertTrue(paths)
        rows = self.query_ok([self.jobs], "SELECT job_title_h, count(*) AS n FROM jobs "
                                          "GROUP BY job_title_h ORDER BY job_title_h")
        hashes = [r[0] for r in rows[1:]]
        self.assertIn(title_hash(f"Staff Pharmacist JT{SENTINEL}-00"), hashes)
        self.assertTrue(all(len(h) == 12 for h in hashes))
        self.assertNotIn(SENTINEL, "".join(sum(rows, [])))

    def test_unlisted_text_column_in_profile_is_denied(self):
        extra, _ = make_arora(self.src, name="extra.db",
                              extra_sql=["CREATE TABLE notes(note_id INTEGER, body TEXT)"])
        code, out, err = self.query([f"x={extra}"], "SELECT count(body) FROM x.notes")
        self.assert_refused(code, out, err, 2)
        self.assertIn("notes.body", err)
        rows = self.query_ok([f"x={extra}"], "SELECT count(note_id) FROM x.notes")
        self.assertEqual(rows[1], ["0"])

    def test_raw_page_and_statement_tables_refused(self):
        for sql in ["SELECT sum(payload) FROM dbstat", "SELECT count(sql) FROM sqlite_stmt",
                    "SELECT count(*) FROM pragma_table_info('records', 'arora')"]:
            with self.subTest(sql=sql):
                code, out, err = self.query([f"arora={self.arora}"], sql)
                self.assert_refused(code, out, err, 2)


class TestClassification(unittest.TestCase):
    """Every TEXT/BLOB column of each fixture schema is classified explicitly."""

    def _unclassified(self, profile, columns):
        return [(t, c) for t, c in columns if profiles.explicit(profile, t, c) is None]

    def _arora_text_columns(self, path):
        uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            cols = []
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            for t in tables:
                for _, name, decl, *_ in conn.execute(f"PRAGMA table_info({_q(t)})"):
                    if not any(k in decl.upper() for k in ("INT", "REAL", "FLOA", "DOUB", "NUM")):
                        cols.append((t, name))
            return cols
        finally:
            conn.close()

    def test_arora_fixture_fully_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = make_arora(tmp)
            cols = self._arora_text_columns(path)
            self.assertGreater(len(cols), 20)
            self.assertEqual(self._unclassified("arora_v2", cols), [])
            # The check is real: a new unclassified column is reported.
            path2, _ = make_arora(tmp, name="more.db",
                                  extra_sql=["ALTER TABLE urls ADD COLUMN lang TEXT"])
            self.assertEqual(self._unclassified("arora_v2", self._arora_text_columns(path2)),
                             [("urls", "lang")])
            self.assertTrue(Path(path2).exists())

    def test_loaded_profiles_fully_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_job_scans(tmp)
            make_gate_logs(tmp)
            session = query.open_sources([f"jobs={tmp}/*Openings.csv",
                                          f"gate={tmp}/decisions-gate-*.jsonl.gz"])
            self.assertIsNotNone(session)
            try:
                jobs = session.columns[("main", "jobs")]
                gate = session.columns[("main", "gate")]
                self.assertEqual(jobs, JOB_HEADER + ["_file", "_file_date", "job_title_h"])
                self.assertEqual(gate, GATE_KEYS + ["_file", "_file_date"])
                self.assertEqual(self._unclassified("job_scan", [("*", c) for c in jobs]), [])
                self.assertEqual(self._unclassified("gate_decisions", [("*", c) for c in gate]),
                                 [])
                self.assertEqual(self._unclassified("job_scan", [("*", "Extra")]),
                                 [("*", "Extra")])
            finally:
                session.close()

    def test_profile_verdicts_match_brief(self):
        arora_allow = {("failures", "error"), ("failures", "at"), ("images", "site"),
                       ("images", "content_type"), ("images", "fetched_at"),
                       ("records", "fetched_at"), ("records", "published_at"),
                       ("records", "published_precision"), ("refetch_queue", "due_at"),
                       ("refetch_queue", "reason"), ("runs", "kind"), ("runs", "started_at"),
                       ("runs", "finished_at"), ("sightings", "fetched_at"),
                       ("transcripts", "source"), ("urls", "site"),
                       ("urls", "first_seen_at"), ("urls", "last_seen_at")}
        for t, c in arora_allow:
            self.assertEqual(profiles.explicit("arora_v2", t, c), "allow", (t, c))
        for t, c in ARORA_DENIED:
            self.assertEqual(profiles.explicit("arora_v2", t, c), "deny", (t, c))
        for c in ["State", "City", "Employer", "Setting", "Schedule", "Posted", "Status",
                  "job_title_h"]:
            self.assertEqual(profiles.explicit("job_scan", "*", c), "allow", c)
        for c in JOB_DENIED:
            self.assertEqual(profiles.explicit("job_scan", "*", c), "deny", c)
        for c in GATE_KEYS:
            want = "deny" if c in GATE_DENIED else "allow"
            self.assertEqual(profiles.explicit("gate_decisions", "*", c), want, c)


class TestAggregateRule(QueryTestCase):
    def setUp(self):
        super().setUp()
        self.arora, _ = make_arora(self.src)
        self.pull, _ = make_pull_log(self.src)

    def test_text_rows_refused_group_by_allowed(self):
        code, out, err = self.query([self.pull], "SELECT line FROM pull")
        self.assert_refused(code, out, err, 1)
        self.assertIn("result is not aggregated; use GROUP BY/aggregates, or --rows", err)
        rows = self.query_ok([self.pull], "SELECT substr(line, 21, 7) AS w, count(*) AS n "
                                          "FROM pull GROUP BY w ORDER BY w")
        self.assertEqual(rows[0], ["w", "n"])
        self.assertGreater(len(rows), 2)

    def test_allowed_text_column_without_aggregate_refused(self):
        code, out, err = self.query([f"arora={self.arora}"], "SELECT site FROM arora.urls")
        self.assert_refused(code, out, err, 1)
        rows = self.query_ok([f"arora={self.arora}"],
                             "SELECT site, count(*) FROM arora.urls GROUP BY site")
        self.assertEqual(len(rows), 5)

    def test_numeric_and_bucket_results_pass_without_aggregate(self):
        rows = self.query_ok([f"arora={self.arora}"],
                             "SELECT url_id, date(first_seen_at) AS d, NULL AS z, 1.5 AS f "
                             "FROM arora.urls ORDER BY url_id")
        self.assertEqual(rows[1], ["1", "2026-01-01", "", "1.5"])
        rows = self.query_ok([self.pull], "SELECT DISTINCT '2026-W36' AS wk, "
                                          "'2026-09-01 12' AS h FROM pull")
        self.assertEqual(rows[1], ["2026-W36", "2026-09-01 12"])

    def test_bucket_regex_is_anchored(self):
        code, out, err = self.query([self.pull], "SELECT DISTINCT '2026-09-01 x' FROM pull")
        self.assert_refused(code, out, err, 1)

    def test_row_cap(self):
        log = make_log(self.src, "big.log", [f"line {i}" for i in range(600)])
        code, out, err = self.query([log], "SELECT line_no FROM big")
        self.assert_refused(code, out, err, 1)
        self.assertIn("600 rows", err)
        self.assertIn("--max-rows 500", err)
        code, out, err = self.query([log], "SELECT line_no FROM big", "--max-rows", "10")
        self.assert_refused(code, out, err, 1)
        self.assertIn("600 rows", err)
        rows = self.query_ok([log], "SELECT line_no FROM big", "--max-rows", "600")
        self.assertEqual(len(rows), 601)

    def test_rows_with_profiled_source_refused(self):
        code, out, err = self.query([f"arora={self.arora}"],
                                    "SELECT count(*) FROM arora.urls", "--rows")
        self.assert_refused(code, out, err, 2)
        self.assertIn("arora_v2", err)
        make_job_scans(self.src)
        code, out, err = self.query([f"jobs={self.src}/*Openings.csv"],
                                    "SELECT count(*) FROM jobs", "--rows")
        self.assert_refused(code, out, err, 2)
        code, out, err = self.query([self.pull, f"arora={self.arora}"],
                                    "SELECT line FROM pull", "--rows")
        self.assert_refused(code, out, err, 2)

    def test_rows_with_only_a_log_prints_rows(self):
        rows = self.query_ok([self.pull], "SELECT line_no, line FROM pull "
                                          "WHERE line REGEXP 'FAILED' ORDER BY line_no", "--rows")
        self.assertEqual(rows[0], ["line_no", "line"])
        self.assertGreater(len(rows), 1)
        self.assertTrue(all("FAILED: sftp get" in r[1] for r in rows[1:]))


class TestStatementsRefused(QueryTestCase):
    def setUp(self):
        super().setUp()
        self.arora, _ = make_arora(self.src)
        self.pull, _ = make_pull_log(self.src)

    def test_non_select_refused(self):
        other = self.root / "other.db"
        for sql in ["PRAGMA table_info(pull)", "PRAGMA query_only=0",
                    f"ATTACH DATABASE '{other}' AS o", "CREATE TEMP VIEW v AS SELECT 1",
                    "CREATE TEMP TABLE t(a)", "INSERT INTO pull VALUES (1, 'x', 'f', NULL)",
                    "DELETE FROM arora.urls", "UPDATE arora.urls SET site = 'x'",
                    "SELECT load_extension('x')", "DETACH DATABASE arora",
                    "SELECT 1; SELECT 2", "SELEC 1"]:
            with self.subTest(sql=sql):
                code, out, err = self.query([self.pull, f"arora={self.arora}"], sql)
                self.assert_refused(code, out, err, 2)
        self.assertFalse(other.exists())

    def test_attached_source_is_opened_read_only(self):
        session = query.open_sources([f"arora={self.arora}"])
        self.assertIsNotNone(session)
        try:
            # Take away the authorizer and query_only: mode=ro must still hold.
            session.conn.set_authorizer(None)
            session.conn.execute("PRAGMA query_only=0")
            with self.assertRaises(sqlite3.OperationalError) as cm:
                session.conn.execute("INSERT INTO arora.urls(url_id, site, url, slug, "
                                     "first_seen_at, last_seen_at) VALUES (99,'a','b','c','d','e')")
            self.assertIn("readonly", str(cm.exception))
        finally:
            session.close()

    def test_source_unchanged_and_nothing_created(self):
        before = sorted(p.name for p in self.src.iterdir())
        self.assertTrue(before)
        rows = self.query_ok([self.pull, f"arora={self.arora}"],
                             "SELECT count(*) FROM arora.records")
        self.assertEqual(rows[1], ["60"])
        self.assertEqual(sorted(p.name for p in self.src.iterdir()), before)


class TestInputShapes(QueryTestCase):
    def test_mixed_shapes_in_one_glob(self):
        (self.src / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        (self.src / "b.csv").write_text("x,z\n1,2\n", encoding="utf-8")
        code, out, err = self.query([f"t={self.src}/*.csv"], "SELECT count(*) FROM t")
        self.assert_refused(code, out, err, 3)
        self.assertIn("different shapes", err)

    def test_mixed_kinds_in_one_glob(self):
        (self.src / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        make_log(self.src, "a.log", ["one"])
        code, out, err = self.query([f"t={self.src}/a.*"], "SELECT count(*) FROM t")
        self.assert_refused(code, out, err, 3)

    def test_wal_without_shm_refused_and_frozen_works(self):
        path, _ = make_arora(self.src, name="wal.db", wal=True)
        self.assertEqual(path.read_bytes()[18:20], b"\x02\x02")
        self.assertFalse((self.src / "wal.db-shm").exists())
        code, out, err = self.query([f"w={path}"], "SELECT count(*) FROM w.urls")
        self.assert_refused(code, out, err, 3)
        self.assertIn("--frozen", err)
        rows = self.query_ok([f"w={path}"], "SELECT count(*) FROM w.urls", "--frozen")
        self.assertEqual(rows[1], ["16"])

    def test_profile_detected_with_extra_column(self):
        header = JOB_HEADER + ["Extra"]
        row = ["OK", "City 1", "Employer A", f"JT{SENTINEL}", "chain", "PRN", "x", "y", "z",
               "w", "Open", "v", f"EXTRA{SENTINEL}"]
        (self.src / "2026-09-01 jobs.csv").write_text(
            ",".join(header) + "\n" + ",".join(row) + "\n", encoding="utf-8")
        code, out, err = self.query([f"j={self.src}/*jobs.csv"],
                                    "SELECT count(*) FROM j", "--rows")
        self.assert_refused(code, out, err, 2)
        code, out, err = self.query([f"j={self.src}/*jobs.csv"],
                                    "SELECT count(Extra) FROM j")
        self.assert_refused(code, out, err, 2)


if __name__ == "__main__":
    unittest.main()
