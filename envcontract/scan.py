"""Find the env names a repo reads and the names it documents.

Code side: *.py files parsed with ast (never imported or run). Docs side:
files named *.example, *.template or *.sample. Every file passes the one
gate, resolve(), before it is opened: the name and the symlink-resolved
name must both carry an allowed suffix, and the resolved path must lie
inside the repo. Only names, paths and line numbers are kept; values
(example values, getenv defaults) are never looked at.
"""
import ast
import os
import re
import stat

CODE_SUFFIXES = (".py",)
DOC_SUFFIXES = (".example", ".template", ".sample")
SKIP_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", "node_modules"})

# NAME=..., export NAME=..., # NAME=...; group 1 is the name, nothing else is kept.
_DOC_LINE = re.compile(r"^\s*(?:#\s*)?(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class Refused(Exception):
    """A file the gate will not open; str() is a fixed reason, never content."""


class Result:
    def __init__(self):
        self.reads = {}           # name -> [(path, line)]
        self.docs = {}            # name -> [(path, line)]
        self.dynamic = []         # [(path, line)]
        self.unreadable = []      # [(path, reason)]
        self.example_files = []   # [path]


def inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def resolve(path, suffixes, root):
    """The one gate. Return the resolved path of a regular file that may be
    opened, or raise Refused. root must already be a realpath."""
    if not os.path.basename(path).endswith(suffixes):
        raise Refused("not an allowed file name")
    real = os.path.realpath(path)
    if not os.path.basename(real).endswith(suffixes):
        raise Refused("refused symlink")
    if not inside(real, root):
        raise Refused("outside repo")
    if not stat.S_ISREG(os.stat(real).st_mode):
        raise Refused("not a regular file")
    return real


def read(real):
    with open(real, "rb") as stream:
        return stream.read()


def _rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _walk(root):
    """Yield (path, kind) for candidate files, sorted; never follows symlinks."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS
            and not os.path.lexists(os.path.join(dirpath, d, "pyvenv.cfg")))
        for name in sorted(filenames):
            if name.endswith(CODE_SUFFIXES):
                yield os.path.join(dirpath, name), "code"
            elif name.endswith(DOC_SUFFIXES):
                yield os.path.join(dirpath, name), "docs"


class _Reads:
    """Env reads in one module: (name or None, line); None is a dynamic read."""

    def __init__(self, tree):
        self.os, self.getenv, self.environ = set(), set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "os":
                        self.os.add(alias.asname or "os")
            elif isinstance(node, ast.ImportFrom) and node.module == "os" and not node.level:
                for alias in node.names:
                    if alias.name == "getenv":
                        self.getenv.add(alias.asname or alias.name)
                    elif alias.name == "environ":
                        self.environ.add(alias.asname or alias.name)
        self.found = []
        for node in ast.walk(tree):
            self._visit(node)
        self.found.sort(key=lambda item: item[1])

    def _is_os_attr(self, node, attr):
        return (isinstance(node, ast.Attribute) and node.attr == attr
                and isinstance(node.value, ast.Name) and node.value.id in self.os)

    def _is_environ(self, node):
        return ((isinstance(node, ast.Name) and node.id in self.environ)
                or self._is_os_attr(node, "environ"))

    def _is_getenv(self, node):
        return ((isinstance(node, ast.Name) and node.id in self.getenv)
                or self._is_os_attr(node, "getenv"))

    def _record(self, key, line):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            self.found.append((key.value, line))
        else:
            self.found.append((None, line))

    def _visit(self, node):
        if isinstance(node, ast.Call):
            func = node.func
            if self._is_getenv(func) or (isinstance(func, ast.Attribute)
                                         and func.attr == "get"
                                         and self._is_environ(func.value)):
                key = node.args[0] if node.args else next(
                    (k.value for k in node.keywords if k.arg == "key"), None)
                if key is not None:
                    self._record(key, node.lineno)
        elif isinstance(node, ast.Subscript):
            if isinstance(node.ctx, ast.Load) and self._is_environ(node.value):
                self._record(node.slice, node.lineno)
        elif isinstance(node, ast.Compare):
            left = node.left
            for op, right in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and self._is_environ(right):
                    self._record(left, node.lineno)
                left = right


def _code(data, rel, result):
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError, UnicodeDecodeError, RecursionError):
        result.unreadable.append((rel, "parse error"))   # never the error text
        return
    for name, line in _Reads(tree).found:
        if name is None:
            result.dynamic.append((rel, line))
        else:
            result.reads.setdefault(name, []).append((rel, line))


def docs(data, rel, result):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        result.unreadable.append((rel, "not UTF-8"))
        return
    result.example_files.append(rel)
    for number, line in enumerate(text.splitlines(), 1):
        match = _DOC_LINE.match(line)
        if match:
            result.docs.setdefault(match.group(1), []).append((rel, number))


def scan(root, example=None):
    """Scan the repo at root (a realpath). example is (rel, data) of an
    explicit --example already read by the caller; it replaces discovery."""
    result = Result()
    if example is not None:
        docs(example[1], example[0], result)
    for path, kind in _walk(root):
        if kind == "docs" and example is not None:
            continue
        rel = _rel(path, root)
        suffixes = CODE_SUFFIXES if kind == "code" else DOC_SUFFIXES
        try:
            data = read(resolve(path, suffixes, root))
        except Refused as exc:
            result.unreadable.append((rel, str(exc)))
            continue
        except OSError:
            result.unreadable.append((rel, "cannot read"))
            continue
        if kind == "code":
            _code(data, rel, result)
        else:
            docs(data, rel, result)
    return result
