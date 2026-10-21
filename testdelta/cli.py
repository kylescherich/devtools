"""Command line for testdelta. Everything printed passes through _redact.

testdelta opens only the two inputs (read-only, never through a symlink) and
--output; it runs no tests, no subprocess and makes no network access.
"""
import argparse
import os
import stat
import sys

from testdelta._redact import redact
from testdelta.compare import compare
from testdelta.readers import DEFAULT_MAX_BYTES, InputError, load_pair
from testdelta.report import FORMATS, render

EXIT_OK = 0
EXIT_REGRESSIONS = 1
EXIT_USAGE = 2
EXIT_INPUT = 4

INPUT_FORMATS = ("junit", "unittest")
DEFAULT_SLOW_RATIO = 2.0
DEFAULT_SLOW_MIN = 0.5

EPILOG = """\
exit codes:
  code  meaning
  0     no regressions
  1     regressions: newly failing or erroring tests, added tests that fail,
        removed tests, newly skipped tests; slower tests only with
        --fail-on-slow
  2     usage error: unknown option, a bad number, --force without --output,
        or a refused --output
  4     input missing, unreadable, a symlink, empty, over --max-bytes, not a
        recognised format, the two inputs in different formats, malformed
        XML, XML with a DOCTYPE or ENTITY declaration (refused unparsed), or
        a report that says the run failed but names no failing test

testdelta never runs tests. It reads the two inputs and writes only --output.
By default the report holds test ids, statuses and times only: no failure
messages, tracebacks or captured output. --details adds, for regressions
only, the first line of the failure message and up to 20 traceback lines,
redacted. Captured stdout/stderr is never shown.

JUnit tests whose [...] parameter part changed (same base id, as many
before as after, one status throughout) are renamed parametrized tests,
not removed/added, and count as unchanged; --strict-ids turns this off.
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
    if not number > 0 or number == float("inf"):
        raise argparse.ArgumentTypeError("must be a number > 0")
    return number


def _positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _build_parser():
    parser = _Parser(prog="testdelta", epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter,
                     description="Compare two test-run results (unittest -v logs "
                                 "or JUnit XML): newly failing, fixed, removed, "
                                 "added, slower. Never runs tests.")
    parser.add_argument("before", help="results before the change")
    parser.add_argument("after", help="results after the change")
    parser.add_argument("--input-format", choices=INPUT_FORMATS,
                        help="input format for both files (default: detected)")
    parser.add_argument("--format", choices=FORMATS, default="text",
                        help="report format (default: text)")
    parser.add_argument("--output", metavar="PATH",
                        help="write the report to PATH (default: stdout)")
    parser.add_argument("--force", action="store_true",
                        help="allow --output to replace an existing regular file")
    parser.add_argument("--details", action="store_true",
                        help="for regressions: first line of the failure message "
                             "and up to 20 traceback lines, redacted")
    parser.add_argument("--slow-ratio", type=_positive, default=DEFAULT_SLOW_RATIO,
                        metavar="R",
                        help=f"slower means after >= R x before (default: "
                             f"{DEFAULT_SLOW_RATIO})")
    parser.add_argument("--slow-min", type=_positive, default=DEFAULT_SLOW_MIN,
                        metavar="SECONDS",
                        help=f"... and after - before >= SECONDS (default: "
                             f"{DEFAULT_SLOW_MIN})")
    parser.add_argument("--fail-on-slow", action="store_true",
                        help="slower tests count as regressions (exit 1)")
    parser.add_argument("--max-bytes", type=_positive_int, default=DEFAULT_MAX_BYTES,
                        metavar="N",
                        help=f"per-file size limit (default: {DEFAULT_MAX_BYTES})")
    parser.add_argument("--strict-ids", action="store_true",
                        help="match tests by full id only: no renamed parametrized "
                             "tests, removed/added as before")
    return parser


def _write(stream, text):
    """The single exit point for everything testdelta prints; always redacted."""
    stream.write(redact(text))


def _fail(message, code):
    _write(sys.stderr, f"testdelta: {message}\n")
    return code


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


def _check_output(path, force, inputs):
    """Refusal message for --output PATH, or None. Re-checked atomically by
    the O_EXCL / O_NOFOLLOW open in _open_output."""
    for label, source in zip(("before", "after"), inputs):
        if _same_file(path, source):
            return f"--output {path}: is the {label} input; refusing to overwrite it"
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


def _open_output(path, force):
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_TRUNC if force else os.O_EXCL
    fd = os.open(path, flags, 0o644)
    return os.fdopen(fd, "w", encoding="utf-8", newline="\n")


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgError as exc:
        _write(sys.stderr, parser.format_usage())
        return _fail(str(exc), EXIT_USAGE)
    except SystemExit as exc:          # --help
        return exc.code or EXIT_OK
    if args.force and not args.output:
        return _fail("--force needs --output", EXIT_USAGE)
    if args.output:
        refusal = _check_output(args.output, args.force, (args.before, args.after))
        if refusal:
            return _fail(refusal, EXIT_USAGE)
    try:
        before, after = load_pair(args.before, args.after, args.input_format,
                                  args.max_bytes)
    except InputError as exc:
        return _fail(str(exc), EXIT_INPUT)
    delta = compare(before, after, args.slow_ratio, args.slow_min, args.strict_ids)
    names = (os.path.basename(args.before), os.path.basename(args.after))
    document = render(delta, args.format, args.details, names, args.fail_on_slow)
    if args.output:
        try:
            with _open_output(args.output, args.force) as stream:
                _write(stream, document)
        except OSError as exc:
            return _fail(f"--output {args.output}: cannot write ({exc.strerror})",
                         EXIT_USAGE)
    else:
        _write(sys.stdout, document)
    return EXIT_REGRESSIONS if delta.regressions(args.fail_on_slow) else EXIT_OK
