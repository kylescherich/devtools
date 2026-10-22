"""keycheck command line: help, usage, formats, JSON schema keycheck/1, the
redaction copy (BRIEF-DEVWS-041). Every run asserts non-empty output first
(DEC-071)."""
import json
import os
import pathlib
import unittest
from unittest import mock

from keycheck.cli import EXIT_OK, EXIT_USAGE
from tests import keycheck_fixtures as fx

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestUsage(unittest.TestCase):
    def test_help(self):
        code, out, err = fx.run(["--help"])
        self.assertTrue(out, err)
        self.assertEqual(code, EXIT_OK)
        for word in ("ROOT", "--format", "--max-age-days", "exit codes",
                     "IN-REPO-NOT-IGNORED", "never opens a matched file"):
            self.assertIn(word, out)

    def test_usage_errors(self):
        for argv in (["--format", "html"], ["--max-age-days", "-1"],
                     ["--max-age-days", "x"], ["/nonexistent/keycheck-root"],
                     ["--bogus"]):
            with self.subTest(argv=argv):
                code, out, err = fx.run(argv)
                self.assertTrue(err)
                self.assertEqual(code, EXIT_USAGE)
                self.assertEqual(out, "")


class TestReports(fx.Tree):
    def test_default_root_is_home(self):
        self.make(".ssh/id_rsa")
        with mock.patch.dict(os.environ, {"HOME": self.root}), \
                fx.open_guard(self.root) as attempts:
            code, out, err = fx.run(["--format", "json"])
        self.assertTrue(out, err)
        self.assertEqual(attempts, [])
        data = json.loads(out)
        self.assertEqual(data["root"], "~")
        self.assertEqual([f["path"] for f in data["files"]], [".ssh/id_rsa"])

    def test_root_under_home_shown_with_tilde(self):
        self.make("sub/id_rsa")
        with mock.patch.dict(os.environ, {"HOME": self.root}):
            _, data, _ = self.scan(root=os.path.join(self.root, "sub"))
        self.assertEqual(data["root"], "~/sub")
        self.assertEqual([f["path"] for f in data["files"]], ["id_rsa"])

    def test_json_schema(self):
        self.make("id_rsa", 0o600, age_days=3)
        _, data, _ = self.scan()
        self.assertEqual(list(data), ["schema", "root", "files", "counts"])
        self.assertEqual(data["schema"], "keycheck/1")
        self.assertEqual(data["root"], self.root)
        self.assertEqual(data["files"], [{"path": "id_rsa",
                                          "size": len(fx.CONTENT.encode("utf-8")),
                                          "age_days": 3, "mode": "0600",
                                          "flags": ["OK"]}])
        self.assertEqual(list(data["counts"]), [
            "total", "IN-REPO-NOT-IGNORED", "OTHERS-CAN-READ", "STALE-COPY", "OLD",
            "SYMLINK", "OK", "unreadable_dirs"])

    def test_several_roots(self):
        a = fx.mkdir(self.root, "a")
        b = fx.mkdir(self.root, "b")
        fx.make(a, "id_rsa")
        fx.make(b, "server.key", 0o644)
        with mock.patch.dict(os.environ, {"HOME": self.root}), \
                fx.open_guard(self.root) as attempts:
            code, out, err = fx.run([a, b, "--format", "json"])
        self.assertTrue(out, err)
        self.assertEqual(attempts, [])
        data = json.loads(out)
        self.assertEqual(data["root"], ["~/a", "~/b"])
        self.assertEqual([f["path"] for f in data["files"]],
                         ["~/b/server.key", "~/a/id_rsa"])
        self.assertEqual(code, 1)

    def test_text_and_md(self):
        self.make("id_rsa", 0o644)
        self.make("server.key", 0o600)
        with fx.open_guard(self.root):
            _, text, err = fx.run([self.root])
            _, md, _ = fx.run([self.root, "--format", "md"])
        self.assertTrue(text, err)
        self.assertTrue(md)
        self.assertIn("  OTHERS-CAN-READ  id_rsa  size ", text)
        self.assertIn("  OK  server.key  size ", text)
        self.assertIn("mode 0644", text)
        self.assertIn("| flags | path | size | age (days) | mode |", md)
        self.assertIn("| OTHERS-CAN-READ | id_rsa | ", md)

    def test_untrusted_names_escaped(self):
        self.make("evil\x1b[31m.key")
        self.make("pipe|star*.key")
        with fx.open_guard(self.root):
            _, text, err = fx.run([self.root])
            _, md, _ = fx.run([self.root, "--format", "md"])
        self.assertTrue(text, err)
        self.assertTrue(md)
        self.assertNotIn("\x1b", text + md)
        self.assertIn("evil\\x1b[31m.key", text)
        self.assertIn("pipe\\|star\\*.key", md)

    def test_redaction_is_a_no_op_for_key_names(self):
        names = ("api_token", "client_secret.json", ".env.production", "id_ed25519",
                 "github_token.bak", "SECRET_KEY.pem", "password_token.txt")
        for name in names:
            self.make(name)
        for fmt in ("text", "md", "json"):
            with self.subTest(fmt=fmt), fx.open_guard(self.root):
                _, out, err = fx.run([self.root, "--format", fmt])
                self.assertTrue(out, err)
                for name in names:
                    self.assertIn(name, out)
                self.assertNotIn("redacted", out.lower())

    def test_redact_copy_identical(self):
        copy = (ROOT / "keycheck" / "_redact.py").read_bytes()
        original = (ROOT / "loglens" / "redact.py").read_bytes()
        self.assertTrue(copy)
        self.assertEqual(copy, original)


if __name__ == "__main__":
    unittest.main()
