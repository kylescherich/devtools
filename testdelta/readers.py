"""Readers for the two supported inputs: JUnit XML and unittest -v text.

Both give a Run: test id -> Result(status, time, message, traceback).
Statuses are pass, fail, error, skip, xfail (expected failure) and xpass
(unexpected success). A time is a float in seconds or None (n/a); a time is
never invented. Captured output (JUnit system-out/system-err, unittest -b
Stdout:/Stderr: sections) and skip reasons are never stored.

Inputs are read without following symlinks, refused over max_bytes before
reading, and must be UTF-8. XML containing a DOCTYPE or ENTITY declaration,
or declaring another encoding, is refused before it reaches the parser.
Error messages never quote input content.
"""
import math
import os
import re
import stat
from dataclasses import dataclass
from xml.etree import ElementTree as ET

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
STATUSES = ("pass", "fail", "error", "skip", "xfail", "xpass")
BAD = frozenset({"fail", "error", "xpass"})
# Duplicate ids keep the worst status: error > fail > xpass > skip > xfail > pass.
RANK = {"pass": 0, "xfail": 1, "skip": 2, "xpass": 3, "fail": 4, "error": 5}

_BOM = "\ufeff"
_FORBIDDEN = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_XML_DECL = re.compile(r"<\?xml\b[^>]*?\?>")
_DECL_ENCODING = re.compile(r"""\bencoding\s*=\s*["']([^"']*)["']""")
_OK_ENCODINGS = {"utf-8", "utf8", "us-ascii", "ascii"}
_XPASS_STRICT = "[XPASS(strict)]"


class InputError(Exception):
    """An input that is missing, unreadable, refused or not understood."""


@dataclass(frozen=True)
class Result:
    status: str
    time: float = None
    message: str = None
    traceback: tuple = ()


@dataclass(frozen=True)
class Run:
    format: str
    results: dict
    duplicates: tuple = ()      # ids seen more than once (worst status kept)
    unresolved: tuple = ()      # unittest ids started but with no status
    has_durations: bool = False


# --- reading ----------------------------------------------------------------

def read_bounded(path, max_bytes):
    """The bytes of PATH: never through a symlink, refused over max_bytes
    before anything is read."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise InputError("file not found") from None
    except OSError as exc:
        raise InputError(f"cannot read ({exc.strerror})") from None
    if stat.S_ISLNK(info.st_mode):
        raise InputError("is a symlink; symlinks are not followed")
    if stat.S_ISDIR(info.st_mode):
        raise InputError("is a directory")
    if not stat.S_ISREG(info.st_mode):
        raise InputError("is not a regular file")
    if info.st_size > max_bytes:
        raise InputError(f"larger than --max-bytes ({max_bytes} bytes)")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise InputError(f"cannot read ({exc.strerror})") from None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise InputError("is not a regular file")
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise InputError(f"larger than --max-bytes ({max_bytes} bytes)")
    return data


def decode(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("not UTF-8 text") from None
    if text.startswith(_BOM):
        text = text[1:]
    if not text.strip():
        raise InputError("empty file")
    return text


def detect(text):
    return "junit" if text.lstrip().startswith("<") else "unittest"


def load(path, input_format=None, max_bytes=DEFAULT_MAX_BYTES):
    text = decode(read_bounded(path, max_bytes))
    fmt = input_format or detect(text)
    return parse_junit(text) if fmt == "junit" else parse_unittest(text)


def load_pair(before, after, input_format=None, max_bytes=DEFAULT_MAX_BYTES):
    """Both runs; InputError names the side. Both must be the same format."""
    runs = []
    for label, path in (("before", before), ("after", after)):
        try:
            runs.append(load(path, input_format, max_bytes))
        except InputError as exc:
            raise InputError(f"{label} file: {exc}") from None
    if runs[0].format != runs[1].format:
        raise InputError(f"inputs are different formats (before: {runs[0].format}, "
                         f"after: {runs[1].format}); both must be JUnit XML or "
                         "both unittest -v output")
    return runs[0], runs[1]


def _time(value):
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _first_line(value):
    for line in (value or "").split("\n"):
        if line.strip():
            return line.strip()
    return None


def _lines(value):
    lines = [line.rstrip("\r") for line in (value or "").split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return tuple(lines)


class _Collector:
    """Results by id; a repeated id keeps the worst status (first among equals)."""

    def __init__(self):
        self.results = {}
        self.duplicates = set()

    def add(self, test_id, result):
        old = self.results.get(test_id)
        if old is None:
            self.results[test_id] = result
            return
        self.duplicates.add(test_id)
        if RANK[result.status] > RANK[old.status]:
            self.results[test_id] = result


# --- JUnit XML --------------------------------------------------------------

def check_xml_safe(text):
    """Refusals that must happen before any XML parsing."""
    if _FORBIDDEN.search(text):
        raise InputError("XML with a DOCTYPE or ENTITY declaration is refused "
                         "(not parsed)")
    decl = _XML_DECL.match(text.lstrip())
    if decl:
        encoding = _DECL_ENCODING.search(decl.group(0))
        if encoding and encoding.group(1).strip().lower() not in _OK_ENCODINGS:
            raise InputError("XML must be UTF-8 (another encoding is declared)")


def _junit_result(case):
    found = []
    for child in case:
        if child.tag == "error":
            found.append(("error", child))
        elif child.tag == "failure":
            message = child.get("message") or ""
            kind = "xpass" if message.startswith(_XPASS_STRICT) else "fail"
            found.append((kind, child))
        elif child.tag == "skipped":
            kind = "xfail" if child.get("type") == "pytest.xfail" else "skip"
            found.append((kind, None))
    time = _time(case.get("time"))
    if not found:
        return Result("pass", time)
    status, element = max(found, key=lambda item: RANK[item[0]])
    if element is None:
        return Result(status, time)
    message = _first_line(element.get("message")) or _first_line(element.text)
    return Result(status, time, message, _lines(element.text))


def parse_junit(text):
    check_xml_safe(text)
    try:
        root = ET.fromstring(text.lstrip())
    except ET.ParseError:
        raise InputError("malformed XML") from None
    if root.tag not in ("testsuites", "testsuite"):
        raise InputError("not a JUnit report (root element is not testsuite or "
                         "testsuites)")
    collector = _Collector()
    for case in root.iter("testcase"):
        name = case.get("name") or ""
        prefix = case.get("classname") or case.get("file") or ""
        test_id = f"{prefix}::{name}" if prefix else name
        collector.add(test_id, _junit_result(case))
    return Run("junit", collector.results, tuple(sorted(collector.duplicates)))


# --- unittest -v text -------------------------------------------------------

_SEP_EQ = re.compile(r"={20,}")
_SEP_DASH = re.compile(r"-{20,}")
_BLOCK = re.compile(r"(FAIL|ERROR|UNEXPECTED SUCCESS): (.*)")
_BLOCK_STATUS = {"FAIL": "fail", "ERROR": "error", "UNEXPECTED SUCCESS": "xpass"}
_LINE_STATUS = {"ok": "pass", "FAIL": "fail", "ERROR": "error",
                "expected failure": "xfail", "unexpected success": "xpass"}
_SKIPPED = re.compile(r"skipped(?: .*)?")
_SHORT_WHERE = re.compile(r"([A-Za-z_][\w.]*)\)(.*)")
_TAIL = re.compile(r"(?: \[.*\])?(?: \(.*\))?")
_DURATION = re.compile(r"(\d+(?:\.\d+)?)s\s+(.*)")
_RAN = re.compile(r"Ran \d+ tests? in ")
_MODULE_FIXTURES = {"setUpModule", "tearDownModule"}
_CAPTURED = {"Stdout:", "Stderr:"}


def _line_status(value):
    value = value.strip()
    if value in _LINE_STATUS:
        return _LINE_STATUS[value]
    if _SKIPPED.fullmatch(value):
        return "skip"
    return None


def parse_head(head):
    """The test id in 'name (pkg.mod.Class.name)' (3.11+ form) or
    'name (pkg.mod.Class)' (older form), optionally followed by subtest
    parameters, or None."""
    parsed = _parse_head(head)
    return parsed[0] if parsed else None


def _parse_head(head):
    """(test id, has subtest parameters) or None. Verbose subtest lines are
    indented, so leading spaces are dropped."""
    head = head.lstrip(" ")
    start = 0
    while True:
        i = head.find(" (", start)
        if i <= 0:
            return None
        name, rest = head[:i], head[i + 2:]
        marker = "." + name + ")"
        k = rest.find(marker)
        if k > 0 and _TAIL.fullmatch(rest[k + len(marker):]):
            return rest[:k + len(name) + 1], bool(rest[k + len(marker):])
        short = _SHORT_WHERE.fullmatch(rest)
        if short and _TAIL.fullmatch(short.group(2)) and (
                "." in short.group(1) or name in _MODULE_FIXTURES):
            return short.group(1) + "." + name, bool(short.group(2))
        start = i + 1


def _block_message(lines):
    """The exception line of the last traceback, else the first line."""
    starts = [i for i, line in enumerate(lines)
              if line.startswith("Traceback (most recent call last):")]
    if starts:
        for line in lines[starts[-1] + 1:]:
            if line.strip() and not line.startswith(" "):
                return line.strip()
    return _first_line("\n".join(lines))


def _read_block(lines, i):
    """Lines of a FAIL/ERROR block body from index i, without captured
    output; returns (body, next index)."""
    body = []
    captured = False
    while i < len(lines):
        line = lines[i]
        if _SEP_EQ.fullmatch(line) or _SEP_DASH.fullmatch(line) or _RAN.match(line):
            break
        if line == "Slowest test durations" and (not body or not body[-1].strip()):
            break
        if line in _CAPTURED:
            captured = True
        if not captured:
            body.append(line)
        i += 1
    return _lines("\n".join(body)), i


def _read_durations(lines, i, durations):
    if i < len(lines) and _SEP_DASH.fullmatch(lines[i]):
        i += 1
    while i < len(lines):
        match = _DURATION.fullmatch(lines[i])
        if match:
            test_id = parse_head(match.group(2))
            if test_id is not None:
                durations.setdefault(test_id, _time(match.group(1)))
        elif not lines[i].startswith("(durations < "):
            break
        i += 1
    return i


def parse_unittest(text):
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    line_status = {}            # id -> [status, ...] from "... ok" lines
    subtest_status = {}         # id -> [status, ...] from indented subtest lines
    blocks = {}                 # id -> [(status, message, traceback), ...]
    durations = {}
    pending = None
    started = []
    recognised = False
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if _SEP_EQ.fullmatch(line) and i < len(lines):
            block = _BLOCK.fullmatch(lines[i])
            if block:
                test_id = parse_head(block.group(2))
                if test_id is not None:
                    i += 1
                    if block.group(1) == "UNEXPECTED SUCCESS":
                        body = ()       # no traceback; its dash line ends the run
                    else:
                        if i < len(lines) and _SEP_DASH.fullmatch(lines[i]):
                            i += 1
                        body, i = _read_block(lines, i)
                    blocks.setdefault(test_id, []).append(
                        (_BLOCK_STATUS[block.group(1)], _block_message(body), body))
                    recognised = True
            pending = None
            continue
        if line == "Slowest test durations":
            i = _read_durations(lines, i, durations)
            pending = None
            continue
        if _RAN.match(line):
            recognised = True
            pending = None
            continue
        head, sep, after = line.partition(" ... ")
        parsed = _parse_head(head) if sep else _parse_head(line)
        if parsed is not None:
            test_id, subtest = parsed
            started.append(test_id)
            status = _line_status(after.rpartition(" ... ")[2]) if sep else None
            if status:
                target = subtest_status if subtest else line_status
                target.setdefault(test_id, []).append(status)
                recognised = True
                pending = None
            else:
                pending = test_id
            continue
        if pending is not None:
            status = _line_status(line.rpartition(" ... ")[2] if sep else line)
            if status:
                line_status.setdefault(pending, []).append(status)
                recognised = True
                pending = None
    if not recognised:
        raise InputError("not JUnit XML or unittest -v output (no test results "
                         "found)")
    collector = _Collector()
    for test_id in sorted(set(line_status) | set(subtest_status) | set(blocks)):
        found = blocks.get(test_id, [])
        statuses = (line_status.get(test_id, []) + subtest_status.get(test_id, [])
                    + [s for s, _, _ in found])
        status = max(statuses, key=RANK.__getitem__)
        detail = next((b for b in found if b[0] == status), found[0] if found else None)
        result = Result(status, durations.get(test_id),
                        detail[1] if detail else None, detail[2] if detail else ())
        collector.add(test_id, result)
        if len(line_status.get(test_id, [])) > 1:
            collector.duplicates.add(test_id)
    unresolved = sorted(set(started) - set(collector.results))
    return Run("unittest", collector.results, tuple(sorted(collector.duplicates)),
               tuple(unresolved), bool(durations))
