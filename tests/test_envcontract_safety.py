"""envcontract hard safety rules (BRIEF-DEVWS-034), one named test each:
1. only *.py and *.example/*.template/*.sample are ever opened;
2. symlinks are resolved first and refused when the target has another
   suffix or lies outside the repo;
3. no value (example value or getenv default) is printed in any format;
4. the package imports no subprocess or network modules.
Every test asserts non-empty output before asserting absence (DEC-071);
sentinels come from tests/secret_shapes.py (DEC-195) via the fixtures."""
import ast
import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from envcontract.cli import EXIT_OK, EXIT_USAGE, main
from tests import envcontract_fixtures as fx

ROOT = pathlib.Path(__file__).resolve().parent.parent
FORMATS = ("text", "md", "json")
FORBIDDEN = ("subprocess", "socket", "urllib", "http", "ctypes")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class _Repo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.repo = os.path.join(self.tmp, "proj")
        os.mkdir(self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text):
        return fx.write(self.repo, rel, text)

    def guarded(self, argv):
        with fx.open_guard(self.repo) as attempts:
            result = run(argv)
        self.assertEqual(attempts, [], "a forbidden file was opened")
        return result

    def assert_no_sentinel(self, text):
        self.assertTrue(text)
        for sentinel in fx.SENTINELS:
            self.assertNotIn(sentinel, text)


class TestRule1Suffixes(_Repo):
    def test_rule1_env_beside_example_never_opened(self):
        secret_files = {".env": "GATE=", "secrets.env": "S1=", "arora.env": "S2=",
                        "id.key": "", "server.pem": "", "secrets.json": ""}
        paths = []
        for name, prefix in secret_files.items():
            paths.append(self.write(name, f"{prefix}{fx.SECRET_MARK} {fx.GH}\n"
                                          f"LEAKNAME_{name.replace('.', '_')}=1\n"))
        self.write(".env.example", "GATE=changeme\n")
        self.write("app.py", 'import os\nos.getenv("GATE")\n')
        if not (hasattr(os, "geteuid") and os.geteuid() == 0):
            for path in paths:
                os.chmod(path, 0)
        try:
            for fmt in FORMATS:
                with self.subTest(fmt=fmt):
                    code, out, err = self.guarded([self.repo, "--format", fmt])
                    self.assert_no_sentinel(out)
                    self.assertNotIn("LEAKNAME", out + err)
                    # .env.example was read (GATE documented, so exit 0);
                    # .env and the others were not.
                    self.assertIn(".env.example", out)
                    self.assertEqual(code, EXIT_OK)
        finally:
            for path in paths:
                os.chmod(path, 0o600)

    def test_rule1_explicit_example_refused(self):
        self.write("app.py", 'import os\nos.getenv("A")\n')
        for name in (".env", "secrets.env", "arora.env", "id.key", "server.pem"):
            path = self.write(name, f"A={fx.SECRET_MARK}\n")
            for arg in (name, str(path)):
                with self.subTest(arg=arg):
                    code, out, err = self.guarded([self.repo, "--example", arg])
                    self.assertTrue(err)
                    self.assertIn("refus", err)
                    self.assertNotIn(fx.SECRET_MARK, out + err)
                    self.assertEqual(code, EXIT_USAGE)


class TestRule2Symlinks(_Repo):
    def test_rule2_symlink_example_to_env_refused(self):
        self.write(".env", f"LEAKED_NAME={fx.SECRET_MARK}\n")
        fx.symlink(self.repo, "bad.example", ".env")
        self.write("app.py", 'import os\nos.getenv("A")\n')
        code, out, err = self.guarded([self.repo, "--format", "json"])
        self.assertTrue(out, err)
        data = json.loads(out)
        self.assertIn("bad.example", [u["path"] for u in data["unreadable"]])
        self.assertEqual(data["example_files"], [])
        self.assertNotIn("LEAKED_NAME", out)
        self.assertNotIn(fx.SECRET_MARK, out + err)
        code, out, err = self.guarded([self.repo, "--example", "bad.example"])
        self.assertTrue(err)
        self.assertNotIn(fx.SECRET_MARK, out + err)
        self.assertEqual(code, EXIT_USAGE)

    def test_rule2_symlink_py_to_non_py_refused(self):
        self.write("notes.txt", 'import os\nos.getenv("FROM_TXT")\n')
        fx.symlink(self.repo, "evil.py", "notes.txt")
        self.write("app.py", 'import os\nos.getenv("A")\n')
        code, out, err = self.guarded([self.repo, "--format", "json"])
        self.assertTrue(out, err)
        data = json.loads(out)
        self.assertIn("evil.py", [u["path"] for u in data["unreadable"]])
        self.assertNotIn("FROM_TXT", out)

    def test_rule2_symlink_outside_repo_refused(self):
        outside = os.path.join(self.tmp, "outside")
        fx.write(outside, "real.example", "OUTSIDE_DOC=1\n")
        fx.write(outside, "real.py", 'import os\nos.getenv("OUTSIDE_READ")\n')
        fx.write(outside, "deep/inner.py", 'import os\nos.getenv("OUTSIDE_DIR")\n')
        fx.symlink(self.repo, "link.example", os.path.join(outside, "real.example"))
        fx.symlink(self.repo, "link.py", os.path.join(outside, "real.py"))
        fx.symlink(self.repo, "linkdir", outside)
        self.write("app.py", 'import os\nos.getenv("A")\n')
        code, out, err = self.guarded([self.repo, "--format", "json"])
        self.assertTrue(out, err)
        data = json.loads(out)
        unreadable = [u["path"] for u in data["unreadable"]]
        self.assertIn("link.example", unreadable)
        self.assertIn("link.py", unreadable)
        for name in ("OUTSIDE_DOC", "OUTSIDE_READ", "OUTSIDE_DIR"):
            self.assertNotIn(name, out)
        for arg in ("link.example", os.path.join(outside, "real.example")):
            with self.subTest(arg=arg):
                code, out, err = self.guarded([self.repo, "--example", arg])
                self.assertTrue(err)
                self.assertNotIn("OUTSIDE_DOC", out + err)
                self.assertEqual(code, EXIT_USAGE)


class TestRule3NoValues(_Repo):
    def test_rule3_no_values_in_any_format(self):
        self.write(".env.example",
                   f"API_TOKEN={fx.GH}\nexport AWS_KEY={fx.AWS}\n"
                   f"# PEM_LINE={fx.PEM_BODY}\nPLAIN_SETTING={fx.EXAMPLE_MARK}\n"
                   f"UNUSED_SETTING={fx.EXAMPLE_MARK}2\n")
        self.write("app.py",
                   "import os\n"
                   f'a = os.getenv("API_TOKEN", "{fx.GH}")\n'
                   f'b = os.environ.get("UNDOC_SETTING", "{fx.DEFAULT_MARK}")\n'
                   f'c = os.getenv("PLAIN_SETTING", default="{fx.DEFAULT_MARK}")\n'
                   f'd = os.getenv("AWS_KEY", "{fx.AWS}")\n'
                   f'e = os.getenv("PEM_LINE", "{fx.PEM_BODY}")\n')
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                code, out, err = self.guarded([self.repo, "--format", fmt])
                self.assert_no_sentinel(out)
                self.assertNotIn(fx.EXAMPLE_MARK, err)
                self.assertNotIn(fx.DEFAULT_MARK, err)
                self.assertIn("UNDOC_SETTING", out)
                self.assertIn("UNUSED_SETTING", out)


class TestRule4NoSubprocessOrNetwork(unittest.TestCase):
    def test_rule4_no_subprocess_or_network_imports(self):
        files = sorted((ROOT / "envcontract").glob("*.py"))
        self.assertTrue(files)
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    self.assertNotIn(module.split(".")[0], FORBIDDEN, f"{path}: {module}")


class TestRedaction(_Repo):
    def test_redact_copy_identical(self):
        copy = (ROOT / "envcontract" / "_redact.py").read_bytes()
        original = (ROOT / "loglens" / "redact.py").read_bytes()
        self.assertTrue(copy)
        self.assertEqual(copy, original)

    def test_normal_key_names_not_redacted(self):
        self.write(".env.example", "SESSION_SECRET=\n")
        self.write("token_utils.py", 'import os\nos.getenv("OPENAI_API_KEY")\n'
                                     'os.getenv("PHARMRAG_GATE_KEY")\n')
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                code, out, err = run([self.repo, "--format", fmt])
                self.assertTrue(out, err)
                for word in ("OPENAI_API_KEY", "PHARMRAG_GATE_KEY", "SESSION_SECRET",
                             "token_utils.py"):
                    self.assertIn(word, out)
                self.assertNotIn("redacted", out)
        code, out, _ = run([self.repo, "--format", "json"])
        data = json.loads(out)
        self.assertEqual(data["undocumented"], [
            {"name": "OPENAI_API_KEY", "uses": [{"path": "token_utils.py", "line": 2}]},
            {"name": "PHARMRAG_GATE_KEY", "uses": [{"path": "token_utils.py", "line": 3}]}])
        self.assertEqual([u["name"] for u in data["unused"]], ["SESSION_SECRET"])
        self.assertEqual(data["summary"]["documented"], 1)


if __name__ == "__main__":
    unittest.main()
