"""Synthetic repos for envcontract tests, built at test time in temp dirs.

Sentinels are built at runtime from tests/secret_shapes.py (DEC-195). The
plain markers matter as much as the token shapes: redact() would hide a
token shape anyway, so only a plain marker proves a value was never printed.
"""
import builtins
import contextlib
import io
import os
import pathlib
from unittest import mock

from tests import secret_shapes as ss

GH = ss.GHP + "A1b2C3d4E5F6g7H8i9J0k1L2m3N4o5P6q7R8"
AWS = ss.AKIA + "QWERTYUIOPASDFGH"
PEM_BODY = "MIIEsentinelPEMbodyQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo"
EXAMPLE_MARK = "EXAMPLEVALUEMARK"     # a plain value in an example file
DEFAULT_MARK = "DEFAULTVALUEMARK"     # a plain getenv default
SECRET_MARK = "SECRETFILEMARK"        # content of a file that must never be opened
SENTINELS = (GH, AWS, PEM_BODY, EXAMPLE_MARK, DEFAULT_MARK, SECRET_MARK)

ALLOWED = (".py", ".example", ".template", ".sample")


def write(root, rel, text):
    path = pathlib.Path(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def symlink(root, rel, target):
    path = pathlib.Path(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)
    return path


def _path_of(arg):
    if isinstance(arg, int):
        return None
    try:
        return os.fsdecode(os.fspath(arg))
    except TypeError:
        return None


@contextlib.contextmanager
def open_guard(root):
    """Record (and refuse) every open whose resolved name lacks an allowed
    suffix or which resolves outside root. Yields the list of attempts."""
    attempts = []
    real_root = os.path.realpath(root)
    real_open, real_os_open = builtins.open, os.open
    real_path_open = pathlib.Path.open
    real_read_text, real_read_bytes = pathlib.Path.read_text, pathlib.Path.read_bytes

    def check(arg):
        path = _path_of(arg)
        if path is None:
            return
        resolved = os.path.realpath(path)
        inside = resolved == real_root or resolved.startswith(real_root + os.sep)
        if not resolved.endswith(ALLOWED) or not path.endswith(ALLOWED) or not inside:
            attempts.append(path)
            raise AssertionError("envcontract opened a forbidden file")

    def g_open(file, *a, **k):
        check(file)
        return real_open(file, *a, **k)

    def g_os_open(path, *a, **k):
        check(path)
        return real_os_open(path, *a, **k)

    def g_path_open(self, *a, **k):
        check(self)
        return real_path_open(self, *a, **k)

    def g_read_text(self, *a, **k):
        check(self)
        return real_read_text(self, *a, **k)

    def g_read_bytes(self, *a, **k):
        check(self)
        return real_read_bytes(self, *a, **k)

    with mock.patch.object(builtins, "open", g_open), \
            mock.patch.object(io, "open", g_open), \
            mock.patch.object(os, "open", g_os_open), \
            mock.patch.object(pathlib.Path, "open", g_path_open), \
            mock.patch.object(pathlib.Path, "read_text", g_read_text), \
            mock.patch.object(pathlib.Path, "read_bytes", g_read_bytes):
        yield attempts
