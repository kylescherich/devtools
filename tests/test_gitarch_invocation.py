"""How gitarch invokes git (BRIEF-DEVWS-023 / DEC-202 task 2): environment,
literal pathspecs, resolving git by absolute path, the single --version
exception, the git version header and "path not in history"."""
import contextlib
import inspect
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gitarch.cli import EXIT_GIT, EXIT_OK, EXIT_USAGE
from gitarch.gitcmd import ALLOWED, GLOBAL_FLAGS, CommandRefused, Git
from tests import gitarch_fixtures as fx
from tests.test_gitarch_gitcmd import run_cli, spy

REAL_GIT = shutil.which("git")
REAL_GIT_DIR = os.path.dirname(REAL_GIT)
NEW_ENV = {"GIT_NO_LAZY_FETCH": "1", "GIT_LITERAL_PATHSPECS": "1", "GIT_ATTR_NOSYSTEM": "1"}
MODES = ([], ["--path", "src/c.py"], ["--path", "src/c.py", "--messages", "--diffs"],
         ["--authors", "--messages"], ["--path", "src"], ["--format", "html"])


class InvocationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.repo = fx.basic_repo(self.dir)

    def tearDown(self):
        self._tmp.cleanup()

    def fake_git(self, directory):
        """An executable named git in directory that writes a marker."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        return fx.trap_script(directory, name="git")

    def cli(self, argv, *, cwd=None, path=None):
        with contextlib.ExitStack() as stack:
            if path is not None:
                stack.enter_context(mock.patch.dict(os.environ, {"PATH": path}))
            if cwd is not None:
                stack.enter_context(contextlib.chdir(cwd))
            calls = stack.enter_context(spy())
            code, out, err = run_cli(argv)
        return code, out, err, calls

    def assert_real_git_used(self, argv, *, cwd, path, marker):
        code, out, err, calls = self.cli(argv, cwd=cwd, path=path)
        self.assertEqual(code, EXIT_OK, err)
        self.assertTrue(out.strip())
        self.assertTrue(calls)
        for call, _ in calls:
            self.assertEqual(os.path.realpath(call[0]), os.path.realpath(REAL_GIT))
            self.assertTrue(os.path.isabs(call[0]))
        self.assertFalse(marker.exists(), "the fake git ran")

    def assert_git_refused(self, argv, *, cwd, path, marker, reason):
        code, out, err, calls = self.cli(argv, cwd=cwd, path=path)
        self.assertTrue(err.strip())
        self.assertEqual(code, EXIT_GIT, err)
        self.assertIn(reason, err)
        self.assertEqual(out, "")
        self.assertEqual(calls, [], "a process was started")
        self.assertFalse(marker.exists(), "the fake git ran")


class TestEnvironmentAndFlags(InvocationTestCase):
    def test_env_has_no_lazy_fetch_literal_pathspecs_attr_nosystem(self):
        hostile = {key: "0" for key in NEW_ENV}
        hostile["PATH"] = ".:relative/bin:" + os.environ.get("PATH", "")
        for mode in MODES:
            with self.subTest(mode=mode):
                with mock.patch.dict(os.environ, hostile), spy() as calls:
                    code, out, err = run_cli([self.repo, *mode])
                self.assertEqual(code, EXIT_OK, err)
                self.assertTrue(out.strip())
                self.assertTrue(calls)
                for argv, kwargs in calls:
                    env = kwargs["env"]
                    for key, value in NEW_ENV.items():
                        self.assertEqual(env[key], value, key)
                    entries = env["PATH"].split(os.pathsep)
                    self.assertTrue(entries)
                    self.assertTrue(all(os.path.isabs(e) for e in entries), entries)
                    if argv[1:] == ["--version"]:
                        continue
                    self.assertEqual(argv[1:1 + len(GLOBAL_FLAGS)], list(GLOBAL_FLAGS))
                    self.assertIn("--literal-pathspecs", argv)
                    pairs = list(zip(argv, argv[1:]))
                    self.assertIn(("-c", "core.attributesFile=/dev/null"), pairs)

    def test_executable_is_keyword_only_and_not_a_cli_option(self):
        parameter = inspect.signature(Git).parameters["executable"]
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)
        code, _, err = run_cli([self.repo, "--executable", "/bin/true"])[:3]
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)


class TestLiteralPathspecs(InvocationTestCase):
    def test_literal_pathspec_attr_magic(self):
        fx.write(self.repo, ".gitattributes", "x.py foo\n")
        fx.write(self.repo, "x.py", "x = 1\n")
        fx.commit(self.repo, "add x", "2026-01-09T10:00:00Z")
        magic = ":(attr:foo)x.py"
        # Without literal pathspecs, plain git would match x.py.
        self.assertTrue(fx.git(self.repo, "log", "--format=%H", "--", magic).strip())
        code, out, err, _ = self.cli([self.repo, "--path", magic])
        self.assertTrue(err.strip())
        self.assertEqual(code, EXIT_GIT, err)
        self.assertIn("path not in history", err)
        self.assertEqual(out, "")

    def test_literal_pathspec_exclude_magic(self):
        fx.write(self.repo, "x", "x\n")
        fx.commit(self.repo, "add x", "2026-01-09T10:00:00Z")
        magic = ":!x"
        self.assertTrue(fx.git(self.repo, "log", "--format=%H", "--", magic).strip())
        for extra in ([], ["--diffs"], ["--messages"]):
            code, out, err, _ = self.cli([self.repo, "--path", magic, *extra])
            self.assertTrue(err.strip())
            self.assertEqual(code, EXIT_GIT, err)
            self.assertIn("path not in history", err)
            self.assertEqual(out, "")

    def test_path_not_in_history_exit_3(self):
        for path in ("no/such/file.py", "missing-dir/", "*.py"):
            with self.subTest(path=path):
                code, out, err, _ = self.cli([self.repo, "--path", path])
                self.assertTrue(err.strip())
                self.assertEqual(code, EXIT_GIT, err)
                self.assertIn("path not in history", err)
                self.assertEqual(out, "")
        # A real path still works.
        code, out, _, _ = self.cli([self.repo, "--path", "src/c.py"])
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(out.strip())


class TestPathTraps(InvocationTestCase):
    def test_path_trap_fake_git_in_repo_root(self):
        _, marker = self.fake_git(self.repo)
        self.assert_real_git_used([self.repo], cwd=self.repo, marker=marker,
                                  path=os.pathsep.join([".", REAL_GIT_DIR]))

    def test_path_trap_fake_git_in_cwd(self):
        work = self.dir / "work"
        _, marker = self.fake_git(work)
        self.assert_real_git_used([self.repo], cwd=work, marker=marker,
                                  path=os.pathsep.join([".", "", REAL_GIT_DIR]))

    def test_path_trap_dot_first_in_path(self):
        work = self.dir / "work"
        _, marker = self.fake_git(work)
        for path in (os.pathsep.join([".", REAL_GIT_DIR]),
                     os.pathsep.join(["", REAL_GIT_DIR]),
                     os.pathsep.join(["./", "..", REAL_GIT_DIR])):
            with self.subTest(path=path):
                self.assert_real_git_used([self.repo, "--path", "src/c.py", "--diffs"],
                                          cwd=work, path=path, marker=marker)

    def test_path_trap_relative_entry(self):
        work = self.dir / "work"
        _, marker = self.fake_git(work / "bin")
        self.assert_real_git_used([self.repo], cwd=work, marker=marker,
                                  path=os.pathsep.join(["bin", "work/bin", REAL_GIT_DIR]))

    def test_refuse_git_inside_repo_on_path(self):
        for where in (self.repo, self.repo / "tools"):
            with self.subTest(where=where):
                _, marker = self.fake_git(where)
                self.assert_git_refused([self.repo], cwd=self.dir, marker=marker,
                                        path=os.pathsep.join([str(where), REAL_GIT_DIR]),
                                        reason="inside the repository")
                (Path(where) / "git").unlink()

    def test_refuse_git_in_cwd_on_path(self):
        work = self.dir / "work"
        _, marker = self.fake_git(work)
        self.assert_git_refused([self.repo], cwd=work, marker=marker,
                                path=os.pathsep.join([str(work), REAL_GIT_DIR]),
                                reason="current directory")

    def test_git_missing_from_sanitized_path_exit_3(self):
        code, out, err, calls = self.cli([self.repo], path=".:bin")
        self.assertTrue(err.strip())
        self.assertEqual(code, EXIT_GIT)
        self.assertIn("git not found", err)
        self.assertEqual(calls, [])


class TestVersion(InvocationTestCase):
    def test_header_shows_git_version(self):
        version = subprocess.run([REAL_GIT, "--version"], capture_output=True,
                                 check=True).stdout.decode().strip()
        self.assertTrue(version.startswith("git version "))
        number = version.removeprefix("git version ")
        self.assertEqual(Git(self.repo).version(), number)
        for fmt in ("text", "md", "html"):
            code, out, err, _ = self.cli([self.repo, "--format", fmt])
            self.assertEqual(code, EXIT_OK, err)
            self.assertTrue(out.strip())
            self.assertIn(number, out)
            if fmt == "text":
                self.assertIn(f"\ngit: {number}\n", out)

    def test_version_is_only_non_allow_listed_argv(self):
        seen_version = 0
        for mode in MODES:
            code, out, err, calls = self.cli([self.repo, *mode])
            self.assertEqual(code, EXIT_OK, err)
            self.assertTrue(out.strip())
            self.assertTrue(calls)
            for argv, _ in calls:
                self.assertTrue(os.path.isabs(argv[0]))
                if argv[1] == "--version":
                    self.assertEqual(argv, [argv[0], "--version"])
                    seen_version += 1
                    continue
                index = argv.index("-C") + 2
                self.assertIn(argv[index], ALLOWED, argv)
        self.assertEqual(seen_version, len(MODES))
        with self.assertRaises(CommandRefused):
            Git(self.repo).run("--version")
