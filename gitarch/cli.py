"""Command line for gitarch. Everything printed passes through _redact."""
import argparse
import os
import sys

from gitarch import history
from gitarch._redact import redact
from gitarch.gitcmd import DEFAULT_TIMEOUT, Git, GitError, UsageError, check_rev
from gitarch.report import FORMATS, Report, render

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


def _days(value):
    number = int(value)
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
    parser.add_argument("--format", choices=FORMATS, default="text",
                        help="report format (default: text)")
    parser.add_argument("--recent", type=_days, default=history.RECENT_DAYS,
                        metavar="DAYS",
                        help=f"recent hotspots cover the DAYS before the newest "
                             f"analysed commit (default: {history.RECENT_DAYS})")
    parser.add_argument("--authors", action="store_true",
                        help="list author names with commit counts (e-mails never)")
    parser.add_argument("--messages", action="store_true",
                        help="show commit subjects (first line, at most "
                             f"{history.SUBJECT_CHARS} characters, redacted)")
    parser.add_argument("--diffs", action="store_true",
                        help=f"with --path: diffs of the newest {history.DIFF_COMMITS} "
                             f"commits, at most {history.DIFF_LINES} lines each, "
                             "redacted; binary diffs skipped")
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
    if args.diffs and args.path is None:
        return _fail("--diffs requires --path", EXIT_USAGE)
    git = Git(args.repo, timeout=args.timeout)
    try:
        model = history.build(git, args.rev, path=args.path, recent_days=args.recent,
                              authors=args.authors)
        subjects = history.subjects(git, model) if args.messages else {}
        diffs = history.diffs(git, model) if args.diffs else None
    except GitError as exc:
        return _fail(str(exc), EXIT_GIT)
    name = os.path.basename(os.path.abspath(args.repo)) or "/"
    _write(sys.stdout, render(Report(name, model, args.messages, diffs, subjects),
                              args.format))
    return EXIT_OK
