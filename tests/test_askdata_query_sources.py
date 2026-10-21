"""askdata query: sources, loaders, SQL functions, formats and exit codes."""
import gzip
import json
import os
import unittest
from datetime import date

from tests.askdata_query_fixtures import (
    QueryTestCase, csv_rows, make_governance_log, make_log, make_pull_log, run_cli,
)


class TestLoaders(QueryTestCase):
    def test_csv_types_and_file_columns(self):
        (self.src / "2026-09-07 sales.csv").write_text(
            "item,qty,price,code\nx,2,1.5,007\ny,3,2.25,010\nz,,0.5,99\n", encoding="utf-8")
        rows = self.query_ok([f"{self.src}/2026-09-07 sales.csv"],
                             "SELECT sum(qty), sum(price), count(qty), typeof(min(code)), "
                             "max(_file), max(_file_date) FROM \"2026_09_07_sales\"")
        self.assertEqual(rows[1], ["5", "4.25", "2", "text", "2026-09-07 sales.csv",
                                   "2026-09-07"])

    def test_tsv_and_semicolon_and_cp1252(self):
        (self.src / "t.tsv").write_text("a\tb\n1\t2\n3\t4\n", encoding="utf-8")
        (self.src / "s.csv").write_text("a;b\n1;2\n3;4\n5;6\n", encoding="utf-8")
        (self.src / "w.csv").write_bytes(b"name,n\ncaf\xe9,1\nbar,2\n")
        rows = self.query_ok([self.src / "t.tsv", self.src / "s.csv", self.src / "w.csv"],
                             "SELECT (SELECT sum(b) FROM t), (SELECT sum(b) FROM s), "
                             "(SELECT sum(n) FROM w), (SELECT count(*) FROM w "
                             "WHERE name = 'caf' || char(233))")
        self.assertEqual(rows[1], ["6", "12", "3", "1"])

    def test_jsonl_scalars_objects_and_bad_lines(self):
        lines = [json.dumps({"s": "a", "i": 1, "f": 1.5, "b": True, "n": None,
                             "o": {"k": 1}, "l": [1, 2]}),
                 "not json BADLINEMARK", "[1, 2]", "",
                 json.dumps({"s": "b", "i": 2, "f": 2.5, "b": False, "n": None,
                             "o": {}, "l": []})]
        (self.src / "ev.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, out, err = self.query([self.src / "ev.jsonl"],
                                    "SELECT s, i, f, b, typeof(b), typeof(n), o, l "
                                    "FROM ev ORDER BY i", "--rows", "--format", "csv")
        self.assertTrue(out, err)
        self.assertEqual(code, 0, err)
        rows = csv_rows(out)
        self.assertEqual(rows[1], ["a", "1", "1.5", "1", "integer", "null", '{"k":1}', "[1,2]"])
        self.assertEqual(rows[2], ["b", "2", "2.5", "0", "integer", "null", "{}", "[]"])
        self.assertIn("2 unparsable lines", err)
        self.assertNotIn("BADLINEMARK", out + err)

    def test_jsonl_gz_and_glob_one_table(self):
        for day in ("2026-10-01", "2026-10-02"):
            with gzip.open(self.src / f"d-{day}.jsonl.gz", "wt", encoding="utf-8") as f:
                f.write(json.dumps({"k": day}) + "\n" + json.dumps({"k": day}) + "\n")
        rows = self.query_ok([f"d={self.src}/d-*.jsonl.gz"],
                             "SELECT _file_date, count(*) FROM d GROUP BY 1 ORDER BY 1")
        self.assertEqual(rows[1:], [["2026-10-01", "2"], ["2026-10-02", "2"]])

    def test_default_name_for_jsonl_gz(self):
        with gzip.open(self.src / "Decisions-Gate-2026-10-01.jsonl.gz", "wt") as f:
            f.write('{"a": 1}\n')
        rows = self.query_ok([self.src / "Decisions-Gate-2026-10-01.jsonl.gz"],
                             "SELECT sum(a) FROM decisions_gate_2026_10_01")
        self.assertEqual(rows[1], ["1"])

    def test_log_and_txt_lines(self):
        make_log(self.src, "app.log", ["first", "second", "third"])
        make_log(self.src, "notes.txt", ["a,b", "c,d"])
        rows = self.query_ok([self.src / "app.log", self.src / "notes.txt"],
                             "SELECT line_no, line, _file FROM app UNION ALL "
                             "SELECT line_no, line, _file FROM notes ORDER BY 3, 1", "--rows")
        self.assertEqual(rows[1:], [["1", "first", "app.log"], ["2", "second", "app.log"],
                                    ["3", "third", "app.log"], ["1", "a,b", "notes.txt"],
                                    ["2", "c,d", "notes.txt"]])

    def test_file_date_null_without_date(self):
        make_log(self.src, "plain.log", ["x"])
        rows = self.query_ok([self.src / "plain.log"],
                             "SELECT count(*), count(_file_date) FROM plain")
        self.assertEqual(rows[1], ["1", "0"])

    def test_sqlite_unprofiled_rows_allowed(self):
        import sqlite3
        db = self.src / "small.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t(name TEXT, n INTEGER)")
        conn.execute("INSERT INTO t VALUES ('alpha', 1), ('beta', 2)")
        conn.commit()
        conn.close()
        rows = self.query_ok([db], "SELECT name, n FROM small.t ORDER BY n", "--rows")
        self.assertEqual(rows[1:], [["alpha", "1"], ["beta", "2"]])


class TestFunctions(QueryTestCase):
    def setUp(self):
        super().setUp()
        self.pull, self.events = make_pull_log(self.src)

    def test_regexp_and_regexp_extract(self):
        first = date(2026, 8, 31)
        seconds = [300 + (dt.date() - first).days for dt, k in self.events if k == "failed"]
        self.assertTrue(seconds)
        rows = self.query_ok([self.pull],
                             "SELECT count(*), sum(regexp_extract(line, 'after (\\d+) s', 1)) "
                             "FROM pull WHERE line REGEXP 'FAILED: sftp'")
        self.assertEqual(rows[1], [str(len(seconds)), str(sum(seconds))])
        rows = self.query_ok([self.pull], "SELECT regexp_extract('ab12', '(\\d)(\\d)', 2), "
                                          "regexp_extract('ab', '\\d', 0), "
                                          "regexp_extract(NULL, 'x', 0)")
        self.assertEqual(rows[1], ["2", "", ""])

    def test_tz_in_sql(self):
        rows = self.query_ok([self.pull],
                             "SELECT tz('2026-07-04T17:00:00Z'), tz('2026-01-15 18:00:00+00:00'), "
                             "tz('2026-07-04 17:00:00'), tz('2026-07-04'), "
                             "tz('2026-07-04T17:00:00Z', 'Europe/Berlin')")
        self.assertEqual(rows[1], ["2026-07-04 12:00:00", "2026-01-15 12:00:00",
                                   "2026-07-04 17:00:00", "2026-07-04", "2026-07-04 19:00:00"])

    def test_tz_unparsable_warns(self):
        code, out, err = self.query([self.pull], "SELECT count(tz('soon')) FROM pull")
        self.assertTrue(out, err)
        self.assertEqual(code, 0, err)
        self.assertIn("tz", err)

    def test_governance_naive_time_unchanged(self):
        gov, events = make_governance_log(self.src)
        want = {}
        for ts, what in events:
            day = ts[:10]
            ok, fail = want.get(day, (0, 0))
            want[day] = (ok + (what == "OK"), fail + (what == "FAIL"))
        rows = self.query_ok([gov], "SELECT date(tz(substr(line, 1, 19))) AS d, "
                                    "sum(line REGEXP ' OK ') AS ok, sum(line REGEXP ' FAIL: ') "
                                    "AS fail FROM governance_backup GROUP BY d ORDER BY d")
        self.assertEqual(rows[1:], [[d, str(o), str(f)] for d, (o, f) in sorted(want.items())])


class TestCliBehaviour(QueryTestCase):
    def setUp(self):
        super().setUp()
        self.log = make_log(self.src, "app.log", ["alpha", "beta|pipe", "esc\x1b[31mred"])

    def test_format_table_md_csv(self):
        sql = "SELECT line_no, line FROM app ORDER BY line_no"
        code, out, err = self.query([self.log], sql, "--rows")
        self.assertTrue(out, err)
        self.assertEqual(code, 0)
        self.assertIn("line_no", out.splitlines()[0])
        self.assertIn("beta|pipe", out)
        self.assertIn("\\x1b", out)
        self.assertNotIn("\x1b", out)
        code, out, err = self.query([self.log], sql, "--rows", "--format", "md")
        self.assertTrue(out, err)
        self.assertEqual(out.splitlines()[0], "| line_no | line |")
        self.assertEqual(out.splitlines()[1], "| --- | --- |")
        self.assertIn("beta\\|pipe", out)
        self.assertNotIn("\x1b", out)
        code, out, err = self.query([self.log], sql, "--rows", "--format", "csv")
        self.assertTrue(out, err)
        self.assertEqual(csv_rows(out)[2], ["2", "beta|pipe"])
        self.assertNotIn("\x1b", out)

    def test_null_shown_in_table(self):
        code, out, err = self.query([self.log], "SELECT NULL AS z, 1 AS one")
        self.assertTrue(out, err)
        self.assertEqual(code, 0)
        self.assertIn("NULL", out)

    def test_usage_errors(self):
        cases = [
            ([f"x={self.src}/*.log"], "SELECT 1", ["--format", "xml"]),
            ([f"{self.src}/*.log"], "SELECT 1", []),
            ([self.log, f"app={self.log}"], "SELECT 1", []),
            ([f"main={self.log}"], "SELECT 1", []),
            ([self.log], "SELECT 1", ["--max-rows", "0"]),
        ]
        for sources, sql, extra in cases:
            with self.subTest(sources=sources, extra=extra):
                code, out, err = self.query(sources, sql, *extra)
                self.assert_refused(code, out, err, 2)

    def test_input_errors(self):
        (self.src / "data.parquet").write_bytes(b"PAR1")
        link = self.src / "link.log"
        os.symlink(self.log, link)
        cases = [[self.src / "missing.log"], [f"x={self.src}/none-*.log"],
                 [self.src / "data.parquet"], [link]]
        for sources in cases:
            with self.subTest(sources=sources):
                code, out, err = self.query(sources, "SELECT 1")
                self.assert_refused(code, out, err, 3)

    def test_timeout(self):
        code, out, err = self.query([self.log], "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL "
                                                "SELECT x + 1 FROM c) SELECT count(*) FROM c",
                                    "--timeout", "0.3")
        self.assert_refused(code, out, err, 3)
        self.assertIn("timed out", err)

    def test_help_lists_options(self):
        code, out, err = run_cli(["query", "--help"])
        self.assertTrue(out)
        self.assertEqual(code, 0)
        for opt in ("--sql", "--format", "--frozen", "--rows", "--max-rows", "--timeout"):
            self.assertIn(opt, out)
        self.assertIn("exit", out.lower())


if __name__ == "__main__":
    unittest.main()
