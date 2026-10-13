"""Process bounds of the git runner (BRIEF-DEVWS-023 / DEC-202 task 3):
process groups, timeouts, output limits on stdout and stderr.

The fake git helpers are small Python scripts, passed with the test-only
Git(..., executable=...) keyword; the repository is real, so preflight runs.
"""
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import gitarch.gitcmd
from gitarch.gitcmd import Git, GitError
from tests import gitarch_fixtures as fx


class _LateOverflow:
    """An overflow flag that stays hidden while its reader runs and from the
    first check after the reader has finished (at EOF): exactly the race in
    which a reader hits its cap after the wait loop's top-of-loop check but
    before the "git exited and both pipes closed" branch. Only the runner's
    final check can see it."""

    def __init__(self, reader):
        self.reader = reader
        self.requested = False
        self.checks_after_eof = 0

    def set(self):
        self.requested = True

    def is_set(self):
        if not self.requested or self.reader.is_alive():
            return False
        self.checks_after_eof += 1
        return self.checks_after_eof > 1


class _EofOnlyReader(gitarch.gitcmd._Reader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.overflow = _LateOverflow(self)


class BoundsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.repo = fx.basic_repo(self.dir)
        self.marker = self.dir / "MARKER"

    def tearDown(self):
        self._tmp.cleanup()

    def helper(self, body, name="fake-git"):
        """An executable Python script outside the repository."""
        path = self.dir / "helpers" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="ascii")
        path.chmod(0o755)
        return path

    def assert_finished(self, git):
        """Both reader threads joined and the process reaped."""
        record = git.last_run
        self.assertIsNotNone(record)
        self.assertEqual(len(record.readers), 2)
        for reader in record.readers:
            self.assertFalse(reader.is_alive(), reader.name)
        self.assertIsNotNone(record.proc.poll())
        return record


class TestTimeout(BoundsTestCase):
    def test_timeout_kills_process_group(self):
        """The helper forks a child that writes the marker 1.5 s after start;
        the 0.5 s timeout must kill the whole group, child included. The test
        waits until 3 s after the start before asserting the marker is absent."""
        helper = self.helper(f"""
            import os, sys, time
            if os.fork() == 0:
                time.sleep(1.5)
                open({str(self.marker)!r}, "a").write("child survived\\n")
                sys.exit(0)
            time.sleep(30)
            """)
        git = Git(self.repo, executable=str(helper), timeout=0.5)
        start = time.monotonic()
        with self.assertRaisesRegex(GitError, "git timed out after 0.5 s"):
            git.run("log")
        self.assertLess(time.monotonic() - start, 1.4)   # killed before the child writes
        self.assert_finished(git)
        time.sleep(max(0.0, 3.0 - (time.monotonic() - start)))
        self.assertFalse(self.marker.exists(), "a process of the group survived the timeout")

    def test_timeout_sigkill_after_grace(self):
        """A child that ignores SIGTERM still dies: SIGKILL follows the grace
        period (shortened to 0.5 s here)."""
        helper = self.helper(f"""
            import os, signal, sys, time
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            if os.fork() == 0:
                time.sleep(2.0)
                open({str(self.marker)!r}, "a").write("child survived\\n")
                sys.exit(0)
            time.sleep(30)
            """)
        git = Git(self.repo, executable=str(helper), timeout=0.5)
        start = time.monotonic()
        with mock.patch.object(gitarch.gitcmd, "KILL_GRACE", 0.5):
            with self.assertRaisesRegex(GitError, "git timed out"):
                git.run("log")
        self.assert_finished(git)
        time.sleep(max(0.0, 3.5 - (time.monotonic() - start)))
        self.assertFalse(self.marker.exists(), "SIGTERM-ignoring child survived")

    def test_pipe_held_open_by_child_counts_against_timeout(self):
        """git exits at once, but a child keeps stdout open: still bounded."""
        helper = self.helper("""
            import os, sys, time
            if os.fork() == 0:
                time.sleep(30)
                sys.exit(0)
            sys.exit(0)
            """)
        git = Git(self.repo, executable=str(helper), timeout=0.5)
        start = time.monotonic()
        with self.assertRaisesRegex(GitError, "git timed out"):
            git.run("log")
        self.assertLess(time.monotonic() - start, 5)
        self.assert_finished(git)


class TestOutputLimits(BoundsTestCase):
    def test_stdout_flood_killed_exit_3(self):
        cap = 256 * 1024
        helper = self.helper("""
            import sys
            block = b"x" * 65536
            while True:
                sys.stdout.buffer.write(block)
            """)
        git = Git(self.repo, executable=str(helper), max_output=cap, timeout=30)
        start = time.monotonic()
        with self.assertRaisesRegex(GitError, "output limit") as caught:
            git.run("log")
        self.assertIn("stdout", str(caught.exception))
        self.assertLess(time.monotonic() - start, 10)
        record = self.assert_finished(git)
        out = record.readers[0]
        self.assertTrue(out.overflow.is_set())
        self.assertGreater(out.kept, 0)
        self.assertLessEqual(out.kept, cap)
        self.assertLessEqual(len(out.data()), cap)

    def test_stderr_flood_killed_exit_3(self):
        cap = 64 * 1024
        helper = self.helper("""
            import sys
            sys.stdout.buffer.write(b"some stdout\\n")
            sys.stdout.flush()
            block = b"e" * 65536
            while True:
                sys.stderr.buffer.write(block)
            """)
        git = Git(self.repo, executable=str(helper), max_stderr=cap, timeout=30)
        start = time.monotonic()
        with self.assertRaisesRegex(GitError, "output limit") as caught:
            git.run("log")
        self.assertIn("stderr", str(caught.exception))
        self.assertLess(time.monotonic() - start, 10)
        record = self.assert_finished(git)
        err = record.readers[1]
        self.assertTrue(err.overflow.is_set())
        self.assertGreater(err.kept, 0)
        self.assertLessEqual(err.kept, cap)
        self.assertLessEqual(len(err.data()), cap)
        self.assertEqual(record.readers[0].data(), b"some stdout\n")

    def test_output_within_cap_returned_whole(self):
        """Exactly cap bytes on stdout, interleaved with 512 KB on stderr
        (under its cap): no deadlock, no overflow, every byte returned."""
        size = 3 * 1024 * 1024 + 17
        helper = self.helper(f"""
            import sys
            data = bytes(range(256)) * ({size} // 256 + 1)
            data = data[:{size}]
            for i in range(0, {size}, 65536):
                sys.stdout.buffer.write(data[i:i + 65536])
                if i < 512 * 1024:
                    sys.stderr.buffer.write(b"w" * 65536)
            """)
        expected = (bytes(range(256)) * (size // 256 + 1))[:size]
        git = Git(self.repo, executable=str(helper), max_output=size, timeout=30)
        out = git.run("log")
        self.assertTrue(out)
        self.assertEqual(len(out), size)
        self.assertEqual(out, expected)
        record = self.assert_finished(git)
        self.assertFalse(record.readers[0].overflow.is_set())
        self.assertFalse(record.readers[1].overflow.is_set())
        self.assertEqual(record.readers[1].kept, 512 * 1024)

    def test_fast_flood_then_exit_0_is_exit_3(self):
        """cap + 1 bytes written at once, then exit 0: git is gone and both
        pipes are at EOF before the overrun is noticed, yet it is still
        "output limit" (stdout and stderr variants)."""
        cap = 4096
        for stream, kwargs in (("stdout", {"max_output": cap}),
                               ("stderr", {"max_stderr": cap})):
            with self.subTest(stream=stream):
                helper = self.helper(f"""
                    import sys
                    sys.{stream}.buffer.write(b"z" * {cap + 1})
                    sys.{stream}.flush()
                    sys.exit(0)
                    """, name=f"fast-{stream}")
                git = Git(self.repo, executable=str(helper), timeout=30, **kwargs)
                with self.assertRaisesRegex(GitError, "output limit") as caught:
                    git.run("log")
                self.assertIn(stream, str(caught.exception))
                record = self.assert_finished(git)
                self.assertEqual(record.proc.returncode, 0)   # git itself said ok
                reader = record.readers[0 if stream == "stdout" else 1]
                self.assertTrue(reader.overflow.is_set())
                self.assertEqual(reader.kept, cap)

    def test_overrun_seen_only_at_eof_is_exit_3(self):
        """Deterministic version of the fast flood: the overrun becomes
        visible only after EOF and after the top-of-loop check, so only the
        runner's final check can turn it into "output limit"."""
        cap = 4096
        for stream, kwargs in (("stdout", {"max_output": cap}),
                               ("stderr", {"max_stderr": cap})):
            with self.subTest(stream=stream):
                helper = self.helper(f"""
                    import sys
                    sys.{stream}.buffer.write(b"q" * {cap + 1})
                    sys.{stream}.flush()
                    sys.exit(0)
                    """, name=f"eof-{stream}")
                git = Git(self.repo, executable=str(helper), timeout=30, **kwargs)
                with mock.patch.object(gitarch.gitcmd, "_Reader", _EofOnlyReader):
                    with self.assertRaisesRegex(GitError, "output limit") as caught:
                        git.run("log")
                self.assertIn(stream, str(caught.exception))
                record = git.last_run
                self.assertIsNotNone(record)
                for reader in record.readers:
                    self.assertIsInstance(reader, _EofOnlyReader)
                    self.assertFalse(reader.is_alive())
                self.assertEqual(record.proc.returncode, 0)
                reader = record.readers[0 if stream == "stdout" else 1]
                self.assertTrue(reader.overflow.requested)
                self.assertEqual(reader.overflow.checks_after_eof, 2)
                self.assertEqual(reader.kept, cap)

    def test_one_byte_over_cap_is_refused(self):
        helper = self.helper("""
            import sys
            sys.stdout.buffer.write(b"y" * 1001)
            """)
        git = Git(self.repo, executable=str(helper), max_output=1000, timeout=30)
        with self.assertRaisesRegex(GitError, "output limit"):
            git.run("log")
        record = self.assert_finished(git)
        self.assertEqual(record.readers[0].kept, 1000)
