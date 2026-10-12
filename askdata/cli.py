import argparse
import sys
import time
from pathlib import Path

from askdata.csvinspect import inspect_csv
from askdata.sqliteinspect import inspect_sqlite, InspectError
from askdata.report import render_terminal, render_md, render_html


def main(argv=None):
    parser = argparse.ArgumentParser(prog="askdata")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a data file")
    inspect_parser.add_argument("path", help="Path to the data file")
    inspect_parser.add_argument("--output", help="Output file path")
    inspect_parser.add_argument("--format", choices=["html", "md"],
                                help="Output format (default: html)")
    inspect_parser.add_argument("--max-bytes", type=int, default=500_000_000,
                                help="Maximum file size in bytes (default: 500000000)")
    inspect_parser.add_argument("--max-rows", type=int, default=1_000_000,
                                help="Maximum rows to process (default: 1000000)")
    inspect_parser.add_argument("--timeout", type=float, default=60,
                                help="Timeout in seconds (default: 60)")
    inspect_parser.add_argument("--force", action="store_true",
                                help="Force processing despite warnings")
    inspect_parser.add_argument("--frozen", action="store_true",
                                help="Treat the database as unchanging (immutable=1); only for copies you know are static, results may be stale")
    inspect_parser.add_argument("--samples", action="store_true",
                                help="Include up to 5 example values and top values per column; off by default for privacy")
    
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse errors exit with code 2
        return e.code if e.code is not None else 2
    
    # Input validation
    input_path = Path(args.path)
    
    # Check if path exists
    if not input_path.exists():
        print("askdata: file not found", file=sys.stderr)
        return 3
    
    # Check if it's a symlink (before resolving)
    if input_path.is_symlink():
        print("askdata: symlinks are not allowed", file=sys.stderr)
        return 3
    
    # Check if it's a directory
    if input_path.is_dir():
        print("askdata: path is a directory", file=sys.stderr)
        return 3
    
    # Check if readable
    try:
        with open(input_path, 'rb') as f:
            header = f.read(16)
    except (OSError, PermissionError):
        print("askdata: file is not readable", file=sys.stderr)
        return 3
    
    # Determine kind
    if header.startswith(b"SQLite format 3\x00"):
        kind = "sqlite"
    else:
        suffix = input_path.suffix.lower()
        if suffix in ('.csv', '.tsv', '.txt'):
            kind = "csv"
        else:
            print("askdata: unsupported format", file=sys.stderr)
            return 3
    
    # Calculate deadline
    deadline = time.monotonic() + args.timeout
    
    # Inspect the file
    try:
        if kind == "sqlite":
            model = inspect_sqlite(input_path, max_rows=args.max_rows, deadline=deadline, 
                                 frozen=args.frozen, samples=args.samples)
        else:  # csv
            model = inspect_csv(input_path, max_bytes=args.max_bytes, 
                              max_rows=args.max_rows, deadline=deadline, samples=args.samples)
    except InspectError as e:
        print(f"askdata: {e}", file=sys.stderr)
        return 3
    
    # Handle output
    if args.output is None:
        # Print to stdout
        output_text = render_terminal(model)
        print(output_text)
        return 0
    
    # Determine format
    output_path = Path(args.output)
    if args.format:
        format_type = args.format
    elif output_path.suffix.lower() == '.md':
        format_type = "md"
    else:
        format_type = "html"
    
    # Validate format choice
    if format_type not in ("html", "md"):
        print("askdata: invalid format", file=sys.stderr)
        return 2
    
    # Resolve paths for comparison
    try:
        resolved_output = output_path.resolve()
        resolved_input = input_path.resolve()
        input_dir = resolved_input.parent
        
        # Check if output is inside input directory
        try:
            resolved_output.relative_to(input_dir)
            print("askdata: output cannot be inside input directory", file=sys.stderr)
            return 2
        except ValueError:
            # Not inside input directory, this is good
            pass
        
        # Check if output exists and --force not given
        if resolved_output.exists() and not args.force:
            print("askdata: output file exists (use --force to overwrite)", file=sys.stderr)
            return 2
        
    except (OSError, RuntimeError):
        print("askdata: cannot resolve output path", file=sys.stderr)
        return 2
    
    # Generate output
    if format_type == "md":
        output_text = render_md(model)
    else:
        output_text = render_html(model)
    
    # Write output
    try:
        with open(resolved_output, 'w', encoding='utf-8') as f:
            f.write(output_text)
        print(f"wrote {resolved_output}")
        return 0
    except (OSError, PermissionError) as e:
        print(f"askdata: cannot write output: {e}", file=sys.stderr)
        return 3
