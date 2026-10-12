"""No file under tests/ may contain a complete provider-shaped token (DEC-193).

Synthetic sentinels are built at runtime from tests/secret_shapes.py, so
secret scanners never see a full shape in the repository. The patterns below
are written so that their own source text does not match them.
"""
import pathlib
import re
import unittest

TESTS = pathlib.Path(__file__).resolve().parent

SHAPES = [
    ("AWS key id", r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    ("Slack token", r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    ("GitLab token", r"glpat-[A-Za-z0-9_-]{20,}"),
    ("Google API key", r"AIza[0-9A-Za-z_-]{35}"),
    ("Stripe key", r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}"),
    ("GitHub token", r"gh[pousr]_[A-Za-z0-9]{10,}"),
    ("GitHub fine-grained token", r"github_pat_[A-Za-z0-9_]{10,}"),
    ("sk- key", r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{10,}"),
    ("JWT", r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    ("PEM private key", r"-----(?:BEGIN|END) [A-Z ]*PRIVATE KEY-----"),
]
PATTERNS = [(name, re.compile(source)) for name, source in SHAPES]


class TestFixtureHygiene(unittest.TestCase):
    def test_no_committed_secret_shapes(self):
        self.assertTrue(PATTERNS, "no scanner patterns")
        files = sorted(TESTS.rglob("*.py"))
        self.assertTrue(files, "no test files found")
        self.assertIn(pathlib.Path(__file__).resolve(), files)
        hits = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for num, line in enumerate(text.splitlines(), 1):
                for name, pattern in PATTERNS:
                    if pattern.search(line):
                        hits.append(f"{path.relative_to(TESTS.parent)}:{num}: {name}")
        self.assertEqual(hits, [], "provider-shaped literals in tests:\n" + "\n".join(hits))

    def test_patterns_still_match_runtime_values(self):
        # The split prefixes still join into full shapes, so the scan is real.
        from tests import secret_shapes as ss
        samples = [ss.AKIA + "ABCDEFGHIJKLMNOP", ss.XOXB + "1234567890",
                   ss.GLPAT + "abcdefghij0123456789", ss.AIZA + "B" * 35,
                   ss.SK_LIVE + "abcdef012345", ss.GHP + "A1b2C3d4E5F6",
                   ss.GITHUB_PAT + "abcdef012345", ss.SK_DASH + "abcdef012345",
                   ss.JWT + "a." + ss.JWT + "b.c", ss.pem("BEGIN", "RSA ")]
        self.assertEqual(len(samples), len(PATTERNS))
        for (name, pattern), sample in zip(PATTERNS, samples):
            self.assertTrue(sample)
            self.assertRegex(sample, pattern, name)
