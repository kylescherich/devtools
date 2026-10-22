"""keycheck never opens a matched file and never prints content, a hash of
content or an environment value (BRIEF-DEVWS-041). Every run asserts
non-empty output first (DEC-071); sentinels come from tests/secret_shapes.py
(DEC-195) via the fixtures."""
import ast
import hashlib
import os
import pathlib
import unittest
from unittest import mock

from tests import keycheck_fixtures as fx

FORMATS = ("text", "md", "json")
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_MARK = "ENVVALUEMARK"
NETWORK = ("socket", "urllib", "http", "ctypes", "ssl")
SUBPROCESS_ALLOWED = {"gitcheck.py", "acl.py"}


class TestNeverOpen(fx.Tree):
    def setUp(self):
        super().setUp()
        repo = fx.git_repo(self.root, "proj")
        fx.make(repo, ".gitignore", 0o644, content=".env\n")
        for rel in ("id_rsa", "server.key", ".keys", "secrets.env.bak-1",
                    ".openhands/settings.json", ".claude/.credentials.json",
                    "pc-tasks/healthchecks/job.url", "proj/.env", "proj/deploy.key",
                    "notes.txt"):
            self.make(rel, 0o644)
        self.digests = [hashlib.new(name, fx.CONTENT.encode("utf-8")).hexdigest()
                        for name in ("md5", "sha1", "sha256", "sha512")]

    def test_never_open_any_format(self):
        for fmt in FORMATS:
            with self.subTest(fmt=fmt), \
                    mock.patch.dict(os.environ, {"KEYCHECK_TEST_VALUE": ENV_MARK}), \
                    fx.open_guard(self.root) as attempts:
                code, out, err = fx.run([self.root, "--format", fmt])
                self.assertTrue(out, err)
                self.assertEqual(attempts, [])
                self.assertIn("deploy.key", out)
                self.assertIn("credentials.json", out)
                for absent in (fx.SECRET_MARK, fx.GH, ENV_MARK, *self.digests):
                    self.assertNotIn(absent, out)
                    self.assertNotIn(absent, err)

    def test_guard_catches_open(self):
        """The guard itself works: opening a fixture file under it fails."""
        path = os.path.join(self.root, "id_rsa")
        for opener in (lambda: open(path), lambda: pathlib.Path(path).read_text(),
                       lambda: pathlib.Path(path).read_bytes(),
                       lambda: pathlib.Path(path).open(), lambda: os.open(path, 0)):
            with fx.open_guard(self.root) as attempts:
                with self.assertRaises(AssertionError):
                    opener()
            self.assertEqual(attempts, [path])


class TestSourceRules(unittest.TestCase):
    def _imports(self, py):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module.split(".")[0])
        return names

    def test_no_network_and_subprocess_only_in_runners(self):
        files = sorted((ROOT / "keycheck").glob("*.py"))
        self.assertTrue(files)
        for py in files:
            names = self._imports(py)
            for bad in NETWORK:
                self.assertNotIn(bad, names, py.name)
            if py.name not in SUBPROCESS_ALLOWED:
                self.assertNotIn("subprocess", names, py.name)

    def test_no_shell_true(self):
        for name in sorted(SUBPROCESS_ALLOWED):
            source = (ROOT / "keycheck" / name).read_text(encoding="utf-8")
            self.assertTrue(source)
            self.assertNotIn("shell=True", source)
            self.assertNotIn("os.system", source)


if __name__ == "__main__":
    unittest.main()
