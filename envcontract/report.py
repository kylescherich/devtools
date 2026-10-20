"""envcontract reports in text, Markdown and JSON.

A report holds names, repo-relative paths, line numbers, fixed reasons and
counts only; scan.Result never holds a value, so none can be printed.
Names and paths are untrusted: control characters are shown as \\xNN, bidi
and line-separator characters as \\uXXXX, a literal backslash doubled in
text and Markdown; Markdown also escapes | ` [ ] * < > &; JSON uses
ensure_ascii. The CLI passes the whole document through redact(). The
layout keeps that pass a no-op for names: a name or path is never followed
by ':' or '=' (a use prints as "path line N"), and JSON names are values,
never keys.
"""
import json
import re

FORMATS = ("text", "md", "json")
SCHEMA = "envcontract/1"
SECTIONS = ("undocumented", "unused", "dynamic", "unreadable")
TITLES = {"undocumented": "Undocumented (read in code, not in an example file)",
          "unused": "Unused (in an example file, never read)",
          "dynamic": "Dynamic (not checked)",
          "unreadable": "Unreadable or refused (not opened or not parsed)"}
NO_EXAMPLE = ("no example file found (*.example, *.template, *.sample); "
              "every name read is undocumented")

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


def build(result, repo):
    """The report as one dict, keys in schema order, lists sorted."""
    undocumented = sorted(set(result.reads) - set(result.docs))
    unused = sorted(set(result.docs) - set(result.reads))
    data = {
        "schema": SCHEMA,
        "repo": repo,
        "example_files": sorted(result.example_files),
        "summary": {"read": len(result.reads), "documented": len(result.docs),
                    "undocumented": len(undocumented), "unused": len(unused),
                    "dynamic": len(result.dynamic),
                    "unreadable": len(result.unreadable)},
        "undocumented": [{"name": n, "uses": _places(result.reads[n])}
                         for n in undocumented],
        "unused": [{"name": n, "where": _places(result.docs[n])} for n in unused],
        "dynamic": _places(result.dynamic),
        "unreadable": [{"path": p, "reason": r} for p, r in sorted(result.unreadable)],
    }
    return data


def _places(pairs):
    return [{"path": p, "line": n} for p, n in sorted(pairs)]


def differences(data):
    return bool(data["undocumented"] or data["unused"])


def render(data, fmt):
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=True) + "\n"
    if fmt == "md":
        return _render_lines(data, _md, "# ", "## ", "- ", "  - ", "`*.example`, "
                             "`*.template` or `*.sample`")
    return _render_lines(data, _text, "", "", "  ", "    ", None)


def _render_lines(data, esc, h1, h2, item, sub, md_suffixes):
    lines = [f"{h1}envcontract report for {esc(data['repo'])}", "", f"{h2}Example files"]
    if data["example_files"]:
        lines += [item + esc(p) for p in data["example_files"]]
    else:
        note = NO_EXAMPLE
        if md_suffixes:
            note = note.replace("*.example, *.template, *.sample", md_suffixes)
        lines.append(item + note)
    lines += ["", f"{h2}Summary"]
    lines += [f"{item}{key} {value}" for key, value in data["summary"].items()]
    for section in SECTIONS:
        lines += ["", f"{h2}{TITLES[section]}"]
        entries = data[section]
        if not entries:
            lines.append(item + "(none)")
        for entry in entries:
            if section in ("undocumented", "unused"):
                places = entry["uses"] if section == "undocumented" else entry["where"]
                lines.append(item + esc(entry["name"]))
                lines += [f"{sub}{esc(p['path'])} line {p['line']}" for p in places]
            elif section == "dynamic":
                lines.append(f"{item}{esc(entry['path'])} line {entry['line']}")
            else:
                lines.append(f"{item}{esc(entry['path'])} ({entry['reason']})")
    return "\n".join(lines) + "\n"
