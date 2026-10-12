"""Read text and JSONL log files (optionally gzipped) into Entry records.

Files are opened read-only, never through a symlink, and decoded as UTF-8 with
errors="replace". Nothing here prints; callers render through loglens.redact.
"""
import codecs
import gzip
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DEFAULT_MAX_BYTES = 1024 ** 3          # 1 GB, total across all files
DEFAULT_MAX_LINES = 10_000_000         # total across all files
MAX_CONTINUATION = 200
TRUNCATED_MARKER = "...(truncated)"
LEVEL_WINDOW = 64
INVALID_KEEP = 3

ERROR, WARNING, INFO, DEBUG, UNKNOWN = "ERROR", "WARNING", "INFO", "DEBUG", "UNKNOWN"
# UNKNOWN sits below WARNING: "at least WARNING" never includes it.
LEVEL_RANK = {DEBUG: 0, UNKNOWN: 1, INFO: 2, WARNING: 3, ERROR: 4}
LEVEL_WORDS = {
    "ERROR": ERROR, "ERR": ERROR, "CRITICAL": ERROR, "FATAL": ERROR,
    "ALERT": ERROR, "EMERG": ERROR,
    "WARN": WARNING, "WARNING": WARNING,
    "INFO": INFO, "NOTICE": INFO,
    "DEBUG": DEBUG, "TRACE": DEBUG,
}
_WORDS = "|".join(sorted(LEVEL_WORDS, key=len, reverse=True))
# A level word counts only when it is UPPERCASE, or (any case) inside [..] or
# <..>, or right after level=/lvl=/severity= (optionally quoted). A plain
# lowercase word in a message ("no error") never sets the level.
_LEVEL_RE = re.compile(
    r"\b(?P<upper>" + _WORDS + r")\b"
    r"|[\[<]\s*(?i:(?P<bracket>" + _WORDS + r"))\s*[\]>]"
    r"|(?i:\b(?:level|lvl|severity)=[\"']?(?P<key>" + _WORDS + r"))\b")
# Any case followed directly by ':' as the first word after the timestamp.
_LEVEL_COLON_RE = re.compile(r"\s*(?i:(" + _WORDS + r")):")

_ISO_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:[.,](\d+))?"
    r"(Z|[+-]\d{2}(?::?\d{2})?)?(?![\d])", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}
_SYSLOG_RE = re.compile(r"(" + "|".join(_MONTHS) + r") +(\d{1,2}) (\d{2}):(\d{2}):(\d{2})\b")
# "host app[pid]:" or "host app:" right after a syslog timestamp.
_SYSLOG_TAG_RE = re.compile(r"\s+(?P<host>[^\s:\[\]]+) +(?P<app>[^\s:\[\]]+)"
                            r"(?:\[\d+\])?:(?=\s|$)")

FIELD_KEYS = {
    "ts": ("ts", "time", "timestamp", "@timestamp"),
    "level": ("level", "lvl", "severity"),
    "message": ("msg", "message"),
}
_JSONL_SUFFIXES = (".jsonl", ".ndjson")


class InputError(Exception):
    """Input missing, unreadable, a symlink, or over --max-bytes (exit 3)."""


class UsageError(Exception):
    """Bad option value (exit 2)."""


@dataclass
class Entry:
    ts: datetime | None
    level: str
    message: str
    continuation_lines: list = field(default_factory=list)
    file: str = ""
    line_no: int = 0
    truncated: bool = False
    host: str | None = None       # syslog "host app[pid]:" header, if any
    app: str | None = None

    def add_continuation(self, line):
        if self.truncated:
            return
        if len(self.continuation_lines) >= MAX_CONTINUATION:
            self.continuation_lines.append(TRUNCATED_MARKER)
            self.truncated = True
            return
        self.continuation_lines.append(line)


@dataclass
class ReadResult:
    entries: list                 # timestamped, merged in timeline order
    untimed: list                 # no timestamp: kept, excluded from the timeline
    files: list
    lines_read: int = 0
    bytes_read: int = 0
    decode_errors: int = 0        # lines that held non-UTF-8 bytes
    replaced_bytes: int = 0       # non-UTF-8 bytes replaced by U+FFFD
    file_sizes: dict = field(default_factory=dict)            # file -> bytes on disk
    invalid_json: int = 0
    invalid_json_lines: dict = field(default_factory=dict)   # file -> first 3 line_nos
    sampled: bool = False
    max_lines: int = DEFAULT_MAX_LINES
    year: int = 0                 # year applied to syslog timestamps
    syslog_used: bool = False     # any syslog timestamp parsed (year matters)

    @property
    def sampled_note(self):
        return f"sampled: first {self.max_lines} lines" if self.sampled else ""


def normalise_level(word):
    if not isinstance(word, str):
        return UNKNOWN
    return LEVEL_WORDS.get(word.strip().upper(), UNKNOWN)


def find_level(line):
    """First level word (see _LEVEL_RE) within the first 64 characters."""
    window = line[:LEVEL_WINDOW]
    stamp = _ISO_RE.match(window) or _SYSLOG_RE.match(window)
    match = _LEVEL_COLON_RE.match(window, stamp.end() if stamp else 0)
    if match:
        return LEVEL_WORDS[match.group(1).upper()]
    match = _LEVEL_RE.search(window)
    if not match:
        return UNKNOWN
    word = match.group("upper") or match.group("bracket") or match.group("key")
    return LEVEL_WORDS[word.upper()]


def _offset(text):
    if text is None:
        return timezone.utc
    if text.upper() == "Z":
        return timezone.utc
    sign = -1 if text[0] == "-" else 1
    digits = text[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:4] or 0)
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _iso_at(text):
    match = _ISO_RE.match(text)
    if not match:
        return None, None
    year, month, day, hour, minute, second, frac, off = match.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    try:
        ts = datetime(int(year), int(month), int(day), int(hour), int(minute),
                      int(second), micro, tzinfo=_offset(off))
    except ValueError:
        return None, None
    return ts.astimezone(timezone.utc), match.end()


def parse_line_timestamp(line, year):
    """Timestamp at the start of a text line -> (UTC datetime, end index)."""
    ts, end = _iso_at(line)
    if ts is not None:
        return ts, end
    return _syslog_at(line, year)


def syslog_tag(line, start):
    """(host, app, end) for a "host app[pid]:" / "host app:" header at start,
    or None. A level word in either place means it is not a header."""
    match = _SYSLOG_TAG_RE.match(line, start)
    if not match:
        return None
    host, app = match.group("host", "app")
    if host.upper() in LEVEL_WORDS or app.upper() in LEVEL_WORDS:
        return None
    return host, app, match.end()


def _syslog_at(line, year):
    match = _SYSLOG_RE.match(line)
    if match:
        month, day, hour, minute, second = match.groups()
        try:
            ts = datetime(year, _MONTHS[month], int(day), int(hour), int(minute),
                          int(second), tzinfo=timezone.utc)
        except ValueError:
            return None, None
        return ts, match.end()
    return None, None


def parse_json_timestamp(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if abs(value) > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        ts, end = _iso_at(value.strip())
        if ts is not None and end == len(value.strip()):
            return ts
    return None


def parse_fields(specs):
    """--field name=key overrides -> {name: (key,)}."""
    keys = dict(FIELD_KEYS)
    for spec in specs or ():
        name, sep, key = spec.partition("=")
        name, key = name.strip(), key.strip()
        if not sep or not key:
            raise UsageError(f"--field expects NAME=KEY, got {spec!r}")
        if name == "msg":
            name = "message"
        if name not in FIELD_KEYS:
            raise UsageError(f"--field NAME must be one of ts, level, message; got {name!r}")
        keys[name] = (key,)
    return keys


def _open_nofollow(path):
    """Open path read-only as binary, refusing symlinks and non-regular files."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise InputError(f"{path}: file not found") from None
    except OSError as exc:
        raise InputError(f"{path}: cannot read ({exc.strerror})") from None
    if stat.S_ISLNK(info.st_mode):
        raise InputError(f"{path}: is a symlink; symlinks are not followed")
    if not stat.S_ISREG(info.st_mode):
        raise InputError(f"{path}: not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise InputError(f"{path}: cannot read ({exc.strerror})") from None
    raw = os.fdopen(fd, "rb")
    try:
        magic = raw.peek(2)[:2] if hasattr(raw, "peek") else b""
    except OSError as exc:
        raw.close()
        raise InputError(f"{path}: cannot read ({exc.strerror})") from None
    if magic == b"\x1f\x8b" or str(path).lower().endswith(".gz"):
        return gzip.GzipFile(fileobj=raw, mode="rb"), raw
    return raw, raw


_replaced = [0]


def _count_replace(exc):
    _replaced[0] += exc.end - exc.start
    return "\ufffd", exc.end


codecs.register_error("loglens-count-replace", _count_replace)


def _decode(data):
    """UTF-8 with U+FFFD replacement, as errors="replace" does, plus the
    number of bytes that were replaced."""
    try:
        return data.decode("utf-8"), 0
    except UnicodeDecodeError:
        pass
    _replaced[0] = 0
    text = data.decode("utf-8", errors="loglens-count-replace")
    return text, _replaced[0]


def _is_jsonl_name(path):
    name = str(path).lower()
    if name.endswith(".gz"):
        name = name[:-3]
    return name.endswith(_JSONL_SUFFIXES)


class _Reader:
    def __init__(self, *, year, fields, max_bytes, max_lines, fmt):
        self.year = year if year is not None else datetime.now(timezone.utc).year
        self.fields = fields
        self.max_bytes = max_bytes
        self.max_lines = max_lines
        self.fmt = fmt
        self.result = ReadResult(entries=[], untimed=[], files=[], max_lines=max_lines,
                                 year=self.year)

    def _lines(self, path):
        """Yield (line_no, decoded line) within the byte and line guards."""
        stream, raw = _open_nofollow(path)
        res = self.result
        try:
            line_no = 0
            while True:
                remaining = self.max_bytes - res.bytes_read
                try:
                    data = stream.readline(remaining + 1)
                except (OSError, EOFError, gzip.BadGzipFile) as exc:
                    raise InputError(f"{path}: cannot read ({exc})") from None
                if not data:
                    return
                if res.lines_read >= self.max_lines:
                    res.sampled = True
                    return
                res.bytes_read += len(data)
                if res.bytes_read > self.max_bytes:
                    raise InputError(f"input exceeds --max-bytes ({self.max_bytes} bytes)")
                line_no += 1
                res.lines_read += 1
                text, replaced = _decode(data)
                if replaced:
                    res.decode_errors += 1
                    res.replaced_bytes += replaced
                if line_no == 1:
                    text = text.removeprefix("\ufeff")
                yield line_no, text.rstrip("\r\n")
        finally:
            stream.close()
            raw.close()

    def _is_jsonl(self, path):
        if self.fmt != "auto":
            return self.fmt == "jsonl"
        return _is_jsonl_name(path)

    def read_file(self, path):
        if self._is_jsonl(path):
            yield from self._read_jsonl(path)
        else:
            yield from self._read_text(path)

    def _read_text(self, path):
        current = None
        for line_no, line in self._lines(path):
            ts, end = _iso_at(line)
            host = app = None
            if ts is None:
                ts, end = _syslog_at(line, self.year)
                if ts is not None:
                    self.result.syslog_used = True
                    tag = syslog_tag(line, end)
                    if tag:
                        host, app, end = tag
            if ts is None:
                if not line.strip():
                    continue
                if current is not None:
                    current.add_continuation(line)
                    continue
                # Leading lines with no timestamp form one untimed entry.
                current = Entry(None, find_level(line), line.strip(), [], str(path), line_no)
                continue
            if current is not None:
                yield current
            current = Entry(ts, find_level(line), line[end:].strip(), [], str(path), line_no,
                            host=host, app=app)
        if current is not None:
            yield current

    def _first(self, record, name):
        for key in self.fields[name]:
            if key in record:
                return record[key]
        return None

    def _read_jsonl(self, path):
        res = self.result
        for line_no, line in self._lines(path):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                record = None
            if not isinstance(record, dict):
                res.invalid_json += 1
                kept = res.invalid_json_lines.setdefault(str(path), [])
                if len(kept) < INVALID_KEEP:
                    kept.append(line_no)
                continue
            message = self._first(record, "message")
            message = "" if message is None else (
                message if isinstance(message, str) else json.dumps(message))
            first, *rest = message.split("\n") or [""]
            entry = Entry(parse_json_timestamp(self._first(record, "ts")),
                          normalise_level(self._first(record, "level")),
                          first.rstrip("\r"), [], str(path), line_no)
            for extra in rest:
                entry.add_continuation(extra.rstrip("\r"))
            yield entry


def read_logs(paths, *, year=None, fields=None, max_bytes=DEFAULT_MAX_BYTES,
              max_lines=DEFAULT_MAX_LINES, fmt="auto"):
    """Read every path and merge entries in (ts, file order, line_no) order."""
    if max_bytes < 1 or max_lines < 1:
        raise UsageError("--max-bytes and --max-lines must be at least 1")
    if fmt not in ("auto", "text", "jsonl"):
        raise UsageError(f"unknown format {fmt!r}")
    reader = _Reader(year=year, fields=parse_fields(fields), max_bytes=max_bytes,
                     max_lines=max_lines, fmt=fmt)
    res = reader.result
    total = 0
    for path in paths:
        try:
            info = os.lstat(path)
        except OSError:
            continue                    # reported by _open_nofollow below
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
            res.file_sizes[str(path)] = info.st_size
    if total > max_bytes:
        raise InputError(f"input exceeds --max-bytes ({max_bytes} bytes)")

    timed = []
    for order, path in enumerate(paths):
        res.files.append(str(path))
        for entry in reader.read_file(path):
            if entry.ts is None:
                res.untimed.append(entry)
            else:
                timed.append((entry.ts, order, entry.line_no, entry))
        if res.sampled:
            break
    timed.sort(key=lambda item: item[:3])
    res.entries = [item[3] for item in timed]
    return res
