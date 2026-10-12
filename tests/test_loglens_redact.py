"""Table-driven redaction tests for Log Lens M1.2 (BRIEF-DEVWS-021).

Each row is (input, expected). Every row also checks that the output is
non-empty and that redact() leaves its own output unchanged.
"""
import pathlib
import unittest

import loglens.redact
from loglens.redact import REDACTED as R, redact

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOUNDARY = "Do not run Log Lens on logs you would not show in a terminal."


class RedactTable(unittest.TestCase):
    def check_rows(self, rows):
        for text, expected in rows:
            with self.subTest(text=text):
                out = redact(text)
                self.assertTrue(out, "redact() returned empty output")
                self.assertEqual(out, expected)
                self.assertEqual(redact(out), out, "not idempotent")


class TestPrivacyBoundary(unittest.TestCase):
    def test_boundary_documented(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        doc = " ".join(loglens.redact.__doc__.split())
        for text in (" ".join(readme.split()), doc):
            self.assertTrue(text)
            self.assertIn(BOUNDARY, text)
            self.assertIn("free-text names and postal addresses are not detected", text)
            self.assertIn("Luhn", text)
