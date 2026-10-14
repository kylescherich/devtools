"""Command line for testdelta. Everything printed passes through _redact."""
import argparse
import sys

from testdelta._redact import redact
from testdelta.readers import DEFAULT_MAX_BYTES

EXIT_OK = 0
EXIT_REGRESSIONS = 1
EXIT_USAGE = 2
EXIT_INPUT = 4

FORMATS = ("text", "md", "html", "json")
INPUT_FORMATS = ("junit", "unittest")
DEFAULT_SLOW_RATIO = 2.0
DEFAULT_SLOW_MIN = 0.5

EPILOG = """\
exit codes:
  code  meaning
  0     no regressions
  1     regressions: newly failing or erroring tests, removed tests, newly
        skipped tests; slower tests only with --fail-on-slow
  2     usage error: unknown option, a bad number, --force without --output,
        or a refused --output
  4     input missing, unreadable, a symlink, empty, over --max-bytes, not a
        recognised format, the two inputs in different formats, malformed
        XML, or XML with a DOCTYPE or ENTITY declaration (refused unparsed)

testdelta never runs tests. It reads the two inputs and writes only --output.
By default the report holds test ids, statuses and times only: no failure
messages, tracebacks or captured output. --details adds, for regressions
only, the first line of the failure message and up to 20 traceback lines,
redacted. Captured stdout/stderr is never shown.
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
    return parser


def _write(stream, text):
    """The single exit point for everything testdelta prints; always redacted."""
    stream.write(redact(text))


def _fail(message, code):
    _write(sys.stderr, f"testdelta: {message}\n")
    return code


def main(argv=None):
    parser = _build_parser()
    try:
        parser.parse_args(argv)
    except _ArgError as exc:
        _write(sys.stderr, parser.format_usage())
        return _fail(str(exc), EXIT_USAGE)
    except SystemExit as exc:          # --help
        return exc.code or EXIT_OK
    return _fail("comparison not implemented yet", EXIT_USAGE)
