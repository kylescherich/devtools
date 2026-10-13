"""Repository boundary preflight (BRIEF-DEVWS-023 / DEC-202 task 1).

Every refusal test goes through the CLI and asserts exit 3, a non-empty
"refused" message, and (with a spy on subprocess.Popen, which subprocess.run
also uses) that no process at all was started.
"""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gitarch.gitcmd
from gitarch.cli import EXIT_GIT, EXIT_OK, EXIT_USAGE, main
from gitarch.preflight import Refused, find_repo, read_config
from tests import gitarch_fixtures as fx
from tests.test_gitarch_e2e import snapshot

REAL_POPEN = subprocess.Popen


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def popen_spy():
    started = []

    def recorder(*args, **kwargs):
        started.append(args[0] if args else kwargs.get("args"))
        return REAL_POPEN(*args, **kwargs)
    with mock.patch.object(gitarch.gitcmd.subprocess, "Popen", side_effect=recorder):
        yield started


class PreflightTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def repo(self, name="repo"):
        return fx.basic_repo(self.dir, name)

    def assert_refused(self, target, *extra, reason=None):
        with popen_spy() as started:
            code, out, err = run([target, *extra])
        self.assertTrue(err.strip(), "no message")
        self.assertEqual(code, EXIT_GIT, err)
        self.assertIn("refused", err)
        if reason:
            self.assertIn(reason, err)
        self.assertEqual(out, "")
        self.assertEqual(started, [], "a process was started before the refusal")
        return err

    def assert_works(self, target, *extra):
        code, out, err = run([target, *extra])
        self.assertEqual(code, EXIT_OK, err)
        self.assertTrue(out.strip())
        return out


class TestPartialClone(PreflightTestCase):
    def test_refuse_partial_clone_no_git_started(self):
        """The trap is proven live first: plain git (outside gitarch) on this
        repository lazy-fetches the missing blob through the ext:: promisor
        remote and the helper writes the marker. gitarch refuses the
        repository before starting any process, in every mode."""
        repo, marker, path = fx.partial_clone_repo(self.dir)
        for mode in ([], ["--path", path, "--diffs"], ["--messages", "--authors"],
                     ["--format", "html"]):
            self.assert_refused(repo, *mode, reason="partial")
        self.assertFalse(marker.exists(), "gitarch reached the promisor remote")
        fx.git(repo, "cat-file", "-p", f"HEAD:{path}", check=False)
        self.assertTrue(marker.exists(), "trap not live: plain git did not lazy-fetch")

    def test_refuse_promisor_pack(self):
        repo = self.repo()
        pack = repo / ".git" / "objects" / "pack"
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "pack-1111111111111111111111111111111111111111.promisor").write_bytes(b"")
        self.assert_refused(repo, reason="promisor")

    def test_refuse_remote_partialclonefilter(self):
        repo = self.repo()
        fx.append_config(repo, '[remote "origin"]\n\tpartialclonefilter = blob:none\n')
        self.assert_refused(repo, reason="partialclonefilter")

    def test_refuse_remote_promisor(self):
        repo = self.repo()
        fx.append_config(repo, '[remote "origin"]\n\tpromisor = true\n')
        self.assert_refused(repo, reason="promisor")


class TestGitdirPointers(PreflightTestCase):
    def test_refuse_gitdir_file_outside(self):
        real = self.repo("real")
        work = self.dir / "work"
        work.mkdir()
        (work / ".git").write_text(f"gitdir: {real / '.git'}\n", encoding="ascii")
        self.assert_refused(work, reason="gitdir pointer")
        self.assert_refused(work, "--allow-linked-worktree", reason="not a linked worktree")

    def test_refuse_linked_worktree_without_flag(self):
        repo = self.repo()
        wt = fx.linked_worktree(repo, self.dir)
        self.assert_refused(wt, reason="--allow-linked-worktree")

    def test_allow_linked_worktree_with_flag(self):
        repo = self.repo()
        wt = fx.linked_worktree(repo, self.dir)
        fx.write(wt, "wt_only.py", "w = 1\n")
        fx.commit(wt, "worktree commit", "2026-01-09T10:00:00Z")
        before = (snapshot(repo / ".git"), snapshot(wt))
        scope = find_repo(wt, allow_linked=True)
        self.assertEqual(scope.common_dir, os.path.realpath(repo / ".git"))
        self.assertTrue(scope.git_dir.startswith(os.path.realpath(repo / ".git" / "worktrees")))
        for mode in ([], ["--path", "wt_only.py", "--diffs", "--messages"], ["--authors"]):
            out = self.assert_works(wt, "--allow-linked-worktree", *mode)
            self.assertIn("wt_only.py", out)
        self.assertEqual((snapshot(repo / ".git"), snapshot(wt)), before)
        # The common dir and the main work tree holding it count as inside
        # the repository for --output (Scope.output_roots), though only the
        # common dir is in the reading scope.
        self.assertNotIn(os.path.realpath(repo), scope.roots)
        self.assertIn(os.path.realpath(repo), scope.output_roots)
        for target in (repo / ".git" / "r.txt", repo / ".git" / "worktrees" / "r.txt",
                       repo / "r.txt", wt / "r.txt"):
            code, _, err = run([wt, "--allow-linked-worktree", "--output", target])
            self.assertEqual(code, EXIT_USAGE, target)
            self.assertIn("inside the analysed repository", err)
        self.assertEqual((snapshot(repo / ".git"), snapshot(wt)), before)

    def test_refuse_linked_worktree_include_in_common_config(self):
        repo = self.repo()
        wt = fx.linked_worktree(repo, self.dir)
        fx.append_config(repo, "[include]\n\tpath = /tmp/elsewhere\n")
        self.assert_refused(wt, "--allow-linked-worktree", reason="[include]")

    def test_refuse_linked_worktree_config_worktree_include(self):
        repo = self.repo()
        wt = fx.linked_worktree(repo, self.dir)
        fx.append_config(repo, "[extensions]\n\tworktreeConfig = true\n")
        admin = Path(find_repo(wt, allow_linked=True).git_dir)
        (admin / "config.worktree").write_text("[include]\n\tpath = x\n", encoding="ascii")
        self.assert_refused(wt, "--allow-linked-worktree", reason="config.worktree")

    def test_refuse_linked_worktree_gitdir_not_pointing_back(self):
        repo = self.repo()
        wt = fx.linked_worktree(repo, self.dir)
        admin = Path(find_repo(wt, allow_linked=True).git_dir)
        (admin / "gitdir").write_text(str(self.dir / "other" / ".git") + "\n",
                                      encoding="ascii")
        self.assert_refused(wt, "--allow-linked-worktree", reason="point back")

    def test_refuse_commondir_in_plain_repo(self):
        repo = self.repo()
        (repo / ".git" / "commondir").write_text("../../elsewhere\n", encoding="ascii")
        self.assert_refused(repo, reason="commondir")


class TestObjectsElsewhere(PreflightTestCase):
    def test_refuse_alternates_absolute(self):
        other = self.repo("other")
        repo = self.repo()
        (repo / ".git" / "objects" / "info" / "alternates").write_text(
            f"{other / '.git' / 'objects'}\n", encoding="ascii")
        self.assert_refused(repo, reason="alternates")

    def test_refuse_alternates_relative(self):
        self.repo("other")
        repo = self.repo()
        (repo / ".git" / "objects" / "info" / "alternates").write_text(
            "../../../other/.git/objects\n", encoding="ascii")
        self.assert_refused(repo, reason="alternates")

    def test_refuse_http_alternates(self):
        repo = self.repo()
        (repo / ".git" / "objects" / "info" / "http-alternates").write_text(
            "http://example.test/objects\n", encoding="ascii")
        self.assert_refused(repo, reason="http-alternates")

    def test_empty_alternates_file_is_fine(self):
        repo = self.repo()
        (repo / ".git" / "objects" / "info" / "alternates").write_text("\n", encoding="ascii")
        self.assert_works(repo)


class TestConfigRefusals(PreflightTestCase):
    def check(self, text, reason):
        repo = self.repo()
        fx.append_config(repo, text)
        self.assert_refused(repo, reason=reason)

    def test_refuse_include_path(self):
        self.check("[include]\n\tpath = /tmp/other.gitconfig\n", "[include]")

    def test_refuse_include_empty_section(self):
        self.check("[include]\n", "[include]")

    def test_refuse_includeif_gitdir(self):
        self.check('[includeIf "gitdir:/tmp/"]\n\tpath = /tmp/other.gitconfig\n',
                   "[includeif]")

    def test_refuse_includeif_any_key(self):
        self.check('[IncludeIf "onbranch:main"]\n\tharmless = 1\n', "[includeif]")

    def test_refuse_core_attributesfile(self):
        self.check("[core]\n\tattributesFile = /tmp/attrs\n", "core.attributesfile")

    def test_refuse_core_worktree(self):
        self.check("[core]\n\tworktree = /tmp\n", "core.worktree")

    def test_refuse_extensions_partialclone(self):
        self.check("[extensions]\n\tpartialClone = origin\n", "extensions.partialclone")

    def test_refuse_mailmap_file(self):
        self.check("[mailmap]\n\tfile = /tmp/mailmap\n", "mailmap.file")

    def test_refuse_mailmap_blob(self):
        self.check("[mailmap]\n\tblob = HEAD:.mailmap\n", "mailmap.blob")

    def test_refuse_worktree_config_include(self):
        repo = self.repo()
        fx.append_config(repo, "[extensions]\n\tworktreeConfig = true\n")
        fx.append_config(repo, "[include]\n\tpath = x\n", name="config.worktree")
        self.assert_refused(repo, reason="config.worktree")

    def test_refuse_unreadable_config(self):
        repo = self.repo()
        config = repo / ".git" / "config"
        config.write_bytes(b"[core]\n\tname = \xff\xfe\n")
        self.assert_refused(repo, reason="not UTF-8")
        config.unlink()
        config.mkdir()
        self.assert_refused(repo, reason="not a regular file")
        config.rmdir()
        self.assert_refused(repo, reason="missing")

    def test_refuse_ambiguous_config(self):
        cases = {
            "[core.sub]\n\tx = 1\n": "deprecated",
            "x = 1\n[core]\n": "outside any section",
            "[core]\n\tx = \"open\n": "unterminated quote",
            "[core]\n\tx = a\\qb\n": "unsupported escape",
            "[core] x = 1\n": "section header",
            "[core\n\tx = 1\n": "section header",
            "[core]\n\t= 1\n": "unreadable line",
            "[core]\n\tx = 1 \\": "continuation at end of file",
            "[core]\n\tx = 1\0\n": "control characters",
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                repo = self.repo(f"r{abs(hash(text)) % 10**8}")
                (repo / ".git" / "config").write_text(text, encoding="ascii")
                self.assert_refused(repo, reason=reason)


class TestSymlinks(PreflightTestCase):
    def test_refuse_symlinked_repo_path(self):
        repo = self.repo()
        link = self.dir / "link-to-repo"
        os.symlink(repo, link)
        self.assert_refused(link, reason="symlink")

    def test_refuse_symlinked_dot_git(self):
        real = self.repo("real")
        work = self.dir / "work"
        work.mkdir()
        os.symlink(real / ".git", work / ".git")
        self.assert_refused(work, reason=".git is a symlink")

    def test_refuse_symlinked_objects(self):
        repo = self.repo()
        objects = repo / ".git" / "objects"
        moved = self.dir / "objects-elsewhere"
        objects.rename(moved)
        os.symlink(moved, objects)
        self.assert_refused(repo, reason="objects is a symlink")

    def test_refuse_symlinked_head(self):
        repo = self.repo()
        head = repo / ".git" / "HEAD"
        moved = self.dir / "HEAD-elsewhere"
        head.rename(moved)
        os.symlink(moved, head)
        self.assert_refused(repo, reason="HEAD is a symlink")

    def test_refuse_symlinked_objects_pack(self):
        repo = self.repo()
        pack = repo / ".git" / "objects" / "pack"
        moved = self.dir / "pack-elsewhere"
        pack.rename(moved)
        os.symlink(moved, pack)
        self.assert_refused(repo, reason="pack is a symlink")


class TestRepoShape(PreflightTestCase):
    def test_refuse_bare_repo(self):
        bare = self.dir / "bare.git"
        fx.git(self.dir, "init", "-q", "--bare", str(bare))
        self.assert_refused(bare, reason="bare")

    def test_refuse_not_a_repo(self):
        plain = self.dir / "plain"
        plain.mkdir()
        self.assert_refused(plain, reason="not a git repository")

    def test_subdirectory_finds_work_tree_root(self):
        repo = self.repo()
        scope = find_repo(repo / "src")
        self.assertEqual(scope.work_tree, os.path.realpath(repo))
        self.assertEqual(scope.git_dir, os.path.realpath(repo / ".git"))
        self.assertIsNone(scope.common_dir)
        self.assert_works(repo / "src")


class TestConfigReader(PreflightTestCase):
    def test_config_reader_valid_forms(self):
        text = ('# leading comment\n'
                '; another\n'
                '[Core]\n'
                '\trepositoryformatversion = 0\n'
                '\tBare=false   ; inline comment\n'
                '\tlogallrefupdates\n'
                '\tfilemode # bare boolean with a comment\n'
                '\tquoted = "a \\"b\\" \\\\ c\\n\\t"   # trailing comment\n'
                '\tmixed = x "y z" w\n'
                '\tlong = one \\\n'
                '  two\n'
                '[remote "Origin"]\n'
                '\turl = git@host.example.test:org/repo.git\n'
                '\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
                '[branch "feature/x"]\n'
                '\tremote = origin\n'
                '[section "with \\"quote\\""]\n'
                '\tk = "#not a comment;"\n')
        path = self.dir / "config"
        path.write_text(text, encoding="ascii")
        entries = [e for e in read_config(path) if e[2] is not None]
        self.assertTrue(entries)
        self.assertEqual(entries, [
            ("core", None, "repositoryformatversion", "0"),
            ("core", None, "bare", "false"),
            ("core", None, "logallrefupdates", "true"),
            ("core", None, "filemode", "true"),
            ("core", None, "quoted", 'a "b" \\ c\n\t'),
            ("core", None, "mixed", "x y z w"),
            ("core", None, "long", "one   two"),
            ("remote", "Origin", "url", "git@host.example.test:org/repo.git"),
            ("remote", "Origin", "fetch", "+refs/heads/*:refs/remotes/origin/*"),
            ("branch", "feature/x", "remote", "origin"),
            ("section", 'with "quote"', "k", "#not a comment;"),
        ])

    def test_preflight_real_style_config(self):
        """A config as git itself writes it: init, remote add, core.* and
        branch settings, user name with quotes."""
        repo = self.repo()
        for args in (["remote", "add", "origin", "git@host.example.test:org/repo.git"],
                     ["config", "core.autocrlf", "input"],
                     ["config", "core.ignorecase", "false"],
                     ["config", "branch.main.remote", "origin"],
                     ["config", "branch.main.merge", "refs/heads/main"],
                     ["config", "user.name", 'Ann "The Dev" Lee'],
                     ["config", "alias.lg", "log --oneline # not a comment ; really"],
                     ["config", "core.excludesfile", "~/.gitignore_x"]):
            fx.git(repo, *args)
        scope = find_repo(repo)
        entries = read_config(Path(scope.git_dir) / "config")
        values = {(s, sub, k): v for s, sub, k, v in entries if k is not None}
        self.assertEqual(values[("user", None, "name")], 'Ann "The Dev" Lee')
        self.assertEqual(values[("alias", None, "lg")], "log --oneline # not a comment ; really")
        self.assertEqual(values[("remote", "origin", "url")],
                         "git@host.example.test:org/repo.git")
        self.assert_works(repo)

    def test_refused_is_raised_without_cli(self):
        repo = self.repo()
        fx.append_config(repo, "[include]\n")
        with self.assertRaises(Refused):
            find_repo(repo)
        with self.assertRaises(Refused):
            gitarch.gitcmd.Git(repo)
