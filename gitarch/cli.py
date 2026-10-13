"""Command line for gitarch. Everything printed passes through _redact."""
import argparse
import os
import stat
import sys

from gitarch import history
from gitarch._redact import redact
from gitarch.gitcmd import DEFAULT_TIMEOUT, Git, GitError, UsageError, check_rev
from gitarch.preflight import Refused
from gitarch.report import FORMATS, Report, render

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GIT = 3

EPILOG = """\
exit codes:
  code  meaning
  0     ok
  2     usage error: unknown option, a --rev starting with "-", --diffs
        without --path, --force without --output, or a refused --output
  3     git: not a git repository, no commits, unknown revision, git
        missing, a git call timed out, or git output too large; or a
        repository outside gitarch's boundary (refused before any git call)

gitarch reads one ordinary repository: its work tree and its own .git
directory. It refuses bare repositories, partial clones, alternates, config
includes, gitdir pointers and symlinked git dirs; a linked worktree only
with --allow-linked-worktree.

--output never overwrites an existing file unless --force is given, never
writes through a symlink, and refuses any place inside the analysed
repository (its work tree or its .git directory).

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
    parser.add_argument("--output", metavar="PATH",
                        help="write the report to PATH (default: stdout); never "
                             "inside the repository")
    parser.add_argument("--force", action="store_true",
                        help="allow --output to replace an existing regular file")
    parser.add_argument("--allow-linked-worktree", action="store_true",
                        help="accept a linked worktree (its common directory joins "
                             "the scope)")
    parser.add_argument("--timeout", type=_positive, default=DEFAULT_TIMEOUT,
                        metavar="SECONDS",
                        help=f"per git call timeout (default: {DEFAULT_TIMEOUT})")
    return parser


def _write(stream, text):
    """The single exit point for everything gitarch prints; always redacted."""
    stream.write(redact(text))


def _check_output(path, force):
    """Refusal message for --output PATH, or None. Re-checked atomically by
    the O_EXCL / O_NOFOLLOW open in _open_output."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"--output {path}: cannot check ({exc.strerror})"
    if stat.S_ISLNK(info.st_mode):
        return f"--output {path}: is a symlink; refusing to follow it"
    if not force:
        return f"--output {path}: already exists (use --force to replace it)"
    if not stat.S_ISREG(info.st_mode):
        return f"--output {path}: not a regular file"
    return None


def _check_not_in_repo(path, scope):
    """Refusal message when PATH would land in the repository's work tree,
    git directory or, for a linked worktree, the common directory and the
    main work tree that holds it (Scope.output_roots)."""
    if scope.inside(path, scope.output_roots):
        return f"--output {path}: refusing to write inside the analysed repository"
    return None


def _open_output(path, force):
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_TRUNC if force else os.O_EXCL
    fd = os.open(path, flags, 0o644)
    return os.fdopen(fd, "w", encoding="utf-8", newline="\n")


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
    if args.force and not args.output:
        return _fail("--force needs --output", EXIT_USAGE)
    if args.output:
        refusal = _check_output(args.output, args.force)
        if refusal:
            return _fail(refusal, EXIT_USAGE)
    try:
        git = Git(args.repo, timeout=args.timeout,
                  allow_linked=args.allow_linked_worktree)
    except Refused as exc:
        return _fail(f"refused: {exc}", EXIT_GIT)
    refusal = _check_not_in_repo(args.output, git.scope) if args.output else None
    if refusal:
        return _fail(refusal, EXIT_USAGE)
    try:
        model = history.build(git, args.rev, path=args.path, recent_days=args.recent,
                              authors=args.authors)
        subjects = history.subjects(git, model) if args.messages else {}
        diffs = history.diffs(git, model) if args.diffs else None
    except GitError as exc:
        return _fail(str(exc), EXIT_GIT)
    name = os.path.basename(os.path.abspath(args.repo)) or "/"
    document = render(Report(name, model, args.messages, diffs, subjects), args.format)
    if not args.output:
        _write(sys.stdout, document)
        return EXIT_OK
    try:
        with _open_output(args.output, args.force) as stream:
            _write(stream, document)
    except OSError as exc:
        return _fail(f"--output {args.output}: cannot write ({exc.strerror})", EXIT_USAGE)
    return EXIT_OK
