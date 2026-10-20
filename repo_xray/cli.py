import argparse
import sys
from pathlib import Path

from repo_xray import boundaries
from repo_xray.analyzer import analyze, TargetError, TooManyFiles
from repo_xray.report import render_html, render_markdown


def main(argv=None):
    parser = argparse.ArgumentParser(prog="repo-xray")
    parser.add_argument("path", help="Path to repository")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--format", choices=["html", "md", "text", "json"], default=None,
                        help="Output format: html|md for the report (default html), "
                             "text|md|json with --cycles/--boundaries (default text)")
    parser.add_argument("--max-files", type=int, default=2000, help="Maximum number of files to process")
    parser.add_argument("--no-docstrings", action="store_true", help="Omit docstrings from report")
    parser.add_argument("--cycles", action="store_true",
                        help="List import cycles to stdout; exit 1 if any")
    parser.add_argument("--boundaries", action="store_true",
                        help="Check independent packages to stdout; exit 1 on a violation")
    parser.add_argument("--independent", metavar="PKG,PKG",
                        help="Independent packages for --boundaries "
                             "(default: [tool.repo-xray] independent in pyproject.toml)")

    args = parser.parse_args(argv)

    if args.independent is not None and not args.boundaries:
        print("repo-xray: --independent needs --boundaries", file=sys.stderr)
        return 2

    if args.cycles or args.boundaries:
        return _check(args)

    if args.format is None:
        args.format = "html"
    elif args.format not in ("html", "md"):
        print(f"repo-xray: --format {args.format} needs --cycles or --boundaries", file=sys.stderr)
        return 2

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


def _check(args):
    """--cycles / --boundaries: print to stdout; exit 0 ok, 1 findings, 2 usage, 4 unreadable."""
    fmt = args.format or "text"
    if fmt not in boundaries.RENDERERS:
        print(f"repo-xray: --format {fmt} is not available with --cycles/--boundaries "
              f"(use text, md or json)", file=sys.stderr)
        return 2
    if args.output is not None:
        print("repo-xray: --output is not available with --cycles/--boundaries "
              "(they print to stdout)", file=sys.stderr)
        return 2

    target = Path(args.path).resolve()

    packages = None
    if args.boundaries:
        try:
            if args.independent is not None:
                packages = boundaries.parse_independent(args.independent)
            elif target.is_dir():
                packages = boundaries.load_config(target)
        except boundaries.ConfigError as e:
            print(f"repo-xray: {e}", file=sys.stderr)
            return 2

    try:
        graph = boundaries.build_graph(target, max_files=args.max_files)
    except TooManyFiles as e:
        print(f"repo-xray: {e} (use --max-files to increase limit)", file=sys.stderr)
        return 4
    except TargetError as e:
        print(f"repo-xray: {e}", file=sys.stderr)
        return 4

    result = boundaries.build_result(graph, target.name, cycles=args.cycles, packages=packages)
    sys.stdout.write(boundaries.RENDERERS[fmt](result))
    return 1 if boundaries.has_findings(result) else 0
