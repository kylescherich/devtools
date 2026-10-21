# askdata

Deterministic, read-only explorer for CSV, JSONL, log and SQLite files. No AI, no network, no subprocesses. `inspect` is Milestone 1; `query` is Milestone 2a (no model calls; natural-language questions, M2b, are not built).

## Usage
    askdata inspect FILE [--output F] [--format html|md] [--samples] [--frozen]
                         [--max-bytes N] [--max-rows N] [--timeout S] [--force]

- Without `--output` the report prints to the terminal. With `--output` the format comes from `--format`, else `.md` gives Markdown, else HTML.
- `--output` is refused when it is inside the input file's folder, or when it exists (use `--force` to overwrite).
- Exit codes: `0` ok; `2` usage error or refused output; `3` missing, unreadable or unsupported input, not a database, or a WAL database without `-shm`.

## Privacy
- By default reports contain schema and statistics only: names, types, counts, missing, distinct, numeric min/max/mean/median, date min/max.
- Example values and top-value lists appear only with `--samples`. The report header says `Samples: on` or `Samples: off`.
- Without `--samples`, integer columns whose values reach 13 or more digits show `hidden (identifier-like)` instead of their numbers.
- Table and column names are always shown.

## Read-only design
- CSV files are opened for reading only. Reports are never written next to the input.
- SQLite is opened with `mode=ro`, then `PRAGMA query_only=1`, then an authorizer that allows only: SELECT, READ and recursive CTEs; the PRAGMAs `table_info`, `table_xinfo`, `foreign_key_list`, `index_list`, `index_info`, and `data_version` without an argument (FTS5 needs it); and SQL functions except `load_extension`, `readfile`, `writefile`, `edit`, `fts3_tokenizer`. Extensions are never enabled.
- The function policy is a **deny-list today** (also in `query`). It becomes an **allow-list before any model-generated SQL runs** (M2b).
- WAL databases: a WAL-mode database with no `-shm` file is refused (exit 3), because opening it read-only would create `-wal` and `-shm` files beside it; copy it and use `--frozen`. With a live writer the reader sees committed data; SQLite updates its reader marks inside the existing `-shm`, no new files appear, and the database and `-wal` are unchanged.
- `--frozen` adds `immutable=1`: SQLite assumes the file never changes and ignores any WAL. Use it only on copies you know are static; results may be stale.

## What is read
- Every table and view listed in `sqlite_master` is read. Reading a view executes the view's SQL.
- Virtual tables (for example FTS5) and their shadow tables are traversed like other tables.
- An object that cannot be read (for example a view that calls a blocked function) is reported with an error, and the run continues.

## Limits and labels
- `--max-rows` (default 1,000,000) analyzes the first rows: SQLite tables in rowid or primary-key order; views have no defined order and the report says so. CSV reports show rows analyzed, malformed rows skipped and physical data rows separately.
- Distinct counts are labelled `exact` or `estimated`. CSV medians use a fixed-seed sample of up to 100,000 values and are labelled `approx.` beyond that.
- `--timeout` (default 60 s) stops cooperatively and the report says statistics are partial.
- SQLite numbers stored as TEXT are reported as text. SQLite example values are unordered. Headerless CSVs get names `col1..colN` and keep the first row as data.

## query (Milestone 2a)
    askdata query SOURCE [SOURCE ...] --sql "SELECT ..." [--format table|md|csv]
                  [--frozen] [--rows] [--max-rows N] [--timeout S]

### Sources
- `SOURCE` is `PATH` or `NAME=PATH`. `PATH` may be a quoted glob in the file name only (`jobs="scans/*Openings.csv"`); a glob needs `NAME`, dotfiles are skipped, and all matched files must have the same shape (same format, same CSV header, same JSONL key set; empty JSONL files are ignored for this). They become one table.
- `NAME` defaults to the file stem (`.jsonl.gz` drops both suffixes), lower-cased, with anything outside `[a-z0-9_]` replaced by `_`. `main`, `temp` and `sqlite_*` are reserved.
- SQLite (by header) is attached read-only as schema `NAME` (`mode=ro`; `immutable=1` only with `--frozen`): query `NAME.table`. A WAL database without `-shm` is refused, as in `inspect`. One SQLite file per source.
- `.csv`/`.tsv`: header row; UTF-8 (BOM ok), else cp1252 with a warning; delimiter sniffed as in `inspect` (`.tsv` is tab). Empty cells are NULL; canonical integers and decimals become numbers (`007` stays text).
- `.jsonl`/`.jsonl.gz`: one column per top-level key. Booleans are 0/1, null is NULL, objects and lists are JSON text. Unparsable or non-object lines are counted on stderr, never printed.
- `.log`/`.txt`: `line_no`, `line`.
- Loaded tables also get `_file` (basename) and `_file_date` (first valid `YYYY-MM-DD` in the basename, else NULL). Loaded tables live in an in-memory database; nothing is written anywhere.

### Functions
- `x REGEXP pattern`, `regexp_extract(text, pattern[, group])` (Python `re`, first match, NULL if none).
- `tz(ts[, zone])`, zone default `America/Chicago`: ISO-8601 with `Z` or `+-HH:MM`, optional fraction, `T` or space, to local `YYYY-MM-DD HH:MM:SS`. **Naive timestamps are returned unchanged** (taken to be local already), date-only values too; anything else is NULL with a warning.
- Without a tz database (Windows without `tzdata`) `tz()` uses a built-in US Central rule: UTC-6, UTC-5 from the second Sunday of March 02:00 to the first Sunday of November 02:00 local; 2007 onward, earlier values are NULL with a warning. It is tested against `zoneinfo` for every hour of 2007-2030.

### Output guard (DEC-427)
- **(a) Deny-list in the authorizer.** Source profiles are recognised by shape: `arora_v2` (SQLite with `records`, `urls`, `runs`), `job_scan` (CSV whose header contains the job-scan header), `gate_decisions` (JSONL whose keys contain the gate keys). Every listed TEXT/BLOB column is `allow` or `deny` (`askdata/profiles.py`); an unlisted non-numeric column of a profiled source is denied. A read of a denied column is refused (exit 2), whatever the query shape (`SELECT *`, functions, CTEs, subqueries, joins, UNION, LIKE). `job_scan` gets `job_title_h` = first 12 hex of sha256 of the stripped, case-folded `Job Title`; `Job Title` stays denied.
- Only SELECT and reads run: PRAGMA (except `data_version` without an argument, as in `inspect`), ATTACH, writes and CREATE (also TEMP) are refused, and so are the blocked functions. Reads are allowed only from the sources' own tables, `sqlite_master` and `json_each`/`json_tree` (so `dbstat` and `sqlite_stmt` are refused).
- **(b) Aggregate-only default.** Without `--rows`, a result is refused (exit 1) unless every cell is numeric, NULL or a date/time bucket (`YYYY`, `YYYY-MM`, ... `YYYY-MM-DD HH:MM:SS`, `YYYY-Www`), or the statement's EXPLAIN has an aggregate opcode. `GROUP BY` without an aggregate function has none: add `count(*)`. More than `--max-rows` (default 500) rows is refused with the count (exit 1).
- `--rows` turns (b) off and is refused (exit 2) when any source has a profile.
- Known limit of (b), by design (no SQL parser): an aggregate or window function makes any result pass, e.g. `max(line)` or `line, count(*) OVER ()` on an unprofiled log. (a) is the guarantee for profiled data; (b) is a default for everything.
- Error messages name sources, tables and columns, never cell values.

### Output and exit codes
- `--format table` (default; control and bidi characters escaped as in `inspect`, NULL shown as `NULL`), `md`, `csv` (cells escaped the same way, NULL empty). BLOBs print as `<blob N bytes>`.
- `0` ok; `1` refused by the guard (not aggregated, over `--max-rows`); `2` usage, bad or refused SQL, `--rows` with a profiled source; `3` input missing, unreadable, unsupported, mixed shapes in one glob, WAL without `-shm` and no `--frozen`, or `--timeout` reached (default 60 s, loading and query).
- No size cap on inputs beyond `--timeout`: loaded files are held in memory.
