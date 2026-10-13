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
- Environment: copy `os.environ`, delete every key starting with `GIT_`, then set `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`, `LC_ALL=C`.
- Every call starts: `git --no-pager -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.pager=cat -c color.ui=false -c log.showSignature=false -C <repo> <subcommand> ...`
- Every log/show/diff-tree call also carries `--no-ext-diff --no-textconv`. Never use `%G` format placeholders.
- Allowed subcommands only: rev-parse, log, show, diff-tree, ls-files, cat-file, enforced by an allow-list inside the runner (anything else raises before running). Never status, checkout, fetch, pull, gc, blame.
- User input: `--rev` must not start with `-` (usage error, exit 2) and is passed after `--end-of-options`; `--path` always after `--`.
- Everything printed passes through `gitarch/_redact.py`, a byte-for-byte copy of `loglens/redact.py` (no import between packages; `test_redact_copy_identical` checks it).
- Standard library only; Python 3.11 and 3.12; pure ASCII source and commit messages; both suites OK before every commit. Test repos are synthetic, built at test time in temp dirs by `tests/gitarch_fixtures.py`.
