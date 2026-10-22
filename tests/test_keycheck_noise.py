"""keycheck without the name-only noise (BRIEF-DEVWS-044, DEC-465/469):
default skips ~/.local/share/uv and ~/.claude/plugins (--all turns them
off), *secret*/*token* only outside .py .h .md .msg .txt, and the
"in-repo fixture?" note. Every negative case sits next to a positive
control; every run asserts non-empty output first (DEC-071) and runs under
the never-open guard."""
import os
import unittest
from unittest import mock

from keycheck.cli import EXIT_FLAGGED, EXIT_OK
from tests import keycheck_fixtures as fx

SAFETY = (".git", "node_modules", "__pycache__", "site-packages", "~/.cache",
          "~/.local/share/docker")
NOISE = ("~/.local/share/uv", "~/.claude/plugins")
FIXTURE = "in-repo fixture?"


class TestDefaultSkips(fx.Tree):
    def setUp(self):
        super().setUp()
        for rel in (".local/share/uv/python/cpython-3.12/lib/token.py",
                    ".claude/plugins/x/secret.py", ".claude/plugins/x/.env",
                    ".local/share/uv/y/.env", ".ssh/id_rsa"):
            self.make(rel)

    def test_uv_and_plugins_skipped_by_default(self):
        with mock.patch.dict(os.environ, {"HOME": self.root}):
            flags, code = self.flags()
        self.assertEqual(flags, {".ssh/id_rsa": ["OK"]})
        self.assertEqual(code, EXIT_OK)

    def test_all_reports_them_again(self):
        with mock.patch.dict(os.environ, {"HOME": self.root}):
            flags, _ = self.flags("--all")
        # token.py and secret.py stay out: the extension rule, not the skip
        self.assertEqual(flags, {".ssh/id_rsa": ["OK"], ".claude/plugins/x/.env": ["OK"],
                                 ".local/share/uv/y/.env": ["OK"]})

    def test_skip_is_home_relative(self):
        self.make("proj/.claude/plugins/p/.env")
        with mock.patch.dict(os.environ, {"HOME": self.tmp}):
            flags, _ = self.flags()
        self.assertEqual(sorted(flags), [".claude/plugins/x/.env",
                                         ".local/share/uv/y/.env", ".ssh/id_rsa",
                                         "proj/.claude/plugins/p/.env"])


class TestAllKeepsSafetySkips(fx.Tree):
    def test_safety_skips_stay_on(self):
        self.make("venv/pyvenv.cfg")
        for rel in (".git/hooks/id_rsa", "node_modules/p/cert.pem", "venv/server.key",
                    "__pycache__/x/id_rsa", "lib/site-packages/x/server.key",
                    ".cache/x/server.key", ".local/share/docker/id_rsa", "top.key"):
            self.make(rel)
        with mock.patch.dict(os.environ, {"HOME": self.root}):
            flags, _ = self.flags("--all")
        self.assertEqual(flags, {"top.key": ["OK"]})


class TestExtensionRule(fx.Tree):
    def test_source_and_doc_names_not_matched(self):
        for rel in ("tokenize.py", "secret_shapes.py", "token.h", "notes-token.md",
                    "x_secret.msg", "TOKEN.PY", "Secret.Txt", "lib/tokenizer/tokens.py",
                    "id_rsa"):
            self.make(rel)
        flags, _ = self.flags()
        self.assertEqual(flags, {"id_rsa": ["OK"]})

    def test_other_names_still_matched(self):
        names = ("id_token.txt", "secrets.env", "api.token", "secret.json", "token_store")
        for name in names:
            self.make(name)
        flags, _ = self.flags()
        # id_token.txt: matched by the exact pattern id_*, not by *token*
        self.assertEqual(flags, {name: ["OK"] for name in names})

    def test_backup_uses_extension_after_suffix(self):
        self.make("secrets.py.bak")
        self.make("secret.json.bak")
        self.make("id_rsa")
        flags, code = self.flags()
        self.assertEqual(flags, {"secret.json.bak": ["STALE-COPY"], "id_rsa": ["OK"]})
        self.assertEqual(code, EXIT_FLAGGED)


class TestInRepoFixture(fx.Tree):
    def setUp(self):
        super().setUp()
        self.repo = fx.git_repo(self.root, "proj")

    def _notes(self):
        code, data, _ = self.scan()
        return {f["path"]: (f["flags"], f["note"]) for f in data["files"]}, code

    def test_fixture_env_flagged_with_note(self):
        self.make("proj/tests/fixtures/.env")
        self.make("proj/test/x.key")
        self.make("proj/lib/tokenizer/.env")
        self.make("proj/docs/.env")
        notes, code = self._notes()
        flagged = ["IN-REPO-NOT-IGNORED"]
        self.assertEqual(notes, {"proj/tests/fixtures/.env": (flagged, FIXTURE),
                                 "proj/test/x.key": (flagged, FIXTURE),
                                 "proj/lib/tokenizer/.env": (flagged, ""),
                                 "proj/docs/.env": (flagged, "")})
        self.assertEqual(code, EXIT_FLAGGED)

    def test_folder_name_never_hides_exact_pattern(self):
        self.make("proj/tests/fixtures/.env")
        self.make("proj/lib/tokenizer/.env")
        self.make("proj/docs/.env")
        flags, code = self.flags()
        self.assertEqual(sorted(flags), ["proj/docs/.env", "proj/lib/tokenizer/.env",
                                         "proj/tests/fixtures/.env"])
        self.assertEqual(code, EXIT_FLAGGED)

    def test_note_only_for_path_inside_repo(self):
        self.make("tests/.env")                    # tests/ but no repo
        inner = fx.git_repo(self.root, "test/inner")
        fx.make(inner, ".env")                     # test/ above the repo only
        notes, _ = self._notes()
        self.assertEqual(notes, {"test/inner/.env": (["IN-REPO-NOT-IGNORED"], ""),
                                 "tests/.env": (["OK"], "")})


class TestReportShape(fx.Tree):
    def test_skips_and_note_in_json(self):
        self.make("id_rsa")
        with mock.patch.dict(os.environ, {"HOME": self.root}):
            _, data, _ = self.scan()
            _, data_all, _ = self.scan("--all")
        self.assertEqual(list(data), ["schema", "root", "skips", "files", "counts"])
        self.assertEqual(data["schema"], "keycheck/1")
        for skip in SAFETY + NOISE:
            self.assertIn(skip, data["skips"])
        for skip in SAFETY:
            self.assertIn(skip, data_all["skips"])
        for skip in NOISE:
            self.assertNotIn(skip, data_all["skips"])
        self.assertEqual(len(data["skips"]), len(data_all["skips"]) + 2)
        self.assertEqual(data["files"][0]["note"], "")

    def test_note_in_text_and_md(self):
        fx.git_repo(self.root, "proj")
        self.make("proj/tests/.env")
        self.make("server.key")
        with fx.open_guard(self.root) as attempts:
            _, text, err = fx.run([self.root])
            _, md, _ = fx.run([self.root, "--format", "md"])
        self.assertTrue(text, err)
        self.assertTrue(md)
        self.assertEqual(attempts, [])
        self.assertIn("  IN-REPO-NOT-IGNORED  proj/tests/.env  size ", text)
        self.assertIn("  note in-repo fixture?", text)
        self.assertIn("| flags | path | size | age (days) | mode | note |", md)
        self.assertIn("| IN-REPO-NOT-IGNORED | proj/tests/.env | ", md)
        self.assertIn(" | in-repo fixture? |", md)
        for out in (text, md):
            self.assertIn("~/.local/share/uv", out)
            self.assertIn("~/.claude/plugins", out)

    def test_help_lists_every_skip(self):
        code, out, err = fx.run(["--help"])
        self.assertTrue(out, err)
        self.assertEqual(code, EXIT_OK)
        for word in SAFETY + NOISE + ("pyvenv.cfg", "device", "symlink", "--all"):
            self.assertIn(word, out)


if __name__ == "__main__":
    unittest.main()
