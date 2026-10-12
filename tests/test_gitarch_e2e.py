"""gitarch end to end: exit codes, reproducibility, --output guards and a
read-only .git (BRIEF-DEVWS-022 task 5)."""
import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gitarch.cli import EXIT_GIT, EXIT_OK, EXIT_USAGE, main
from gitarch.report import FORMATS
from tests import gitarch_fixtures as fx

MODES = ([], ["--path", "src/c.py"], ["--path", "src/c.py", "--messages"],
         ["--path", "src/c.py", "--diffs"], ["--authors"])


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def snapshot(repo):
    """sha256 of every file under repo/.git (nothing excluded), plus the
    work tree listing with each file's sha256; directories and symlinks
    are listed too."""
    state = {}
    for root, dirs, files in os.walk(repo, followlinks=False):
        rel = os.path.relpath(root, repo)
        state[("dir", rel)] = sorted(dirs)
        for name in files:
            path = os.path.join(root, name)
            key = os.path.relpath(path, repo)
            if os.path.islink(path):
                state[("link", key)] = os.readlink(path)
            else:
                with open(path, "rb") as f:
                    state[("file", key)] = hashlib.sha256(f.read()).hexdigest()
    return state


class E2ETestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.repo = fx.basic_repo(self.dir)
        fx.write(self.repo, "src/c.py", "x = 2\ny = 3\n")
        fx.commit(self.repo, "edit c", "2026-01-08T10:00:00Z")
        self.out_dir = self.dir / "reports"
        self.out_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()


class TestExitCodes(E2ETestCase):
    def test_exit_codes_0_2_3(self):
        empty = fx.init_repo(self.dir, "empty")
        plain = self.dir / "plain"
        plain.mkdir()
        existing = self.out_dir / "exists.txt"
        existing.write_text("keep\n", encoding="ascii")
        cases = [
            ([self.repo], EXIT_OK),
            ([self.repo, "--path", "src/c.py", "--diffs", "--messages"], EXIT_OK),
            ([self.repo, "--diffs"], EXIT_USAGE),                  # --diffs without --path
            ([self.repo, "--rev", "-x"], EXIT_USAGE),
            ([self.repo, "--rev=--all"], EXIT_USAGE),
            ([self.repo, "--bogus"], EXIT_USAGE),
            ([self.repo, "--format", "pdf"], EXIT_USAGE),
            ([self.repo, "--recent", "0"], EXIT_USAGE),
            ([self.repo, "--force"], EXIT_USAGE),                  # --force without --output
            ([self.repo, "--output", existing], EXIT_USAGE),
            ([self.repo, "--rev", "no-such-rev"], EXIT_GIT),
            ([plain], EXIT_GIT),
            ([self.dir / "missing"], EXIT_GIT),
            ([empty], EXIT_GIT),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv[1:]):
                code, out, err = run(argv)
                self.assertEqual(code, expected, err)
                if expected == EXIT_OK:
                    self.assertTrue(out.strip())
                else:
                    self.assertTrue(err.strip())
                    self.assertIn("gitarch:", err)
                    self.assertEqual(out, "")
        self.assertEqual(existing.read_text(encoding="ascii"), "keep\n")

    def test_help_has_exit_code_table(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(out)
        for line in ("0     ok", "2     usage error", "3     git"):
            self.assertIn(line, out)


class TestReproducible(E2ETestCase):
    def test_reproducible_byte_identical(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for fmt in FORMATS:
            for mode in MODES:
                with self.subTest(fmt=fmt, mode=mode):
                    first = run([self.repo, "--format", fmt, *mode])
                    second = run([self.repo, "--format", fmt, *mode])
                    self.assertEqual(first[0], EXIT_OK)
                    self.assertTrue(first[1].strip())
                    self.assertEqual(first, second)
                    self.assertNotIn(today, first[1])
        # Through --output too: the file equals stdout.
        target = self.out_dir / "r.html"
        code, out, _ = run([self.repo, "--format", "html", "--output", target])
        self.assertEqual((code, out), (EXIT_OK, ""))
        content = target.read_text(encoding="utf-8")
        self.assertTrue(content)
        self.assertEqual(content, run([self.repo, "--format", "html"])[1])


class TestOutputGuards(E2ETestCase):
    def test_output_guards(self):
        before = snapshot(self.repo)
        target = self.out_dir / "report.md"
        code, _, err = run([self.repo, "--format", "md", "--output", target])
        self.assertEqual(code, EXIT_OK, err)
        self.assertTrue(target.read_text(encoding="utf-8").startswith("# gitarch report"))

        # Exists: refused without --force, replaced with it.
        target.write_text("old\n", encoding="ascii")
        code, _, err = run([self.repo, "--output", target])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)
        self.assertIn("already exists", err)
        self.assertEqual(target.read_text(encoding="ascii"), "old\n")
        code, _, _ = run([self.repo, "--output", target, "--force"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("gitarch report", target.read_text(encoding="utf-8"))

        # A symlink is refused, with or without --force; its target is untouched.
        victim = self.out_dir / "victim.txt"
        victim.write_text("victim\n", encoding="ascii")
        link = self.out_dir / "link.txt"
        os.symlink(victim, link)
        dangling = self.out_dir / "dangling.txt"
        os.symlink(self.out_dir / "nowhere.txt", dangling)
        for path in (link, dangling):
            for extra in ([], ["--force"]):
                code, _, err = run([self.repo, "--output", path, *extra])
                self.assertEqual(code, EXIT_USAGE, (path, extra))
                self.assertTrue(err)
                self.assertIn("symlink", err)
        self.assertEqual(victim.read_text(encoding="ascii"), "victim\n")
        self.assertFalse((self.out_dir / "nowhere.txt").exists())

        # A directory is not a regular file.
        code, _, err = run([self.repo, "--output", self.out_dir, "--force"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)

        # Inside the analysed repository: work tree, subdirectory, .git, and
        # through a symlinked directory pointing into it; also when REPO is
        # given as a subdirectory.
        into = self.dir / "into-repo"
        os.symlink(self.repo / "src", into)
        for argv in ([self.repo, "--output", self.repo / "report.txt"],
                     [self.repo, "--output", self.repo / "src" / "report.txt"],
                     [self.repo, "--output", self.repo / ".git" / "report.txt"],
                     [self.repo, "--output", self.repo / ".git" / "hooks" / "post-checkout"],
                     [self.repo, "--output", into / "report.txt"],
                     [self.repo / "src", "--output", self.repo / "report.txt"]):
            with self.subTest(output=argv[-1]):
                code, out, err = run(argv)
                self.assertEqual(code, EXIT_USAGE)
                self.assertTrue(err)
                self.assertIn("inside the analysed repository", err)
                self.assertEqual(out, "")
        self.assertEqual(snapshot(self.repo), before)


class TestReadOnly(E2ETestCase):
    def test_git_dir_read_only(self):
        before = snapshot(self.repo)
        self.assertTrue(any(kind == "file" and key.startswith(".git" + os.sep)
                            for kind, key in before))
        self.assertIn(("file", os.path.join("src", "c.py")), before)
        runs = 0
        for fmt in FORMATS:
            for mode in MODES:
                with self.subTest(fmt=fmt, mode=mode):
                    code, out, err = run([self.repo, "--format", fmt, *mode])
                    self.assertEqual(code, EXIT_OK, err)
                    self.assertTrue(out.strip())
                    self.assertEqual(snapshot(self.repo), before)
                    runs += 1
        target = self.out_dir / "all.txt"
        code, _, _ = run([self.repo, "--path", "src/c.py", "--messages", "--diffs",
                          "--authors", "--output", target])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(snapshot(self.repo), before)
        self.assertEqual(runs, len(FORMATS) * len(MODES))
