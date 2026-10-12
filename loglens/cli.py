import argparse
import sys

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
    return parser


def _fail(message, code):
    print(f"loglens: {message}", file=sys.stderr)
    return code


def main(argv=None):
    parser = _build_parser()
    try:
        parser.parse_args(argv)
    except _ArgError as exc:
        return _fail(str(exc), EXIT_USAGE)
    except SystemExit as exc:          # --help
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    return _fail("analysis is not implemented yet", EXIT_OK)
