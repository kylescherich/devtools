"""Synthetic trees for keycheck tests, built at test time in temp dirs.

Every matched file holds SECRET_MARK and token shapes from
tests/secret_shapes.py (DEC-195); keycheck must never open one, so none of
them may ever appear in a report. Files are written with os.open/os.write
before any guard is installed.
"""
import builtins
import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from keycheck.cli import main
from tests import secret_shapes as ss

GH = ss.GHP + "A1b2C3d4E5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SECRET_MARK = "SECRETFILEMARK"        # content of a file that must never be opened
CONTENT = f"{SECRET_MARK}\n{GH}\n"
DAY = 86400


def make(root, rel, mode=0o600, age_days=0, content=CONTENT):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, mode)
    stamp = time.time() - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def mkdir(root, rel):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(path, exist_ok=True)
    return path


def symlink(root, rel, target):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.symlink(target, path)
    return path


def _git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0")
    return env


def git_repo(root, rel):
    path = mkdir(root, rel)
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", path],
                   check=True, env=_git_env(), capture_output=True)
    return path


def git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], check=True, env=_git_env(), capture_output=True)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class Tree(unittest.TestCase):
    """A temp dir with an empty scan root at self.root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = os.path.realpath(self._tmp.name)
        self.root = mkdir(self.tmp, "root")

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, rel, mode=0o600, age_days=0):
        return make(self.root, rel, mode, age_days)

    def scan(self, *extra, root=None):
        """(exit code, parsed JSON, raw output) for a JSON run, under the
        open guard; asserts the output is non-empty first (DEC-071)."""
        root = root or self.root
        with open_guard(root) as attempts:
            code, out, err = run([root, "--format", "json", *extra])
        self.assertTrue(out, err)
        self.assertEqual(attempts, [], "a file under the root was opened")
        return code, json.loads(out), out

    def flags(self, *extra):
        """{path: flags} and the exit code."""
        code, data, _ = self.scan(*extra)
        return {f["path"]: f["flags"] for f in data["files"]}, code


def _path_of(arg):
    if isinstance(arg, int):
        return None
    try:
        return os.fsdecode(os.fspath(arg))
    except TypeError:
        return None


@contextlib.contextmanager
def open_guard(root):
    """Fail on any open of a path inside root, through builtins.open,
    io.open, os.open, Path.open, Path.read_text or Path.read_bytes. Yields
    the list of attempts."""
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
        if resolved == real_root or resolved.startswith(real_root + os.sep):
            attempts.append(path)
            raise AssertionError("keycheck opened a file under the scan root")

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
