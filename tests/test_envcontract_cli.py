"""envcontract behaviour through the CLI entry point: every access form,
aliases, dynamic reads, example-file syntax, parse errors, skipped dirs,
the no-example case, JSON schema and exit codes. Repos are synthetic,
built in temp dirs. Every test asserts non-empty output first (DEC-071)."""
import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from envcontract.cli import EXIT_DIFFERENCES, EXIT_INPUT, EXIT_OK, EXIT_USAGE, main
from tests import envcontract_fixtures as fx

ROOT = pathlib.Path(__file__).resolve().parent.parent


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

    def report(self, *extra):
        code, out, err = run([self.repo, "--format", "json", *extra])
        self.assertTrue(out, err)
        return code, json.loads(out)

    def names(self, data, section):
        return [item["name"] for item in data[section]]

    def uses(self, data, name):
        for item in data["undocumented"]:
            if item["name"] == name:
                return [(u["path"], u["line"]) for u in item["uses"]]
        self.fail(f"{name} not undocumented")


class TestAccessForms(_Repo):
    def test_getenv_literal(self):
        self.write("app.py", 'import os\nA = os.getenv("ALPHA")\n')
        _, data = self.report()
        self.assertEqual(self.uses(data, "ALPHA"), [("app.py", 2)])

    def test_getenv_with_default(self):
        self.write("app.py", 'import os\nA = os.getenv("BETA", "x")\n')
        _, data = self.report()
        self.assertEqual(self.uses(data, "BETA"), [("app.py", 2)])

    def test_environ_subscript(self):
        self.write("app.py", 'import os\n\nA = os.environ["GAMMA"]\n')
        _, data = self.report()
        self.assertEqual(self.uses(data, "GAMMA"), [("app.py", 3)])

    def test_environ_get(self):
        self.write("app.py", 'import os\nA = os.environ.get("DELTA")\n'
                             'B = os.environ.get("DELTA", "d")\n')
        _, data = self.report()
        self.assertEqual(self.uses(data, "DELTA"), [("app.py", 2), ("app.py", 3)])

    def test_in_environ(self):
        self.write("app.py", 'import os\nif "EPS" in os.environ:\n    pass\n'
                             'if "ZETA" not in os.environ:\n    pass\n')
        _, data = self.report()
        self.assertEqual(self.uses(data, "EPS"), [("app.py", 2)])
        self.assertEqual(self.uses(data, "ZETA"), [("app.py", 4)])

    def test_import_os_alias(self):
        self.write("app.py", 'import os as o\nA = o.getenv("ETA")\nB = o.environ["THETA"]\n')
        _, data = self.report()
        self.assertEqual(self.uses(data, "ETA"), [("app.py", 2)])
        self.assertEqual(self.uses(data, "THETA"), [("app.py", 3)])

    def test_from_os_import_getenv_alias(self):
        self.write("a.py", 'from os import getenv, environ\n'
                           'A = getenv("IOTA")\nB = environ["KAPPA"]\n'
                           'C = environ.get("LAMBDA")\nD = "MU" in environ\n')
        self.write("b.py", 'from os import getenv as ge, environ as en\n'
                           'A = ge("NU")\nB = en.get("XI", "d")\n')
        _, data = self.report()
        self.assertEqual(self.uses(data, "IOTA"), [("a.py", 2)])
        self.assertEqual(self.uses(data, "KAPPA"), [("a.py", 3)])
        self.assertEqual(self.uses(data, "LAMBDA"), [("a.py", 4)])
        self.assertEqual(self.uses(data, "MU"), [("a.py", 5)])
        self.assertEqual(self.uses(data, "NU"), [("b.py", 2)])
        self.assertEqual(self.uses(data, "XI"), [("b.py", 3)])

    def test_writes_setdefault_pop_not_reads(self):
        self.write("app.py", 'import os\nos.environ["WRITTEN"] = "1"\n'
                             'os.environ.setdefault("SETDEF", "x")\n'
                             'os.environ.pop("POPPED", None)\n'
                             'other = {}\nother.getenv = 1\n')
        code, data = self.report()
        self.assertEqual(data["undocumented"], [])
        self.assertEqual(data["dynamic"], [])
        self.assertEqual(code, EXIT_OK)

    def test_other_objects_not_counted(self):
        self.write("app.py", 'import config\nA = config.getenv("NOT_OS")\n'
                             'B = config.environ["NOT_OS2"]\n')
        _, data = self.report()
        self.assertEqual(data["undocumented"], [])


class TestDynamic(_Repo):
    def test_dynamic_reads_listed_not_checked(self):
        self.write("app.py", 'import os\nname = "X"\n'
                             'a = os.getenv(name)\n'
                             'b = os.environ[f"P_{name}"]\n'
                             'c = os.environ.get("P_" + name)\n'
                             'd = name in os.environ\n')
        code, data = self.report()
        self.assertEqual(data["undocumented"], [])
        self.assertEqual([(d["path"], d["line"]) for d in data["dynamic"]],
                         [("app.py", 3), ("app.py", 4), ("app.py", 5), ("app.py", 6)])
        self.assertEqual(data["summary"]["dynamic"], 4)

    def test_dynamic_alone_does_not_change_exit_code(self):
        self.write(".env.example", "")
        self.write("app.py", 'import os\nn = "X"\na = os.getenv(n)\n')
        code, data = self.report()
        self.assertEqual(data["summary"]["dynamic"], 1)
        self.assertEqual(code, EXIT_OK)


class TestDocs(_Repo):
    def test_plain_export_and_commented_lines(self):
        self.write(".env.example", "PLAIN=1\nexport EXP=2\n# COMMENTED=3\n#NOSPACE=\n"
                                   "# just a comment\n\n  INDENTED = 4\n")
        self.write("app.py", 'import os\nos.getenv("PLAIN")\n')
        code, data = self.report()
        self.assertEqual(data["example_files"], [".env.example"])
        self.assertEqual(self.names(data, "unused"),
                         ["COMMENTED", "EXP", "INDENTED", "NOSPACE"])
        where = {i["name"]: i["where"] for i in data["unused"]}
        self.assertEqual(where["EXP"], [{"path": ".env.example", "line": 2}])
        self.assertEqual(data["undocumented"], [])
        self.assertEqual(code, EXIT_DIFFERENCES)

    def test_template_and_sample_discovered(self):
        self.write("config.env.template", "TPL=\n")
        self.write("deploy/vars.sample", "SMP=\n")
        self.write("notes.txt", "TXT=\n")
        self.write("app.py", 'import os\nos.getenv("TPL")\nos.getenv("SMP")\n')
        code, data = self.report()
        self.assertEqual(data["example_files"], ["config.env.template", "deploy/vars.sample"])
        self.assertEqual(data["unused"], [])
        self.assertEqual(code, EXIT_OK)


class TestUnreadable(_Repo):
    def test_parse_error_lists_path_only(self):
        self.write("bad.py", "import os\ndef f(:\n    SOURCEMARK = os.getenv('INBAD')\n")
        self.write("good.py", 'import os\nos.getenv("GOOD")\n')
        self.write(".env.example", "GOOD=\n")
        for fmt in ("text", "md", "json"):
            with self.subTest(fmt=fmt):
                code, out, err = run([self.repo, "--format", fmt])
                self.assertTrue(out, err)
                self.assertIn("bad.py", out)
                for leak in ("SOURCEMARK", "INBAD", "def f", "invalid syntax",
                             "SyntaxError", "line 2"):
                    self.assertNotIn(leak, out + err)
                self.assertEqual(code, EXIT_OK)
        _, data = self.report()
        self.assertEqual(data["unreadable"], [{"path": "bad.py", "reason": "parse error"}])


class TestSkipDirs(_Repo):
    def test_skip_dirs(self):
        skipped = {".git": "S_GIT", ".venv": "S_DOTVENV", "venv": "S_VENV",
                   "__pycache__": "S_CACHE", "node_modules": "S_NODE",
                   "myenv": "S_PYVENV"}
        for folder, name in skipped.items():
            self.write(f"{folder}/sub/mod.py", f'import os\nos.getenv("{name}")\n')
            self.write(f"{folder}/x.example", f"{name}_DOC=\n")
        self.write("myenv/pyvenv.cfg", "home = /usr/bin\n")
        self.write("app.py", 'import os\nos.getenv("KEPT")\n')
        _, data = self.report()
        self.assertEqual(self.names(data, "undocumented"), ["KEPT"])
        self.assertEqual(data["unused"], [])
        self.assertEqual(data["example_files"], [])


class TestNoExample(_Repo):
    def test_no_example_file_reported(self):
        self.write("app.py", 'import os\nos.getenv("ONLY")\n')
        code, out, err = run([self.repo])
        self.assertTrue(out, err)
        self.assertIn("no example file found", out)
        self.assertEqual(code, EXIT_DIFFERENCES)
        code, data = self.report()
        self.assertEqual(data["example_files"], [])
        self.assertEqual(self.names(data, "undocumented"), ["ONLY"])

    def test_no_example_and_no_reads_is_ok(self):
        self.write("app.py", "x = 1\n")
        code, out, err = run([self.repo])
        self.assertTrue(out, err)
        self.assertIn("no example file found", out)
        self.assertEqual(code, EXIT_OK)


class TestFormats(_Repo):
    def setUp(self):
        super().setUp()
        self.write(".env.example", "DOCD=\nUNUSEDNAME=\n")
        self.write("pkg/app.py", 'import os\nos.getenv("DOCD")\nos.getenv("UNDOCNAME")\n'
                                 'n = "x"\nos.getenv(n)\n')
        self.write("broken.py", "def (:\n")

    def test_json_schema(self):
        code, data = self.report()
        self.assertEqual(list(data), ["schema", "repo", "example_files", "summary",
                                      "undocumented", "unused", "dynamic", "unreadable"])
        self.assertEqual(data["schema"], "envcontract/1")
        self.assertEqual(data["repo"], "proj")
        self.assertEqual(data["summary"], {"read": 2, "documented": 2, "undocumented": 1,
                                           "unused": 1, "dynamic": 1, "unreadable": 1})
        self.assertEqual(data["undocumented"],
                         [{"name": "UNDOCNAME", "uses": [{"path": "pkg/app.py", "line": 3}]}])
        self.assertEqual(data["unused"],
                         [{"name": "UNUSEDNAME", "where": [{"path": ".env.example", "line": 2}]}])
        self.assertEqual(data["dynamic"], [{"path": "pkg/app.py", "line": 5}])
        self.assertEqual(data["unreadable"], [{"path": "broken.py", "reason": "parse error"}])
        self.assertEqual(code, EXIT_DIFFERENCES)

    def test_text_and_md_sections(self):
        for fmt in ("text", "md"):
            with self.subTest(fmt=fmt):
                code, out, err = run([self.repo, "--format", fmt])
                self.assertTrue(out, err)
                for word in ("Undocumented", "Unused", "Dynamic", "Unreadable", "Summary",
                             "UNDOCNAME", "UNUSEDNAME", "pkg/app.py line 3",
                             ".env.example line 2", "broken.py"):
                    self.assertIn(word, out)
                self.assertEqual(code, EXIT_DIFFERENCES)

    def test_reruns_identical(self):
        for fmt in ("text", "md", "json"):
            first = run([self.repo, "--format", fmt])
            self.assertTrue(first[1])
            self.assertEqual(first, run([self.repo, "--format", fmt]))


class TestExitCodes(_Repo):
    def test_consistent_exit_0(self):
        self.write(".env.example", "A=\n")
        self.write("app.py", 'import os\nos.getenv("A")\n')
        code, out, err = run([self.repo])
        self.assertTrue(out, err)
        self.assertEqual(code, EXIT_OK)

    def test_undocumented_exit_1(self):
        self.write(".env.example", "A=\n")
        self.write("app.py", 'import os\nos.getenv("A")\nos.getenv("B")\n')
        code, out, err = run([self.repo])
        self.assertTrue(out, err)
        self.assertEqual(code, EXIT_DIFFERENCES)

    def test_unused_exit_1(self):
        self.write(".env.example", "A=\nB=\n")
        self.write("app.py", 'import os\nos.getenv("A")\n')
        code, out, err = run([self.repo])
        self.assertTrue(out, err)
        self.assertEqual(code, EXIT_DIFFERENCES)

    def test_usage_errors_exit_2(self):
        for argv in ([], [self.repo, "--frobnicate"], [self.repo, "--format", "xml"]):
            with self.subTest(argv=argv):
                code, _, err = run(argv)
                self.assertTrue(err)
                self.assertEqual(code, EXIT_USAGE)

    def test_missing_repo_exit_4(self):
        code, _, err = run([os.path.join(self.tmp, "nope")])
        self.assertTrue(err)
        self.assertEqual(code, EXIT_INPUT)

    def test_repo_is_a_file_exit_4(self):
        path = fx.write(self.tmp, "file.py", "x = 1\n")
        code, _, err = run([path])
        self.assertTrue(err)
        self.assertEqual(code, EXIT_INPUT)

    def test_missing_example_exit_4(self):
        self.write("app.py", "x = 1\n")
        code, _, err = run([self.repo, "--example", "missing.example"])
        self.assertTrue(err)
        self.assertIn("missing.example", err)
        self.assertEqual(code, EXIT_INPUT)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads mode 000")
    def test_unreadable_example_exit_4(self):
        path = self.write("locked.example", "A=" + fx.EXAMPLE_MARK + "\n")
        os.chmod(path, 0)
        try:
            code, out, err = run([self.repo, "--example", "locked.example"])
        finally:
            os.chmod(path, 0o600)
        self.assertTrue(err)
        self.assertNotIn(fx.EXAMPLE_MARK, out + err)
        self.assertEqual(code, EXIT_INPUT)

    def test_refused_example_beats_missing_repo(self):
        code, _, err = run([os.path.join(self.tmp, "nope"), "--example", ".env"])
        self.assertTrue(err)
        self.assertEqual(code, EXIT_USAGE)

    def test_example_relative_to_repo_dir(self):
        self.write("docs/app.env.example", "FROMDOCS=\n")
        self.write(".env.example", "OTHER=\n")
        self.write("app.py", 'import os\nos.getenv("FROMDOCS")\n')
        fx.write(self.tmp, "docs/app.env.example", "CWDNAME=\n")
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            code, out, err = run([self.repo, "--example", "docs/app.env.example",
                                  "--format", "json"])
        finally:
            os.chdir(cwd)
        self.assertTrue(out, err)
        data = json.loads(out)
        self.assertEqual(data["example_files"], ["docs/app.env.example"])
        self.assertEqual(data["undocumented"], [])
        self.assertEqual(data["unused"], [])
        self.assertNotIn("CWDNAME", out)
        self.assertEqual(code, EXIT_OK)


class TestBootstrap(unittest.TestCase):
    def test_console_script_declared(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertTrue(text)
        self.assertIn('envcontract = "envcontract.cli:main"', text)
        packages = [line for line in text.splitlines() if line.startswith("packages = [")]
        self.assertEqual(len(packages), 1)
        self.assertIn('"envcontract"', packages[0])

    def test_help_lists_exit_codes(self):
        code, out, _ = run(["--help"])
        self.assertTrue(out)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("exit codes:", out)

    def test_sources_are_ascii(self):
        files = sorted((ROOT / "envcontract").glob("*.py"))
        files += sorted((ROOT / "tests").glob("test_envcontract*.py"))
        files += [ROOT / "tests" / "envcontract_fixtures.py"]
        self.assertGreater(len(files), 5)
        for path in files:
            data = path.read_bytes()
            self.assertTrue(data, path)
            self.assertTrue(data.isascii(), f"{path} is not pure ASCII")


if __name__ == "__main__":
    unittest.main()
