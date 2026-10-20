"""Differential test: loglens.redact.redact() == the frozen reference
(BRIEF-DEVWS-027).

tests/redact_reference.py is loglens/redact.py as of 5f30924, before the
pre-checks; it is loaded from its path, never imported as a package module,
and its SHA-256 is pinned. Every input below must give byte-identical output
from both. Inputs: everything the existing loglens tests pass to redact()
(recorded while running them), every provider shape after every separator,
cards, e-mails, URL userinfo, Basic/Bearer, PEM, JWTs, every keyword
alternative of the gated rules in every form and case variant, lines that
contain a gate's literal but no secret, and >= 20,000 seeded random lines.

Values are synthetic and built at runtime (DEC-193/DEC-195). A failure
reports counts and indices only, never an input or an output.
"""
import functools
import hashlib
import importlib.util
import pathlib
import random
import unittest

import loglens.cli
import loglens.group
import loglens.redact
import loglens.report
import loglens.timeline
from tests import secret_shapes as ss
from tests import test_loglens_redact
from tests.test_redact_gates import RULE_KEYWORDS, alternatives, case_variants

HERE = pathlib.Path(__file__).resolve().parent
REFERENCE_PATH = HERE / "redact_reference.py"
REFERENCE_SHA256 = "242a671110f8ef7b3be61c078375030dcbf4a759a41be148680844f370161f97"
SEED = 27027
RANDOM_LINES = 22000
RANDOM_TEXTS = 2000

LONG_S, KELVIN, DOTTED_I = chr(0x17F), chr(0x212A), chr(0x130)
UNICODE_BITS = [
    "caf" + chr(0xE9), chr(0x4E2D) + chr(0x6587), chr(0x1F600), LONG_S, KELVIN,
    DOTTED_I, chr(0xA0), chr(0x200F), chr(0x202E) + "abc", chr(0x3000),
    "".join(chr(0x660 + d) for d in (4, 1, 1, 1)), chr(0xFF14) + chr(0xFF11),
    "na" + chr(0x308) + "ive", chr(0xDF) + "n", "\t", "\x00", "\x1b[31m",
]
ORDINARY_BITS = [
    "2026-10-14T10:00:00.123Z", "2026-10-14 10:00:00,123", "INFO", "ERROR",
    "[api.handlers]", "GET /api/v1/orders 200 in 12ms", "status=200",
    "user=user42", "latency_ms=87", "host=10.0.3.4 port=5432", "took 1234 ms",
    "request req-8f3a2c1d9e7b done", "retry 3 of 5", "worker-7", "key orders:991",
    "SELECT id FROM items WHERE id = ?", "pool_size=16", "bypass=1", "design=x",
    "passenger=3", "authority=x", "signal=9", "hotel=7", "telemetry=on",
    "session_count=4", "sidebar=left", "monkey=1", "keyboard=us", "tokens 12",
    "ts=1767225600123", "v1.2.3", "0x7ffe", "a=b", "x: y", "@", "://",
]
SEPARATORS = [" ", " ", " ", "", ",", ", ", ";", "&", "=", ":", "\t", '"', "'",
              "/", "_", "-", "|"]


@functools.lru_cache(maxsize=None)
def reference():
    spec = importlib.util.spec_from_file_location("_redact_reference", REFERENCE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def with_check_digit(body):
    for d in "0123456789":
        if luhn_ok(body + d):
            return body + d
    raise AssertionError("no check digit")


def grouped(digits, sizes, sep):
    out, i = [], 0
    for n in sizes:
        out.append(digits[i:i + n])
        i += n
    if i < len(digits):
        out.append(digits[i:])
    return sep.join(out)


def to_digits(digits, zero):
    return "".join(chr(zero + int(d)) for d in digits)


# --- corpus parts ---------------------------------------------------------

@functools.lru_cache(maxsize=None)
def recorded_inputs():
    """Every text the existing loglens tests pass to redact(): the tests are
    run once with redact() wrapped by a recorder. Returns (inputs, result)."""
    real = loglens.redact.redact
    seen = []

    def recorder(text):
        seen.append(text)
        return real(text)

    names = ["tests.test_loglens_redact", "tests.test_loglens_report",
             "tests.test_loglens_group", "tests.test_loglens_e2e",
             "tests.test_loglens_cli"]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    modules = [loglens.redact, loglens.cli, loglens.report, loglens.group,
               loglens.timeline]
    modules += [importlib.import_module(name) for name in names]
    patched = [m for m in modules if getattr(m, "redact", None) is real]
    result = unittest.TestResult()
    try:
        for module in patched:
            module.redact = recorder
        suite.run(result)
    finally:
        for module in patched:
            module.redact = real
    return tuple(dict.fromkeys(seen)), result


def provider_tokens():
    tokens = dict(test_loglens_redact.glued_shapes())
    for name in ("GHP",):
        for kind in "pousr":
            tokens["GH" + kind] = "gh" + kind + "_" + "SNTgluedGitHub"
    tokens["XOX_ALL"] = ss.XOXB[:3] + "p-" + "SNTglued-Slack"
    tokens["SK_TEST"] = ss.SK_LIVE.replace("live", "test") + "SNTgluedStripe"
    tokens["RK_LIVE"] = ss.RK_TEST.replace("test", "live") + "SNTgluedStripe"
    tokens["JWT_EMPTY_SIG"] = ss.JWT + "SNTa." + ss.JWT + "SNTb."
    return tokens


def glued_shape_inputs():
    prefixes = set(test_loglens_redact.TestGluedTokens.PREFIXES)
    prefixes |= {"-", "/", ".", "k", "K", "1", "x_", "a-", "(", "<", "\t"}
    suffixes = [" end", "", ".", "_x", "-x", "x", "1", '"', "\n"]
    out = []
    for token in provider_tokens().values():
        for prefix in sorted(prefixes):
            for suffix in suffixes:
                out.append(prefix + token + suffix)
        # Too short and cut versions: the literal is there, the shape is not.
        for cut in (3, 6, 9, 12, len(token) - 1):
            out.append("x " + token[:cut] + " y")
    return out


def card_inputs():
    rng = random.Random(SEED + 1)
    out = []
    for length in range(12, 21):
        for first in "1234567":
            body = first + "".join(rng.choice("0123456789") for _ in range(length - 2))
            valid = with_check_digit(body)
            invalid = valid[:-1] + str((int(valid[-1]) + 1) % 10)
            for number in (valid, invalid):
                forms = [number, grouped(number, [4] * 4, " "),
                         grouped(number, [4] * 4, "-"),
                         grouped(number, [4, 6], " "), grouped(number, [4, 6], "-"),
                         grouped(number, [4, 4], " ").replace(" ", "-", 1),
                         grouped(number, [4] * 4, "  "),
                         grouped(number, [2, 4, 4], " ")]
                for form in forms:
                    out += [form, "card " + form + " ok", "x" + form, form + "a",
                            "0" + form, form + " 34", "12 " + form, "#" + form + ".",
                            "id=" + form, to_digits(form.replace(" ", "").replace("-", ""), 0x660),
                            to_digits(number, 0xFF10), number[:6] + to_digits(number[6:], 0x660)]
    return out


def misc_secret_inputs():
    pem_rsa = ss.pem("BEGIN", "RSA ") + "\nMIISNTbase64body\n" + ss.pem("END", "RSA ")
    out = [
        pem_rsa, "key:\n" + pem_rsa + "\nafter", ss.pem("BEGIN") + "\ncut short",
        ss.pem("BEGIN").lower() + "\nbody\n" + ss.pem("END").lower(),
        ss.pem("BEGIN", "rsa ").lower(), ss.pem("BEGIN", "Rsa ").title(),
        "-----begin rsa private key-----\nMIIabc\n-----end rsa private key-----",
        "-----Begin RSA Private Key-----", "-----BEGIN -----x", "-----BEGIN",
        "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----",
    ]
    for user in ("user", "a.b+c", "UPPER", "x_y-z", "n" + chr(0xE9)):
        for host in ("example.test", "mail.example.test", "EXAMPLE.TEST", "x-y.test"):
            address = user + "@" + host
            out += [address, "to <" + address + ">", "from=" + address + ";",
                    "mailto:" + address, "https://" + address + "/x",
                    address + ".", "@" + address, address + "@" + host]
    for url in ("https://user:SNTpw@host.test/p", "ftp://u:p@h.test:21",
                "git+ssh://git@host.test/r", "ssh://git@host.test", "postgres://a:b@db/x",
                "https://<redacted>@host.test", "https://&lt;redacted&gt;@h.test",
                "https://user@host.test", "https://host.test/@x:y",
                "HTTPS://U:P@H.TEST", "x://:@h", "http://a:b@c@d.test",
                "1http://a:b@c", "redis://:SNTpw@cache:6379/0"):
        out += [url, "connect " + url + " ok", '"' + url + '"']
    for scheme in ("Basic", "Bearer", "basic", "BEARER", "bearer", "Digest", "Token"):
        for token in ("SNTabcdefghijklmnop", "SNTa1b2c3", "short", "abcdefgh",
                      "abcdefgH", "dXNlcjpwYXNz", "a+b/c=dd", "<redacted>", "SNT a b"):
            out += [scheme + " " + token, "Authorization: " + scheme + " " + token,
                    "x-auth: " + scheme + " " + token, scheme + "  " + token,
                    scheme + "\t" + token, scheme + " " + scheme + " " + token]
    jwt = ss.JWT + "SNTa." + ss.JWT + "SNTb.SNTc"
    out += [jwt, "token=" + jwt, "Bearer " + jwt, "x" + jwt, "_" + jwt,
            ss.JWT + "only", ss.JWT + "a.b", ss.JWT + ".."]
    for header in ("Cookie", "Set-Cookie", "cookie", "COOKIE", "set-cookie"):
        out += [header + ": a=1; b=SNTv", header + ':"q"', header + ": none",
                header + "=SNTv", header + ": <redacted>", header + ":"]
    out += ["Authorization: none", "authorization='SNT q'", "Authorization:",
            "AUTHORIZATION=SNTv\nnext=1", "Authorization: &lt;redacted&gt;"]
    return out


KEYWORD_FORMS_WITH_VALUE = [
    "{k}=SNTv1", "{k}: SNTv2", '"{k}": "SNT v3"', "'{k}': 'SNT v4'", "{k} = SNTv5,x=1",
    "{k}=my secret phrase user=bob", "{k}: two words; next=1", "{k} is SNTv6",
    '{k} was "SNT v7"', "{k} were 'a b'", "{k} are SNTv8.", "X-{k}: Bearer SNTabc123456",
    "db_{k}=SNTv9", "{k}_hash=SNTv10", "my{K}=SNTv11", "{k}.x=SNTv12&y=2",
    "{k}:\"SNT\\\"v\"", "{k}=SNTv13\n{k}=SNTv14", "[{k}=SNTv15]", "{{{k}: SNTv16}}",
]
KEYWORD_FORMS_WITHOUT_VALUE = [
    "{k}", "{k}=", "{k}:", "{k} is", "{k}=none", "{k}: null", '{k}="expired"',
    "{k}=<redacted>", '"{k}": "&lt;redacted&gt;"', "{k} is <redacted>",
    "no {k} here", "{k}_count=3", "{k}s", "x{k}y=1", "{k} was ok", "{k}=true; a=b",
]


def keyword_variants():
    words = set()
    for _gate, sources in RULE_KEYWORDS.values():
        for source in sources:
            words.update(alternatives(source))
    return sorted({v for w in words for v in case_variants(w)})


def keyword_inputs():
    out = []
    for word in keyword_variants():
        cap = word[:1].upper() + word[1:]
        for form in KEYWORD_FORMS_WITH_VALUE + KEYWORD_FORMS_WITHOUT_VALUE:
            out.append(form.format(k=word, K=cap))
    return out


# The words the tuning replaced by "key", and "/api/" paths (BRIEF-DEVWS-027).
TUNED_WORDS = ["api_key", "apikey", "api-key", "API_KEY", "ApiKey", "apiKey",
               "x-api-key", "access_token", "accessToken", "access_key", "AccessKey",
               "private_key", "privateKey", "private-key", "account_key", "AccountKey",
               "api", "access", "private", "account", "key", "keyboard", "monkey",
               "Kelvin" + KELVIN + "ey", "api_" + KELVIN + "ey", "acce" + LONG_S + "s_key"]


def tuned_inputs():
    out = []
    for word in TUNED_WORDS:
        out += ["GET /api/v1/items?" + word + "=SNTv1&x=1", "GET /api/v1/items?" + word + "=",
                word + "=SNTv2", word + ": SNTv3", word + "=", word + ":", word,
                "POST /api/v2/" + word + " 200 in 5ms", "/api/" + word + "/rotate",
                word + " is SNTv4", '{"' + word + '": "SNT v5"}', word + "=none",
                "GET /api/v1/orders " + word + " 200", "path=/api/" + word]
    out += ["GET /api/v1/orders 200", "GET /api/ ok", "/api/", "api:", "api=1",
            "GET /api/v1/users/42?page=2&sort=asc 200 in 3ms"]
    return out


def gate_literal_inputs():
    """Each gate's literal with no secret behind it, and texts where only
    one gate condition holds."""
    literals = ["-----BEGIN ", "-----BEGIN", "-----", "@", "://", "a@b", "x://y",
                "github_pat_", "github_pat_short", "ghp_", "gho_x", "ghu_", "ghs_", "ghr_",
                "gh_", "sk-", "sk-short", "AKIA", "ASIA", "AKIAshort", "xox", "xoxz-",
                "glpat-", "glpat-short", "AIza", "AIzashort", "sk_", "rk_", "sk_live_",
                "rk_test_x", "eyJ", "eyJx", "eyJ.a", ":", "=", "pass", "secret",
                "credential", "token", "key", "sig", "csrf", "xsrf", "cookie", "pwd",
                "auth", "phone", "tel", "mobile", "dob", "birth", "ssn", "patient",
                "member", "customer", "mrn", "sess", "sid", "basic", "bearer",
                "1234567890123", "123456789012", "1 2 3 4 5 6 7 8 9 0 1 2 3",
                "2026-10-14 10:00:00", "authorization", "set-cookie"]
    out = []
    for literal in literals:
        out += [literal, "x " + literal + " y", literal + literal, literal * 3,
                "INFO " + literal + " done in 12ms", literal.upper(), literal.lower()]
    return out


def random_lines():
    rng = random.Random(SEED)
    pools = [ORDINARY_BITS * 3, UNICODE_BITS, glued_shape_inputs(), card_inputs(),
             misc_secret_inputs(), keyword_inputs(), tuned_inputs(),
             gate_literal_inputs()]
    weights = [6, 2, 2, 2, 2, 3, 2, 2]
    lines = []
    for _ in range(RANDOM_LINES):
        parts = []
        for _ in range(rng.randint(1, 6)):
            pool = rng.choices(pools, weights)[0]
            parts.append(rng.choice(pool))
            parts.append(rng.choice(SEPARATORS))
        lines.append("".join(parts[:-1]))
    texts = []
    for _ in range(RANDOM_TEXTS):
        texts.append(rng.choice(["\n", "\r\n"]).join(
            rng.choice(lines) for _ in range(rng.randint(2, 5))))
    return lines, texts


# --- the test -------------------------------------------------------------

class TestRedactDifferential(unittest.TestCase):
    def assert_identical(self, inputs, minimum):
        inputs = list(inputs)
        self.assertGreaterEqual(len(inputs), minimum)
        ref = reference().redact
        new = loglens.redact.redact
        differ, empty = [], []
        for i, text in enumerate(inputs):
            out = new(text)
            if text and not out:
                empty.append(i)
            if out != ref(text):
                differ.append(i)
        # Indices only: an input or output may hold a synthetic secret.
        self.assertEqual(empty, [], f"{len(empty)} empty outputs")
        self.assertEqual(differ[:20], [],
                         f"{len(differ)} of {len(inputs)} inputs differ from the reference")
        return len(inputs)

    def test_reference_is_frozen(self):
        data = REFERENCE_PATH.read_bytes()
        self.assertTrue(data)
        self.assertEqual(hashlib.sha256(data).hexdigest(), REFERENCE_SHA256)
        self.assertIsNot(reference().redact, loglens.redact.redact)

    def test_existing_test_inputs(self):
        inputs, result = recorded_inputs()
        self.assertGreater(result.testsRun, 0)
        self.assertTrue(result.wasSuccessful(), "recording run of loglens tests failed")
        self.assert_identical(inputs, 300)

    def test_provider_shapes_after_every_separator(self):
        self.assert_identical(glued_shape_inputs(), 2000)

    def test_cards(self):
        self.assert_identical(card_inputs(), 10000)

    def test_emails_urls_auth_pem_jwt_cookies(self):
        self.assert_identical(misc_secret_inputs(), 400)

    def test_keyword_alternatives_every_form_and_case(self):
        self.assert_identical(keyword_inputs(), 10000)

    def test_tuned_key_words_and_api_paths(self):
        self.assert_identical(tuned_inputs(), 300)

    def test_gate_literals_without_secret(self):
        self.assert_identical(gate_literal_inputs(), 400)

    def test_random_lines(self):
        lines, texts = random_lines()
        self.assertGreaterEqual(len(lines), 20000)
        self.assert_identical(lines, 20000)
        self.assert_identical(texts, RANDOM_TEXTS)

    def test_corpus_fires_every_rule(self):
        """The corpus is only as good as what it reaches: every reference rule
        must change at least 10 inputs, and every gated rule must also be
        skipped by its gate on some input where the rule would not match."""
        lines, texts = random_lines()
        inputs = (list(recorded_inputs()[0]) + glued_shape_inputs() + card_inputs()
                  + misc_secret_inputs() + keyword_inputs() + tuned_inputs()
                  + gate_literal_inputs() + lines[:5000])
        self.assertTrue(inputs)
        rules = reference()._RULES
        fired = [0] * len(rules)
        for text in inputs:
            for i, (pattern, replacement) in enumerate(rules):
                after = pattern.sub(replacement, text)
                if after != text:
                    fired[i] += 1
                text = after
        self.assertEqual(len(rules), len(loglens.redact._RULES))
        low = [i for i, n in enumerate(fired) if n < 10]
        self.assertEqual(low, [], f"rules fired: {fired}")
        skipped = [0] * len(rules)
        for text in inputs:
            for i, (gate, pattern, replacement) in enumerate(loglens.redact._RULES):
                if gate is None or gate(text):
                    text = pattern.sub(replacement, text)
                else:
                    skipped[i] += 1
        gated = [i for i, (gate, _, _) in enumerate(loglens.redact._RULES) if gate]
        rare = [i for i in gated if skipped[i] < 10]
        self.assertEqual(rare, [], f"gates skipped: {skipped}")


if __name__ == "__main__":
    unittest.main()
