"""askdata query (M2a): one SELECT over local sources, behind an output guard.

CSV/TSV, JSONL(.gz) and log files are loaded into an in-memory database;
SQLite files are attached read-only (mode=ro, immutable=1 only with
--frozen). Nothing is written next to a source.

Two guard layers (DEC-427), both always on:
(a) The authorizer allows only SELECT, reads and recursive CTEs. SQLITE_READ
    is denied for every column a source profile denies (askdata.profiles),
    so no query shape can read it, and for every table that is not one of
    the sources' own tables.
(b) Without --rows the result must be aggregated: every cell is numeric,
    NULL or a date/time bucket, or the statement's EXPLAIN bytecode has an
    aggregate opcode. --rows is refused when any source has a profile.

Error messages name sources, tables and columns only, never cell values.
"""
import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
import time
import urllib.parse
from datetime import date
from pathlib import Path

from askdata import profiles
from askdata.report import _escape_md_cell, _escape_terminal
from askdata.sqlfuncs import SqlFunctions

SQLITE_MAGIC = b"SQLite format 3\x00"
BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}
# Readable without being a source table: the schema table and the JSON
# table-valued functions, which only expose their own arguments.
SCHEMA_TABLES = {"sqlite_master", "sqlite_schema"}
SAFE_VTABS = {"json_each", "json_tree"}
RESERVED_NAMES = {"main", "temp"}
FILE_COLUMNS = ["_file", "_file_date"]
NOT_AGGREGATED = "result is not aggregated; use GROUP BY/aggregates, or --rows"

_BUCKETS = (re.compile(r"\d{4}(-\d{2}(-\d{2}( \d{2}(:\d{2}(:\d{2})?)?)?)?)?\Z"),
            re.compile(r"\d{4}-W\d{2}\Z"))
_AGG_OPCODE = re.compile(r"Agg(Step\d*|Final|Value)\Z")
_NAME_SPEC = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.+)\Z", re.S)
_FILE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_INT = re.compile(r"-?(?:0|[1-9]\d*)\Z")
_FLOAT = re.compile(r"-?(?:0|[1-9]\d*)\.\d+\Z")
_GLOB_CHARS = set("*?[")
_INT64 = (-(2 ** 63), 2 ** 63 - 1)
_CHUNK = 1000


class QueryError(Exception):
    """A refusal or failure with its exit code; the message holds no cell values."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _ident(name):
    return '"' + name.replace('"', '""') + '"'


def default_name(path):
    """File stem (.jsonl.gz drops both suffixes), lower-cased, [a-z0-9_] only."""
    stem = path.name[:-len(".jsonl.gz")] if path.name.lower().endswith(".jsonl.gz") else path.stem
    return re.sub(r"[^a-z0-9_]", "_", stem.lower())


def parse_source(spec):
    """PATH or NAME=PATH -> (name, path, is_glob)."""
    spec = str(spec)
    m = _NAME_SPEC.match(spec)
    name, path = (m.group(1), m.group(2)) if m else (None, spec)
    is_glob = any(c in _GLOB_CHARS for c in path)
    if is_glob and name is None:
        raise QueryError(2, 'a glob source needs a name: NAME="dir/*.csv"')
    if name is None:
        name = default_name(Path(path))
        if not name.strip("_"):
            raise QueryError(2, "cannot derive a table name from the file name; use NAME=PATH")
    folded = name.casefold()
    if folded in RESERVED_NAMES or folded.startswith("sqlite_"):
        raise QueryError(2, f"source name {name} is reserved")
    return name, path, is_glob


def _resolve(name, path, is_glob):
    if is_glob:
        p = Path(path)
        if any(c in _GLOB_CHARS for c in str(p.parent)):
            raise QueryError(2, f"source {name}: a glob may only be in the file name")
        if not p.parent.is_dir():
            raise QueryError(3, f"source {name}: folder not found")
        files = sorted(f for f in p.parent.glob(p.name) if not f.name.startswith("."))
        if not files:
            raise QueryError(3, f"source {name}: no files match")
    else:
        files = [Path(path)]
    for f in files:
        if f.is_symlink():
            raise QueryError(3, f"source {name}: symlinks are not allowed ({f.name})")
        if not f.exists():
            raise QueryError(3, f"source {name}: file not found ({f.name})")
        if not f.is_file():
            raise QueryError(3, f"source {name}: not a file ({f.name})")
    return files


def _kind(name, f):
    try:
        with open(f, "rb") as fh:
            head = fh.read(16)
    except OSError:
        raise QueryError(3, f"source {name}: file is not readable ({f.name})") from None
    if head.startswith(SQLITE_MAGIC):
        return "sqlite"
    lower = f.name.lower()
    if lower.endswith((".jsonl", ".jsonl.gz")):
        return "jsonl"
    if lower.endswith((".csv", ".tsv")):
        return "csv"
    if lower.endswith((".log", ".txt")):
        return "log"
    raise QueryError(3, f"source {name}: unsupported format ({f.name})")


def _file_date(basename):
    for m in _FILE_DATE.finditer(basename):
        try:
            date.fromisoformat(m.group())
        except ValueError:
            continue
        return m.group()
    return None


def _csv_value(v):
    if v == "":
        return None
    if _INT.match(v):
        n = int(v)
        return n if _INT64[0] <= n <= _INT64[1] else v
    if _FLOAT.match(v):
        return float(v)
    return v


def _json_value(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v if _INT64[0] <= v <= _INT64[1] else str(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return v


class Session:
    """The in-memory database with every source loaded or attached."""

    def __init__(self, deadline=None):
        self.conn = sqlite3.connect("file::memory:", uri=True)
        self.deadline = deadline
        self.sources = []
        self.columns = {}
        self.readable = set()
        self.deny = set()
        self.warnings = []
        self.funcs = SqlFunctions()
        self.funcs.register(self.conn)

    def close(self):
        self.conn.close()

    def profiled(self):
        return [s for s in self.sources if s["profile"]]

    def _check_deadline(self, name):
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise QueryError(3, f"source {name}: timed out while loading")

    # ---------------------------------------------------------------- SQLite

    def attach(self, name, f, frozen):
        try:
            with open(f, "rb") as fh:
                header = fh.read(100)
        except OSError:
            raise QueryError(3, f"source {name}: file is not readable ({f.name})") from None
        if len(header) >= 20 and header[18] == 2 and header[19] == 2 and not frozen:
            if not Path(str(f) + "-shm").exists():
                raise QueryError(3, f"source {name}: WAL database without -shm access; "
                                    f"copy it and use --frozen")
        uri = "file:" + urllib.parse.quote(str(f.resolve())) + "?mode=ro"
        if frozen:
            uri += "&immutable=1"
        schema = _ident(name)
        try:
            self.conn.execute(f"ATTACH DATABASE ? AS {schema}", (uri,))
            objects = self.conn.execute(
                f"SELECT name FROM {schema}.sqlite_master WHERE type IN ('table', 'view') "
                f"ORDER BY name").fetchall()
        except sqlite3.Error:
            raise QueryError(3, f"source {name}: not a readable SQLite database "
                                f"({f.name})") from None
        tables = [r[0] for r in objects]
        profile = profiles.detect_sqlite(tables)
        for table in tables:
            try:
                info = self.conn.execute(
                    f"PRAGMA {schema}.table_xinfo({_ident(table)})").fetchall()
            except sqlite3.Error:
                continue  # not readable at all: never added to self.readable
            cols = [(row[1], row[2]) for row in info]
            self.columns[(name, table)] = [c for c, _ in cols]
            self.readable.add((name.casefold(), table.casefold()))
            if profile:
                for col, declared in cols:
                    if profiles.verdict(profile, table, col, declared) == profiles.DENY:
                        self.deny.add((name.casefold(), table.casefold(), col.casefold()))
        self.sources.append({"name": name, "kind": "sqlite", "profile": profile})

    # ---------------------------------------------------------------- loaded tables

    def _create(self, name, kind, columns, rows, profile):
        seen = set()
        for c in columns:
            if c.casefold() in seen:
                raise QueryError(3, f"source {name}: column {c} appears twice")
            seen.add(c.casefold())
        table = f"main.{_ident(name)}"
        self.conn.execute(f"CREATE TABLE {table} ({', '.join(_ident(c) for c in columns)})")
        insert = f"INSERT INTO {table} VALUES ({', '.join('?' * len(columns))})"
        for start in range(0, len(rows), _CHUNK):
            self._check_deadline(name)
            self.conn.executemany(insert, rows[start:start + _CHUNK])
        self.conn.commit()
        self.columns[("main", name)] = list(columns)
        self.readable.add(("main", name.casefold()))
        if profile:
            for col in columns:
                if profiles.verdict(profile, "*", col) == profiles.DENY:
                    self.deny.add(("main", name.casefold(), col.casefold()))
        self.sources.append({"name": name, "kind": kind, "profile": profile})

    def _reserved_check(self, name, columns, extra):
        for c in columns:
            if c.casefold() in {e.casefold() for e in extra}:
                raise QueryError(3, f"source {name}: column {c} is reserved")

    def _read_csv(self, name, f):
        raw = f.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
            self.warnings.append(f"source {name}: {f.name} is not UTF-8, read as cp1252")
        if f.name.lower().endswith(".tsv"):
            delimiter = "\t"
        else:
            delimiter = ","
            sample = text[:65536]
            first_line = sample.split("\n", 1)[0]
            try:
                sniffed = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
                if sniffed in first_line:
                    delimiter = sniffed
            except csv.Error:
                pass
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            raise QueryError(3, f"source {name}: empty file ({f.name})") from None
        except csv.Error:
            raise QueryError(3, f"source {name}: not a readable CSV ({f.name})") from None
        header = [h.strip() or f"col{i}" for i, h in enumerate(header, 1)]
        rows, ragged = [], 0
        try:
            for n, row in enumerate(reader, 1):
                if n % _CHUNK == 0:
                    self._check_deadline(name)
                if not row:
                    continue
                if len(row) != len(header):
                    ragged += 1
                    row = (row + [""] * len(header))[:len(header)]
                rows.append(row)
        except csv.Error:
            raise QueryError(3, f"source {name}: not a readable CSV ({f.name})") from None
        if ragged:
            self.warnings.append(f"source {name}: {ragged} rows in {f.name} have a different "
                                 f"number of fields, padded or cut")
        return header, rows

    def load_csv(self, name, files):
        parts = [(f, *self._read_csv(name, f)) for f in files]
        header = parts[0][1]
        if any(h != header for _, h, _ in parts):
            raise QueryError(3, f"source {name}: files in one glob have different shapes "
                                f"(headers differ)")
        profile = profiles.detect_csv(header)
        extra = FILE_COLUMNS + (["job_title_h"] if profile == "job_scan" else [])
        self._reserved_check(name, header, extra)
        title_at = None
        if profile == "job_scan":
            title_at = [h.casefold() for h in header].index("job title")
        rows = []
        for f, _, raw_rows in parts:
            tail = [f.name, _file_date(f.name)]
            for raw in raw_rows:
                values = [_csv_value(v) for v in raw] + tail
                if title_at is not None:
                    title = raw[title_at].strip().casefold()
                    values.append(hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
                                  if title else None)
                rows.append(values)
        self._create(name, "csv", header + extra, rows, profile)

    def _read_jsonl(self, name, f):
        opener = gzip.open if f.name.lower().endswith(".gz") else open
        keys, objects, bad = {}, [], 0
        try:
            with opener(f, "rb") as fh:
                for n, line in enumerate(fh, 1):
                    if n % _CHUNK == 0:
                        self._check_deadline(name)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError, RecursionError):
                        bad += 1
                        continue
                    if not isinstance(obj, dict):
                        bad += 1
                        continue
                    for k in obj:
                        keys.setdefault(k, None)
                    objects.append(obj)
        except (OSError, EOFError):
            raise QueryError(3, f"source {name}: cannot read {f.name}") from None
        if bad:
            self.warnings.append(f"source {name}: {bad} unparsable lines skipped in {f.name}")
        return list(keys), objects

    def load_jsonl(self, name, files):
        parts = [(f, *self._read_jsonl(name, f)) for f in files]
        shapes = {frozenset(k) for _, k, objs in parts if objs}
        if len(shapes) > 1:
            raise QueryError(3, f"source {name}: files in one glob have different shapes "
                                f"(keys differ)")
        columns = {}
        for _, k, _ in parts:
            for key in k:
                columns.setdefault(key, None)
        columns = list(columns)
        profile = profiles.detect_jsonl(columns)
        self._reserved_check(name, columns, FILE_COLUMNS)
        rows = []
        for f, _, objs in parts:
            tail = [f.name, _file_date(f.name)]
            for obj in objs:
                rows.append([_json_value(obj.get(c)) for c in columns] + tail)
        self._create(name, "jsonl", columns + FILE_COLUMNS, rows, profile)

    def load_log(self, name, files):
        rows = []
        for f in files:
            tail = [f.name, _file_date(f.name)]
            try:
                with open(f, "rb") as fh:
                    for n, line in enumerate(fh, 1):
                        if n % _CHUNK == 0:
                            self._check_deadline(name)
                        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                        rows.append([n, text] + tail)
            except OSError:
                raise QueryError(3, f"source {name}: cannot read {f.name}") from None
        self._create(name, "log", ["line_no", "line"] + FILE_COLUMNS, rows, None)

    # ---------------------------------------------------------------- guard (a)

    def _authorize(self, action, arg1, arg2, db_name, _source):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if not arg2:
                # A table or CTE is used but none of its columns is read.
                return sqlite3.SQLITE_OK
            db, table = (db_name or "").casefold(), (arg1 or "").casefold()
            if table in SCHEMA_TABLES or (db == "main" and table in SAFE_VTABS):
                return sqlite3.SQLITE_OK
            if (db, table) not in self.readable:
                return sqlite3.SQLITE_DENY
            if (db, table, (arg2 or "").casefold()) in self.deny:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if (arg2 or "").lower() in BLOCKED_FUNCTIONS:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA:
            # As in M1: FTS5 reads data_version without an argument.
            if (arg1 or "").lower() == "data_version" and arg2 is None:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    def lockdown(self):
        self.conn.commit()
        self.conn.execute("PRAGMA query_only=1")
        self.conn.set_authorizer(self._authorize)


def open_sources(specs, frozen=False, deadline=None):
    """Load or attach every source; returns a locked-down Session."""
    parsed = [parse_source(s) for s in specs]
    seen = set()
    for name, _, _ in parsed:
        if name.casefold() in seen:
            raise QueryError(2, f"source name {name} is used twice")
        seen.add(name.casefold())
    session = Session(deadline)
    try:
        for name, path, is_glob in parsed:
            files = _resolve(name, path, is_glob)
            kinds = {_kind(name, f) for f in files}
            if len(kinds) > 1:
                raise QueryError(3, f"source {name}: files in one glob have different shapes "
                                    f"(formats differ)")
            kind = kinds.pop()
            if kind == "sqlite":
                if len(files) > 1:
                    raise QueryError(3, f"source {name}: a glob may match only one SQLite file")
                session.attach(name, files[0], frozen)
            elif kind == "csv":
                session.load_csv(name, files)
            elif kind == "jsonl":
                session.load_jsonl(name, files)
            else:
                session.load_log(name, files)
        session.lockdown()
    except BaseException:
        session.close()
        raise
    return session


# -------------------------------------------------------------------- guard (b)

def _cells_are_buckets(rows):
    for row in rows:
        for v in row:
            if v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)):
                continue
            if isinstance(v, str) and any(b.match(v) for b in _BUCKETS):
                continue
            return False
    return True


def _has_aggregate(conn, sql):
    try:
        ops = [row[1] for row in conn.execute("EXPLAIN " + sql)]
    except (sqlite3.Error, sqlite3.Warning):
        return False
    return any(_AGG_OPCODE.match(str(op)) for op in ops)


def execute(session, sql, rows_allowed=False, max_rows=500, deadline=None, timeout=None):
    """Run sql; returns (column names, rows) or raises QueryError."""
    conn = session.conn
    interrupted = []

    def progress():
        if deadline is not None and time.monotonic() > deadline:
            interrupted.append(True)
            return 1
        return 0

    conn.set_progress_handler(progress, 10000)
    kept, count = [], 0
    try:
        cur = conn.execute(sql)
        if cur.description is None:
            raise QueryError(2, "the statement returns no result; only SELECT is allowed")
        names = [d[0] for d in cur.description]
        for row in cur:
            count += 1
            if count <= max_rows:
                kept.append(row)
    except (sqlite3.Error, sqlite3.Warning) as e:
        if interrupted:
            raise QueryError(3, f"query timed out after {timeout} s") from None
        raise QueryError(2, f"SQL refused or invalid: {e}") from None
    if count > max_rows:
        raise QueryError(1, f"result has {count} rows, over --max-rows {max_rows}")
    if not rows_allowed and not _cells_are_buckets(kept) and not _has_aggregate(conn, sql):
        raise QueryError(1, NOT_AGGREGATED)
    return names, kept


# -------------------------------------------------------------------- output

def _cell(v, null):
    if v is None:
        return null
    if isinstance(v, bytes):
        return f"<blob {len(v)} bytes>"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def render(names, rows, fmt):
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow([_escape_terminal(n) for n in names])
        for row in rows:
            writer.writerow([_escape_terminal(_cell(v, "")) for v in row])
        return buf.getvalue()
    if fmt == "md":
        lines = ["| " + " | ".join(_escape_md_cell(n) for n in names) + " |",
                 "| " + " | ".join("---" for _ in names) + " |"]
        for row in rows:
            lines.append("| " + " | ".join(_escape_md_cell(_cell(v, "NULL")) for v in row)
                         + " |")
        return "\n".join(lines) + "\n"
    table = [[_escape_terminal(n) for n in names]]
    table += [[_escape_terminal(_cell(v, "NULL")) for v in row] for row in rows]
    widths = [max(len(r[i]) for r in table) for i in range(len(names))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in table]
    lines.insert(1, "  ".join("-" * w for w in widths))
    lines.append(f"({len(rows)} row{'' if len(rows) == 1 else 's'})")
    return "\n".join(lines) + "\n"


def run(args, out, err):
    """askdata query entry point; returns the exit code."""
    def fail(code, message):
        print(f"askdata: {_escape_terminal(message)}", file=err)
        return code

    if args.max_rows < 1:
        return fail(2, "--max-rows must be at least 1")
    if not args.timeout > 0:
        return fail(2, "--timeout must be positive")
    deadline = time.monotonic() + args.timeout
    try:
        session = open_sources(args.sources, frozen=args.frozen, deadline=deadline)
    except QueryError as e:
        return fail(e.code, str(e))
    try:
        for warning in session.warnings:
            print(f"askdata: warning: {_escape_terminal(warning)}", file=err)
        if args.rows and session.profiled():
            s = session.profiled()[0]
            return fail(2, f"--rows is refused: source {s['name']} has profile {s['profile']} "
                           f"with a deny-list")
        try:
            names, rows = execute(session, args.sql, rows_allowed=args.rows,
                                  max_rows=args.max_rows, deadline=deadline,
                                  timeout=args.timeout)
        except QueryError as e:
            return fail(e.code, str(e))
        finally:
            for warning in session.funcs.warnings():
                print(f"askdata: warning: {warning}", file=err)
        out.write(render(names, rows, args.format))
        return 0
    finally:
        session.close()
