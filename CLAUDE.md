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
