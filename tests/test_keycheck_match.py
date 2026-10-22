"""keycheck matching and skips (BRIEF-DEVWS-041, DEC-459/462). Every
negative case sits next to a positive control, so a scan that finds
nothing fails. Every run asserts non-empty output first (DEC-071)."""
import os
import unittest
from unittest import mock

from keycheck import scan as kscan
from tests import keycheck_fixtures as fx

MATCHED = ("id_rsa", "id_ed25519", "cert.pem", "server.key", ".keys",
           "my_secret.json", "API_TOKEN", "Client-Secrets.json", ".env", ".env.local",
           ".git-credentials", ".netrc", "pc-tasks/healthchecks/backup.url",
           "a/healthchecks/deep/x.url", ".openhands/settings.json",
           ".claude/.credentials.json")
NOT_MATCHED = ("id_rsa.pub", "notes.txt", "settings.json", "other/x.url",
               ".openhands/sub/settings.json", "x/.credentials.json",
               ".claude/settings.json", ".env.example", "README.md", "keys.txt",
               "my_secret.txt")


class TestMatching(fx.Tree):
    def test_patterns(self):
        for rel in MATCHED + NOT_MATCHED:
            self.make(rel)
        flags, _ = self.flags()
        self.assertEqual(sorted(flags), sorted(MATCHED))

    def test_env_example_not_matched(self):
        self.make(".env.example")
        self.make(".env.production")
        flags, _ = self.flags()
        self.assertEqual(list(flags), [".env.production"])

    def test_openhands_and_claude_matched(self):
        self.make(".openhands/settings.json")
        self.make(".claude/.credentials.json")
        flags, _ = self.flags()
        self.assertEqual(sorted(flags), [".claude/.credentials.json",
                                         ".openhands/settings.json"])

    def test_plain_settings_json_not_matched(self):
        self.make("settings.json")
        self.make("proj/settings.json")
        self.make(".vscode/settings.json")
        self.make(".openhands/settings.json")
        flags, _ = self.flags()
        self.assertEqual(list(flags), [".openhands/settings.json"])


class TestSymlinks(fx.Tree):
    def test_symlink_never_followed(self):
        outside = fx.mkdir(self.tmp, "outside")
        fx.make(outside, "id_rsa")
        fx.make(outside, "inner/server.key")
        fx.symlink(self.root, "linkdir", outside)
        fx.symlink(self.root, "id_link", os.path.join(outside, "id_rsa"))
        fx.symlink(self.root, "plain", os.path.join(outside, "id_rsa"))
        self.make("real.key")
        flags, code = self.flags()
        self.assertEqual(flags, {"id_link": ["SYMLINK"], "real.key": ["OK"]})
        self.assertEqual(code, 0)

    def test_symlink_loop_terminates(self):
        fx.symlink(self.root, "a/loop", "..")
        fx.symlink(self.root, "a/self", "self")
        self.make("a/id_rsa")
        flags, _ = self.flags()
        self.assertEqual(flags, {"a/id_rsa": ["OK"]})


class TestSkips(fx.Tree):
    def test_venv_and_git_skipped(self):
        self.make("venv-any-name/pyvenv.cfg")
        self.make("venv-any-name/lib/server.key")
        self.make(".git/hooks/id_rsa")
        self.make("node_modules/pkg/cert.pem")
        self.make("__pycache__/token.pyc")
        self.make("lib/site-packages/secret.py")
        self.make("top.key")
        flags, _ = self.flags()
        self.assertEqual(flags, {"top.key": ["OK"]})

    def test_home_cache_and_docker_skipped(self):
        self.make(".cache/pip/server.key")
        self.make(".local/share/docker/volumes/id_rsa")
        self.make(".local/share/other/id_rsa")
        with mock.patch.dict(os.environ, {"HOME": self.root}):
            flags, _ = self.flags()
        self.assertEqual(flags, {".local/share/other/id_rsa": ["OK"]})

    def test_cache_not_skipped_outside_home(self):
        self.make(".cache/server.key")
        with mock.patch.dict(os.environ, {"HOME": self.tmp}):
            flags, _ = self.flags()
        self.assertEqual(flags, {".cache/server.key": ["OK"]})

    def test_other_device_skipped(self):
        mount = fx.mkdir(self.root, "mnt/c")
        fx.make(mount, "id_rsa")
        self.make("home.key")
        real = kscan._device

        def device(path):
            if os.path.realpath(path) == mount:
                return real(path) + 1
            return real(path)

        with mock.patch.object(kscan, "_device", device):
            flags, _ = self.flags()
        self.assertEqual(flags, {"home.key": ["OK"]})

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX non-root")
    def test_unreadable_dir_counted(self):
        locked = fx.mkdir(self.root, "locked")
        fx.make(locked, "id_rsa")
        self.make("open.key")
        os.chmod(locked, 0o000)
        try:
            _, data, _ = self.scan()
        finally:
            os.chmod(locked, 0o755)
        self.assertEqual([f["path"] for f in data["files"]], ["open.key"])
        self.assertEqual(data["counts"]["unreadable_dirs"], 1)


if __name__ == "__main__":
    unittest.main()
