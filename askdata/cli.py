import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="askdata")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a data file")
    inspect_parser.add_argument("path", help="Path to the data file")
    inspect_parser.add_argument("--output", help="Output file path")
    inspect_parser.add_argument("--format", choices=["html", "md"], default="html",
                                help="Output format (default: html)")
    inspect_parser.add_argument("--max-bytes", type=int, default=500_000_000,
                                help="Maximum file size in bytes (default: 500000000)")
    inspect_parser.add_argument("--max-rows", type=int, default=1_000_000,
                                help="Maximum rows to process (default: 1000000)")
    inspect_parser.add_argument("--timeout", type=float, default=60,
                                help="Timeout in seconds (default: 60)")
    inspect_parser.add_argument("--force", action="store_true",
                                help="Force processing despite warnings")
    
    args = parser.parse_args(argv)
    
    print("askdata: not implemented yet", file=sys.stderr)
    return 2
