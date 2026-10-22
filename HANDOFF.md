# HANDOFF — devtools

## Status: keycheck noise (BRIEF-DEVWS-044 / DEC-465, DEC-469, branch devtools/keycheck-noise, not pushed)
- Default skips ~/.local/share/uv and ~/.claude/plugins (home relative); `--all` scans them, the safety skips stay on. `*secret*`/`*token*` only when the last extension is not .py .h .md .msg .txt (backup variants judged after the suffix); exact patterns unchanged. IN-REPO-NOT-IGNORED under tests/ or test/ in its repo: note "in-repo fixture?", still flagged. keycheck/1 gains `skips` (top level) and `note` (per file); text/md show both.
- Test-first: 13 new tests (tests/test_keycheck_noise.py), 11 failed on ae49c58, 2 pass by design; 1 cycle. Existing test edits: my_secret.txt and password_token.txt renamed to .json (now source/doc names), test_json_schema expects skips and note. Mutation (no extension check) fails 5 tests. Tests: 857 in total (was 844); 3.12 and 3.11 OK. Live DevWS run: 6 OK rows plus the devtools fixture tests/fixtures/sample_repo/.env (IN-REPO-NOT-IGNORED, OTHERS-CAN-READ, in-repo fixture?), exit 1. Evidence in ~/brief-044/.

### Next (keycheck noise)
- Kyle reviews and pushes.

## Status: keycheck (BRIEF-DEVWS-041 / DEC-449, DEC-459, DEC-462, branch devtools/keycheck, not pushed)
- New tool #8: `keycheck [ROOT ...] [--format text|md|json] [--max-age-days 180]`, default root home. Name-only matching (brief list plus .openhands/settings.json and .claude/.credentials.json), backup variants STALE-COPY; flags IN-REPO-NOT-IGNORED (first), OTHERS-CAN-READ (POSIX bits, Windows icacls text), STALE-COPY, OLD, SYMLINK, OK; exit 0/1/2; JSON keycheck/1. Never opens a matched file (scandir/lstat, git check-ignore --stdin, icacls); skips .git, node_modules, __pycache__, site-packages, venvs, ~/.cache, ~/.local/share/docker, other devices.
- Test-first: commit 1 tests + stub (45 failures at their own assertions), commit 2 implementation (1 cycle). Existing test edits: PACKAGES in test_xray_adoption and test_askdata_isolation. Mutation (every in-repo file treated as ignored) fails 5 tests. Tests: 844 in total (was 794); 3.12 and 3.11 OK. Live DevWS run done (read-only, nothing fixed); evidence in ~/brief-041/.

### Next (keycheck)
- Kyle reviews and pushes; the Windows live run (%LOCALAPPDATA%\pc-tasks, the harness key folder, %USERPROFILE%\.ssh) is Kyle's step; every fix goes to a Hub DEC.

## Status: Test Delta renamed_params (BRIEF-DEVWS-039 / DEC-446, branch devtools/testdelta-renamed-params, not pushed)
- JUnit ids unmatched by full id are grouped by base id (trailing [...] removed); equal counts and one status throughout -> renamed_params (not removed/added, not a regression), one warning per base id ("... -- fix the ids", ASCII dashes), a section in text/md/html when any, JSON key renamed_params (additive, testdelta/1). Unequal counts or any status difference: as before. unittest never grouped. --strict-ids: output of 8789cfe byte for byte (SHA-256 pinned in tests/test_testdelta_renamed.py).
- Test-first: 18 new tests failed on 8789cfe, 1 cycle. Existing test edit: test_top_level_keys_in_order expects the new key. Mutation (no status-equality check) fails the two status-difference tests. Tests: 794 in total (was 776); 3.12 and 3.11 OK. CI pin stays 05e771e. Evidence in ~/brief-039/.

### Next (renamed_params)
- Kyle reviews and pushes; the CI pin move needs a Hub decision.

## Status: Ask My Data M2a (BRIEF-DEVWS-037 step 2 / DEC-427, DEC-430, DEC-431, branch devtools/askdata-m2a, not pushed)
- `askdata query SOURCE... --sql` (askdata/query.py, sqlfuncs.py, profiles.py): CSV/TSV, JSONL(.gz) and logs loaded in memory with _file/_file_date; SQLite attached mode=ro. REGEXP, regexp_extract, tz() with a US Central fallback. Guard (a): authorizer denies reads of profile-denied columns (arora_v2, job_scan, gate_decisions; default deny for unlisted text) and of non-source tables; guard (b): aggregate-only default via cell shapes or EXPLAIN Agg opcodes, 500-row cap, --rows refused for profiled sources.
- Test-first: commit 1 tests + stub (61 tests, 252 failures at their own assertions), commit 2 implementation (3 cycles). Tests: 776 in total (was 715); 3.12 and 3.11 OK. Mutations (title allowed / unlisted, no EXPLAIN check, DST start +1 week, no mode=ro, --rows with a profile) each fail tests. Evidence in ~/brief-037/m2a/.
- Known limit (README): guard (b) passes any statement with an aggregate or window function; (a) is the guarantee for profiled data.

### Next (askdata M2a)
- Kyle reviews and pushes; PRJ-DEVWS writes the Completion Report. M2b (model questions) is not authorized.

## Status: Log Lens redact speed (BRIEF-DEVWS-027 / DEC-240, branch devtools/redact-speed)
- One commit per step: (1) scripts/redact_bench.py, a timing harness (seeded 20 MB ordinary-line log in a temp dir, redact() alone and loglens end to end, 3 runs, median; no timing assertion in CI). (2) Pre-checks in loglens/redact.py: each rule has a gate that is a necessary condition of its regex, checked on the current text (PEM "-----BEGIN ", [:=] for header/key rules, "@" and "://", provider prefixes, >= 13 \d for cards, plus a < 13 digit fast path in _cards). Hub-visible addition beyond the brief's list: (?i) word gates for the multi-word, spoken-form and key=value rules ("key" covers api_key/private_key/access_key/account_key), compiled with the rules' own case folding, never str.lower(); tests/test_redact_gates.py enumerates every keyword alternative from the rules' own patterns and checks each case variant (incl. long s, Kelvin sign) passes its gate. No pattern, rule or order changed; gitarch/_redact.py and testdelta/_redact.py refreshed. (3) Differential test tests/test_redact_differential.py against the frozen tests/redact_reference.py (5f30924, SHA-256 pinned): 54,454 inputs, byte-identical; every rule fires and every gate skips >= 10 times.
- Mutations (git-archive copies in scratch dirs): provider gates reduced to "ghp_" -> 117 failures incl. 5 differential tests; card threshold 20 -> 15 failures incl. test_cards, test_random_lines, test_existing_test_inputs.
- Timing, 20 MB log, median of 3 (before -> after): redact() alone 11.48 -> 4.15 s (2.8x) on 3.12, 11.84 -> 4.47 s (2.6x) on 3.11; loglens end to end 12.02 -> 6.34 s (1.9x) on 3.12, 12.58 -> 6.31 s (2.0x) on 3.11. The 2x target is met for redact() itself; end to end is about 1.9x on 3.12 and 2.0x on 3.11 because log reading and normalise are outside this brief.
- Tests: 640 in total (was 627); 3.12 and 3.11 OK locally; 3.13 not installed locally, CI only.

### Next (redact speed)
- PR open (last line: "Do not merge until the Hub names the head hash (BRIEF-DEVWS-027)"); Hub names the head hash, then merge.

## Status: Test Delta M1.1 (BRIEF-DEVWS-026 / DEC-232, branch devtools/testdelta-m1.1, no PR)
- Fixes from the outside review, one commit per task: (1) unittest result lines read from the right end (status first, then the id from the last self-consistent parenthesised group), so names with " ... ", "(" and ")" work; docstring lines always belong to the head above, also between FAIL:/ERROR: and the dashes; a bare status after output-glued status is kept (worst wins). (2) Report-level check: JUnit suite attributes and suite-level error/failure elements, unittest "Ran N" and OK/FAILED footer; a report that says the run failed with no fail/error/xpass parsed is refused (exit 4); other mismatches and a missing unittest summary are loud warnings. (3) Interleaving matrix, 48 cases, none ends in "no regressions"; it found "FAILcaptured ... ok" read as pass, fixed: a failing status at the start of glued output counts (accepted safe-side false regression, pinned by a test, in README). (4) sk- over-redaction after _ - / documented and pinned as accepted (redact.py unchanged, three copies identical). (5) README, --help exit 4, HANDOFF. (6) Hub DEC-233: a test absent before and fail/error/xpass after is a regression, new category "added failing" (after newly failing in every format, --details like newly failing, JSON "added_failing", exit 1); the interim "N added tests fail" warning is removed; added passing tests stay improvements.
- Tests: 627 in total (was 595); both interpreters OK. JSON schema still testdelta/1.
- Known limit (README): a log cut so that the only evidence of the last test's failure is gone looks like a pass; the loud "no summary line" warning is the signal.

### Next (Test Delta M1.1)
- PR #15 open (last line: "Do not merge until the Hub names the head hash (BRIEF-DEVWS-026)"); Hub names the head hash, then merge.

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
- Import Boundary Guard (BRIEF-DEVWS-035 / DEC-407, branch devtools/xray-boundaries, not pushed): `--cycles` and `--boundaries [--independent a,b]` in repo_xray/boundaries.py print text|md|json to stdout, exit 0/1/2/4; config from [tool.repo-xray] independent; devtools adopts it for its seven tool packages in pyproject.toml; tests in tests/test_xray_boundaries.py and tests/test_xray_adoption.py.
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
