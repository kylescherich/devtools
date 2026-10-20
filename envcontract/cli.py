"""Command line for envcontract. Everything printed passes through _redact.

envcontract opens only *.py and *.example/*.template/*.sample files inside
REPO_DIR, each through scan.resolve(); it imports and runs nothing from the
repo, writes nothing, and uses no subprocess or network.
"""
import argparse
import os
import sys

from envcontract import scan
from envcontract._redact import redact
from envcontract.report import FORMATS, build, differences, render

EXIT_OK = 0
EXIT_DIFFERENCES = 1
EXIT_USAGE = 2
EXIT_INPUT = 4

EPILOG = """\
exit codes:
  code  meaning
  0     consistent: every name read is documented and every documented
        name is read (dynamic reads and unreadable files do not count)
  1     differences: undocumented or unused names (with no example file,
        any name read)
  2     usage error, or a refused --example (not *.example, *.template or
        *.sample, a symlink to another name, or outside REPO_DIR)
  4     REPO_DIR missing, not a directory or unreadable, or --example
        missing or unreadable

envcontract reads *.py files with ast (never imports or runs them) and only
*.example, *.template and *.sample files as documentation. The report holds
names, paths, line numbers and counts, never a value.
"""


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgError(message)

    def _print_message(self, message, file=None):
        if message:
            _write(file or sys.stderr, message)


def _build_parser():
    parser = _Parser(prog="envcontract", epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter,
                     description="Does the environment a program reads match what "
                                 "it documents? Compares env names read in *.py "
                                 "with names in *.example/*.template/*.sample.")
    parser.add_argument("repo_dir", metavar="REPO_DIR", help="the repository to check")
    parser.add_argument("--example", metavar="FILE",
                        help="use only this example file (relative paths are "
                             "taken from REPO_DIR); must be inside REPO_DIR")
    parser.add_argument("--format", choices=FORMATS, default="text",
                        help="report format (default: text)")
    return parser


def _write(stream, text):
    """The single exit point for everything envcontract prints; always redacted.
    Messages put the path last, so redact() never reads it as a key."""
    stream.write(redact(text))


def _fail(message, code):
    _write(sys.stderr, f"envcontract: {message}\n")
    return code


def _example(arg, repo, root):
    """(exit code, message) for a refused or unreadable --example, or
    (None, (rel, data)). The name is checked before anything touches the file."""
    path = arg if os.path.isabs(arg) else os.path.join(repo, arg)
    if not os.path.basename(path).endswith(scan.DOC_SUFFIXES):
        return EXIT_USAGE, (f"refused (only *.example, *.template and *.sample "
                            f"files are read): --example {arg}")
    if root is None:
        return None, None
    if not os.path.lexists(path):
        return EXIT_INPUT, f"not found: --example {arg}"
    try:
        real = scan.resolve(path, scan.DOC_SUFFIXES, root)
    except scan.Refused as exc:
        if str(exc) == "not a regular file":
            return EXIT_INPUT, f"not a regular file: --example {arg}"
        return EXIT_USAGE, f"refused ({exc}): --example {arg}"
    except OSError:
        return EXIT_INPUT, f"cannot read: --example {arg}"
    try:
        data = scan.read(real)
    except OSError:
        return EXIT_INPUT, f"cannot read: --example {arg}"
    return None, (os.path.relpath(real, root).replace(os.sep, "/"), data)


def _root(repo):
    """realpath of REPO_DIR, or an error message."""
    if not os.path.isdir(repo):
        return None, f"not a directory: {repo}"
    root = os.path.realpath(repo)
    try:
        os.listdir(root)
    except OSError:
        return None, f"cannot read: {repo}"
    return root, None


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgError as exc:
        _write(sys.stderr, parser.format_usage())
        return _fail(str(exc), EXIT_USAGE)
    except SystemExit as exc:          # --help
        return exc.code or EXIT_OK
    if args.example is not None:       # a refused name wins over a missing repo
        code, message = _example(args.example, args.repo_dir, None)
        if code is not None:
            return _fail(message, code)
    root, problem = _root(args.repo_dir)
    if problem:
        return _fail(problem, EXIT_INPUT)
    example = None
    if args.example is not None:
        code, example = _example(args.example, args.repo_dir, root)
        if code is not None:
            return _fail(example, code)
    data = build(scan.scan(root, example), os.path.basename(root))
    _write(sys.stdout, render(data, args.format))
    return EXIT_DIFFERENCES if differences(data) else EXIT_OK
