import argparse
import os
import stat
import sys

from loglens.readers import (DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, ERROR, LEVEL_RANK,
                             WARNING, InputError, UsageError, read_logs)
from loglens.redact import redact
from loglens.report import FORMATS, analyse, render
from loglens.timeline import AUTO, BUCKETS

EXIT_OK = 0
EXIT_WARNING = 1
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_ERROR = 4

EPILOG = """\
exit codes (when several apply: 2 > 3 > 4 > 1 > 0):
  0  no entries at WARNING or above
  1  WARNING entries, none at ERROR or above
  2  usage error, including a refused --output
  3  input missing, unreadable, invalid, a symlink, or over --max-bytes
  4  entries at ERROR or above (ERROR, ERR, CRITICAL, FATAL, ALERT, EMERG)

--output never overwrites an existing file unless --force is given, never
writes through a symlink, and refuses the directory of any input file.

Raw log lines are never shown unless --raw is given, and everything loglens
prints passes through redaction.
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
    parser = _Parser(prog="loglens", epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter,
                     description="Summarise log files into grouped, redacted "
                                 "findings.")
    parser.add_argument("files", nargs="+", metavar="FILE",
                        help="log file (.log, .jsonl, optionally .gz)")
    parser.add_argument("--output", metavar="PATH",
                        help="write the report to PATH (default: stdout)")
    parser.add_argument("--force", action="store_true",
                        help="allow --output to replace an existing regular file")
    parser.add_argument("--raw", action="store_true",
                        help="include up to 2 redacted raw examples per group "
                             "(off by default)")
    parser.add_argument("--format", choices=FORMATS, default="text",
                        help="report format (default: text)")
    parser.add_argument("--bucket", choices=[AUTO, *BUCKETS], default=AUTO,
                        help="timeline bucket size (default: auto = smallest size "
                             "giving at most 200 buckets)")
    parser.add_argument("--context", type=int, default=0, metavar="N",
                        help="signatures of the N entries before and after each "
                             "incident's first occurrence (default: 0)")
    parser.add_argument("--input-format", choices=["auto", "text", "jsonl"],
                        default="auto",
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
    """The single exit point for everything loglens prints; always redacted."""
    stream.write(redact(text))


def _fail(message, code):
    _write(sys.stderr, f"loglens: {message}\n")
    return code


def _same_dir(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


def _check_output(path, inputs, force):
    """Refusal message for --output PATH, or None. Re-checked atomically by
    the O_EXCL / O_NOFOLLOW open in _open_output."""
    parent = os.path.dirname(os.path.abspath(path))
    for name in inputs:
        if _same_dir(parent, os.path.dirname(os.path.abspath(name))):
            return (f"--output {path}: refusing to write into the directory of "
                    f"input {name}")
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


def _exit_code(groups):
    worst = max((LEVEL_RANK.get(g.level, 0) for g in groups), default=0)
    if worst >= LEVEL_RANK[ERROR]:
        return EXIT_ERROR
    if worst >= LEVEL_RANK[WARNING]:
        return EXIT_WARNING
    return EXIT_OK


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgError as exc:
        return _fail(str(exc), EXIT_USAGE)
    except SystemExit as exc:          # --help
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if args.context < 0:
        return _fail("--context must be at least 0", EXIT_USAGE)
    if args.force and not args.output:
        return _fail("--force needs --output", EXIT_USAGE)
    if args.output:
        refusal = _check_output(args.output, args.files, args.force)
        if refusal:
            return _fail(refusal, EXIT_USAGE)
    try:
        result = read_logs(args.files, year=args.year, fields=args.field,
                           max_bytes=args.max_bytes, max_lines=args.max_lines,
                           fmt=args.input_format)
    except UsageError as exc:
        return _fail(str(exc), EXIT_USAGE)
    except InputError as exc:
        return _fail(str(exc), EXIT_INPUT)
    report = analyse(result, raw=args.raw, bucket=args.bucket, context=args.context)
    document = render(report, args.format)
    if not args.output:
        _write(sys.stdout, document)
    else:
        try:
            stream = _open_output(args.output, args.force)
        except OSError as exc:
            return _fail(f"--output {args.output}: cannot write ({exc.strerror})",
                         EXIT_USAGE)
        with stream:
            _write(stream, document)
    return _exit_code(report.groups)
