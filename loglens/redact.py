"""The one redaction function. Everything loglens prints or writes passes
through redact(); there is deliberately no switch to turn it off."""
import re

REDACTED = "<redacted>"

_KEYS = r"(?:password|passwd|secret|token|api_key|api-key|apikey)"


def _keep_quotes(match):
    quote = match.group(2) or match.group(3) or ""
    return match.group(1) + quote + REDACTED + quote


# (pattern, replacement) in order; replacements keep the label, drop the value.
_RULES = [
    # PEM blocks, including ones split over continuation lines or cut short.
    (re.compile(r"-----BEGIN [A-Z0-9 ]*-----.*?(?:-----END [A-Z0-9 ]*-----|\Z)",
                re.DOTALL), REDACTED),
    # Authorization header value (any scheme), plain or quoted.
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)[^\"'\r\n]+"),
     r"\1" + REDACTED),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1" + REDACTED),
    # key=value, key: value, "key": "value", 'key': 'value' (quotes kept).
    (re.compile(r"(?i)(" + _KEYS + r"[\"']?\s*[:=]\s*)"
                r"(?:(\")(?:[^\"\\\r\n]|\\.)*\"|(')(?:[^'\\\r\n]|\\.)*'"
                r"|[^\s,;&\"'}\]]+)"),
     _keep_quotes),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{10,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"), REDACTED),
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), REDACTED),
    # 16-digit numbers: plain, or 4-4-4-4 with one consistent space/dash.
    (re.compile(r"(?<![\d])\d{4}([ -]?)\d{4}\1\d{4}\1\d{4}(?![\d])"), REDACTED),
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
