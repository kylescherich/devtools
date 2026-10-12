import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="repo-xray")
    parser.add_argument("path", help="Path to repository")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--format", choices=["html", "md"], default="html", help="Output format")
    parser.add_argument("--max-files", type=int, default=2000, help="Maximum number of files to process")
    
    args = parser.parse_args(argv)
    
    print("repo-xray: not implemented yet", file=sys.stderr)
    return 2
