"""Command line for gitarch. Everything printed passes through _redact."""
import argparse
import sys

from gitarch._redact import redact
from gitarch.gitcmd import DEFAULT_TIMEOUT, Git, GitError, UsageError, check_rev

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GIT = 3

EPILOG = """\
exit codes:
  0  ok
  2  usage error (including a --rev starting with "-")
  3  not a git repository, no commits, unknown revision, git missing,
     a git call timed out, or git output too large

gitarch only reads: it runs git (rev-parse, log, show, diff-tree, ls-files,
cat-file) with hooks, pagers, external diffs, textconv and signature checks
switched off, and everything it prints passes through redaction.
"""


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgError(message)

    def _print_message(self, message, file=None):
        if message:
            _write(file or sys.stderr, message)


def _positive(value):
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return number


def _build_parser():
    parser = _Parser(prog="gitarch", epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter,
                     description="Summarise the history of a git repository "
                                 "(read-only).")
    parser.add_argument("repo", nargs="?", default=".", metavar="REPO",
                        help="path to the repository (default: .)")
    parser.add_argument("--rev", default="HEAD",
                        help="revision to analyse (default: HEAD); must not "
                             "start with '-'")
    parser.add_argument("--path", metavar="PATH",
                        help="only this file or directory (a file's renames "
                             "are followed)")
    parser.add_argument("--timeout", type=_positive, default=DEFAULT_TIMEOUT,
                        metavar="SECONDS",
                        help=f"per git call timeout (default: {DEFAULT_TIMEOUT})")
    return parser


def _write(stream, text):
    """The single exit point for everything gitarch prints; always redacted."""
    stream.write(redact(text))


def _fail(message, code):
    _write(sys.stderr, f"gitarch: {message}\n")
    return code


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgError as exc:
        _write(sys.stderr, parser.format_usage())
        return _fail(str(exc), EXIT_USAGE)
    except SystemExit as exc:          # --help
        return exc.code or EXIT_OK
    try:
        check_rev(args.rev)
    except UsageError as exc:
        return _fail(str(exc), EXIT_USAGE)
    try:
        sha = Git(args.repo, timeout=args.timeout).resolve(args.rev)
    except GitError as exc:
        return _fail(str(exc), EXIT_GIT)
    _write(sys.stdout, f"gitarch: {args.rev} = {sha}; report not implemented yet\n")
    return EXIT_OK
