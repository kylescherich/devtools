"""Synthetic fixtures for askdata query tests (BRIEF-DEVWS-037, M2a).

Only the shapes listed in the brief are copied; every value is invented and
every file is generated at test time into a temp dir. The generators also
return the records they wrote, so tests compute expected answers in plain
Python, independently of the SQL under test.

Every value of a denied column contains "SENTINEL", so a leak check is a
substring check.
"""
import contextlib
import csv
import gzip
import hashlib
import io
import json
import random
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
SENTINEL = "SENTINEL"
SITES = ["site-a", "site-b", "site-c", "site-d"]

ARORA_SCHEMA = [
    "CREATE TABLE failures(run_id INTEGER, url TEXT NOT NULL, http_status INTEGER,"
    " error TEXT NOT NULL, at TEXT NOT NULL)",
    "CREATE TABLE images(image_id INTEGER PRIMARY KEY, url TEXT, site TEXT, sha256 TEXT,"
    " content_type TEXT, bytes_z BLOB, fetched_at TEXT, first_record_id INTEGER)",
    "CREATE TABLE records(record_id INTEGER PRIMARY KEY, url_id INTEGER NOT NULL,"
    " fetched_at TEXT, published_at TEXT, published_precision TEXT, title TEXT,"
    " content_hash TEXT, html_z BLOB, text_z BLOB, http_status INTEGER,"
    " content_length INTEGER)",
    "CREATE TABLE refetch_queue(url_id INTEGER, due_at TEXT, reason TEXT, attempts INTEGER)",
    "CREATE TABLE runs(run_id INTEGER PRIMARY KEY, kind TEXT, started_at TEXT,"
    " finished_at TEXT, summary_json TEXT, ok INTEGER)",
    "CREATE TABLE sightings(url_id INTEGER, record_id INTEGER, fetched_at TEXT)",
    "CREATE TABLE transcripts(transcript_id INTEGER PRIMARY KEY, url_id INTEGER,"
    " record_id INTEGER, vimeo_id TEXT, source TEXT, text TEXT)",
    "CREATE TABLE urls(url_id INTEGER PRIMARY KEY, site TEXT NOT NULL, url TEXT NOT NULL,"
    " slug TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,"
    " last_listing_page INTEGER)",
]

# (table, column) of every denied arora column, with a query-able table name.
ARORA_DENIED = [
    ("records", "title"), ("records", "content_hash"), ("records", "html_z"),
    ("records", "text_z"), ("urls", "url"), ("urls", "slug"),
    ("runs", "summary_json"), ("images", "url"), ("images", "sha256"),
    ("images", "bytes_z"), ("transcripts", "vimeo_id"), ("transcripts", "text"),
    ("failures", "url"),
]

JOB_HEADER = ["State", "City", "Employer", "Job Title", "Setting", "Schedule", "Pay",
              "Posted", "Notes", "Apply link", "Status", "My Notes"]
JOB_DENIED = ["Job Title", "Pay", "Notes", "Apply link", "My Notes"]
JOB_WEEKS = ["2026-08-24", "2026-09-14", "2026-09-21"]
SETTINGS = ["chain", "grocery", "independent", "tribal/IHS", "health-system"]
SCHEDULES = ["Full-time", "Part-time", "PRN"]

GATE_KEYS = ["action", "decision", "mode", "principal", "reason", "resource",
             "rule_id", "span_id", "surface", "trace_id", "ts"]
GATE_DENIED = ["span_id", "trace_id"]
GATE_DAYS = ["2026-10-05", "2026-10-06", "2026-10-07"]
PRINCIPALS = ["p-one", "p-two", "p-three", "p-four"]
ACTIONS = ["read", "write", "exec", "list", "fetch", "delete", "search"]

PULL_WINDOW = ("2026-09-01", "2026-09-14")


def to_local(ts):
    """Independent tz() for expectations: ISO UTC -> Chicago local datetime."""
    text = ts.replace("Z", "+00:00").replace(" ", "T", 1)
    return datetime.fromisoformat(text).astimezone(CHICAGO).replace(tzinfo=None)


def title_hash(title):
    return hashlib.sha256(title.strip().casefold().encode("utf-8")).hexdigest()[:12]


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


# ---------------------------------------------------------------- arora_v2

def make_arora(directory, name="arora.db", wal=False, extra_sql=()):
    """Arora v2 shaped SQLite file. Returns (path, {"urls": [...], "records": [...]})."""
    path = Path(directory) / name
    rng = random.Random(3701)
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    for stmt in ARORA_SCHEMA:
        conn.execute(stmt)
    urls = []
    for i in range(1, 17):
        site = SITES[i % 4]
        urls.append({"url_id": i, "site": site})
        conn.execute("INSERT INTO urls VALUES (?,?,?,?,?,?,?)",
                     (i, site, f"https://{site}.example.invalid/URL{SENTINEL}-{i}",
                      f"SLUG{SENTINEL}-{i}", "2026-01-01T00:00:00+00:00",
                      "2026-06-30T00:00:00+00:00", i % 3))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    published = []
    for _ in range(56):
        dt = start + timedelta(seconds=rng.randrange(181 * 86400))
        published.append((dt, "second" if rng.random() < 0.6 else "day"))
    # Edge cases: a UTC month start that is still the previous month in
    # Chicago, both sides of the 2026 DST start, a day-precision month start.
    published += [
        (datetime(2026, 4, 1, 3, 0, tzinfo=timezone.utc), "second"),
        (datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc), "second"),
        (datetime(2026, 3, 8, 8, 30, tzinfo=timezone.utc), "second"),
        (datetime(2026, 5, 1, 2, 0, tzinfo=timezone.utc), "day"),
    ]
    records = []
    for rid, (dt, precision) in enumerate(published, 1):
        url_id = rng.randint(1, 16)
        pub = _iso(dt) if precision == "second" else dt.strftime("%Y-%m-%d")
        title = f"TITLE{SENTINEL}-{rid:03d} breaking story"
        records.append({"record_id": rid, "url_id": url_id, "published_at": pub,
                        "published_precision": precision})
        conn.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, url_id, _iso(dt + timedelta(hours=1)), pub, precision, title,
                      f"HASH{SENTINEL}{rid}", b"zhtml" + bytes([rid]), b"ztext" + bytes([rid]),
                      200, 1000 + rid))
    conn.execute("INSERT INTO runs VALUES (1,'pull','2026-06-01T00:00:00+00:00',"
                 "'2026-06-01T00:10:00+00:00',?,1)", (json.dumps({"note": f"RUN{SENTINEL}"}),))
    conn.execute("INSERT INTO failures VALUES (1,?,503,'timeout','2026-06-01T00:05:00+00:00')",
                 (f"https://site-a.example.invalid/FAIL{SENTINEL}",))
    conn.execute("INSERT INTO images VALUES (1,?,'site-a',?,'image/png',?,"
                 "'2026-06-01T00:00:00+00:00',1)",
                 (f"https://site-a.example.invalid/IMG{SENTINEL}.png", f"SHA{SENTINEL}",
                  b"zimg"))
    conn.execute("INSERT INTO refetch_queue VALUES (1,'2026-07-01T00:00:00+00:00','stale',2)")
    conn.execute("INSERT INTO sightings VALUES (1,1,'2026-06-01T00:00:00+00:00')")
    conn.execute("INSERT INTO transcripts VALUES (1,1,1,?,'vimeo',?)",
                 (f"VIMEO{SENTINEL}", f"TRANSCRIPT{SENTINEL} words"))
    for stmt in extra_sql:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    return path, {"urls": urls, "records": records}


# ---------------------------------------------------------------- job_scan

def job_scan_name(week):
    return f"{week} Retail_Pharmacist_Jobs_OK_NM_OR - Openings.csv"


def make_job_scans(directory, weeks=JOB_WEEKS):
    """Weekly job-scan CSVs. Returns (paths, rows) with rows carrying _file_date."""
    rng = random.Random(431)
    pool = []
    for j in range(18):
        pool.append({
            "State": ["OK", "NM", "OR"][j % 3],
            "City": f"City {j % 5 + 1}",
            "Employer": f"Employer {'ABCDE'[rng.randrange(5)]}",
            "Job Title": f"Staff Pharmacist JT{SENTINEL}-{j:02d}",
            "Setting": SETTINGS[rng.randrange(5)],
            "Schedule": SCHEDULES[rng.randrange(3)],
        })
    paths, rows = [], []
    for w, week in enumerate(weeks):
        path = Path(directory) / job_scan_name(week)
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(JOB_HEADER)
            for j, job in enumerate(pool):
                if rng.random() >= 0.7:
                    continue
                title = job["Job Title"]
                if w == 1:
                    # Same job, other case and padding: job_title_h must match.
                    title = "  " + title.upper() + " "
                row = dict(job)
                row.update({
                    "Job Title": title,
                    "Pay": f"${60 + j}/hr PAY{SENTINEL}",
                    "Posted": week,
                    "Notes": f"NOTES{SENTINEL} {j}",
                    "Apply link": f"https://jobs.example.invalid/APPLY{SENTINEL}-{j}",
                    "Status": ["Open", "Applied", "Closed"][(j + w) % 3],
                    "My Notes": f"MYNOTES{SENTINEL}",
                })
                writer.writerow([row[h] for h in JOB_HEADER])
                row["_file_date"] = week
                rows.append(row)
        paths.append(path)
    return paths, rows


# ---------------------------------------------------------------- gate_decisions

def gate_name(day):
    return f"decisions-gate-{day}.jsonl.gz"


def make_gate_logs(directory, days=GATE_DAYS):
    """Daily gate decision logs (.jsonl.gz). Returns (paths, events)."""
    rng = random.Random(4270)
    paths, events = [], []
    span = 0
    formats = ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f+00:00", "%Y-%m-%d %H:%M:%SZ"]
    for day in days:
        base = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        offsets = [rng.randrange(86400) for _ in range(30)]
        # 01:30 and 04:59:59 UTC are still the previous day in Chicago (CDT).
        offsets += [5400, 17999, 18000]
        day_events = []
        for k, off in enumerate(sorted(offsets)):
            span += 1
            dt = base + timedelta(seconds=off, microseconds=rng.randrange(1000000))
            ev = {
                "action": ACTIONS[rng.randrange(7)],
                "decision": "deny" if rng.random() < 0.3 else "allow",
                "mode": ["enforce", "audit"][rng.randrange(2)],
                "principal": PRINCIPALS[rng.randrange(4)],
                "reason": ["policy", "default"][rng.randrange(2)],
                "resource": None if rng.random() < 0.3 else f"res-{rng.randrange(9)}",
                "rule_id": ["r-1", "r-2", "r-3"][rng.randrange(3)],
                "span_id": f"SPAN{SENTINEL}{span:06d}",
                "surface": "gate",
                "trace_id": f"TRACE{SENTINEL}{span // 3:06d}",
                "ts": dt.strftime(formats[k % 3]),
            }
            day_events.append(ev)
        path = Path(directory) / gate_name(day)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for ev in day_events:
                f.write(json.dumps(ev) + "\n")
        paths.append(path)
        events += day_events
    return paths, events


# ---------------------------------------------------------------- logs

def make_pull_log(directory, name="pull.log"):
    """pull.log shaped lines. Returns (path, events) with events (utc, kind)."""
    events, lines = [], []
    first = datetime(2026, 8, 31, tzinfo=timezone.utc)
    for d in range(16):
        base = first + timedelta(days=d)
        kind = d % 5
        plan = []
        if kind == 0:
            plan = [(12, 0, "pulled")]
        elif kind == 1:
            plan = [(3, 30, "have")]          # previous local day
        elif kind == 2:
            plan = [(14, 0, "failed"), (15, 0, "pulled")]
        elif kind == 3:
            plan = [(10, 0, "failed")]
        noise = base + timedelta(hours=6, minutes=d)
        stamp = noise.strftime("%Y-%m-%dT%H%MZ")
        ts = noise.strftime("%Y-%m-%d %H:%M:%SZ")
        lines.append(f"{ts} email: unchanged since {noise.date().isoformat()}")
        lines.append(f"{ts} rag: done: {d + 3} chunks")
        if d % 4 == 0:
            lines.append(f"{ts} state: PAUSE-{d} saved to arora-archive-{stamp}.db.state")
            lines.append(f"{ts} removed arora-archive-{stamp}.db")
        for hour, minute, what in plan:
            dt = base + timedelta(hours=hour, minutes=minute, seconds=d)
            ts = dt.strftime("%Y-%m-%d %H:%M:%SZ")
            stamp = dt.strftime("%Y-%m-%dT%H%MZ")
            if what == "pulled":
                digest = hashlib.sha256(ts.encode()).hexdigest()
                lines.append(f"{ts} pulled arora-archive-{stamp}.db {41000 + d * 7} bytes "
                             f"from /a/b/c/d/backups sha256 {digest} verified")
                lines.append(f"{ts} LATEST -> arora-archive-{stamp}.db")
            elif what == "have":
                lines.append(f"{ts} already have arora-archive-{stamp}.db (sha256 matches); "
                             f"nothing pulled")
            else:
                lines.append(f"{ts} FAILED: sftp get /a/b/c/d/e/arora-archive-{stamp}.db "
                             f"timed out after {300 + d} s and was stopped")
            events.append((dt, what))
    path = Path(directory) / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, events


def make_governance_log(directory, name="governance_backup.log"):
    """governance_backup.log shaped lines (local time, no zone). Returns (path, events)."""
    events = [("2026-09-01 23:30:00", "OK"), ("2026-09-02 00:15:00", "FAIL"),
              ("2026-09-02 23:30:00", "OK"), ("2026-09-03 23:30:00", "OK"),
              ("2026-09-03 23:45:00", "FAIL")]
    lines = []
    for ts, what in events:
        compact = ts.replace("-", "").replace(":", "").replace(" ", "-")
        if what == "OK":
            lines.append(f"{ts} OK harness-governance-{compact}.zip (42 files, 1.2 MB)"
                         r" -> C:\backups\governance")
        else:
            lines.append(f"{ts} FAIL: target folder not reachable")
    path = Path(directory) / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, events


def make_log(directory, name, lines):
    path = Path(directory) / name
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------- CLI harness

def run_cli(argv):
    """Run askdata.cli.main and return (exit code, stdout, stderr)."""
    from askdata.cli import main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def snapshot(directory):
    """sha256 of every entry under directory, dotfiles and sidecars included."""
    result = {}
    for p in sorted(Path(directory).rglob("*")):
        rel = str(p.relative_to(directory))
        result[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
    return result


def csv_rows(text):
    return list(csv.reader(io.StringIO(text)))


class QueryTestCase(unittest.TestCase):
    """Base class: a temp dir with src/ for sources; every query() call
    asserts that src/ is byte-for-byte unchanged and has no new files."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.src = self.root / "src"
        self.src.mkdir()

    def query(self, sources, sql, *extra):
        before = snapshot(self.src)
        self.assertTrue(before, "fixture dir is empty")
        code, out, err = run_cli(["query", *[str(s) for s in sources], "--sql", sql, *extra])
        self.assertEqual(snapshot(self.src), before, "a source changed or a file appeared")
        return code, out, err

    def query_ok(self, sources, sql, *extra):
        code, out, err = self.query(sources, sql, "--format", "csv", *extra)
        self.assertTrue(out, f"no output (exit {code}, stderr {err!r})")
        self.assertEqual(code, 0, err)
        rows = csv_rows(out)
        self.assertTrue(rows)
        return rows

    def assert_refused(self, code, out, err, expected_code):
        self.assertTrue(err, f"no error message (exit {code})")
        self.assertEqual(code, expected_code, err)
        self.assertEqual(out, "")
        self.assertNotIn(SENTINEL, err)
