# repo-xray

A static, read-only overview of an unfamiliar Python repository as a single HTML file.

## Install

```bash
uv tool install --editable .
```

## Usage

```bash
repo-xray <path> [--output FILE] [--format html|md] [--max-files N] [--no-docstrings]
```

**Arguments:**
- `<path>` — Path to the repository to analyze
- `--output FILE` — Output file path (default: `<repo-name>-xray.html` in current directory)
- `--format html|md` — Output format: HTML (default) or Markdown
- `--max-files N` — Maximum number of files to process (default: 2000)
- `--no-docstrings` — Omit docstrings from the report

**Exit codes:**
- `0` — Success
- `2` — Usage error or output path is inside the target
- `3` — Target is unreadable or has too many files

## What it reads

It reads .py files and the [project.scripts] table of the root pyproject.toml, nothing else.

repo-xray never runs or imports code from the target repository.

Symlinks and Windows junctions are not followed.

.py files over 1 MB, files with syntax errors, and non-UTF-8 files are listed under "Skipped / not parsed".

## What the report contains

Names, signatures and the first docstring line (optional; --no-docstrings leaves it out). Never full source or string literals.

The report includes:
- Module structure and package hierarchy
- Classes with their bases and methods
- Functions with their signatures
- Import relationships (internal and external)
- Entry points (console scripts and `if __name__ == "__main__"` blocks)
- Test modules with inferred targets
- Dependency graph (HTML format only, for repos with ≤150 non-test modules)

## Limits

Static analysis only. Test targets are statically inferred. Dynamic and optional imports are listed, not guessed.

# loglens

Summarises text and JSONL log files (optionally gzipped) into grouped,
redacted findings: which errors and warnings happened, how often, when, and
where they spiked. Standard library only; Python 3.11 and 3.12.

## Usage

```bash
loglens FILE [FILE ...] [--format text|md|html] [--output PATH [--force]]
        [--bucket auto|1m|5m|15m|1h|6h|1d] [--context N] [--raw]
        [--input-format auto|text|jsonl] [--year YYYY] [--field NAME=KEY]
        [--max-bytes N] [--max-lines N]
```

Examples (synthetic file names):

```bash
loglens app.log                                  # text report on stdout
loglens app.log worker.log.gz events.jsonl       # merged into one timeline
loglens app.log --format html --output ~/reports/app.html
loglens syslog.1 --year 2026 --bucket 15m --format md
loglens app.log --context 3                      # neighbours of each incident
loglens app.log --context 3 --raw                # plus redacted raw messages
loglens export.log --input-format jsonl --field message=text --field ts=when
```

Every report has the same sections in all three formats: a header (files and
sizes, lines read, entries, untimed entries, invalid JSONL lines, replaced
non-UTF-8 bytes, sampling, syslog year, bucket size, raw lines on/off,
redaction on), a one-sentence summary, warnings, the groups table, the timeline,
spikes, the incident view (the top 5 WARNING+ groups by count; INFO and
DEBUG groups appear only in the groups table) and, only with `--raw`,
examples.
Timeline bounds: `--bucket auto` picks the smallest size giving at most 200
buckets; an explicit `--bucket` giving more than 2,000 buckets is refused with
exit 2 and the message names the smallest size that fits. Entries more than
30 days before or after the median timestamp are outliers: they still count
in their groups, but are left off the timeline, its span and spike detection,
and the warnings section reports them ("none" when there are none). Runs of 5
or more buckets without WARNING+ events are shown as one "... N empty buckets
..." line (one row in HTML).

Reports are reproducible: the same inputs and arguments (including `--year`)
give byte-identical output; the current date or time never appears.

## Privacy defaults

- No raw log line is printed unless you pass `--raw`. The default report shows
  signatures: messages with numbers, IDs, IPs, paths, URLs, e-mails and quoted
  strings replaced by placeholders such as `<n>` and `<path>`.
- Everything printed or written passes through one redaction function, with no
  way to turn it off: bearer/Authorization values, password/secret/token/API-key
  values, GitHub/`sk-`/AWS keys, JWTs, PEM blocks and 16-digit card numbers
  become `<redacted>`, also under `--raw`.
- Control characters and bidi overrides in log text are shown as `\xNN` /
  `\uXXXX`; in text and Markdown a literal backslash from the log is shown
  doubled (`\\`), so a typed `\x1b` reads `\\x1b` and only a real control
  reads `\x1b`; Markdown and HTML are escaped; the HTML report has no scripts,
  links or event handlers.
- Inputs are only read: never through a symlink, and never written.
  `--output` refuses an existing file (unless `--force`), any symlink, and the
  directory of any input file (so `loglens app.log --output report.txt` run
  next to `app.log` is refused; write elsewhere). No network, no subprocess,
  no tailing.

## Exit codes

When several apply, the leftmost wins: 2 > 3 > 4 > 1 > 0.

| code | meaning |
| --- | --- |
| 0 | no entries at WARNING or above |
| 1 | WARNING entries, none at ERROR or above |
| 2 | usage error, including a refused `--output` or a `--bucket` over 2,000 buckets |
| 3 | input missing, unreadable, invalid, a symlink, or over `--max-bytes` |
| 4 | entries at ERROR or above (ERROR, ERR, CRITICAL, FATAL, ALERT, EMERG) |
