"""History model on synthetic repositories (BRIEF-DEVWS-022 task 3)."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gitarch import history
from gitarch.gitcmd import Git
from tests import gitarch_fixtures as fx
from tests import secret_shapes as ss

HOSTILE_NAME = "<img onerror>.py"


def utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, repo, **kwargs):
        model = history.build(Git(repo), **kwargs)
        self.assertTrue(model.overview.commits, "empty model")
        return model


def lifecycle_repo(directory):
    """keep.py and life.py: life.py added -> modified -> renamed -> deleted."""
    repo = fx.init_repo(directory)
    fx.write(repo, "keep.py", "k = 1\n")
    fx.write(repo, "pkg/life.py", "a\nb\n")
    c1 = fx.commit(repo, "add", "2026-01-05T10:00:00Z")
    fx.write(repo, "pkg/life.py", "a\nb\nc\n")
    c2 = fx.commit(repo, "modify", "2026-01-06T10:00:00Z")
    fx.git(repo, "mv", "pkg/life.py", "pkg/renamed.py")
    c3 = fx.commit(repo, "rename", "2026-01-07T10:00:00Z")
    fx.write(repo, "pkg/renamed.py", "a\nc\n")
    fx.write(repo, "keep.py", "k = 2\n")
    c4 = fx.commit(repo, "edit renamed", "2026-01-08T10:00:00Z")
    fx.git(repo, "rm", "-q", "pkg/renamed.py")
    c5 = fx.commit(repo, "delete", "2026-01-09T10:00:00Z")
    return repo, (c1, c2, c3, c4, c5)


class TestLifecycle(HistoryTestCase):
    def test_add_modify_rename_delete(self):
        repo, (c1, c2, c3, c4, c5) = lifecycle_repo(self.dir)
        model = self.build(repo)
        self.assertEqual([f.path for f in model.files], ["keep.py"])
        self.assertEqual(len(model.deleted), 1)
        life = model.deleted[0]
        self.assertEqual(life.path, "pkg/renamed.py")
        self.assertEqual(life.introduced, (c1, utc("2026-01-05T10:00:00Z")))
        self.assertEqual(life.deleted_in, (c5, utc("2026-01-09T10:00:00Z")))
        self.assertEqual(life.last_changed[0], c5)
        self.assertEqual(life.commits, 5)
        self.assertEqual([(r.old, r.new, r.sha) for r in life.renames],
                         [("pkg/life.py", "pkg/renamed.py", c3)])
        # +2 add, +1 modify, 0 rename, -1 edit (b removed), -2 delete
        self.assertEqual((life.added, life.deleted), (2 + 1 + 0 + 0 + 0, 0 + 0 + 0 + 1 + 2))
        self.assertFalse(life.binary)
        keep = model.files[0]
        self.assertEqual((keep.commits, keep.added, keep.deleted), (2, 2, 1))
        self.assertEqual(keep.last_changed, (c4, utc("2026-01-08T10:00:00Z")))

    def test_overview(self):
        repo, (c1, *_, c5) = lifecycle_repo(self.dir)
        ov = self.build(repo).overview
        self.assertEqual((ov.rev, ov.sha), ("HEAD", c5))
        self.assertEqual((ov.commits, ov.merges, ov.ordinary, ov.authors), (5, 0, 5, 1))
        self.assertEqual(ov.first, utc("2026-01-05T10:00:00Z"))
        self.assertEqual(ov.last, utc("2026-01-09T10:00:00Z"))
        older = self.build(repo, rev="HEAD~2").overview
        self.assertEqual((older.rev, older.commits), ("HEAD~2", 3))

    def test_path_follows_renames(self):
        repo, (c1, c2, c3, c4, _) = lifecycle_repo(self.dir)
        model = self.build(repo, rev="HEAD~1", path="pkg/renamed.py")
        self.assertTrue(model.followed)
        self.assertEqual(model.overview.commits, 4)
        self.assertEqual(len(model.files), 1)
        record = model.files[0]
        self.assertEqual(record.path, "pkg/renamed.py")
        self.assertEqual(record.introduced[0], c1)
        self.assertEqual([(r.old, r.new) for r in record.renames],
                         [("pkg/life.py", "pkg/renamed.py")])
        directory = self.build(repo, path="pkg/")
        self.assertFalse(directory.followed)
        self.assertEqual([f.path for f in directory.files], [])
        self.assertEqual([f.path for f in directory.deleted], ["pkg/renamed.py"])

    def test_hotspots_ranked_with_ties_by_path(self):
        repo, _ = lifecycle_repo(self.dir)
        hs = self.build(repo).hotspots
        self.assertIsNone(hs.days)
        self.assertEqual([(h.path, h.changes) for h in hs.files_by_changes],
                         [("pkg/renamed.py", 5), ("keep.py", 2)])
        self.assertEqual([(h.path, h.churn) for h in hs.files_by_churn],
                         [("pkg/renamed.py", 6), ("keep.py", 3)])
        self.assertEqual([(h.path, h.changes) for h in hs.dirs_by_changes],
                         [("pkg", 5), (".", 2)])

    def test_recent_window_relative_to_newest_commit(self):
        repo = fx.init_repo(self.dir)
        fx.write(repo, "old.py", "o\n")
        fx.commit(repo, "old", "2025-06-01T10:00:00Z")
        for day, name in ((1, "b.py"), (2, "a.py"), (3, "a.py")):
            fx.write(repo, name, f"v{day}\n")
            fx.commit(repo, name, f"2026-01-0{day}T10:00:00Z")
        model = self.build(repo, recent_days=30)
        self.assertEqual(model.recent.days, 30)
        self.assertEqual(model.recent.since, utc("2025-12-04T10:00:00Z"))
        self.assertEqual([(h.path, h.changes) for h in model.recent.files_by_changes],
                         [("a.py", 2), ("b.py", 1)])
        self.assertIn("old.py", [h.path for h in model.hotspots.files_by_changes])
        # Equal counts: ties broken by path.
        tie = self.build(repo, recent_days=2)
        self.assertEqual([h.path for h in tie.recent.files_by_changes], ["a.py", "b.py"])
        self.assertEqual(self.build(repo).recent.days, history.RECENT_DAYS)

    def test_top_15(self):
        repo = fx.init_repo(self.dir)
        for i in range(20):
            fx.write(repo, f"f{i:02d}.py", "x\n" * (i + 1))
        fx.commit(repo, "many", "2026-01-05T10:00:00Z")
        hs = self.build(repo).hotspots
        self.assertEqual(len(hs.files_by_changes), history.TOP)
        self.assertEqual(hs.files_by_changes[0].path, "f00.py")      # all 1 change: by path
        self.assertEqual(hs.files_by_churn[0].path, "f19.py")        # 20 lines


class TestMergeBinaryNames(HistoryTestCase):
    def test_merge_counted_separately(self):
        repo = fx.init_repo(self.dir)
        fx.write(repo, "a.py", "1\n")
        fx.commit(repo, "base", "2026-01-05T10:00:00Z")
        fx.git(repo, "checkout", "-q", "-b", "feature")
        fx.write(repo, "b.py", "1\n")
        fx.commit(repo, "feature", "2026-01-06T10:00:00Z")
        fx.git(repo, "checkout", "-q", "main")
        fx.write(repo, "a.py", "1\n2\n")
        fx.commit(repo, "main", "2026-01-07T10:00:00Z")
        fx.git(repo, "merge", "-q", "--no-ff", "-m", "merge feature", "feature",
               date="2026-01-08T10:00:00Z")
        model = self.build(repo)
        ov = model.overview
        self.assertEqual((ov.commits, ov.merges, ov.ordinary), (4, 1, 3))
        self.assertEqual({f.path: f.commits for f in model.files}, {"a.py": 2, "b.py": 1})

    def test_binary_counted_without_line_totals(self):
        repo = fx.init_repo(self.dir)
        fx.write(repo, "img.bin", b"\x00\x01\x02")
        fx.write(repo, "t.py", "x\n")
        fx.commit(repo, "add", "2026-01-05T10:00:00Z")
        fx.write(repo, "img.bin", b"\x00\x09\x09\x09")
        fx.commit(repo, "change", "2026-01-06T10:00:00Z")
        model = self.build(repo)
        binary = {f.path: f for f in model.files}["img.bin"]
        self.assertTrue(binary.binary)
        self.assertEqual(binary.commits, 2)
        self.assertIsNone(binary.added)
        self.assertIsNone(binary.deleted)
        self.assertEqual([(h.path, h.changes) for h in model.hotspots.files_by_changes],
                         [("img.bin", 2), ("t.py", 1)])
        self.assertNotIn("img.bin", [h.path for h in model.hotspots.files_by_churn])

    def test_hostile_file_name_kept_exact(self):
        repo = fx.init_repo(self.dir)
        weird = "tab\there.py"
        fx.write(repo, HOSTILE_NAME, "x = 1\n")
        fx.write(repo, weird, "y\n")
        fx.commit(repo, "hostile names", "2026-01-05T10:00:00Z")
        fx.git(repo, "mv", HOSTILE_NAME, "<b>.py")
        fx.commit(repo, "rename", "2026-01-06T10:00:00Z")
        model = self.build(repo)
        self.assertEqual([f.path for f in model.files], ["<b>.py", weird])
        self.assertEqual([(r.old, r.new) for r in model.files[0].renames],
                         [(HOSTILE_NAME, "<b>.py")])
        followed = self.build(repo, path="<b>.py")
        self.assertTrue(followed.followed)
        self.assertEqual(followed.files[0].introduced[1], utc("2026-01-05T10:00:00Z"))


class TestPrivacy(HistoryTestCase):
    def _repo(self):
        repo = fx.init_repo(self.dir)
        secret = ss.AKIA + "SNTGITARCH000001"
        message = (f"deploy\n\naws key {secret}\npassword=SNT gitarch words\n"
                   f"Authorization: Bearer SNTgitarchBearer99")
        fx.write(repo, "a.py", "1\n")
        fx.commit(repo, message, "2026-01-05T10:00:00Z")
        fx.write(repo, "a.py", "2\n")
        fx.commit(repo, "second", "2026-01-06T10:00:00Z",
                  author=("Bob Second", "bob.second@example.test"))
        return repo, secret

    def test_commit_message_secrets_never_in_model(self):
        repo, secret = self._repo()
        self.assertIn(secret.encode(), fx.git(repo, "log", "--format=%B"))   # data is real
        for authors in (False, True):
            model = self.build(repo, authors=authors)
            text = repr(model)
            self.assertTrue(text)
            for sentinel in (secret, "SNT gitarch", "SNTgitarchBearer99", "deploy"):
                self.assertNotIn(sentinel, text)

    def test_authors_names_only_with_flag_never_emails(self):
        repo, _ = self._repo()
        plain = self.build(repo)
        self.assertEqual(plain.overview.authors, 2)
        self.assertIsNone(plain.authors)
        text = repr(plain)
        self.assertTrue(text)
        for word in ("Ada Fixture", "Bob Second", "@example.test"):
            self.assertNotIn(word, text)
        named = self.build(repo, authors=True)
        self.assertEqual(named.authors, [("Ada Fixture", 1), ("Bob Second", 1)])
        text = repr(named)
        self.assertTrue(text)
        self.assertNotIn("@example.test", text)


class TestActivity(HistoryTestCase):
    def test_weekly_iso_weeks_with_gaps(self):
        repo = fx.init_repo(self.dir)
        for i, date in enumerate(("2025-12-29T10:00:00Z", "2025-12-30T10:00:00Z",
                                  "2026-01-19T10:00:00Z")):
            fx.write(repo, "a.py", f"{i}\n")
            fx.commit(repo, "c", date)
        model = self.build(repo)
        self.assertEqual(model.activity_unit, "week")
        self.assertEqual(model.activity, [("2026-W01", 2), ("2026-W02", 0),
                                          ("2026-W03", 0), ("2026-W04", 1)])

    def test_monthly_when_span_over_a_year(self):
        repo = fx.init_repo(self.dir)
        for i, date in enumerate(("2024-11-15T10:00:00Z", "2025-01-02T10:00:00Z",
                                  "2025-12-20T10:00:00Z")):
            fx.write(repo, "a.py", f"{i}\n")
            fx.commit(repo, "c", date)
        model = self.build(repo)
        self.assertEqual(model.activity_unit, "month")
        self.assertEqual(model.activity[:3], [("2024-11", 1), ("2024-12", 0), ("2025-01", 1)])
        self.assertEqual(model.activity[-1], ("2025-12", 1))
        self.assertEqual(len(model.activity), 14)
        self.assertEqual(sum(n for _, n in model.activity), 3)
