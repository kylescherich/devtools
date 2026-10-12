import argparse
import sys
from pathlib import Path

from repo_xray.analyzer import analyze, TargetError, TooManyFiles
from repo_xray.report import render_html, render_markdown


def main(argv=None):
    parser = argparse.ArgumentParser(prog="repo-xray")
    parser.add_argument("path", help="Path to repository")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--format", choices=["html", "md"], default="html", help="Output format")
    parser.add_argument("--max-files", type=int, default=2000, help="Maximum number of files to process")
    parser.add_argument("--no-docstrings", action="store_true", help="Omit docstrings from report")
    
    args = parser.parse_args(argv)
    
    # Resolve target path
    target = Path(args.path).resolve()
    
    # Determine output path
    if args.output:
        output_path = Path(args.output).resolve()
    else:
        extension = "html" if args.format == "html" else "md"
        output_path = Path.cwd() / f"{target.name}-xray.{extension}"
    
    # Check if output is inside target
    if output_path == target or output_path.is_relative_to(target):
        print("repo-xray: --output must be outside the target", file=sys.stderr)
        return 2
    
    # Analyze the repository
    try:
        model = analyze(target, max_files=args.max_files)
    except TooManyFiles as e:
        print(f"repo-xray: {e} (use --max-files to increase limit)", file=sys.stderr)
        return 3
    except TargetError as e:
        print(f"repo-xray: {e}", file=sys.stderr)
        return 3
    
    # Render the report
    title = target.name
    docstrings = not args.no_docstrings
    if args.format == "html":
        content = render_html(model, title, docstrings=docstrings)
    else:
        content = render_markdown(model, title, docstrings=docstrings)
    
    # Write the output file
    try:
        output_path.write_text(content, encoding='utf-8')
    except OSError as e:
        print(f"repo-xray: {e}", file=sys.stderr)
        return 2
    
    # Success
    num_modules = len(model['modules'])
    print(f"repo-xray: wrote {output_path} ({num_modules} modules)")
    return 0
