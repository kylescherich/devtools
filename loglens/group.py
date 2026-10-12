"""Normalise redacted messages into signatures and group entries by them."""
import ipaddress
import re
from dataclasses import dataclass, field

from loglens.readers import LEVEL_RANK
from loglens.redact import redact, redact_lines

MAX_EXAMPLES = 2
MAX_EXAMPLE_LINES = 20

_URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
_EMAIL = re.compile(r"\b[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
_UNIX_PATH = re.compile(r"(?<![\w.:/~-])(?:~|\.{1,2})?(?:/[\w.@%+-]+){2,}/?")
_WIN_PATH = re.compile(r"(?:\b[A-Za-z]:|\\)(?:\\[\w.@%+-]+){2,}\\?")
_HEX = re.compile(r"\b(?:0x)?(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b")
_STR = re.compile(r"\"(?:[^\"\\]|\\.)*\"|(?<!\w)'(?:[^'\\]|\\.)*'(?!\w)")
_NUM = re.compile(r"\d+(?:\.\d+)?")

_TRACEBACK = "Traceback (most recent call last):"
_FRAME = re.compile(r'^\s*File "([^"]*)", line (\d+), in (.+?)\s*$')
_EXC_LINE = re.compile(r"^([A-Za-z_][\w.]*)(?::|$)")


def _valid_ip(match):
    try:
        ipaddress.ip_address(match.group(0))
    except ValueError:
        return match.group(0)
    return "<ip>"


def normalise(message):
    """Replace variable parts with placeholders, in a fixed order."""
    text = _URL.sub("<url>", message)
    text = _EMAIL.sub("<email>", text)
    text = _UUID.sub("<uuid>", text)
    text = _IPV4.sub(_valid_ip, text)
    text = _IPV6.sub(_valid_ip, text)
    text = _UNIX_PATH.sub("<path>", text)
    text = _WIN_PATH.sub("<path>", text)
    text = _HEX.sub("<hex>", text)
    text = _STR.sub("<str>", text)
    text = _NUM.sub("<n>", text)
    return text


def traceback_signature(lines):
    """'<ExcType> at <basename>:<line> in <func>' or None if no traceback."""
    if not any(line.strip() == _TRACEBACK for line in lines):
        return None
    frame, exc_type = None, None
    for line in lines:
        match = _FRAME.match(line)
        if match:
            frame = match.groups()
            continue
        if line[:1].isspace() or line.strip() == _TRACEBACK:
            continue
        match = _EXC_LINE.match(line)
        if match and frame is not None:
            exc_type = match.group(1)
    if frame is None:
        return None
    path, line_no, func = frame
    basename = re.split(r"[\\/]", path)[-1]
    return f"{exc_type or '<unknown>'} at {basename}:{line_no} in {func}"


@dataclass
class Group:
    level: str
    signature: str
    count: int = 0
    first_ts: object = None
    last_ts: object = None
    files: list = field(default_factory=list)
    examples: list = field(default_factory=list)   # redacted; rendered only with --raw


def _redacted_signature(entry):
    message = redact(entry.message)
    continuation = redact_lines(entry.continuation_lines)
    return message, continuation, traceback_signature(continuation) or normalise(message)


def entry_signature(entry):
    """The signature an entry is grouped under (computed from redacted text)."""
    return _redacted_signature(entry)[2]


def group_entries(entries, *, keep_examples=False):
    """Group entries by (level, signature). Messages are redacted before
    normalising; examples are kept only when keep_examples is set."""
    groups = {}
    for entry in entries:
        message, continuation, signature = _redacted_signature(entry)
        key = (entry.level, signature)
        group = groups.get(key)
        if group is None:
            group = groups[key] = Group(entry.level, signature)
        group.count += 1
        if entry.ts is not None:
            if group.first_ts is None or entry.ts < group.first_ts:
                group.first_ts = entry.ts
            if group.last_ts is None or entry.ts > group.last_ts:
                group.last_ts = entry.ts
        if entry.file not in group.files:
            group.files.append(entry.file)
        if keep_examples and len(group.examples) < MAX_EXAMPLES:
            lines = [message, *continuation][:MAX_EXAMPLE_LINES]
            group.examples.append(redact("\n".join(lines)))
    return sorted(groups.values(),
                  key=lambda g: (-LEVEL_RANK.get(g.level, 0), -g.count, g.signature))
