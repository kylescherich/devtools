"""The hardened git runner (BRIEF-DEVWS-022 task 2)."""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gitarch.gitcmd
from gitarch import history
from gitarch.cli import EXIT_GIT, EXIT_USAGE, main
from gitarch.gitcmd import (ALLOWED, CONFIG, GIT_ENV, CommandRefused, Git, GitError,
                            UsageError, rev_args)
from tests import gitarch_fixtures as fx

REAL_RUN = subprocess.run


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def spy():
    """Record every subprocess.run call gitarch makes, still running it."""
    calls = []

    def recorder(argv, *args, **kwargs):
        calls.append((list(argv), kwargs))
        return REAL_RUN(argv, *args, **kwargs)
    with mock.patch.object(gitarch.gitcmd.subprocess, "run", side_effect=recorder):
        yield calls


def prefix(repo):
    argv = ["git", "--no-pager"]
    for setting in CONFIG:
        argv += ["-c", setting]
    return argv + ["-C", str(repo)]


class GitTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestRunner(GitTestCase):
    def test_env_cleared(self):
        repo = fx.basic_repo(self.dir)
        hostile = {"GIT_DIR": str(self.dir / "nowhere"), "GIT_EXEC_PATH": str(self.dir / "x"),
                   "GIT_CONFIG_PARAMETERS": "'core.pager=evil'", "GIT_WORK_TREE": "/"}
        with mock.patch.dict(os.environ, hostile), spy() as calls:
            sha = Git(repo).resolve("HEAD")
        self.assertEqual(sha, fx.head(repo))
        self.assertTrue(calls)
        for _, kwargs in calls:
            env = kwargs["env"]
            self.assertTrue(env)
            for key in hostile:
                self.assertNotIn(key, env)
            self.assertEqual([k for k in env if k.startswith("GIT_")
                              and k not in GIT_ENV], [])
            for key, value in GIT_ENV.items():
                self.assertEqual(env[key], value)
            self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
            self.assertEqual(env["LC_ALL"], "C")
            self.assertIs(kwargs["shell"], False)
            self.assertIn("timeout", kwargs)

    def test_mandatory_flags_every_call(self):
        repo = fx.basic_repo(self.dir)
        git = Git(repo)
        with spy() as calls:
            git.resolve("HEAD")
            git.run("log", "--numstat", "--format=tformat:%H", *rev_args("HEAD"))
            git.run("show", "--stat", *rev_args("HEAD"))
            git.run("diff-tree", "-r", "--root", *rev_args("HEAD"))
            git.run("ls-files")
            git.run("cat-file", "-t", "HEAD:a.py")
        self.assertGreaterEqual(len(calls), 7)
        seen = set()
        for argv, kwargs in calls:
            head = prefix(repo)
            self.assertEqual(argv[:len(head)], head, argv)
            sub = argv[len(head)]
            seen.add(sub)
            self.assertIn(sub, ALLOWED)
            if sub in ("log", "show", "diff-tree"):
                self.assertEqual(argv[len(head) + 1:len(head) + 3],
                                 ["--no-ext-diff", "--no-textconv"], argv)
            self.assertFalse(any("%G" in a for a in argv))
            self.assertIs(kwargs["shell"], False)
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(seen, set(ALLOWED))

    def test_subcommand_allow_list(self):
        repo = fx.basic_repo(self.dir)
        git = Git(repo)
        with spy() as calls:
            for sub in ("status", "checkout", "fetch", "pull", "gc", "blame", "config",
                        "-c", "--exec-path", "log; rm -rf /"):
                with self.assertRaises(CommandRefused, msg=sub):
                    git.run(sub)
            with self.assertRaises(CommandRefused):
                git.run("log", "--format=%G?")
        self.assertEqual(calls, [])

    def test_output_cap_exit_3(self):
        repo = fx.basic_repo(self.dir)
        with self.assertRaisesRegex(GitError, "git output too large"):
            Git(repo, max_output=40).run("log", "-p", *rev_args("HEAD"))
        self.assertTrue(Git(repo, max_output=10_000).run("log", "-p", *rev_args("HEAD")))


class TestCliExit3(GitTestCase):
    def test_timeout_exit_3(self):
        repo = fx.basic_repo(self.dir)
        seen = []

        def slow(argv, *args, **kwargs):
            seen.append(kwargs.get("timeout"))
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        with mock.patch.object(gitarch.gitcmd.subprocess, "run", side_effect=slow):
            code, out, err = run_cli([repo, "--timeout", "7"])
        self.assertEqual(code, EXIT_GIT)
        self.assertTrue(err)
        self.assertIn("timed out after 7 s", err)
        self.assertEqual(seen, [7])
        self.assertEqual(out, "")

    def test_not_a_repo_exit_3(self):
        plain = self.dir / "plain"
        plain.mkdir()
        for target in (plain, self.dir / "missing"):
            code, _, err = run_cli([target])
            self.assertEqual(code, EXIT_GIT, target)
            self.assertTrue(err)
            self.assertIn("not a git repository", err)

    def test_git_missing_exit_3(self):
        repo = fx.basic_repo(self.dir)
        with mock.patch.dict(os.environ, {"PATH": ""}):
            code, _, err = run_cli([repo])
        self.assertEqual(code, EXIT_GIT)
        self.assertTrue(err)
        self.assertIn("git not found", err)

    def test_empty_repo_exit_3(self):
        repo = fx.init_repo(self.dir, "empty")
        code, _, err = run_cli([repo])
        self.assertEqual(code, EXIT_GIT)
        self.assertTrue(err)
        self.assertIn("repository has no commits", err)

    def test_unknown_rev_exit_3(self):
        repo = fx.basic_repo(self.dir)
        code, _, err = run_cli([repo, "--rev", "no-such-branch"])
        self.assertEqual(code, EXIT_GIT)
        self.assertTrue(err)
        self.assertIn("unknown revision: no-such-branch", err)

    def test_rev_option_injection_refused(self):
        repo = fx.basic_repo(self.dir)
        outside = self.dir / "written-by-git"
        for rev in (f"--output={outside}", "-p", "--all", "-"):
            with spy() as calls:
                code, out, err = run_cli([repo, f"--rev={rev}"])
            self.assertEqual(code, EXIT_USAGE, rev)
            self.assertTrue(err)
            self.assertIn("must not start with '-'", err)
            self.assertEqual(calls, [], rev)
            with self.assertRaises(UsageError):
                rev_args(rev)
        self.assertFalse(outside.exists())
        # A good rev reaches git only after --end-of-options.
        with spy() as calls:
            code, out, _ = run_cli([repo, "--rev", "main"])
        self.assertEqual(code, 0)
        self.assertTrue(out)
        verify = [argv for argv, _ in calls if "--verify" in argv]
        self.assertTrue(verify)
        for argv in verify:
            self.assertEqual(argv[argv.index("--end-of-options") + 1], "main^{commit}")


class TestHostileRepo(GitTestCase):
    def _gitarch_paths(self, repo):
        """Every git call shape gitarch uses, through the runner."""
        git = Git(repo)
        for rev in ("HEAD", "signed", "main"):
            sha = git.resolve(rev)
            self.assertTrue(sha)
            yield git.run("log", "-M", "-z", "--raw", "--numstat", "--reverse",
                          "--topo-order", "--format=tformat:%H%x1f%P%x1f%ct",
                          *rev_args(sha))
            yield git.run("log", "--follow", "-M", "-z", "--raw", "--numstat",
                          "--format=tformat:%H", *rev_args(sha), "--", "lib/helpers.py")
            yield git.run("log", "-p", "--format=tformat:%H %s%n%b", *rev_args(sha))
            yield git.run("show", "-p", "--format=fuller", *rev_args(sha))
            yield git.run("diff-tree", "-r", "-p", "--root", "-M", *rev_args(sha))
            yield git.run("cat-file", "-p", sha)
            yield git.run("cat-file", "-t", sha + ":app.py")
        yield git.run("ls-files", "-z")

    def test_hostile_repo_no_marker(self):
        repo, marker, script = fx.hostile_repo(self.dir)
        self.assertFalse(marker.exists())
        outputs = list(self._gitarch_paths(repo))
        self.assertTrue(outputs)
        self.assertTrue(all(isinstance(o, bytes) for o in outputs))
        self.assertTrue(any(b"gpgsig" in o for o in outputs))   # signed commit was read
        self.assertTrue(any(b"helpers.py" in o for o in outputs))
        code, out, _ = run_cli([repo, "--rev", "signed"])
        self.assertEqual(code, 0)
        self.assertTrue(out)
        # Every history model path: revisions, a followed file, a directory,
        # authors, a short recent window.
        git = Git(repo)
        for kwargs in ({}, {"rev": "signed"}, {"rev": "main~1"},
                       {"rev": "signed", "path": "lib/helpers.py"},
                       {"path": "app.py", "authors": True}, {"path": "lib"},
                       {"rev": "signed", "authors": True, "recent_days": 1}):
            model = history.build(git, **kwargs)
            self.assertTrue(model.overview.commits, kwargs)
            if kwargs.get("path") == "lib/helpers.py":
                self.assertTrue(model.followed)
                self.assertEqual([(r.old, r.new) for r in model.files[0].renames],
                                 [("lib/util.py", "lib/helpers.py")])
        self.assertEqual(history.build(git, rev="signed").overview.commits, 4)
        self.assertFalse(marker.exists(), "gitarch let git run an external program")

        # Every CLI mode in every format, on main and on the signed commit.
        reports = self.dir / "reports"
        reports.mkdir()
        modes = ([], ["--path", "lib/helpers.py"], ["--path", "lib/helpers.py", "--messages"],
                 ["--path", "lib/helpers.py", "--diffs"], ["--path", "app.py", "--diffs",
                                                            "--messages", "--authors"],
                 ["--path", "lib", "--diffs"], ["--messages"], ["--authors"])
        runs = 0
        for rev in ("main", "signed"):
            for fmt in ("text", "md", "html"):
                for mode in modes:
                    code, out, err = run_cli([repo, "--rev", rev, "--format", fmt, *mode])
                    self.assertEqual(code, 0, (rev, fmt, mode, err))
                    self.assertTrue(out.strip())
                    if "--diffs" in mode and "lib/helpers.py" in mode:
                        self.assertIn("rename from lib/util.py", out)    # followed
                    runs += 1
            target = reports / f"{rev}.html"
            code, _, err = run_cli([repo, "--rev", rev, "--format", "html", "--path",
                                    "app.py", "--diffs", "--output", target])
            self.assertEqual(code, 0, err)
            self.assertTrue(target.read_text(encoding="utf-8"))
        self.assertEqual(runs, 2 * 3 * len(modes))
        self.assertFalse(marker.exists(), "gitarch CLI let git run an external program")

        # The trap is live: run directly, it writes the marker ...
        subprocess.run([str(script)], input=b"", check=True)
        self.assertTrue(marker.exists())
        marker.unlink()
        # ... and plain git on this repo does run it (textconv on log -p).
        fx.git(repo, "log", "-p", "-1", "main")
        self.assertTrue(marker.exists(), "hostile config not live for plain git")
