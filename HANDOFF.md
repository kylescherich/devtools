# HANDOFF — devtools

## Status
- BRIEF-DEVWS-015 task 1 (bootstrap) done: package `repo_xray`, console script `repo-xray`, CLI prints usage and exits 2, one unittest.
- No analysis code yet.

## Next tasks (BRIEF-DEVWS-015, branch devtools/repo-xray-mvp)
2. Analyzer: file walk + ast extraction into a plain dict model (packages, modules, entry points, classes, functions with signatures and first docstring line, imports). Fixture package under tests/fixtures/ covering relative imports, a star import, an importlib call, a class with methods, and a test file that imports two modules.
3. Report: Markdown and single-file HTML renderers from the model; inline-SVG module dependency graph (simple layered layout, no external assets); escaping test with a fixture whose docstring contains <script>.
4. CLI + end-to-end: `repo-xray <path> --output <file> [--format html|md] [--max-files N]`; exit 0 ok, 2 usage, 3 target unreadable or too large; e2e test on the fixture and on devtools itself (output parses with html.parser and contains the fixture module names).
5. Acceptance runs on powerplan and pharmacy-rag, read-only; git status of both unchanged.

Then: PR from devtools/repo-xray-mvp; Kyle merges.

## Rules
See CLAUDE.md "Hard rules for repo_xray". Tests: python3 -m unittest discover -v
