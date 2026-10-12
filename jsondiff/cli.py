import argparse
import os
import sys
from pathlib import Path

from jsondiff.diff import diff_shapes
from jsondiff.report import render_html, render_md, render_terminal
from jsondiff.shape import (DEFAULT_MAX_BYTES, DEFAULT_MAX_DEPTH, DEFAULT_MAX_RECORDS,
                            InvalidInput, UsageError, is_jsonl, build_shape,
                            parse_keys)

EXIT_SAME = 0
EXIT_CHANGED = 1
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_BREAKING = 4

EPILOG = """\
exit codes:
  0  no structural change
  1  changes, none potentially breaking
  2  usage error (bad arguments, bad --key, --key with JSONL, refused --output)
  3  input missing, unreadable, a directory or symlink, invalid JSON,
     not UTF-8, or too large
  4  at least one potentially breaking change (a candidate for review)

Example values are never shown unless --values is given. Arrays are compared
by their merged element shape, not by position, unless --key is given.
"""


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgError(message)


def _build_parser():
    parser = _Parser(prog="jsondiff", epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter,
                     description="Compare the structure (shape) of two JSON or "
                                 "JSONL files.")
    parser.add_argument("old", help="old JSON / JSONL file")
    parser.add_argument("new", help="new JSON / JSONL file")
    parser.add_argument("--output", help="write the report to this file "
                        "(default: terminal report on stdout)")
    parser.add_argument("--format", choices=["html", "md"],
                        help="report format for --output (default: md for .md, "
                             "else html)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite --output if it exists")
    parser.add_argument("--jsonl", action="store_true",
                        help="treat both inputs as JSONL (one record per line); "
                             "automatic for .jsonl / .ndjson")
    parser.add_argument("--key", action="append", default=[],
                        metavar="[PATH=]FIELD",
                        help="match array elements by FIELD (root array) or at "
                             "PATH; repeatable; single JSON documents only")
    parser.add_argument("--all", action="store_true",
                        help="also list unchanged paths")
    parser.add_argument("--values", action="store_true",
                        help="include up to 3 example values per changed path "
                             "(off by default for privacy)")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                        help=f"per-file byte limit (default: {DEFAULT_MAX_BYTES}); "
                             "JSONL beyond it is sampled")
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS,
                        help=f"JSONL record limit (default: {DEFAULT_MAX_RECORDS})")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
                        help=f"nesting depth limit (default: {DEFAULT_MAX_DEPTH})")
    return parser


def _fail(message, code):
    print(f"jsondiff: {message}", file=sys.stderr)
    return code


def _check_input(label, path):
    if not os.path.lexists(path):
        return f"{label} file not found"
    if os.path.islink(path):
        return f"{label} file is a symlink; symlinks are not followed"
    if os.path.isdir(path):
        return f"{label} path is a directory"
    if not os.path.isfile(path):
        return f"{label} path is not a regular file"
    return None


def _check_output(output, inputs, force):
    target = Path(output).resolve()
    for path in inputs:
        directory = Path(path).resolve().parent
        if target == directory or directory in target.parents:
            return "refusing to write --output inside the directory of an input"
    if os.path.lexists(output) and not force:
        return "--output exists; use --force to overwrite"
    if not target.parent.is_dir():
        return "--output directory does not exist"
    return None


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgError as exc:
        return _fail(str(exc), EXIT_USAGE)
    except SystemExit as exc:          # --help
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    for name in ("max_bytes", "max_records", "max_depth"):
        if getattr(args, name) < 1:
            return _fail(f"--{name.replace('_', '-')} must be at least 1", EXIT_USAGE)

    try:
        keys = parse_keys(args.key)
    except UsageError as exc:
        return _fail(str(exc), EXIT_USAGE)
    jsonl = True if args.jsonl else None
    if keys and any(is_jsonl(p, jsonl) for p in (args.old, args.new)):
        return _fail("--key applies to single JSON documents, not JSONL", EXIT_USAGE)
    if args.format and not args.output:
        return _fail("--format needs --output", EXIT_USAGE)
    if args.output:
        problem = _check_output(args.output, (args.old, args.new), args.force)
        if problem:
            return _fail(problem, EXIT_USAGE)

    shapes = []
    for label, path in (("old", args.old), ("new", args.new)):
        problem = _check_input(label, path)
        if problem:
            return _fail(problem, EXIT_INPUT)
        try:
            shapes.append(build_shape(path, jsonl=jsonl, max_bytes=args.max_bytes,
                                      max_records=args.max_records,
                                      max_depth=args.max_depth,
                                      values=args.values, keys=keys or None))
        except InvalidInput as exc:
            return _fail(f"{label} file: {exc}", EXIT_INPUT)
        except UsageError as exc:
            return _fail(str(exc), EXIT_USAGE)

    try:
        result = diff_shapes(shapes[0], shapes[1], keys=keys or None)
    except UsageError as exc:
        return _fail(str(exc), EXIT_USAGE)

    if args.output:
        fmt = args.format or ("md" if args.output.lower().endswith(".md") else "html")
        render = render_md if fmt == "md" else render_html
        text = render(result, show_all=args.all)
        try:
            with open(args.output, "w" if args.force else "x", encoding="utf-8",
                      newline="\n") as f:
                f.write(text)
        except FileExistsError:
            return _fail("--output exists; use --force to overwrite", EXIT_USAGE)
        except OSError as exc:
            return _fail(f"cannot write --output: {exc.strerror or exc}", EXIT_USAGE)
    else:
        sys.stdout.write(render_terminal(result, show_all=args.all))

    return _exit_code(result)


def _exit_code(result):
    if any(c["breaking"] for c in result["changes"]):
        return EXIT_BREAKING
    changed_elements = any(m["changed"] for m in result["key_matches"])
    if result["changes"] or changed_elements:
        return EXIT_CHANGED
    return EXIT_SAME
