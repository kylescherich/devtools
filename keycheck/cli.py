"""Command line for keycheck. Everything printed passes through _redact.

keycheck never opens a matched file: it uses lstat, directory listings,
`git check-ignore` (names only, via stdin) and on Windows `icacls <path>`.
It writes nothing and uses no network.
"""
import argparse
import os
import sys
import time

from keycheck import scan
from keycheck._redact import redact
from keycheck.report import FORMATS, build, flagged, render

EXIT_OK = 0
EXIT_FLAGGED = 1
EXIT_USAGE = 2

EPILOG = """\
flags (one row per matched file, worst first):
  IN-REPO-NOT-IGNORED  inside a git work tree and git check-ignore says it
                       is not ignored (listed first)
  OTHERS-CAN-READ      POSIX group/other read bit; Windows: an ACE granting
                       R, RX, M or F to anyone but the owner, SYSTEM or
                       Administrators
  STALE-COPY           a backup variant (*.bak*, *.old, *.orig, *~)
  OLD                  unchanged for more than --max-age-days
  SYMLINK              a matched name that is a symlink (never followed)
  OK                   none of the above

skipped (never scanned):
  .git, node_modules, __pycache__, site-packages, virtualenvs (pyvenv.cfg),
  ~/.cache, ~/.local/share/docker, other devices (mounts) and symlinks
  (never followed); always, also with --all
  ~/.local/share/uv, ~/.claude/plugins: by default; --all scans them
  The report lists the skips in effect.

notes:
  in-repo fixture?     an IN-REPO-NOT-IGNORED file under a tests/ or test/
                       folder of its repo; still flagged, still exit 1

exit codes:
  code  meaning
  0     nothing flagged except OK, OLD or SYMLINK
  1     any OTHERS-CAN-READ, STALE-COPY or IN-REPO-NOT-IGNORED
  2     usage error, or a ROOT that is missing or not a directory

keycheck matches by name only and never opens a matched file. The report
holds paths, sizes, ages, modes and flags, never content, a hash of content
or an environment value.
"""


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgError(message)

    def _print_message(self, message, file=None):
        if message:
            _write(file or sys.stderr, message)


def _days(value):
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {value}") from None
    if days < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more: {value}")
    return days


def _build_parser():
    parser = _Parser(prog="keycheck", epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter,
                     description="Find key and secret files (by name only) that "
                                 "others can read, stale backup copies, old keys "
                                 "and keys in a git repo that are not ignored. "
                                 "Never opens a matched file.")
    parser.add_argument("roots", metavar="ROOT", nargs="*",
                        help="directory to scan (default: your home directory)")
    parser.add_argument("--format", choices=FORMATS, default="text",
                        help="report format (default: text)")
    parser.add_argument("--max-age-days", type=_days, default=180, metavar="N",
                        help="flag OLD when unchanged for more than N days "
                             "(default: 180)")
    parser.add_argument("--all", action="store_true", dest="all_",
                        help="also scan ~/.local/share/uv and ~/.claude/plugins; "
                             "the other skips stay on")
    return parser


def _write(stream, text):
    """The single exit point for everything keycheck prints; always redacted."""
    stream.write(redact(text))


def _fail(message, code):
    _write(sys.stderr, f"keycheck: {message}\n")
    return code


def display(path, home):
    """path with ~ for home, "/" separated."""
    if path == home:
        return "~"
    if path.startswith(home.rstrip(os.sep) + os.sep):
        path = "~" + os.sep + path[len(home.rstrip(os.sep)) + 1:]
    return path.replace(os.sep, "/")


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgError as exc:
        _write(sys.stderr, parser.format_usage())
        return _fail(str(exc), EXIT_USAGE)
    except SystemExit as exc:          # --help
        return exc.code or EXIT_OK
    home = os.path.abspath(os.path.expanduser("~"))
    roots = [os.path.abspath(os.path.expanduser(r)) for r in args.roots] or [home]
    for given, root in zip(args.roots or ["~"], roots):
        if not os.path.isdir(root):
            return _fail(f"not a directory: {given}", EXIT_USAGE)
    now = time.time()
    scanned = [(display(r, home), scan.scan(r, home, now, args.max_age_days, args.all_))
               for r in roots]
    data = build(scanned, scan.skips(args.all_))
    _write(sys.stdout, render(data, args.format))
    return EXIT_FLAGGED if flagged(data) else EXIT_OK
