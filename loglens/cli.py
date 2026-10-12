import argparse
import sys

from loglens.readers import (DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, InputError,
                             UsageError, read_logs)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3

EPILOG = """\
exit codes:
  0  ok
  2  usage error
  3  input missing, unreadable, a symlink, or too large

Raw log lines are never shown unless --raw is given, and everything loglens
prints passes through redaction.
"""


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgError(message)


def _build_parser():
    parser = _Parser(prog="loglens", epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter,
                     description="Summarise log files into grouped, redacted "
                                 "findings.")
    parser.add_argument("files", nargs="+", metavar="FILE",
                        help="log file (.log, .jsonl, optionally .gz)")
    parser.add_argument("--raw", action="store_true",
                        help="include up to 2 redacted raw examples per group "
                             "(off by default)")
    parser.add_argument("--format", choices=["auto", "text", "jsonl"], default="auto",
                        help="input format (default: jsonl for .jsonl/.ndjson, "
                             "else text)")
    parser.add_argument("--year", type=int,
                        help="year for syslog timestamps (default: current UTC year)")
    parser.add_argument("--field", action="append", default=[], metavar="NAME=KEY",
                        help="JSONL key for ts, level or message; repeatable")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                        help=f"total input limit in bytes (default: "
                             f"{DEFAULT_MAX_BYTES}); exceeded -> exit 3")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES,
                        help=f"total line limit (default: {DEFAULT_MAX_LINES}); "
                             "later lines are not read")
    return parser


def _write(stream, text):
    """The single exit point for everything loglens prints."""
    stream.write(text)


def _fail(message, code):
    _write(sys.stderr, f"loglens: {message}\n")
    return code


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgError as exc:
        return _fail(str(exc), EXIT_USAGE)
    except SystemExit as exc:          # --help
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        result = read_logs(args.files, year=args.year, fields=args.field,
                           max_bytes=args.max_bytes, max_lines=args.max_lines,
                           fmt=args.format)
    except UsageError as exc:
        return _fail(str(exc), EXIT_USAGE)
    except InputError as exc:
        return _fail(str(exc), EXIT_INPUT)
    summary = (f"{len(result.entries) + len(result.untimed)} entries "
               f"({len(result.untimed)} without timestamp) from "
               f"{len(result.files)} file(s), {result.lines_read} lines\n")
    if result.sampled:
        summary += result.sampled_note + "\n"
    _write(sys.stdout, summary)
    return EXIT_OK
