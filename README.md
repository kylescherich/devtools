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
