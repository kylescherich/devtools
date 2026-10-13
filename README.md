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
- Multi-word values: for names containing `password`, `passwd`,
  `passphrase`, `secret` or `credential`, and for the personal fields, an
  unquoted value runs to the end of the line or to the next ` key=`,
  `, key=`, `; key=` or `&key=` field: `password=a b c user=bob` becomes
  `password=<redacted> user=bob`. Spoken forms `password is X`, `password was
  X`, `passphrase is X`, `credentials were X` redact the next word (also
  "password is incorrect"; keep-words below stay).
- Session material: names containing `csrf`, `xsrf` or `cookie`; `session`
  and `sid` as the whole name or its last component (`session_id`,
  `jsessionid`, `user_session`, `connect.sid`; not `session_count`,
  `sessions`, `sidebar`). `Cookie:`, `Set-Cookie:` and `cookie=` values are
  redacted to the end of the line.
- Card numbers: 13-19 digits, as one run or in groups with one consistent
  space or dash (4-4-4-4, 4-6-5, ...), first digit 2-6, passing the Luhn
  check and not touching another digit or a letter. Other long numbers
  (ids, epoch-millisecond timestamps) are kept.
- Labelled personal fields: `phone`, `tel`, `mobile`, `dob`, `birth_date`,
  `ssn`, `patient_id`, `member_id`, `customer_id`, `mrn`, `telephone`,
  `date_of_birth`, as a whole name component anywhere in the name
  (`user_phone=`, `mobilePhone=`, `phone_number=`, `patient_id_hash=`; not
  `hotel=`, `hotel_id=`, `telemetry=`, `automobile=`). Privacy over noise:
  `phone_count=` and `mobile_app_version=` are redacted too. There is no
  free-text phone, name or address detection.
- URL userinfo is redacted only when it contains `:` (a password):
  `https://u:pw@host` becomes `https://<redacted>@host`, while
  `ssh://git@host.example.test/r.git` stays unchanged (the e-mail rule does
  not fire on URL userinfo).
- Keep-words: a key-based rule keeps a value that is exactly one of
  `expired`, `invalid`, `missing`, `none`, `null`, `empty`, `true`, `false`,
  `present`, `absent`, `revoked`, `ok` (any case), so `token=expired` and
  `csrf=missing` stay readable.
- Accepted over-redaction: `auth: login ok` becomes `auth: <redacted> ok`
  (DEC-183), and e-mail-like service identities
  (`svc-deploy@proj.iam.example.test`) become `<email>` like any address.
- Provider token shapes anywhere: AWS (`AKIA`/`ASIA`), Slack (`xox?-`),
  GitLab (`glpat-`), Google (`AIza`), Stripe (`sk_live_`, `rk_test_`, ...),
  GitHub (`ghp_` ..., `github_pat_`), `sk-`; JWTs; PEM blocks.
- `Basic <token>` / `Bearer <token>` anywhere, when the token looks like one
  (16+ characters, or 8+ with a digit, `+`, `/`, `=` or an inner capital), so
  "Basic authentication failed" stays; a word that does not qualify is
  skipped, never consumed ("basic Basic <token>" is caught); the whole
  `Authorization:` value always.

## Exit codes

When several apply, the leftmost wins: 2 > 3 > 4 > 1 > 0.

| code | meaning |
| --- | --- |
| 0 | no entries at WARNING or above |
| 1 | WARNING entries, none at ERROR or above |
| 2 | usage error, including a refused `--output` or a `--bucket` over 2,000 buckets |
| 3 | input missing, unreadable, invalid, a symlink, or over `--max-bytes` |
| 4 | entries at ERROR or above (ERROR, ERR, CRITICAL, FATAL, ALERT, EMERG) |

# gitarch

Git Archaeologist: a read-only summary of a git repository's history. It
shows which files and directories change most (by number of commits and by
lines changed, all time and recently), when the repository was active, when
each file was introduced, last changed and renamed, and which files were
deleted. Standard library only; Python 3.11 and 3.12; needs `git` on PATH.

## Usage

```bash
gitarch [REPO] [--rev REV] [--path PATH] [--format text|md|html]
        [--recent DAYS] [--authors] [--messages] [--diffs]
        [--output PATH [--force]] [--timeout SECONDS]
```

Examples (synthetic paths):

```bash
gitarch                                         # the repository in ., text on stdout
gitarch ~/src/app --format html --output ~/reports/app.html
gitarch ~/src/app --rev v1.2.0 --recent 30      # an older revision, 30-day hotspots
gitarch ~/src/app --path src/core/db.py         # one file, followed across renames
gitarch ~/src/app --path src/core/db.py --messages --diffs
gitarch ~/src/app --authors                     # author names with commit counts
```

The report has the same sections in every format: a header (repository,
revision and short hash, commits, merges, first and last commit date in
UTC, author count, which of messages/diffs/authors are on, `redaction: on`),
an overview sentence, hotspots (top 15 files and directories by changes and
by churn, all time and for the `--recent` DAYS before the newest analysed
commit, default 90), activity per ISO week (per month when the history
spans more than a year), the file timeline and deleted files. `--path` adds
the commits that touched the path. Reports are reproducible: the same
repository and arguments give byte-identical output; the current date or
time never appears.

## Safety rules, in plain words

- gitarch only reads. The only program it starts is `git`, without a shell,
  with a fixed list of arguments, and only these read-only subcommands:
  `rev-parse`, `log`, `show`, `diff-tree`, `ls-files`, `cat-file`. The one
  documented exception is `git --version` (exactly that, with the same
  environment, timeout and output cap), run once for the report header's
  `git:` line.
- git is found once, on the absolute entries of PATH only (`.`, empty and
  relative entries are ignored), and always called by that absolute path.
  gitarch refuses (exit 3) a git that resolves inside the repository or
  whose directory is the current directory.
- A repository cannot make gitarch run its programs: hooks, the pager,
  fsmonitor, external diff drivers, textconv filters and signature checks
  (and so `gpg.program`) are switched off on every call, your global and
  system git config and the system attributes file are ignored
  (`core.attributesFile=/dev/null`), lazy fetching from promisor remotes
  is off (`GIT_NO_LAZY_FETCH=1`), and any `GIT_*` environment variable is
  removed before git starts.
- Paths are taken literally (`--literal-pathspecs`): `--path ':!x'` or
  `--path ':(attr:foo)x.py'` names a file called exactly that, and a
  `--path` that matches nothing in the history is exit 3 "path not in
  history".
- Each git call has a timeout (60 s, `--timeout`) and hard output limits
  (100 MB on stdout, 1 MB on stderr), read as a stream so nothing beyond
  the limit is kept. git runs in its own process group; a timeout or an
  output limit kills the whole group, including any child it started
  (SIGTERM, then SIGKILL after 2 s), and gitarch exits 3 ("git timed out"
  or "output limit").
- `--rev` may not start with `-`, and git always reads it as a revision,
  never as an option; `--path` is always passed as a path.
- `--output` never writes inside the analysed repository (work tree or
  `.git`), never through a symlink, and never over an existing file unless
  you pass `--force`. For a linked worktree it also refuses the common
  directory and the main work tree that holds it.

## Repository boundary

gitarch reads one ordinary repository: its working tree and its own .git
directory. It refuses (exit 3, clear message, before any git call) anything
that would make git read or write elsewhere or reach the network. Linked
worktrees are allowed only with --allow-linked-worktree, which adds the
resolved common directory to scope and nothing else.

M1.1 refuses partial clones, alternates, includes, gitdir pointers and
symlinked git dirs.

These checks run in pure Python before any git process starts: bare
repositories; a symlinked repository path, `.git`, or `HEAD`, `config`,
`refs`, `packed-refs`, `objects`, `info`, `commondir`, `objects/info` or
`objects/pack`; a `.git` file unless `--allow-linked-worktree` and it is a
genuine linked worktree; a `commondir` file in an ordinary repository;
non-empty `objects/info/alternates` or `http-alternates`; `.promisor` packs;
and in the repository config (and `config.worktree` with
`extensions.worktreeConfig`) any `[include]` or `[includeIf]` section,
`core.attributesFile`, `core.worktree`, `extensions.partialClone`,
`remote.*.promisor`, `remote.*.partialCloneFilter`, `mailmap.file` or
`mailmap.blob`. The config is read by a strict reader: anything it cannot
read without guessing is refused. Every git call then gets an explicit
`GIT_DIR` and `GIT_WORK_TREE`.

## Privacy defaults

- No commit messages, no diffs and no author names by default. Commit
  subjects (first line only, at most 120 characters) appear only with
  `--messages`; diffs only with `--path ... --diffs` (the newest 10 commits,
  at most 200 lines each, binary diffs skipped); author names with commit
  counts only with `--authors`. Author e-mail addresses are never shown.
- Everything gitarch prints or writes passes through the same redaction as
  Log Lens (see "What is redacted" above), with no way to turn it off.
- File names, subjects and diff lines are escaped for the output format:
  control characters as `\xNN`, bidi and line-separator characters as
  `\uXXXX`; the HTML report has a strict Content-Security-Policy and no
  scripts, links, images or event handlers.

## Exit codes

| code | meaning |
| --- | --- |
| 0 | ok |
| 2 | usage error: unknown option, a `--rev` starting with `-`, `--diffs` without `--path`, `--force` without `--output`, or a refused `--output` |
| 3 | git: not a git repository, no commits, unknown revision, a `--path` not in history, git missing or found inside the repository or the current directory, a git call timed out, git output too large, or a repository outside the boundary (refused before any git call) |
