"""keycheck reports in text, Markdown and JSON.

A report holds root-relative paths, sizes, ages, modes and flags only; a
scan.Entry never holds file content, a hash or an environment value, so
none can be printed. Paths are untrusted: control characters are shown as
\\xNN, bidi and line-separator characters as \\uXXXX, a literal backslash
doubled in text and Markdown; Markdown also escapes | ` [ ] * < > &; JSON
uses ensure_ascii. The CLI passes the whole document through redact(). The
layout keeps that pass a no-op: a path is never followed by ':' or '=' and
never by "is"/"was" (a row prints as "path  size N  age N d ..."). The
skips in effect and a per-file note ("in-repo fixture?") are additive
fields of keycheck/1 (DEC-465).
"""
import json
import re

from keycheck.scan import EXIT_FLAGS, FLAG_ORDER

FORMATS = ("text", "md", "json")
SCHEMA = "keycheck/1"
COUNT_KEYS = ("total",) + FLAG_ORDER + ("OK", "unreadable_dirs")

_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028\u2029"
                     r"\u202a-\u202e\u2066-\u2069]")
_MD_SPECIAL = re.compile(r"([|`\[\]*])")
_MD_HTML = {"<": "&lt;", ">": "&gt;", "&": "&amp;"}


def _escape_char(match):
    code = ord(match.group(0))
    return f"\\x{code:02x}" if code <= 0xff else f"\\u{code:04x}"


def _text(value):
    return _UNSAFE.sub(_escape_char, value.replace("\\", "\\\\"))


def _md(value):
    value = _MD_SPECIAL.sub(r"\\\1", _text(value))
    return re.sub(r"[<>&]", lambda m: _MD_HTML[m.group(0)], value)


def _rank(entry):
    if "IN-REPO-NOT-IGNORED" in entry["flags"]:
        return 0
    if any(f in EXIT_FLAGS for f in entry["flags"]):
        return 1
    return 2


def build(scanned, skips):
    """scanned: [(root display, scan.Result)]. One root: "root" is a string
    and paths are relative to it; several: "root" is a list and each path
    carries its root's display prefix. skips: the skips in effect."""
    several = len(scanned) > 1
    files = []
    unreadable = 0
    for display, result in scanned:
        unreadable += result.unreadable_dirs
        for e in result.files:
            path = _join(display, e.rel) if several else e.rel
            files.append({"path": path, "size": e.size, "age_days": e.age_days,
                          "mode": e.mode, "flags": list(e.flags), "note": e.note})
    files.sort(key=lambda f: (_rank(f), f["path"]))
    counts = {key: 0 for key in COUNT_KEYS}
    counts["total"] = len(files)
    counts["unreadable_dirs"] = unreadable
    for f in files:
        for flag in f["flags"]:
            counts[flag] += 1
    return {"schema": SCHEMA,
            "root": [d for d, _ in scanned] if several else scanned[0][0],
            "skips": list(skips), "files": files, "counts": counts}


def _join(display, rel):
    return display.rstrip("/") + "/" + rel


def flagged(data):
    return any(f in EXIT_FLAGS for e in data["files"] for f in e["flags"])


def render(data, fmt):
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=True) + "\n"
    if fmt == "md":
        return _render_md(data)
    return _render_text(data)


def _roots(data, esc):
    roots = data["root"] if isinstance(data["root"], list) else [data["root"]]
    return ", ".join(esc(r) for r in roots)


def _summary(data):
    c = data["counts"]
    return [f"{key} {c[key]}" for key in COUNT_KEYS]


def _render_text(data):
    lines = [f"keycheck report for {_roots(data, _text)}", "",
             "Summary", *("  " + s for s in _summary(data)), "",
             "Skipped", "  " + ", ".join(_text(s) for s in data["skips"]), "", "Files"]
    if not data["files"]:
        lines.append("  (no key or secret files found)")
    for f in data["files"]:
        note = f"  note {_text(f['note'])}" if f["note"] else ""
        lines.append(f"  {' '.join(f['flags'])}  {_text(f['path'])}  size {f['size']}"
                     f"  age {f['age_days']} d  mode {f['mode']}{note}")
    return "\n".join(lines) + "\n"


def _render_md(data):
    lines = [f"# keycheck report for {_roots(data, _md)}", "", "## Summary", "",
             *("- " + s for s in _summary(data)), "", "## Skipped", "",
             ", ".join(_md(s) for s in data["skips"]), "", "## Files", ""]
    if not data["files"]:
        lines.append("(no key or secret files found)")
    else:
        lines += ["| flags | path | size | age (days) | mode | note |",
                  "| --- | --- | ---: | ---: | --- | --- |"]
        for f in data["files"]:
            lines.append(f"| {' '.join(f['flags'])} | {_md(f['path'])} | {f['size']} "
                         f"| {f['age_days']} | {f['mode']} | {_md(f['note'])} |")
    return "\n".join(lines) + "\n"
