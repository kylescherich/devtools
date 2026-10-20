"""Import boundary checks: import cycles and independent packages (BRIEF-DEVWS-035)."""

import ast
import json
import os
import re
import stat
import sys
import tomllib
from collections import defaultdict, deque
from pathlib import Path

from repo_xray.analyzer import analyze, _resolve_relative_import
from repo_xray.report import _escape_markdown


SCHEMA = "repo-xray-boundaries/1"

NOTE = ("Imports inside functions and try blocks count; "
        "imports under TYPE_CHECKING are listed but not counted.")

MAX_PYPROJECT_BYTES = 1_000_000

NO_CONFIG = ("no independent packages configured; use --independent or "
             "[tool.repo-xray] independent in pyproject.toml")

_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$')


class ConfigError(Exception):
    """Bad --independent value or [tool.repo-xray] table (exit 2)."""
    pass


def _is_type_checking(test):
    """True for `TYPE_CHECKING` and `typing.TYPE_CHECKING`."""
    if isinstance(test, ast.Name):
        return test.id == 'TYPE_CHECKING'
    return (isinstance(test, ast.Attribute) and test.attr == 'TYPE_CHECKING' and
            isinstance(test.value, ast.Name) and test.value.id == 'typing')


class _ImportCollector(ast.NodeVisitor):
    """Collect (import node, under TYPE_CHECKING) from anywhere in a module."""

    def __init__(self):
        self.imports = []
        self._type_checking = 0

    def visit_If(self, node):
        if not _is_type_checking(node.test):
            self.generic_visit(node)
            return
        self._type_checking += 1
        for child in node.body:
            self.visit(child)
        self._type_checking -= 1
        # the else branch runs at import time
        for child in node.orelse:
            self.visit(child)

    def visit_Import(self, node):
        self.imports.append((node, self._type_checking > 0))

    visit_ImportFrom = visit_Import


def _is_stdlib(name):
    return name.split('.')[0] in sys.stdlib_module_names


def _targets(node, package, modules):
    """Yield the repo modules an import node lands on."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _is_stdlib(alias.name):
                continue
            # import X.Y - longest known prefix
            parts = alias.name.split('.')
            for i in range(len(parts), 0, -1):
                candidate = '.'.join(parts[:i])
                if candidate in modules:
                    yield candidate
                    break
        return

    if node.level == 0:
        if node.module is None or _is_stdlib(node.module):
            return
        base = node.module
    else:
        base = _resolve_relative_import(package, node.level, node.module)
        if base is None:
            return

    for alias in node.names:
        # from X import a - X.a if it is a module, else X
        candidate = f"{base}.{alias.name}"
        if alias.name != '*' and candidate in modules:
            yield candidate
        elif base in modules:
            yield base


def build_graph(root, max_files=2000):
    """Return the import graph of the repo at root.

    Uses analyze() for the module set, so its skip and size rules apply.

    Returns:
        dict with "modules" {name: path}, "edges" {(src, dst): lineno}
        and "type_checking" [(src, dst, lineno)].

    Raises:
        TargetError, TooManyFiles: as analyze()
    """
    root_path = Path(root).resolve()
    model = analyze(root_path, max_files=max_files)
    modules = {name: info["path"] for name, info in model["modules"].items()}

    edges = {}
    type_checking = set()
    for name, path in sorted(modules.items()):
        try:
            with open(root_path / path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=path)
        except Exception:
            continue

        # same package rule as analyzer._parse_file
        if Path(path).name == '__init__.py':
            package = name
        else:
            package = name.rpartition('.')[0]

        collector = _ImportCollector()
        collector.visit(tree)
        for node, in_type_checking in collector.imports:
            for target in _targets(node, package, modules):
                if target == name:
                    continue
                if in_type_checking:
                    type_checking.add((name, target, node.lineno))
                elif (name, target) not in edges or node.lineno < edges[(name, target)]:
                    edges[(name, target)] = node.lineno

    return {"modules": modules, "edges": edges, "type_checking": sorted(type_checking)}


def _shortest_loop(adj, start):
    """BFS for the shortest loop start -> ... -> start, or None."""
    prev = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for child in adj[node]:
            if child == start:
                loop = [node]
                while prev[loop[-1]] is not None:
                    loop.append(prev[loop[-1]])
                loop.reverse()
                return loop
            if child not in prev:
                prev[child] = node
                queue.append(child)
    return None


def find_cycles(edges):
    """Return each import cycle once, as a list of module names.

    One shortest loop per module, rotated to start at its smallest name,
    duplicates dropped; sorted by (length, names). Self-imports are ignored.
    """
    adj = defaultdict(list)
    for src, dst in edges:
        if src != dst:
            adj[src].append(dst)
    for children in adj.values():
        children.sort()

    seen = set()
    cycles = []
    for start in sorted(adj):
        loop = _shortest_loop(adj, start)
        if loop is None:
            continue
        i = loop.index(min(loop))
        canonical = tuple(loop[i:] + loop[:i])
        if canonical not in seen:
            seen.add(canonical)
            cycles.append(list(canonical))

    cycles.sort(key=lambda c: (len(c), c))
    return cycles


def _owner(module, packages):
    for package in packages:
        if module == package or module.startswith(package + '.'):
            return package
    return None


def check_independent(graph, packages):
    """Return (violations, not_found) for the independent packages rule.

    violations: [(src, dst, lineno)] for each edge between two different
    listed packages. not_found: listed packages with no module in the repo.
    """
    violations = []
    for (src, dst), lineno in sorted(graph["edges"].items()):
        src_pkg = _owner(src, packages)
        dst_pkg = _owner(dst, packages)
        if src_pkg and dst_pkg and src_pkg != dst_pkg:
            violations.append((src, dst, lineno))

    not_found = [p for p in packages
                 if not any(_owner(m, [p]) for m in graph["modules"])]
    return violations, not_found


def _validate(names):
    """Raise ConfigError unless names is a non-empty list of distinct,
    non-nested dotted package names."""
    if not names:
        raise ConfigError("no package names")
    for name in names:
        if not isinstance(name, str) or not _NAME.match(name):
            raise ConfigError("not a dotted package name")
    if len(set(names)) != len(names):
        raise ConfigError("a package is listed twice")
    for a in names:
        for b in names:
            if b.startswith(a + '.'):
                raise ConfigError("a listed package is inside another")
    return list(names)


def parse_independent(value):
    """Parse the --independent flag value into a list of package names."""
    try:
        return _validate([p.strip() for p in value.split(',')])
    except ConfigError as e:
        raise ConfigError(f"invalid --independent ({e})") from None


def load_config(root):
    """Return the independent package list from root's pyproject.toml.

    Messages name the file only; no file content or TOML error text.
    """
    path = Path(root) / 'pyproject.toml'
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise ConfigError(NO_CONFIG) from None
    except OSError:
        raise ConfigError(f"cannot read {path}") from None
    if not stat.S_ISREG(st.st_mode):
        raise ConfigError(f"refused (not a regular file): {path}")
    if st.st_size > MAX_PYPROJECT_BYTES:
        raise ConfigError(f"too large: {path}")

    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as f:
            data = f.read(MAX_PYPROJECT_BYTES + 1)
    except OSError:
        raise ConfigError(f"cannot read {path}") from None
    try:
        pyproject = tomllib.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise ConfigError(f"malformed {path}") from None

    invalid = f"invalid [tool.repo-xray] independent in {path}"
    tool = pyproject.get('tool', {})
    if not isinstance(tool, dict):
        raise ConfigError(invalid)
    table = tool.get('repo-xray')
    if table is None:
        raise ConfigError(NO_CONFIG)
    if not isinstance(table, dict):
        raise ConfigError(invalid)
    value = table.get('independent')
    if value is None:
        raise ConfigError(NO_CONFIG)
    if not isinstance(value, list):
        raise ConfigError(invalid)
    try:
        return _validate(value)
    except ConfigError:
        raise ConfigError(invalid) from None


def _edge(graph, src, dst, lineno):
    return {"from": src, "to": dst,
            "path": graph["modules"].get(src, ""), "line": lineno}


def build_result(graph, title, cycles=False, packages=None):
    """Build the report data for the requested checks."""
    result = {"schema": SCHEMA, "target": title,
              "cycles": None, "independent": None}
    if cycles:
        result["cycles"] = []
        for loop in find_cycles(graph["edges"]):
            pairs = zip(loop, loop[1:] + loop[:1])
            result["cycles"].append({
                "modules": loop,
                "edges": [_edge(graph, a, b, graph["edges"][(a, b)]) for a, b in pairs],
            })
    if packages is not None:
        violations, not_found = check_independent(graph, packages)
        result["independent"] = {
            "packages": list(packages),
            "not_found": not_found,
            "violations": [_edge(graph, a, b, n) for a, b, n in violations],
        }
    result["type_checking"] = [_edge(graph, a, b, n)
                               for a, b, n in sorted(graph["type_checking"])]
    return result


def has_findings(result):
    if result["cycles"]:
        return True
    return bool(result["independent"] and result["independent"]["violations"])


def _visible(s):
    """Make control and bidi characters visible for terminal output."""
    out = []
    for ch in s:
        o = ord(ch)
        if o < 0x20 or o == 0x7f or 0x80 <= o < 0xa0 or 0x200b <= o <= 0x200f \
                or 0x202a <= o <= 0x202e or 0x2066 <= o <= 0x2069:
            out.append(f"\\x{o:02x}" if o < 0x100 else f"\\u{o:04x}")
        else:
            out.append(ch)
    return ''.join(out)


def _loc(e):
    return f"{e['path']}:{e['line']}"


def render_text(result):
    v = _visible
    lines = [f"repo-xray: import check of {v(result['target'])}", NOTE, ""]
    if result["cycles"] is not None:
        lines.append(f"Cycles: {len(result['cycles'])}")
        if not result["cycles"]:
            lines.append("  none")
        for i, c in enumerate(result["cycles"], 1):
            loop = c["modules"] + c["modules"][:1]
            lines.append(f"  {i}. {' -> '.join(v(m) for m in loop)}")
            for e in c["edges"]:
                lines.append(f"     {v(e['from'])} -> {v(e['to'])}  {v(_loc(e))}")
        lines.append("")
    ind = result["independent"]
    if ind is not None:
        lines.append(f"Independent packages: {', '.join(v(p) for p in ind['packages'])}")
        if ind["not_found"]:
            lines.append(f"Not found: {', '.join(v(p) for p in ind['not_found'])}")
        lines.append(f"Violations: {len(ind['violations'])}")
        if not ind["violations"]:
            lines.append("  none")
        for e in ind["violations"]:
            lines.append(f"  {v(_loc(e))}  {v(e['from'])} -> {v(e['to'])}")
        lines.append("")
    tc = result["type_checking"]
    lines.append(f"TYPE_CHECKING imports (not counted): {len(tc)}")
    for e in tc:
        lines.append(f"  {v(_loc(e))}  {v(e['from'])} -> {v(e['to'])}")
    return '\n'.join(lines) + '\n'


def render_md(result):
    m = _escape_markdown
    lines = [f"# Import check: {m(result['target'])}", "", m(NOTE), ""]
    if result["cycles"] is not None:
        lines += [f"## Cycles ({len(result['cycles'])})", ""]
        if not result["cycles"]:
            lines.append("None")
        for i, c in enumerate(result["cycles"], 1):
            loop = c["modules"] + c["modules"][:1]
            lines.append(f"{i}. {' -> '.join(m(x) for x in loop)}")
            for e in c["edges"]:
                lines.append(f"    - {m(e['from'])} -> {m(e['to'])} ({m(_loc(e))})")
        lines.append("")
    ind = result["independent"]
    if ind is not None:
        lines += [f"## Independent packages ({len(ind['violations'])} violations)", ""]
        lines.append(f"Packages: {', '.join(m(p) for p in ind['packages'])}")
        if ind["not_found"]:
            lines.append("")
            lines.append(f"Not found: {', '.join(m(p) for p in ind['not_found'])}")
        lines.append("")
        if not ind["violations"]:
            lines.append("None")
        for e in ind["violations"]:
            lines.append(f"- {m(e['from'])} -> {m(e['to'])} ({m(_loc(e))})")
        lines.append("")
    tc = result["type_checking"]
    lines += [f"## TYPE\\_CHECKING imports, not counted ({len(tc)})", ""]
    if not tc:
        lines.append("None")
    for e in tc:
        lines.append(f"- {m(e['from'])} -> {m(e['to'])} ({m(_loc(e))})")
    lines.append("")
    return '\n'.join(lines)


def render_json(result):
    return json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True) + '\n'


RENDERERS = {"text": render_text, "md": render_md, "json": render_json}
