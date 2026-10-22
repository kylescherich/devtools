"""keycheck flags and exit codes (BRIEF-DEVWS-041). Every run asserts
non-empty output first (DEC-071) and runs under the never-open guard."""
import os
import unittest

from keycheck.cli import EXIT_FLAGGED, EXIT_OK
from tests import keycheck_fixtures as fx


class TestPermissions(fx.Tree):
    def test_644_key_others_can_read(self):
        self.make("id_ed25519", 0o644)
        flags, code = self.flags()
        self.assertEqual(flags, {"id_ed25519": ["OTHERS-CAN-READ"]})
        self.assertEqual(code, EXIT_FLAGGED)

    def test_600_key_ok(self):
        self.make("server.key", 0o600)
        flags, code = self.flags()
        self.assertEqual(flags, {"server.key": ["OK"]})
        self.assertEqual(code, EXIT_OK)

    def test_640_group_read_others_can_read(self):
        self.make("cert.pem", 0o640)
        flags, code = self.flags()
        self.assertEqual(flags, {"cert.pem": ["OTHERS-CAN-READ"]})
        self.assertEqual(code, EXIT_FLAGGED)

    def test_mode_and_size_reported(self):
        self.make("server.key", 0o600)
        _, data, _ = self.scan()
        self.assertEqual(len(data["files"]), 1)
        entry = data["files"][0]
        self.assertEqual(entry["mode"], "0600")
        self.assertEqual(entry["size"], len(fx.CONTENT.encode("utf-8")))
        self.assertEqual(entry["age_days"], 0)


class TestStaleCopy(fx.Tree):
    def test_secrets_env_bak_stale_copy(self):
        self.make("secrets.env.bak-1", 0o600)
        flags, code = self.flags()
        self.assertEqual(flags, {"secrets.env.bak-1": ["STALE-COPY"]})
        self.assertEqual(code, EXIT_FLAGGED)

    def test_backup_variants(self):
        for name in ("id_rsa.old", "cert.pem.orig", "api_token~", ".env.bak",
                     "server.key.bak.2026-01-01", ".netrc.old~"):
            self.make(name, 0o600)
        flags, code = self.flags()
        self.assertEqual(flags, {name: ["STALE-COPY"] for name in (
            "id_rsa.old", "cert.pem.orig", "api_token~", ".env.bak",
            "server.key.bak.2026-01-01", ".netrc.old~")})
        self.assertEqual(code, EXIT_FLAGGED)

    def test_backup_of_non_key_not_matched(self):
        self.make("notes.txt.bak", 0o600)
        self.make("id_rsa.pub.old", 0o600)
        self.make("id_rsa", 0o600)
        flags, _ = self.flags()
        self.assertEqual(flags, {"id_rsa": ["OK"]})


class TestInRepo(fx.Tree):
    def setUp(self):
        super().setUp()
        self.repo = fx.git_repo(self.root, "proj")

    def test_key_in_repo_not_ignored(self):
        self.make("proj/deploy.key", 0o600)
        flags, code = self.flags()
        self.assertEqual(flags, {"proj/deploy.key": ["IN-REPO-NOT-IGNORED"]})
        self.assertEqual(code, EXIT_FLAGGED)

    def test_key_in_repo_ignored(self):
        fx.make(self.repo, ".gitignore", 0o644, content="deploy.key\n")
        self.make("proj/deploy.key", 0o600)
        flags, code = self.flags()
        self.assertEqual(flags, {"proj/deploy.key": ["OK"]})
        self.assertEqual(code, EXIT_OK)

    def test_tracked_key_not_ignored_even_if_pattern_matches(self):
        self.make("proj/deploy.key", 0o600)
        fx.git(self.repo, "add", "deploy.key")
        fx.make(self.repo, ".gitignore", 0o644, content="deploy.key\n")
        flags, code = self.flags()
        self.assertEqual(flags, {"proj/deploy.key": ["IN-REPO-NOT-IGNORED"]})
        self.assertEqual(code, EXIT_FLAGGED)

    def test_root_inside_repo(self):
        self.make("proj/sub/deploy.key", 0o600)
        code, data, _ = self.scan(root=os.path.join(self.repo, "sub"))
        self.assertEqual({f["path"]: f["flags"] for f in data["files"]},
                         {"deploy.key": ["IN-REPO-NOT-IGNORED"]})
        self.assertEqual(code, EXIT_FLAGGED)

    def test_in_repo_listed_first(self):
        self.make("aaa.key", 0o644)
        self.make("proj/zzz.key", 0o600)
        self.make("bbb.key", 0o600)
        _, data, _ = self.scan()
        self.assertEqual([f["path"] for f in data["files"]],
                         ["proj/zzz.key", "aaa.key", "bbb.key"])


class TestOld(fx.Tree):
    def test_old_mtime_old(self):
        self.make("id_rsa", 0o600, age_days=400)
        flags, code = self.flags()
        self.assertEqual(flags, {"id_rsa": ["OLD"]})
        self.assertEqual(code, EXIT_OK)

    def test_max_age_days(self):
        self.make("id_rsa", 0o600, age_days=400)
        flags, _ = self.flags("--max-age-days", "500")
        self.assertEqual(flags, {"id_rsa": ["OK"]})
        flags, _ = self.flags("--max-age-days", "30")
        self.assertEqual(flags, {"id_rsa": ["OLD"]})

    def test_all_flags_in_order(self):
        fx.git_repo(self.root, "proj")
        self.make("proj/id_rsa.bak", 0o644, age_days=400)
        flags, code = self.flags()
        self.assertEqual(flags, {"proj/id_rsa.bak": [
            "IN-REPO-NOT-IGNORED", "OTHERS-CAN-READ", "STALE-COPY", "OLD"]})
        self.assertEqual(code, EXIT_FLAGGED)

    def test_counts(self):
        self.make("a.key", 0o644)
        self.make("b.key", 0o600, age_days=400)
        self.make("c.key", 0o600)
        _, data, _ = self.scan()
        self.assertEqual(data["counts"], {
            "total": 3, "IN-REPO-NOT-IGNORED": 0, "OTHERS-CAN-READ": 1,
            "STALE-COPY": 0, "OLD": 1, "SYMLINK": 0, "OK": 1, "unreadable_dirs": 0})


if __name__ == "__main__":
    unittest.main()
