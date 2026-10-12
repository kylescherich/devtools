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
Syslog lines: a `host app[pid]:` or `host app:` header right after the
timestamp is taken out of the message before grouping, so the same error from
several hosts is one group; the incident view lists each group's hosts and
apps (count and up to 3 names, e.g. `hosts: 2 (web1, web2)  apps: 1 (nginx)`).

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
  way to turn it off (the report header always says `redaction: on`); it also
  applies under `--raw`. See "What is redacted" below.
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

### What is redacted (the privacy boundary)

Log Lens redacts:

- credentials and secrets: values of labelled keys, known provider token
  shapes, auth headers, URL passwords, PEM blocks and JWTs;
- session material: cookies, session ids, CSRF/refresh tokens, client secrets;
- e-mail addresses;
- payment card numbers (13-19 digits passing the Luhn check);
- values of explicitly labelled personal fields (`phone`, `tel`, `mobile`,
  `dob`, `birth_date`, `ssn`, `patient_id`, `member_id`, `customer_id`, `mrn`).

It does **not** promise general PII redaction: free-text names and postal
addresses are not detected. Do not run Log Lens on logs you would not show in
a terminal.

Details:

- Keys (`key=v`, `key: v`, `"key": "v"`, `'key': 'v'`; case-insensitive) are
  matched by name: any identifier containing `password`, `passwd`,
  `passphrase`, `secret`, `token`, `api_key`/`apikey`, `credential`,
  `private_key`, `access_key`, `account_key` or `signature`
  (`AWS_SECRET_ACCESS_KEY=`, `refresh_token=`, `AccountKey=`). The short
  words `pass`, `pwd`, `sig` and `auth` count only as a whole name component
  (`db_pass=`, `dbPass=`, `X-Auth:`, `sas_sig=`, but not `bypass=`,
  `oauth_state=`, `design=`, `signal=`). Names that merely contain a word are
  redacted too, so `tokens: 5` becomes `tokens: <redacted>`.
- Session material: names containing `csrf`, `xsrf` or `cookie`; `session`
  and `sid` as the whole name or its last component (`session_id`,
  `jsessionid`, `user_session`, `connect.sid`; not `session_count`,
  `sessions`, `sidebar`). `Cookie:`, `Set-Cookie:` and `cookie=` values are
  redacted to the end of the line.
- Provider token shapes anywhere: AWS (`AKIA`/`ASIA`), Slack (`xox?-`),
  GitLab (`glpat-`), Google (`AIza`), Stripe (`sk_live_`, `rk_test_`, ...),
  GitHub (`ghp_` ..., `github_pat_`), `sk-`; JWTs; PEM blocks.
- `Basic <token>` / `Bearer <token>` anywhere, when the token looks like one
  (8+ characters with a digit, `+`, `/`, `=` or an inner capital), so "Basic
  authentication failed" stays; the whole `Authorization:` value always.

## Exit codes

When several apply, the leftmost wins: 2 > 3 > 4 > 1 > 0.

| code | meaning |
| --- | --- |
| 0 | no entries at WARNING or above |
| 1 | WARNING entries, none at ERROR or above |
| 2 | usage error, including a refused `--output` or a `--bucket` over 2,000 buckets |
| 3 | input missing, unreadable, invalid, a symlink, or over `--max-bytes` |
| 4 | entries at ERROR or above (ERROR, ERR, CRITICAL, FATAL, ALERT, EMERG) |
