# jsondiff

Deterministic, read-only comparison of the structure (shape) of two JSON or JSONL files. Standard library only; no AI, no network, no subprocesses.

## Usage
    jsondiff OLD NEW [--output F] [--format html|md] [--force] [--jsonl]
                     [--key [PATH=]FIELD]... [--all] [--values]
                     [--max-bytes N] [--max-records N] [--max-depth N]

- Without `--output` a terminal report prints to stdout. With `--output` the format comes from `--format`, else `.md` gives Markdown, else HTML.
- `--output` is refused (exit 2, nothing written) when it exists without `--force`, or when it resolves inside the directory of either input.
- `--all` also lists the unchanged paths.

## Exit codes
- `0` no structural change
- `1` changes, none potentially breaking (also: keyed elements whose shape changed)
- `2` usage error: bad arguments, bad `--key`, `--key` with JSONL, refused `--output`
- `3` input missing, unreadable, a directory or a symlink, invalid JSON, not UTF-8, or too large
- `4` at least one potentially breaking change

Usage errors (2) and input errors (3) are checked before any comparison; 4 wins over 1, and 1 over 0. Errors are one line, `jsondiff: <message>`, on stderr.

## What is compared
- Paths: root is `$`; keys are joined with `.` (`user.address.city`); array elements are `[]` (`items[].sku`, root array `$[]`). A key that is not a plain identifier is written `["..."]` with JSON escaping (`user["a.b"]`).
- Per path: types seen (object, array, string, integer, number, boolean, null; `1` is integer, `1.0` is number, `true` is boolean), how often it is present in its parent objects, and whether null was seen.
- Categories: added, removed, type changed, became / no longer nullable, became / no longer optional, and an "array element shape changed" summary on every array whose elements changed.
- An empty array is not a type: an array that was always empty and now has elements reports its element paths as added; one that becomes always empty reports them as removed ("array always empty in new file").

## "Potentially breaking — review"
Removed, type changed, became optional ("no longer present in all records") and became nullable are flagged **potentially breaking — review**. They are candidates for review, not proof that anything will break. Added, no longer nullable and no longer optional are not flagged.

## Arrays
- By default array elements are compared by their **merged shape, not by position**; the report says so and lists those arrays.
- `--key FIELD` matches the elements of the root array by `FIELD`; `--key PATH=FIELD` the array at `PATH` (`orders=id`, `orders[].lines=sku`). Key values are compared via `json.dumps`, so `1` and `"1"` differ. The report gives added, removed, changed (different shape) and unchanged element counts, and elements missing the key field. Nested keyed arrays are matched within their keyed parent.
- `--key` works on single JSON documents only; with JSONL it is a usage error.

## Privacy
- By default reports contain paths, types and counts only. The header says `values: off`.
- `--values` adds up to 3 example values per changed path (JSON-encoded, cut to 80 characters); the header then says `values: on`.
- Key values used by `--key` are hashed in memory and never printed.
- Every source-derived string is escaped: control characters become `\xNN`, bidi controls `\uXXXX`; HTML is fully escaped and has no script and no external URLs.

## JSONL
Used for `.jsonl` / `.ndjson` or with `--jsonl`. Each non-blank line is one record; records are merged into one shape. Invalid lines are skipped, counted, and the first 3 line numbers are reported.

## Limits
- `--max-bytes` (default 200,000,000 per file): a larger JSON file is refused (exit 3); JSONL is read up to the limit and marked sampled.
- `--max-records` (JSONL, default 1,000,000): reading stops after N records, marked sampled.
- `--max-depth` (default 64): deeper levels are not descended; the report shows a `…(depth limit)` path and a warning.
- Duplicate object keys (last value kept, as Python's json does) and `NaN` / `Infinity` are reported as warnings.

## Read-only design
Inputs are opened read-only and never executed; symlinks are refused. Reports are never written next to an input.
