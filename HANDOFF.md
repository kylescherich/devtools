# HANDOFF — devtools

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
