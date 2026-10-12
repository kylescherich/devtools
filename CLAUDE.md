# Claude Context

## Project Overview

**devtools** is a public repository of standalone CLI tools for software development workflows. Each tool is packaged independently with no shared framework. All tools use only the Python standard library and are designed to analyze repositories without executing or importing code from the target repository.

## Repository Structure

```
devtools/
├── repo_xray/          # Repository analysis and documentation tool
│   ├── __init__.py
│   ├── __main__.py
│   └── cli.py
├── tests/              # Test suite
│   ├── __init__.py
│   └── test_cli.py
├── pyproject.toml      # Build configuration
└── .gitignore
```

## Development Principles

1. **Standalone Tools**: Each tool is a separate package with its own namespace
2. **Standard Library Only**: No external dependencies; pure Python stdlib
3. **Safe Analysis**: Tools never execute or import code from analyzed repositories
4. **Simple Testing**: Tests run with `python3 -m unittest discover -v`

## Current Tools

### repo-xray

A repository analysis tool that generates documentation and visualizations of repository structure.

**Status**: Bootstrap complete, core functionality pending

**Usage**:
```bash
repo-xray <path> [--output FILE] [--format html|md] [--max-files N]
```

## Testing

Run all tests:
```bash
python3 -m unittest discover -v
```

Run specific test:
```bash
python3 -m unittest tests.test_cli
```

## Installation

Development install:
```bash
pip install -e .
```

## Contributing

When adding new tools:
- Create a new package directory (e.g., `tool_name/`)
- Add entry point in `pyproject.toml`
- Use only standard library
- Never execute target repository code
- Add comprehensive tests
