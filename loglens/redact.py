"""The one redaction function. Everything loglens prints or writes passes
through redact(); there is deliberately no switch to turn it off.

Privacy boundary. Log Lens redacts:
- credentials and secrets: values of labelled keys, known provider token
  shapes, auth headers, URL passwords, PEM blocks and JWTs;
- session material: cookies, session ids, CSRF/refresh tokens, client secrets;
- e-mail addresses;
- payment card numbers (13-19 digits passing the Luhn check);
- values of explicitly labelled personal fields (phone, tel, mobile, dob,
  birth_date, ssn, patient_id, member_id, customer_id, mrn).
It does NOT promise general PII redaction: free-text names and postal
addresses are not detected. Do not run Log Lens on logs you would not show in
a terminal.
"""
import re

REDACTED = "<redacted>"

# A key is a whole identifier [A-Za-z0-9_.-]+ (case-insensitive). It is
# sensitive when it contains one of the long words anywhere (AWS_SECRET_ACCESS_KEY,
# refresh_token), or one of the short words as a whole component: bounded on
# both sides by the identifier's start/end, a non-letter or a camelCase hump.
# So db_pass=, X-Auth:, dbPass=, sas_sig= match; bypass=, oauth_state=,
# design=, passenger=, authority=, signal= do not. session and sid count only
# as the whole name or its last component (user_session, connect.sid; not
# session_count, sidebar); session_id, sessionid, jsessionid, PHPSESSID too.
_ID = r"[A-Za-z0-9_.-]"
_ID_START = r"(?<![A-Za-z0-9_.-])"
_LONG = (r"(?:password|passwd|passphrase|secret|token|api[_-]?key|credential"
         r"|private[_-]?key|access[_-]?key|account[_-]?key|signature"
         r"|csrf|xsrf|cookie)")
# Scoped case-sensitive: a lower-case letter directly before an upper-case one.
_HUMP = r"(?-i:(?<=[a-z])(?=[A-Z]))"
_LEFT = r"(?:(?<![A-Za-z])|" + _HUMP + r")"
_RIGHT = r"(?:(?![A-Za-z])|" + _HUMP + r")"
_SHORT = _LEFT + r"(?:pass|pwd|sig|auth)" + _RIGHT
# Labelled personal fields, as a whole component anywhere in the name
# (phone_number=, mobilePhone=, patient_id_hash=; never hotel= or telemetry=).
_PERSONAL = (_LEFT + r"(?:phone|telephone|tel|mobile|dob|birth[_-]?date"
             r"|date[_-]?of[_-]?birth|ssn|patient[_-]?id|member[_-]?id"
             r"|customer[_-]?id|mrn)" + _RIGHT)
_LAST = (r"(?:" + _LEFT + r"(?:session|sid)|sess(?:ion)?[_-]?id)"
         r"(?![A-Za-z0-9_.-])")
_KEYS = (_ID_START + _ID + r"*?(?:(?:" + _LONG + r"|" + _SHORT + r"|" + _PERSONAL
         + r")" + _ID + r"*+|" + _LAST + r")")
# Values that say something about the secret rather than being it.
_KEEP_WORDS = (r"(?:expired|invalid|missing|none|null|empty|true|false|present"
               r"|absent|revoked|ok)")
_VALUE_END = r"(?:[\s,;&\"'}\]]|\Z)"
_KEEP_VALUE = (r"(?![\"']?+" + _KEEP_WORDS + r"[\"']?+" + _VALUE_END + r")")
# The same for values that run to the end of the line.
_KEEP_LINE = r"(?!" + _KEEP_WORDS + r"[\"']?[ \t]*+(?:[\r\n]|\Z))"
# The marker as reports print it: plain, or HTML-escaped in md/html output.
_MARK = r"(?:<redacted>|&lt;redacted&gt;)"
# Skip a value that already is just the marker, up to where the rule would
# stop anyway. Replacing it would change nothing, except that an escaped
# marker would turn into a raw tag; so the final pass over a rendered report
# leaves it alone. Possessive quantifiers before it stop backtracking around it.
_DONE_AUTH = r"(?!" + _MARK + r"(?:[\"'\r\n]|\Z))"
_DONE_KEY = (r"(?!(?P<dq>[\"'])" + _MARK + r"(?P=dq)"
             r"|" + _MARK + r"(?:[\s,;&\"'}\]]|\Z))")


def _scheme_token(match):
    """Redact the token after Basic/Bearer when it looks like one: >= 8
    characters with a digit, one of + / =, or an upper-case letter after the
    first. "Basic authentication failed" and "Bearer token missing" stay."""
    token = match.group(1)
    if len(token) >= 8 and (any(c.isdigit() or c in "+/=" for c in token)
                            or any(c.isupper() for c in token[1:])):
        return match.group(0)[:match.start(1) - match.start(0)] + REDACTED
    return match.group(0)


def _luhn(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _is_card(groups, seps):
    """Card-number groups: 13-19 digits, first digit 2-6, Luhn-valid; either
    one run of digits, or one consistent space/dash between groups of 4 (the
    last 1-4) or 4-6-4/4-6-5."""
    digits = "".join(groups)
    if not (13 <= len(digits) <= 19 and digits[0] in "23456" and _luhn(digits)):
        return False
    if len(groups) == 1:
        return True
    if len(set(seps)) != 1 or len(groups[0]) != 4:
        return False
    sizes = [len(g) for g in groups[1:]]
    return (all(n == 4 for n in sizes[:-1]) and 1 <= sizes[-1] <= 4
            or sizes in ([6, 4], [6, 5]))


def _cards(match):
    """Redact each card number in a run of digit groups. A card must not touch
    another digit or a letter, so a run followed by a letter cannot end one."""
    parts = re.split(r"([ -])", match.group(0))
    groups, seps = parts[0::2], parts[1::2]
    end = len(groups) - (1 if match.string[match.end():match.end() + 1].isalpha() else 0)
    out, i = [], 0
    while i < len(groups):
        # The longest card starting at group i. Widening stops past 19
        # digits, so a long run of numbers stays linear.
        best, size = None, 0
        for j in range(i, end):
            size += len(groups[j])
            if size > 19:
                break
            if _is_card(groups[i:j + 1], seps[i:j]):
                best = j
        if best is None:
            out.append(groups[i])
            i += 1
        else:
            out.append(REDACTED)
            i = best + 1
        if i < len(groups):
            out.append(seps[i - 1])
    return "".join(out)


def _keep_quotes(match):
    quote = match.group("q2") or match.group("q1") or ""
    return match.group(1) + quote + REDACTED + quote


# (pattern, replacement) in order; replacements keep the label, drop the value.
_RULES = [
    # PEM blocks, including ones split over continuation lines or cut short.
    (re.compile(r"-----BEGIN [A-Z0-9 ]*-----.*?(?:-----END [A-Z0-9 ]*-----|\Z)",
                re.DOTALL), REDACTED),
    # Authorization header value (any scheme), plain or quoted.
    (re.compile(r"(?i)(authorization[\"']?\s*+[:=]\s*+[\"']?+)" + _DONE_AUTH
                + _KEEP_LINE + r"[^\"'\r\n]+"),
     r"\1" + REDACTED),
    # Cookie: / Set-Cookie: / cookie= values run to the end of the line; a
    # quoted value is left to the key rule below.
    (re.compile(r"(?i)(" + _ID_START + r"(?:set-)?cookie[\"']?[ \t]*+[:=][ \t]*+)"
                + _DONE_AUTH + _KEEP_LINE + r"(?=[^\"'\s])[^\r\n]+"),
     r"\1" + REDACTED),
    # "Basic <token>" / "Bearer <token>" anywhere; see _scheme_token.
    (re.compile(r"(?i)\b(?:basic|bearer)[ \t]++([A-Za-z0-9._~+/=-]+)"), _scheme_token),
    # key=value, key: value, "key": "value", 'key': 'value' (quotes kept). A
    # scheme word before the value ("X-Auth: Bearer abc") goes with it.
    (re.compile(r"(?i)(" + _KEYS + r"[\"']?\s*+[:=]\s*+)" + _DONE_KEY + _KEEP_VALUE
                + r"(?:(?:basic|bearer|digest|token)[ \t]++(?=[^\s,;&\"'}\]]))?"
                + r"(?:(?P<q2>\")(?:[^\"\\\r\n]|\\.)*\"|(?P<q1>')(?:[^'\\\r\n]|\\.)*'"
                r"|[^\s,;&\"'}\]]+)"),
     _keep_quotes),
    # URL userinfo (any scheme): scheme://user:secret@host -> scheme://<redacted>@host.
    # Runs before the e-mail rule so "user:secret@host" is not taken for an address.
    (re.compile(r"\b([A-Za-z][A-Za-z0-9+.-]*://)(?!" + _MARK + r"@)[^\s/?#\"'<>]+@"),
     r"\1" + REDACTED + "@"),
    # E-mail addresses, so they are gone with --raw too.
    (re.compile(r"\b[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b"), "<email>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"), REDACTED),
    # Provider token shapes, wherever they appear.
    (re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"), REDACTED),
    (re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}"), REDACTED),
    (re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}"), REDACTED),
    (re.compile(r"(?<![A-Za-z0-9])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), REDACTED),
    # Card numbers inside runs of digit groups; see _cards.
    (re.compile(r"(?<![A-Za-z0-9])\d++(?:[ -]\d++)*+"), _cards),
]


def redact(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_lines(lines):
    """Redact a run of lines as one text, so multi-line secrets are caught."""
    if not lines:
        return []
    return redact("\n".join(lines)).split("\n")
