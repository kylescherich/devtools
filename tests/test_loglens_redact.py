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


# Rows every item's table also carries: timestamps, ports, durations, request ids.
ORDINARY = [
    ("at 2026-03-01T10:00:00Z ok", "at 2026-03-01T10:00:00Z ok"),
    ("at 2026-03-01 10:00:00,123 ok", "at 2026-03-01 10:00:00,123 ok"),
    ("ts=1767225600123 ok", "ts=1767225600123 ok"),
    ("connect host.example.test:8080", "connect host.example.test:8080"),
    ("took 1234 ms", "took 1234 ms"),
    ("request req-8f3a2c1d9e7b done", "request req-8f3a2c1d9e7b done"),
]


class TestCredentials(RedactTable):
    def test_item1_credentials(self):
        gh36 = "ghp_" + "A1b2C3d4E5" * 3 + "F6g7H8"
        self.check_rows(ORDINARY + [
            # a. name-contains keys
            ("AWS_SECRET_ACCESS_KEY=SNTa1", f"AWS_SECRET_ACCESS_KEY={R}"),
            ("client_secret=SNTa2 x", f"client_secret={R} x"),
            ("refresh_token: SNTa3", f"refresh_token: {R}"),
            ("AccountKey=SNTa4;", f"AccountKey={R};"),
            ("sig=SNTa5&x=1", f"sig={R}&x=1"),
            ("sas_sig=SNTa6", f"sas_sig={R}"),
            ("db_pass=SNTa7", f"db_pass={R}"),
            ("dbPass=SNTa8", f"dbPass={R}"),
            ("X-Auth: SNTa9", f"X-Auth: {R}"),
            ("auth_header=SNTa10", f"auth_header={R}"),
            ("my.api-key=SNTa11", f"my.api-key={R}"),
            ("ApiKey: SNTa12", f"ApiKey: {R}"),
            ("private_key=SNTa13", f"private_key={R}"),
            ("accessKey=SNTa14", f"accessKey={R}"),
            ("request_signature=SNTa15", f"request_signature={R}"),
            ("user_credentials=SNTa16", f"user_credentials={R}"),
            ("DB_PASSWD=SNTa17", f"DB_PASSWD={R}"),
            ("MY_PASSPHRASE: SNTa18", f"MY_PASSPHRASE: {R}"),
            ("authToken=SNTa19", f"authToken={R}"),
            ("pwd=SNTa20", f"pwd={R}"),
            ('{"clientSecret": "SNT a21"}', f'{{"clientSecret": "{R}"}}'),
            ("{'x_api_key': 'SNTa22'}", f"{{'x_api_key': '{R}'}}"),
            ("X-Auth: Bearer weaktoken", f"X-Auth: {R}"),
            ("bypass=yes", "bypass=yes"),
            ("oauth_state=abc", "oauth_state=abc"),
            ("design=flat", "design=flat"),
            ("passenger=4", "passenger=4"),
            ("authority=local", "authority=local"),
            ("signal=TERM", "signal=TERM"),
            ("compass=north", "compass=north"),
            ("PASSENGER=4", "PASSENGER=4"),
            ("tokens: 5", f"tokens: {R}"),          # name-contains: accepted over-redaction
            # b. provider shapes anywhere
            ("key AKIAABCDEFGHIJKLMNOP used", f"key {R} used"),
            ("key ASIAABCDEFGHIJKLMNOP used", f"key {R} used"),
            ("slack xoxb-1234567890-abcdef ok", f"slack {R} ok"),
            ("gitlab glpat-abcdefghij0123456789 ok", f"gitlab {R} ok"),
            ("google AIza" + "B" * 35 + " ok", f"google {R} ok"),
            ("stripe sk_live_abcdef012345 ok", f"stripe {R} ok"),
            ("stripe rk_test_abcdef012345 ok", f"stripe {R} ok"),
            (f"github {gh36} ok", f"github {R} ok"),
            ("github github_pat_abcdef0123456789 ok", f"github {R} ok"),
            ("openai sk-abcdef0123456789 ok", f"openai {R} ok"),
            # c. Basic / Bearer anywhere
            ("sent Basic dXNlcjpwYXNz ok", f"sent Basic {R} ok"),
            ("sent Bearer abc123def456 ok", f"sent Bearer {R} ok"),
            ("sent bearer abcdefgHij ok", f"sent bearer {R} ok"),
            ("sent Basic YWxhZGRpbjpvcGVu+= ok", f"sent Basic {R} ok"),
            ("Basic authentication failed", "Basic authentication failed"),
            ("Bearer token missing", "Bearer token missing"),
            ("Bearer abc1", "Bearer abc1"),
            ("a basic setup", "a basic setup"),
        ])
