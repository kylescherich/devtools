# askdata

Deterministic, read-only explorer for one CSV or SQLite file. No AI, no network, no subprocesses (Milestone 1).

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
- The function policy is a **deny-list today**. It becomes an **allow-list in Milestone 2**, before any model-generated SQL runs.
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
