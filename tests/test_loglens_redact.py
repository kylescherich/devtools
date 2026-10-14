"""Table-driven redaction tests for Log Lens M1.2 (BRIEF-DEVWS-021).

Each row is (input, expected). Every row also checks that the output is
non-empty and that redact() leaves its own output unchanged.
"""
import pathlib
import time
import unittest

import loglens.redact
from loglens.redact import REDACTED as R, redact
from tests import secret_shapes as ss

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
        gh36 = ss.GHP + "A1b2C3d4E5" * 3 + "F6g7H8"
        self.check_rows(ORDINARY + [
            # a. name-contains keys
            ("AWS_SECRET_ACCESS_KEY=SNTa1", f"AWS_SECRET_ACCESS_KEY={R}"),
            ("client_secret=SNTa2 x=1", f"client_secret={R} x=1"),
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
            ("key " + ss.AKIA + "ABCDEFGHIJKLMNOP used", f"key {R} used"),
            ("key " + ss.ASIA + "ABCDEFGHIJKLMNOP used", f"key {R} used"),
            ("slack " + ss.XOXB + "1234567890-abcdef ok", f"slack {R} ok"),
            ("gitlab " + ss.GLPAT + "abcdefghij0123456789 ok", f"gitlab {R} ok"),
            ("google " + ss.AIZA + "B" * 35 + " ok", f"google {R} ok"),
            ("stripe " + ss.SK_LIVE + "abcdef012345 ok", f"stripe {R} ok"),
            ("stripe " + ss.RK_TEST + "abcdef012345 ok", f"stripe {R} ok"),
            (f"github {gh36} ok", f"github {R} ok"),
            ("github " + ss.GITHUB_PAT + "abcdef0123456789 ok", f"github {R} ok"),
            ("openai " + ss.SK_DASH + "abcdef0123456789 ok", f"openai {R} ok"),
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

    def test_dec190_scheme_words(self):
        self.check_rows([
            # 1. a declined word is not consumed
            ("sent basic Basic dXNlcjpwYXNzd29yZA== ok", f"sent basic Basic {R} ok"),
            ("use bearer Bearer abc123def456 ok", f"use bearer Bearer {R} ok"),
            ("Bearer Bearer Bearer abc123def456", f"Bearer Bearer Bearer {R}"),
            # 2. >= 16 characters is redacted whatever the mix
            ("Bearer abcdefghijklmnop", f"Bearer {R}"),
            ("Bearer abcdefghijklmno", "Bearer abcdefghijklmno"),
            ("Basic authentication failed", "Basic authentication failed"),
            ("Bearer token missing", "Bearer token missing"),
            ("Basic configuration loaded", "Basic configuration loaded"),
        ])


class TestSession(RedactTable):
    def test_item2_session_material(self):
        self.check_rows(ORDINARY + [
            ("csrf=SNTb1 ok", f"csrf={R} ok"),
            ("X-CSRF-Token: SNTb2", f"X-CSRF-Token: {R}"),
            ("xsrf_token=SNTb3", f"xsrf_token={R}"),
            ("XSRF-TOKEN=SNTb4;", f"XSRF-TOKEN={R};"),
            ("csrfmiddlewaretoken=SNTb5", f"csrfmiddlewaretoken={R}"),
            ("session=SNTb6 ok", f"session={R} ok"),
            ("session_id=SNTb7 ok", f"session_id={R} ok"),
            ("sessionid=SNTb8", f"sessionid={R}"),
            ("JSESSIONID=SNTb9", f"JSESSIONID={R}"),
            ("PHPSESSID=SNTb10", f"PHPSESSID={R}"),
            ("user_session=SNTb11", f"user_session={R}"),
            ("userSession: SNTb12", f"userSession: {R}"),
            ("connect.sid=SNTb13", f"connect.sid={R}"),
            ("sid=SNTb14", f"sid={R}"),
            ("x_sid=SNTb15", f"x_sid={R}"),
            ('{"session_id": "SNT b16"}', f'{{"session_id": "{R}"}}'),
            ("refresh_token=SNTb17", f"refresh_token={R}"),
            ("client_secret: SNTb18", f"client_secret: {R}"),
            ("auth_cookie=SNTb19 ok", f"auth_cookie={R} ok"),
            ("Cookie: theme=dark; sessionid=SNTb20; lang=en",
             f"Cookie: {R}"),
            ("Set-Cookie: JSESSIONID=SNTb21; Path=/; HttpOnly", f"Set-Cookie: {R}"),
            ("req cookie=a=1&b=SNTb22 x", f"req cookie={R}"),
            ('{"cookie": "a=SNTb23; b=2", "n": 1}', f'{{"cookie": "{R}", "n": 1}}'),
            ("Cookie: a=SNTb24\nnext line", f"Cookie: {R}\nnext line"),
            ("Cookie: none", "Cookie: none"),
            ("session_count=5", "session_count=5"),
            ("sessions=12", "sessions=12"),
            ("inside=3", "inside=3"),
            ("sidebar=1", "sidebar=1"),
            ("possession=1", "possession=1"),
            ("session ended", "session ended"),
        ])


class TestPersonal(RedactTable):
    def test_item3_personal_data(self):
        self.check_rows(ORDINARY + [
            # a. card numbers: 13-19 digits, first digit 2-6, Luhn-valid
            ("card 4111111111111111 ok", f"card {R} ok"),
            ("card 4000 0566 5566 5556 ok", f"card {R} ok"),
            ("card 5500-0000-0000-0004 ok", f"card {R} ok"),
            ("amex 3782-822463-10005 ok", f"amex {R} ok"),
            ("amex 378282246310005 ok", f"amex {R} ok"),
            ("visa13 4222222222222 ok", f"visa13 {R} ok"),
            ("card 6011111111111117,", f"card {R},"),
            ("mc 2223000048400011 ok", f"mc {R} ok"),
            ("19 4111 1111 1111 1111 003 ok", f"19 {R} ok"),
            ("id 4000123456789010 ok", "id 4000123456789010 ok"),       # not Luhn
            ("card 4000 1234 5678 9010 ok", "card 4000 1234 5678 9010 ok"),
            ("ts 1767225600123 ok", "ts 1767225600123 ok"),             # epoch ms
            ("ts 1111111111111111 ok", "ts 1111111111111111 ok"),       # first digit 1
            ("card 4111-1111 1111-1111 ok", "card 4111-1111 1111-1111 ok"),  # mixed
            ("ref 4111111111111111x", "ref 4111111111111111x"),         # touches letter
            ("ref x4111111111111111", "ref x4111111111111111"),
            ("n 41111111111111110 ok", "n 41111111111111110 ok"),       # 17, not Luhn
            ("port 8080 pid 31337 took 1234 ms", "port 8080 pid 31337 took 1234 ms"),
            # b. labelled personal fields
            ("phone=SNTc1 ok=1", f"phone={R} ok=1"),
            ("user_phone: SNTc2", f"user_phone: {R}"),
            ("mobilePhone=SNTc3", f"mobilePhone={R}"),
            ("tel=SNTc4", f"tel={R}"),
            ("Tel: SNTc5", f"Tel: {R}"),
            ("mobile=SNTc6", f"mobile={R}"),
            ("dob=1990-01-02 ok=1", f"dob={R} ok=1"),
            ("birth_date: 1990-01-02", f"birth_date: {R}"),
            ("birthDate=1990-01-02", f"birthDate={R}"),
            ("SSN=SNTc7", f"SSN={R}"),
            ("patient_id=SNTc8", f"patient_id={R}"),
            ("member_id=SNTc9", f"member_id={R}"),
            ("customerId=SNTc10", f"customerId={R}"),
            ("MRN: SNTc11", f"MRN: {R}"),
            ('{"phone": "+1 555 0100"}', f'{{"phone": "{R}"}}'),
            ("{'ssn': 'SNTc12'}", f"{{'ssn': '{R}'}}"),
            ("hotel=Ritz", "hotel=Ritz"),
            ("telemetry=on", "telemetry=on"),
            ("automobile=car", "automobile=car"),
            ("phone_number=+15550100 ok=1", f"phone_number={R} ok=1"),
            ("mobile_number: SNTc14", f"mobile_number: {R}"),
            ("telephone=SNTc15", f"telephone={R}"),
            ("date_of_birth=1990-01-02", f"date_of_birth={R}"),
            ("patient_id_hash=SNTc16", f"patient_id_hash={R}"),
            ("hotel_id=42", "hotel_id=42"),
            # whole component anywhere: accepted over-redaction, privacy over noise
            ("phone_count=2", f"phone_count={R}"),
            ("mobile_app_version=3.1", f"mobile_app_version={R}"),
            ("call me at 555 0100 John Smith", "call me at 555 0100 John Smith"),
        ])

    def test_item3_long_number_run_is_linear(self):
        text = " ".join(f"{1000 + i % 9000:04d}" for i in range(5000))
        start = time.perf_counter()
        out = redact(text)
        elapsed = time.perf_counter() - start
        self.assertTrue(out)
        self.assertLess(elapsed, 1.0)


class TestMultiWord(RedactTable):
    def test_item4_multi_word_values(self):
        self.check_rows(ORDINARY + [
            ("password=my secret phrase", f"password={R}"),
            ("password=a b c user=bob", f"password={R} user=bob"),
            ("password=a b c, user=bob", f"password={R}, user=bob"),
            ("password=a b c; user=bob", f"password={R}; user=bob"),
            ("password=a b c&user=bob", f"password={R}&user=bob"),
            ("db_password: two words\nnext=1", f"db_password: {R}\nnext=1"),
            ("PASSWD=x y z", f"PASSWD={R}"),
            ("passphrase=the quick fox user=a", f"passphrase={R} user=a"),
            ("client_secret=SNT d1 x", f"client_secret={R}"),
            ("user_credentials=a b level=info", f"user_credentials={R} level=info"),
            ("phone=+1 555 0100 user=bob", f"phone={R} user=bob"),
            ("dob=02 Jan 1990 ok", f"dob={R}"),
            ("patient_id=AB 12 34 ward=3", f"patient_id={R} ward=3"),
            ('password="a b" user=bob', f'password="{R}" user=bob'),
            ("password=<redacted> SNTd2 more", f"password={R}"),   # marker is no shield
            ("password=none user=bob", "password=none user=bob"),
            ("password=none of your business", f"password={R}"),
            ("token=a b", f"token={R} b"),          # single-token key: unchanged extent
            # spoken forms
            ("user said password is SNTd3 today", f"user said password is {R} today"),
            ("old password was SNTd4.", f"old password was {R}"),
            ("passphrase is SNTd5", f"passphrase is {R}"),
            ("credentials were SNTd6 then", f"credentials were {R} then"),
            ('secret is "SNT d7" ok', f'secret is "{R}" ok'),
            ("password is missing", "password is missing"),
            ("password was invalid", "password was invalid"),
            ("password is incorrect", f"password is {R}"),   # accepted over-redaction
            ("password reset requested", "password reset requested"),
        ])


class TestPreservation(RedactTable):
    def test_item5_preservation(self):
        keep_words = ("expired invalid missing none null empty true false present "
                      "absent revoked ok").split()
        self.check_rows(ORDINARY + [
            # URL userinfo only when it holds a password; never taken for an e-mail
            ("ssh://git@host.example.test/r.git", "ssh://git@host.example.test/r.git"),
            ("ssh://git@host.example.test:22/r.git", "ssh://git@host.example.test:22/r.git"),
            ("https://deploy.bot@host.example.test/x", "https://deploy.bot@host.example.test/x"),
            ("https://u:SNTe1@db.example.test/x", f"https://{R}@db.example.test/x"),
            ("postgres://app:SNTe2@ss@10.0.0.5:5432/db", f"postgres://{R}@10.0.0.5:5432/db"),
            ("redis://:SNTe3@cache.example.test:6379", f"redis://{R}@cache.example.test:6379"),
            # keep-words, any key-based rule, case-insensitive
            *[(f"token={w}", f"token={w}") for w in keep_words],
            ("csrf=missing", "csrf=missing"),
            ("TOKEN=EXPIRED user=bob", "TOKEN=EXPIRED user=bob"),
            ('{"token": "expired"}', '{"token": "expired"}'),
            ("password=none", "password=none"),
            ("session_id=null ok", "session_id=null ok"),
            ("phone=absent", "phone=absent"),
            ("Authorization: missing", "Authorization: missing"),
            ("Cookie: empty", "Cookie: empty"),
            ("password is revoked", "password is revoked"),
            ("token=expired2", f"token={R}"),
            ("token=okay", f"token={R}"),
            ("password=missing secret", f"password={R}"),
            # accepted over-redaction (DEC-183) and service identities
            ("auth: login ok", f"auth: {R} ok"),
            ("svc-deploy@proj.iam.example.test denied", "<email> denied"),
            ("mailto:ops@example.test", "mailto:<email>"),
        ])


# BRIEF-DEVWS-025: "_" is a word character, so a token glued to a name with
# "_" has no \b before it. Every provider shape, after every separator.
def glued_shapes():
    return {
        "AKIA": ss.AKIA + "SNTGLUEDKEYIDABC",
        "ASIA": ss.ASIA + "SNTGLUEDKEYIDABC",
        "XOXB": ss.XOXB + "SNTgluedSlack",
        "GLPAT": ss.GLPAT + "SNTgluedGitLabTokenAb",
        "AIZA": ss.AIZA + "S" * 35,
        "SK_LIVE": ss.SK_LIVE + "SNTgluedStripe",
        "RK_TEST": ss.RK_TEST + "SNTgluedStripe",
        "GHP": ss.GHP + "SNTgluedGitHub",
        "GITHUB_PAT": ss.GITHUB_PAT + "SNTgluedGitHub",
        "SK_DASH": ss.SK_DASH + "SNTgluedApiKey",
        "JWT": ss.JWT + "SNTa." + ss.JWT + "SNTb.SNTc",
    }


class TestGluedTokens(RedactTable):
    PREFIXES = ("_", "key_", "", "=", "x:", "a/", " ", "[", '"')

    def test_redacted_after_any_separator(self):
        shapes = glued_shapes()
        self.assertEqual(len(shapes), 11)
        for name, token in shapes.items():
            with self.subTest(shape=name):
                self.check_rows([(prefix + token + " end", prefix + R + " end")
                                 for prefix in self.PREFIXES])

    def test_no_over_redaction(self):
        words = ["task-force", "risk-free", "keyJar", "high_score",
                 "a task-based risk-aware keyJ high_ value"]
        self.check_rows([(w, w) for w in words]
                        + [("k" + token, "k" + token) for token in glued_shapes().values()])
