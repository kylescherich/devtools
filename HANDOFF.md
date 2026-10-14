# HANDOFF — devtools

## Status: Test Delta M1.1 (BRIEF-DEVWS-026 / DEC-232, branch devtools/testdelta-m1.1, no PR)
- Fixes from the outside review, one commit per task: (1) unittest result lines read from the right end (status first, then the id from the last self-consistent parenthesised group), so names with " ... ", "(" and ")" work; docstring lines always belong to the head above, also between FAIL:/ERROR: and the dashes; a bare status after output-glued status is kept (worst wins). (2) Report-level check: JUnit suite attributes and suite-level error/failure elements, unittest "Ran N" and OK/FAILED footer; a report that says the run failed with no fail/error/xpass parsed is refused (exit 4); other mismatches, a missing unittest summary and failing added tests ("N added tests fail", Kyle's addition; still listed under Added, exit unchanged) are loud warnings. (3) Interleaving matrix, 48 cases, none ends in "no regressions"; it found "FAILcaptured ... ok" read as pass, fixed: a failing status at the start of glued output counts (accepted safe-side false regression, pinned by a test, in README). (4) sk- over-redaction after _ - / documented and pinned as accepted (redact.py unchanged, three copies identical). (5) README, --help exit 4, HANDOFF.
- Tests: 621 in total (was 595); both interpreters OK. JSON schema still testdelta/1.
- Known limit (README): a log cut so that the only evidence of the last test's failure is gone looks like a pass; the loud "no summary line" warning is the signal.

### Next (Test Delta M1.1)
- Hub decides whether a failing added test should become a regression; Kyle says when to open the PR (last line: "Do not merge until the Hub names the head hash (BRIEF-DEVWS-026)").

## Status: Test Delta M1 (BRIEF-DEVWS-024 / DEC-226, branch devtools/testdelta-m1, no PR)
- Tasks 1-5 done, one commit each: bootstrap (package, console script, _redact.py byte copy + identity test, six-package no-cross-import test, CLAUDE.md rules); readers (JUnit XML with DOCTYPE/ENTITY and non-UTF-8 refusal before ElementTree, unittest -v 3.11+/older forms, docstring lines, subtests, setUpClass, FAIL/ERROR blocks, 3.12 --durations; times never invented); comparison (newly failing / removed / newly skipped regressions, fixed / unskipped / added, still failing, other changes, slower/faster with ratio AND minimum, n/a never 0, total-count-drop warning); reports text/md/html/json (per-value redact then escape, final redact pass a tested no-op, --details for newly failing only, traceback redacted then capped at 20 lines, JSON schema testdelta/1); CLI exit codes 0/1/2/4 (2 > 4 > 1 > 0), --fail-on-slow, --output guards (no overwrite without --force, no symlink, never an input).
- Tests: 595 in total (590 after BRIEF-DEVWS-024, was 444); both interpreters OK. Real unittest -v output of the running interpreter is parsed in a test (format drift guard; 3.13 covered by CI only).
- Fixed (BRIEF-DEVWS-025): a token glued to a name with an underscore (test_with_<token>, key_<token>) is now redacted in loglens, gitarch and testdelta; the four provider rules that started with \b now use (?<![A-Za-z0-9]).

### Next (Test Delta)
- Kyle runs the acceptance runs (task 6); then the PR, ending "Do not merge until the Hub names the head hash (BRIEF-DEVWS-024)".

## Status: Git Archaeologist M1 (BRIEF-DEVWS-022 / DEC-198, branch devtools/gitarch-m1, no PR)
- Tasks 1-5 done, one commit each: bootstrap + safety rules in CLAUDE.md; hardened git runner (gitarch/gitcmd.py: allow-list, mandatory -c flags, --no-ext-diff/--no-textconv, cleared GIT_* env, timeout, 100 MB cap, --rev after --end-of-options); history model (overview, file timeline with renames, deleted files, hotspots all-time/recent, weekly/monthly activity); reports text/md/html with --authors, --path, --messages, --diffs; CLI --output guards (no overwrite without --force, no symlink, never inside the repository) and e2e tests.
- Tests: 383 in total (was 342); both interpreters OK. Hostile repo (fsmonitor, hooksPath, pager, textconv, external diff, filters, gpg.program, showSignature, a gpgsig commit) never runs its trap in any CLI mode or format. Mutations (drop --no-textconv, drop GIT_* env clearing, subjects by default) each fail tests.

### Next (gitarch)
- Kyle reviews devtools/gitarch-m1 and opens the PR.

## Status: Log Lens M1.2 (BRIEF-DEVWS-021 / DEC-189, branch devtools/loglens-m1-2, no PR)
- Items 0-5 done, one commit each: privacy boundary in README + redact.py docstring; name-contains keys (pass/pwd/sig/auth as whole components), provider shapes, bare Basic/Bearer; session material (csrf/xsrf/cookie, session/sid last component, Cookie headers to end of line); Luhn-checked cards (13-19 digits) and labelled personal fields (whole component anywhere); multi-word values and spoken forms; URL userinfo only with ":", keep-words, documented over-redaction.
- Tests: 339 in total (was 332); both interpreters OK. Mutations (Luhn removed, exact keys, multi-word extent removed) each fail tests.

### Next (Log Lens)
- Kyle reviews devtools/loglens-m1-2 and opens the PR.

## Status: Log Lens M1.1 (BRIEF-DEVWS-020 / DEC-186, branch devtools/loglens-m1-1, no PR yet)
- Items 1-5 done, one commit each: incident view = top 5 WARNING+ groups by count (ties: first occurrence, then signature; "no warning or error groups" when none); timeline outliers (> 30 days from the median) left off the timeline and listed under a new "warnings" section; explicit --bucket over 2000 buckets -> exit 2 naming the smallest fit; runs of >= 5 empty buckets collapsed (HTML gained a bucket table for this); literal backslashes doubled in text/md; syslog "host app[pid]:" / "host app:" parsed out before grouping, hosts/apps shown in the incident view; signatures computed once in grouping and reused for --context.
- Tests: 332 in total, 92 loglens; both interpreters OK.

### Next (Log Lens)
- Kyle opens the PR devtools/loglens-m1-1 -> main.

## Status: Log Lens milestone 1 (merged as PR #6)
- Tasks 1-5 done, plus fixes A-C: stricter level detection (UPPERCASE, [..]/<..>, level=/lvl=/severity=, or first word + ':'), `--format` renamed to `--input-format`, pure-ASCII sources.
- `loglens FILE... [--format text|md|html] [--output PATH [--force]] [--bucket ...] [--context N] [--raw]`. Exit 0 / 1 WARNING / 4 ERROR+ / 2 usage / 3 input; precedence 2 > 3 > 4 > 1 > 0 (table in --help and README).
- Modules: readers (text/JSONL/gz, guards), redact (the one redact()), group (signatures), timeline (buckets, spikes median + 3*max(MAD,1) from >= 10 buckets, incident view), report (one model, three renderers, per-format escape), cli (output guards: O_EXCL|O_NOFOLLOW or O_TRUNC|O_NOFOLLOW, never an input's directory).
- redact() skips a label followed by just the marker (plain or `&lt;redacted&gt;`), so the final pass over md/html never yields a raw `<redacted>` tag or eats table cells; a marker in the input never shields a secret (tested).
- Tests: 321 in total, 81 loglens (`python3 -m unittest discover` and `uv run --no-project --python 3.11 python -m unittest discover`, both OK). Includes privacy and escaping across all formats with and without --raw, byte-identical reruns, output guards incl. symlinks, sha256 of inputs unchanged, gz + multi-file merge. Mutation spot-checks (escape steps, O_NOFOLLOW, directory guard, --force guard, exit 4) each fail tests.
- Known nits (fixed in M1.1): long timelines from a small --bucket or a far-past timestamp; ambiguous backslashes in text output.

## Status (BRIEF-DEVWS-015, branch devtools/repo-xray-mvp)
- Tasks 1–5 done. `repo-xray <path> --output <file> [--format html|md] [--max-files N]`; exit 0 ok, 2 usage (incl. output inside target), 3 target unreadable/too large.
- Task 6 (review fixes, DEC-156) done: attribute-safe anchors, honest labels, Skipped / not parsed section, 1 MB size cap, Windows junctions treated as links, try-import bookkeeping, --no-docstrings, README.
- Analyzer: stdlib `ast` only, three passes (module names from package structure, collisions, internal vs external); imports collected anywhere in a file; try/importlib/star/`__import__` imports listed as unresolved/dynamic, never guessed. Reads only `.py` and the console-scripts table of `pyproject.toml`; never executes target code; no symlinks, no dotfiles.
- Reports: Markdown + single-file HTML with inline-SVG dependency graph; every source-derived string escaped; names, signatures and first docstring lines only.
- Tests: 29 (`python3 -m unittest discover -v`), incl. hostile-name escaping and a byte-hash check that the target is unchanged.
- Acceptance runs (read-only): pharmacy-rag 35 modules, 3 try-imports unresolved; powerplan 13 modules, none unresolved; both repos' git HEAD + status identical before and after.

## Next
- Kyle opens the PR devtools/repo-xray-mvp → main and merges (deploy key cannot open PRs).
- Hub acceptance: clean-clone test run, mutation spot-check, Kyle's own first-milestone run, then the ChatGPT review before Ask My Data is scoped.
- Known nits: "1 packages" plural in the summary.

## Rules
See CLAUDE.md "Hard rules for repo_xray". Tests: python3 -m unittest discover -v
