"""End-to-end loglens runs through main(argv); no subprocess."""
import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from loglens.cli import (EXIT_ERROR, EXIT_INPUT, EXIT_OK, EXIT_USAGE, EXIT_WARNING,
                         main)
from tests import loglens_fixtures as fx

FORMATS = ("text", "md", "html")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class E2ETestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.logs = self.dir / "logs"
        self.out = self.dir / "out"
        self.logs.mkdir()
        self.out.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def mixed_inputs(self):
        """A gzipped text log, a JSONL log and a syslog file, interleaved in time."""
        a = fx.write_gz(self.logs, "a.log.gz", "".join(f"{line}\n" for line in [
            "2026-03-01T10:00:00Z INFO a start",
            "2026-03-01T10:00:02Z ERROR a db timeout after 300 ms",
            *fx.traceback_lines("u-17"),
            "2026-03-01T10:00:04Z WARN a slow 120 ms",
        ]))
        b = fx.write_jsonl(self.logs, "b.jsonl", [
            {"ts": "2026-03-01T10:00:01Z", "level": "info", "msg": "b hello"},
            {"ts": "2026-03-01T10:00:03Z", "level": "error",
             "msg": "a db timeout after 900 ms"},
            "not json",
        ])
        c = fx.write_lines(self.logs, "c.log", [
            "Mar  1 10:00:05 host app ERROR c disk full token=SNTsecretC",
            "Mar  1 10:00:06 host app INFO c ok",
        ])
        return [a, b, c]


class TestExitCodes(E2ETestCase):
    def test_exit_codes_0_1_2_3_4(self):
        info = fx.write_lines(self.logs, "info.log", ["2026-03-01T10:00:00Z INFO fine"])
        warn = fx.write_lines(self.logs, "warn.log", ["2026-03-01T10:00:00Z INFO fine",
                                                      "2026-03-01T10:00:01Z WARN slow"])
        error = fx.write_lines(self.logs, "error.log", ["2026-03-01T10:00:01Z WARN slow",
                                                        "2026-03-01T10:00:02Z FATAL down"])
        missing = self.logs / "missing.log"
        cases = [
            ([info], EXIT_OK, ""),
            ([warn], EXIT_WARNING, ""),
            ([error], EXIT_ERROR, ""),
            ([info, "--bucket", "2m"], EXIT_USAGE, "--bucket"),
            ([missing], EXIT_INPUT, "not found"),
        ]
        for argv, expected, message in cases:
            with self.subTest(argv=argv):
                code, out, err = run(argv)
                self.assertEqual(code, expected, err)
                if expected in (EXIT_USAGE, EXIT_INPUT):
                    self.assertTrue(err)
                    self.assertIn(message, err)
                else:
                    self.assertTrue(out)
                    self.assertIn("loglens report", out)

    def test_exit_code_precedence(self):
        error = fx.write_lines(self.logs, "error.log", ["2026-03-01T10:00:02Z ERROR x"])
        missing = self.logs / "missing.log"
        big = fx.write_lines(self.logs, "big.log", ["2026-03-01T10:00:02Z ERROR y"] * 50)
        self.assertEqual(run([error, missing, "--context", "-1"])[0], EXIT_USAGE)
        self.assertEqual(run([error, missing])[0], EXIT_INPUT)
        self.assertEqual(run([error, big, "--max-bytes", "100"])[0], EXIT_INPUT)
        self.assertEqual(run([error, "--output", self.logs / "r.txt"])[0], EXIT_USAGE)

    def test_help_has_exit_code_table(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, 0)
        self.assertTrue(out)
        for line in ("2 > 3 > 4 > 1 > 0", "0  no entries at WARNING or above",
                     "1  WARNING entries", "2  usage error", "3  input missing",
                     "4  entries at ERROR or above", "--input-format", "--output"):
            self.assertIn(line, out)


class TestReproducible(E2ETestCase):
    def test_reproducible_byte_identical(self):
        inputs = self.mixed_inputs()
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                outputs = []
                for n in (1, 2):
                    path = self.out / f"report-{n}.{fmt}"
                    code, _, err = run([*inputs, "--year", "2026", "--format", fmt,
                                        "--raw", "--context", "2", "--output", path])
                    self.assertEqual(code, EXIT_ERROR, err)
                    outputs.append(path.read_bytes())
                self.assertTrue(outputs[0].strip(), "report is empty")
                self.assertIn(b"a db timeout after <n> ms" if fmt == "text"
                              else b"a db timeout after &lt;n&gt; ms", outputs[0])
                self.assertEqual(outputs[0], outputs[1])
                _, stdout, _ = run([*inputs, "--year", "2026", "--format", fmt,
                                    "--raw", "--context", "2"])
                self.assertEqual(stdout.encode("utf-8"), outputs[0])
                self.assertNotIn(b"SNTsecretC", outputs[0])


class TestOutputGuards(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.log = fx.write_lines(self.logs, "app.log", ["2026-03-01T10:00:00Z WARN slow"])

    def test_output_guards(self):
        target = self.out / "report.txt"

        # new file: written, stdout stays empty
        code, out, err = run([self.log, "--output", target])
        self.assertEqual(code, EXIT_WARNING, err)
        self.assertEqual(out, "")
        self.assertTrue(target.read_text(encoding="utf-8").strip())
        self.assertIn("WARN slow", target.read_text(encoding="utf-8"))

        # exists: refused, untouched
        target.write_text("keep me\n", encoding="utf-8")
        code, _, err = run([self.log, "--output", target])
        self.assertEqual(code, EXIT_USAGE)
        self.assertTrue(err)
        self.assertIn("already exists", err)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep me\n")

        # exists + --force: replaced
        code, _, err = run([self.log, "--output", target, "--force"])
        self.assertEqual(code, EXIT_WARNING, err)
        text = target.read_text(encoding="utf-8")
        self.assertTrue(text.strip())
        self.assertIn("WARN slow", text)
        self.assertNotIn("keep me", text)

        # inside an input's directory: refused, not created
        inside = self.logs / "report.txt"
        for extra in ([], ["--force"]):
            code, _, err = run([self.log, "--output", inside, *extra])
            self.assertEqual(code, EXIT_USAGE)
            self.assertTrue(err)
            self.assertIn("directory of input", err)
            self.assertFalse(inside.exists())
        other = fx.write_lines(self.out, "other.log", ["2026-03-01T10:00:00Z INFO x"])
        code, _, err = run([self.log, other, "--output", self.out / "new.txt"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("directory of input", err)

        # --force without --output is a usage error
        self.assertEqual(run([self.log, "--force"])[0], EXIT_USAGE)

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks")
    def test_output_symlink_never_followed(self):
        victim = self.dir / "victim.txt"
        victim.write_text("original\n", encoding="utf-8")
        link = fx.symlink_to(self.out, "report.txt", victim)
        dangling = fx.symlink_to(self.out, "dangling.txt", self.dir / "created.txt")
        for path in (link, dangling):
            for extra in ([], ["--force"]):
                with self.subTest(path=path.name, extra=extra):
                    code, _, err = run([self.log, "--output", path, *extra])
                    self.assertEqual(code, EXIT_USAGE)
                    self.assertTrue(err)
                    self.assertIn("symlink", err)
        self.assertEqual(victim.read_text(encoding="utf-8"), "original\n")
        self.assertFalse((self.dir / "created.txt").exists())
        self.assertTrue(link.is_symlink())

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "no O_NOFOLLOW")
    def test_open_refuses_symlink_created_after_check(self):
        from loglens.cli import _open_output
        victim = self.dir / "victim.txt"
        victim.write_text("original\n", encoding="utf-8")
        link = fx.symlink_to(self.out, "late.txt", victim)
        for force in (False, True):
            with self.assertRaises(OSError):
                _open_output(link, force)
        self.assertEqual(victim.read_text(encoding="utf-8"), "original\n")


class TestInputs(E2ETestCase):
    def test_inputs_unchanged(self):
        inputs = self.mixed_inputs()
        before = {p: (sha256(p), os.stat(p).st_size) for p in inputs}
        listing = sorted(os.listdir(self.logs))
        for fmt in FORMATS:
            for extra in ([], ["--raw", "--context", "3"]):
                code, _, err = run([*inputs, "--year", "2026", "--format", fmt,
                                    "--output", self.out / f"r-{fmt}-{len(extra)}",
                                    *extra])
                self.assertEqual(code, EXIT_ERROR, err)
        after = {p: (sha256(p), os.stat(p).st_size) for p in inputs}
        self.assertTrue(before)
        self.assertEqual(before, after)
        self.assertEqual(sorted(os.listdir(self.logs)), listing)

    def test_gz_and_multi_file_merge(self):
        inputs = self.mixed_inputs()
        code, out, err = run([*inputs, "--year", "2026", "--context", "1", "--raw"])
        self.assertEqual(code, EXIT_ERROR, err)
        self.assertTrue(out)
        # header: all three files, the invalid JSONL line, the syslog year
        for name in ("a.log.gz", "b.jsonl", "c.log"):
            self.assertIn(name, out)
        self.assertIn("b.jsonl:3)", out)
        self.assertIn("syslog year: 2026", out)
        self.assertIn("entries: 7\n", out)
        # the gz entry carries a traceback and groups by its last frame
        rows = [l for l in out.splitlines() if l.startswith(("ERROR ", "WARNING ", "INFO "))]
        self.assertIn("ERROR          1  2026-03-01T10:00:02Z  2026-03-01T10:00:02Z  "
                      "KeyError at users.py:42 in load_user", rows)
        self.assertIn("ERROR          1  2026-03-01T10:00:03Z  2026-03-01T10:00:03Z  "
                      "a db timeout after <n> ms", rows)
        # merged timeline order across files: a.log.gz 10:00:02 sits between
        # b.jsonl 10:00:01 and b.jsonl 10:00:03; c.log follows a.log.gz 10:00:04
        incident = out[out.index("incident view"):]
        first = incident[:incident.index("\n2. ")]
        self.assertIn("KeyError at users.py:42 in load_user", first)
        self.assertIn("before (1):\n     INFO     b hello", first)
        self.assertIn("after (1):\n     ERROR    a db timeout after <n> ms", first)
        c_block = incident[incident.index("host app ERROR c disk full"):]
        self.assertIn("before (1):\n     WARNING  WARN a slow <n> ms", c_block)
        self.assertIn("after (1):\n     INFO     host app INFO c ok", c_block)
        self.assertIn("E      3  W      1", out)
        self.assertNotIn("SNTsecretC", out)


if __name__ == "__main__":
    unittest.main()
