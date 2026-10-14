# devtools — working notes for coding agents

## Purpose
devtools is a public repository of standalone command-line developer tools.
Each tool is its own Python package in this repo, with its own console script.
There is no shared framework between tools. First tool: `repo_xray` (`repo-xray`).

## Layout
- `repo_xray/` — the repo-xray package (`cli.py` holds `main(argv=None)`).
- `tests/` — unittest suites; `tests/fixtures/` holds small fixture packages.
- `pyproject.toml` — setuptools build, console scripts, no dependencies.
- `HANDOFF.md` — current status and the next tasks.

## How to test
Run from the repo root:

    python3 -m unittest discover -v

## Hard rules for repo_xray (BRIEF-DEVWS-015)
- Standard library only; analysis uses ast. Never executes or imports code from the target repo.
- Read-only on the target: never write inside it; --output defaults to the current directory and is refused if it resolves inside the target.
- repo-xray reads .py files and the [project.scripts] table of the root pyproject.toml, nothing else.
- Never follows symlinks; skips .git, virtualenvs and __pycache__; never reads or lists dotfiles or config/secret-shaped files (.env*, *.key, *.pem, secrets*).
- .py files over 1 MB (max_file_bytes) are skipped and listed, never opened.
- HTML-escape every source-derived string. Reports contain names, signatures and the first docstring line (optional, --no-docstrings), never full source or string literals.
- Resolve relative imports against the package; list unresolved/dynamic imports (importlib, __import__, star imports, imports inside try) in an "Unresolved / dynamic" section, never guess.
- --max-files guard, default 2000, with a clear message. Exit codes: 0 ok, 2 usage, 3 target unreadable or too large.
- Every test asserts its output is non-empty before checking content.

## Working style
- Small diffs, one feature per step; run the tests after every change.
- Report real command output; never claim a pass without it.
- Work for tasks 2-5 happens on branch devtools/repo-xray-mvp, merged by PR.

## askdata safety rules
- Never weaken `mode=ro`, `PRAGMA query_only=1` or the SQLite authorizer in `askdata/sqliteinspect.py`.
- Never enable SQLite extensions, and never allow `load_extension`, `readfile`, `writefile`, `edit` or `fts3_tokenizer`.
- Never make `--samples` the default; reports stay schema-and-statistics-only unless the user asks.
- Tests that check privacy or leaks assert non-empty output first (DEC-071).
- Milestone 2 (natural-language questions via a model) is not authorized.

## jsondiff rules
- Never print values by default; example values appear only with `--values`.
- Never compare arrays by position without saying so; unkeyed arrays carry the note "array elements compared by merged shape, not position".
- Tests that check privacy or leaks assert non-empty output first (DEC-071).

## Log Lens rules
- Default output never contains a raw log line; raw examples only with `--raw`.
- One function `loglens.redact.redact(text) -> str` runs on EVERYTHING printed or written; no flag, env var or parameter can skip it.
- No tailing, follow mode, watching or daemon. No network, no subprocess, never follow symlinks.
- Standard library only; must run on Python 3.11 and 3.12. Test data is synthetic, generated at test time into temp dirs (`tests/loglens_fixtures.py`).
- Commit messages and source files are pure ASCII; before every commit run both `python3 -m unittest discover` and `uv run --no-project --python 3.11 python -m unittest discover` and commit only if both print OK.

## Git Archaeologist rules (gitarch, BRIEF-DEVWS-022)
- gitarch runs only `git`, via `subprocess.Popen([...], shell=False, start_new_session=True)` by absolute path, in its own process group, with fixed argument lists, a per-call timeout (default 60 s, `--timeout`) and streamed output caps (100 MB stdout, 1 MB stderr); on a timeout or an overrun the whole process group is killed (SIGTERM, SIGKILL after 2 s) and gitarch exits 3 ("git timed out" / "output limit").
- Environment: copy `os.environ`, delete every key starting with `GIT_`, then set `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`, `LC_ALL=C`, `GIT_NO_LAZY_FETCH=1`, `GIT_LITERAL_PATHSPECS=1`, `GIT_ATTR_NOSYSTEM=1`, and the explicit `GIT_DIR` and `GIT_WORK_TREE` from the preflight Scope.
- Every call starts: `/abs/path/git --no-pager --literal-pathspecs -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.pager=cat -c color.ui=false -c log.showSignature=false -c core.attributesFile=/dev/null -C <work tree> <subcommand> ...`
- Every log/show/diff-tree call also carries `--no-ext-diff --no-textconv`. Never use `%G` format placeholders.
- Allowed subcommands only: rev-parse, log, show, diff-tree, ls-files, cat-file, enforced by an allow-list inside the runner (anything else raises before running). Never status, checkout, fetch, pull, gc, blame.
- User input: `--rev` must not start with `-` (usage error, exit 2) and is passed after `--end-of-options`; `--path` always after `--`.
- Everything printed passes through `gitarch/_redact.py`, a byte-for-byte copy of `loglens/redact.py` (no import between packages; `test_redact_copy_identical` checks it).
- Standard library only; Python 3.11 and 3.12; pure ASCII source and commit messages; both suites OK before every commit. Test repos are synthetic, built at test time in temp dirs by `tests/gitarch_fixtures.py`.

## Test Delta rules (testdelta, BRIEF-DEVWS-024)
- testdelta compares two test results (unittest -v text or JUnit XML); it NEVER runs tests. It opens only the two inputs and --output; no network, no subprocess, never follows symlinks.
- XML: refuse any input containing `<!DOCTYPE` or `<!ENTITY` (exit 4) before parsing; inputs must be UTF-8; 50 MB cap per file (`--max-bytes`), checked before reading; parse only with stdlib `xml.etree.ElementTree`, only after those checks.
- Never invent a time: unittest text has times only for tests in a 3.12 `--durations` list; every other time is n/a and n/a is never treated as 0.
- Default output = test ids, statuses and times only. Failure messages and tracebacks only with `--details`, for regressions only (first message line + at most 20 traceback lines). system-out/system-err are never shown in M1.
- Everything printed passes through `testdelta/_redact.py`, a byte-for-byte copy of `loglens/redact.py` (no import between packages; `test_redact_copy_identical` checks it). Test names are untrusted: escape them for terminal (control/bidi visible), md and HTML (single file, strict CSP, no scripts).
- `--output` refused if it exists unless `--force`, and never through a symlink. Reports (text/md/html/json) are byte-for-byte reproducible.
- Exit codes: 0 no regressions, 1 regressions, 2 usage, 4 unreadable/unsupported/refused input.
- Tests that check privacy or leaks assert non-empty output first (DEC-071); sentinels come from `tests/secret_shapes.py` (DEC-195), built at runtime in temp dirs.
- Standard library only; Python 3.11 to 3.13; pure ASCII source, tests and commit messages (write test files via a Python script, non-ASCII as escapes); both suites OK before every commit.
