# HANDOFF — devtools

## Status: Log Lens milestone 1 (branch devtools/loglens-m1, no PR yet)
- Tasks 1-5 done, plus fixes A-C: stricter level detection (UPPERCASE, [..]/<..>, level=/lvl=/severity=, or first word + ':'), `--format` renamed to `--input-format`, pure-ASCII sources.
- `loglens FILE... [--format text|md|html] [--output PATH [--force]] [--bucket ...] [--context N] [--raw]`. Exit 0 / 1 WARNING / 4 ERROR+ / 2 usage / 3 input; precedence 2 > 3 > 4 > 1 > 0 (table in --help and README).
- Modules: readers (text/JSONL/gz, guards), redact (the one redact()), group (signatures), timeline (buckets, spikes median + 3*max(MAD,1) from >= 10 buckets, incident view), report (one model, three renderers, per-format escape), cli (output guards: O_EXCL|O_NOFOLLOW or O_TRUNC|O_NOFOLLOW, never an input's directory).
- redact() skips a label followed by just the marker (plain or `&lt;redacted&gt;`), so the final pass over md/html never yields a raw `<redacted>` tag or eats table cells; a marker in the input never shields a secret (tested).
- Tests: 321 in total, 81 loglens (`python3 -m unittest discover` and `uv run --no-project --python 3.11 python -m unittest discover`, both OK). Includes privacy and escaping across all formats with and without --raw, byte-identical reruns, output guards incl. symlinks, sha256 of inputs unchanged, gz + multi-file merge. Mutation spot-checks (escape steps, O_NOFOLLOW, directory guard, --force guard, exit 4) each fail tests.

### Next (Log Lens)
- Kyle opens the PR devtools/loglens-m1 -> main.
- Known nits: an explicit small --bucket over a long span (or a bogus far-past JSONL timestamp under auto, which falls back to 1d) gives very long timelines; no cap yet. Text format does not escape backslashes, so a literal "\x1b" in a log looks like an escaped control.

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
