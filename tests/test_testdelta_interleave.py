"""testdelta interleaving matrix (BRIEF-DEVWS-026 task 3): two tests whose
result lines interleave with captured output, with and without traceback
blocks, complete and truncated. test_a passes before and FAILs after, so
no case may end in "no regressions" (or exit 0). Inputs are synthetic."""
import contextlib
import io
import tempfile
import unittest

from testdelta.cli import EXIT_OK, main
from tests import testdelta_fixtures as fx

W = "pkg.mod.Class"
HEAD_A = f"test_a ({W}.test_a) ... "
HEAD_B = f"test_b ({W}.test_b) ... "
# Captured output: one chunk that itself ends like a result, one with no newline.
CHUNKS = {"dots-ok": "captured ... ok\n", "no-newline": "captured"}
# Where the chunk lands in the result section.
POSITIONS = ("before-a", "after-a-dots", "before-a-newline", "between", "after-b-dots",
             "after-both-dots")


def result_section(position, chunk):
    """The result lines of the failing run as one text, chunk inserted."""
    at = {p: (chunk if p == position or (position == "after-both-dots"
                                          and p in ("after-a-dots", "after-b-dots"))
              else "") for p in POSITIONS}
    return (at["before-a"] + HEAD_A + at["after-a-dots"] + "FAIL" + at["before-a-newline"]
            + "\n" + at["between"] + HEAD_B + at["after-b-dots"] + "ok\n")


def after_log(position, chunk, blocks, variant):
    """variant: complete (real FAILED footer), cut-after-a (log ends after
    test_a's line), cut-after-results (no blocks section, no footer)."""
    section = result_section(position, chunk)
    if variant == "cut-after-a":
        end = section.index("FAIL") + len("FAIL" + (chunk if position == "before-a-newline"
                                                     else ""))
        return section[:end] + "\n"
    if variant == "cut-after-results":
        return section
    out = section + "\n"
    if blocks:
        out += "\n".join(fx.ut_block("fail", W, "test_a")) + "\n"
    return out + "\n".join(fx.ut_footer(2, final="FAILED (failures=1)")) + "\n"


def injection_point(position, chunk):
    """Offset in the result section where the chunk first lands."""
    with_chunk, without = result_section(position, chunk), result_section(position, "")
    k = 0
    while k < len(without) and with_chunk[k] == without[k]:
        k += 1
    return k


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class TestInterleavingMatrix(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.before = fx.write(self.dir, "before.txt",
                               fx.ut_simple([("test_a", "pass"), ("test_b", "pass")]))

    def tearDown(self):
        self._tmp.cleanup()

    def test_baseline_without_output(self):
        log = after_log("none", "", True, "complete")
        self.assertIn("FAILED (failures=1)", log)
        after = fx.write(self.dir, "after.txt", log)
        code, out, err = run([self.before, after])
        self.assertTrue(out)
        self.assertEqual(code, 1, err)
        self.assertIn("NEWLY FAILING (1)", out)

    def test_failed_run_never_reported_clean(self):
        cases = 0
        for position in POSITIONS:
            for label, chunk in CHUNKS.items():
                for blocks in (True, False):
                    for variant in ("complete", "cut-after-a", "cut-after-results"):
                        if variant != "complete" and blocks:
                            continue        # a cut log has no blocks section
                        log = after_log(position, chunk, blocks, variant)
                        with self.subTest(position=position, chunk=label, blocks=blocks,
                                          variant=variant):
                            point = injection_point(position, chunk)
                            if variant == "complete" or len(log) > point:
                                self.assertIn("captured", log)
                            else:
                                # cut before the injection point: nothing of it
                                self.assertLessEqual(len(log), point)
                                self.assertTrue(result_section(position, chunk)
                                                .startswith(log))
                                self.assertNotIn("captured", log)
                            after = fx.write(self.dir, "after.txt", log)
                            code, out, err = run([self.before, after])
                            self.assertTrue(out or err)
                            self.assertNotEqual(code, EXIT_OK, out)
                            self.assertNotIn("no regressions", out + err)
                        cases += 1
        self.assertEqual(cases, len(POSITIONS) * len(CHUNKS) * 4)


class TestAcceptedSideEffect(unittest.TestCase):
    """Accepted false regression (safe side): a PASSING test whose own output
    on its result line starts with FAIL and holds " ... " before the real ok
    is read as failing, because "FAILcaptured ... ok" (a real FAIL with
    output glued after it) cannot be told apart from it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_passing_test_with_fail_shaped_output_reads_as_failing(self):
        line = f"test_x ({W}.test_x) ... FAIL-safe retry ... ok"
        log = fx.unittest_log([line], count=1, footer="OK")
        self.assertIn("\nOK\n", log)
        before = fx.write(self.dir, "before.txt", fx.ut_simple([("test_x", "pass")]))
        after = fx.write(self.dir, "after.txt", log)
        code, out, err = run([before, after])
        self.assertTrue(out)
        self.assertEqual(code, 1, err)
        self.assertIn("NEWLY FAILING (1)", out)
        self.assertIn("pass -> fail ", out)
        # The OK footer disagrees, so the report also says so, loudly.
        self.assertIn(("!!! after: report declares tests=1 failures=0; "
                      "parsed tests=1 failures=1"), out)


if __name__ == "__main__":
    unittest.main()
