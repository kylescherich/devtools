"""testdelta reports in text, Markdown, HTML and JSON.

All four formats render the same sections from one Delta. Every value taken
from the inputs (test ids, input names and, with --details, failure messages
and traceback lines) is first passed through redact() and then through the
format's escape step: control characters as \\xNN, bidi and line-separator
characters as \\uXXXX, a literal backslash doubled in text and Markdown;
Markdown also escapes | ` [ ] and HTML special characters; HTML uses
html.escape; JSON uses ensure_ascii. A traceback is redacted as one text
before it is capped, so a multi-line secret is never cut in half.

render() then sends the whole document through redact() once more (the CLI
does so again on the way out). The layout keeps that pass a no-op: values
sit last on their line, no status word is followed by ':' or '=', and JSON
counts are a list of {"status": ...} objects rather than status keys.

By default only ids, statuses and times appear. --details adds the first
message line and at most the last TRACEBACK_LINES traceback lines for newly
failing tests only. Skip reasons and captured output are never stored, so
never shown. The HTML report has a strict CSP, no scripts, links, images or
event handlers, and values only ever land in text nodes. The current date or
time never appears.
"""
import html
import json
import re

from testdelta._redact import redact
from testdelta.compare import CATEGORIES, IMPROVEMENTS, REGRESSIONS
from testdelta.readers import STATUSES

FORMATS = ("text", "md", "html", "json")
SCHEMA = "testdelta/1"
TRACEBACK_LINES = 20

TITLES = {
    "newly_failing": "Newly failing",
    "removed": "Removed",
    "newly_skipped": "Newly skipped",
    "slower": "Slower",
    "fixed": "Fixed",
    "unskipped": "Unskipped",
    "added": "Added",
    "faster": "Faster",
    "still_failing": "Still failing (not new)",
    "other_changes": "Other status changes (not counted)",
}

_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028\u2029"
                     r"\u202a-\u202e\u2066-\u2069]")
_MD_SPECIAL = re.compile(r"([\\|`\[\]])")


def _escape_char(match):
    code = ord(match.group(0))
    return f"\\x{code:02x}" if code <= 0xff else f"\\u{code:04x}"


def _escape_controls(value):
    return _UNSAFE.sub(_escape_char, str(value))


def _double_backslashes(value):
    return str(value).replace("\\", "\\\\")


def escape_text(value):
    """Backslashes doubled, then controls escaped: the two never look alike."""
    return _escape_controls(_double_backslashes(redact(str(value))))


def escape_md(value):
    escaped = _MD_SPECIAL.sub(r"\\\1", _double_backslashes(redact(str(value))))
    return _escape_controls(html.escape(escaped, quote=False))


def escape_html(value):
    # Values only land in text nodes, never in attributes.
    return html.escape(_escape_controls(redact(str(value))), quote=False)


# --- shared model -----------------------------------------------------------

def _time(value):
    return "n/a" if value is None else f"{value:.3f}s"


def _status(value):
    return value or "-"


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def detail(change):
    """(message line or None, traceback lines, omitted count): redacted as
    a whole, then capped to the last TRACEBACK_LINES lines."""
    message = redact(change.message).split("\n")[0] if change.message else None
    lines = redact("\n".join(change.traceback)).split("\n") if change.traceback else []
    omitted = max(len(lines) - TRACEBACK_LINES, 0)
    return message, lines[omitted:], omitted


def summary(delta, fail_on_slow=False):
    regressions = delta.regressions(fail_on_slow)
    improvements = delta.improvements()
    parts = [f"{len(delta.categories[n])} {TITLES[n].lower()}" for n in REGRESSIONS]
    if fail_on_slow:
        parts.append(f"{len(delta.categories['slower'])} slower")
    if regressions:
        head = f"WORSE: {_plural(regressions, 'regression')} ({', '.join(parts)})"
    else:
        head = "no regressions"
    return f"{head}; {_plural(improvements, 'improvement')}."


def _slow_rule(delta):
    return (f"slower/faster when one time >= {delta.slow_ratio:g}x the other and "
            f"at least {delta.slow_min:g} s apart; both times known")


def _header(delta, names, details):
    return [("before", names[0]), ("after", names[1]), ("input format", delta.format),
            ("slow rule", _slow_rule(delta)), ("details", "on" if details else "off"),
            ("redaction", "on")]


def _totals(delta):
    rows = [(s, delta.counts_before[s], delta.counts_after[s]) for s in STATUSES]
    return rows + [("total", delta.total_before, delta.total_after)]


def _id_notes(delta):
    """(label, id) pairs for duplicate and unresolved ids."""
    out = []
    for label, values in (("duplicate id in before", delta.duplicates_before),
                          ("duplicate id in after", delta.duplicates_after),
                          ("no status in before", delta.unresolved_before),
                          ("no status in after", delta.unresolved_after)):
        out += [(label, value) for value in values]
    return out


# --- text -------------------------------------------------------------------

def _render_text(delta, names, details, fail_on_slow):
    e = escape_text
    out = ["testdelta report"]
    out += [f"{label} = {e(value)}" for label, value in _header(delta, names, details)]
    out += ["", f"RESULT {summary(delta, fail_on_slow)}", ""]
    if delta.warnings:
        out.append("WARNINGS")
        for warning in delta.warnings:
            loud = "!!! " if warning.startswith("total test count dropped") else "! "
            out.append(f"  {loud}{warning}")
        out += [f"  - {label}  {e(value)}" for label, value in _id_notes(delta)]
    else:
        out.append("WARNINGS none")
    out += ["", "TOTALS", f"  {'status':<8}{'before':>8}{'after':>8}"]
    out += [f"  {status:<8}{b:>8}{a:>8}" for status, b, a in _totals(delta)]
    for name in CATEGORIES:
        changes = delta.categories[name]
        out += ["", f"{TITLES[name].upper()} ({len(changes)})"]
        if not changes:
            out.append("  none")
        for c in changes:
            out.append(f"  {_status(c.before):>5} -> {_status(c.after):<5}  "
                       f"{_time(c.before_time):>9} -> {_time(c.after_time):<9}  {e(c.id)}")
            if details and name == "newly_failing":
                message, lines, omitted = detail(c)
                out.append(f"      message  {e(message) if message else '(none)'}")
                if omitted:
                    out.append(f"      ... {omitted} earlier traceback lines omitted")
                out += [f"      | {e(line)}" for line in lines]
    return "\n".join(out) + "\n"


# --- Markdown ---------------------------------------------------------------

def _render_md(delta, names, details, fail_on_slow):
    e = escape_md
    out = ["# testdelta report", ""]
    out += [f"- {label}: {e(value)}" for label, value in _header(delta, names, details)]
    out += ["", f"**Result**: {summary(delta, fail_on_slow)}", "", "## Warnings", ""]
    if delta.warnings:
        for warning in delta.warnings:
            if warning.startswith("total test count dropped"):
                out.append(f"- **WARNING: {warning}**")
            else:
                out.append(f"- {warning}")
        out += [f"- {label}: {e(value)}" for label, value in _id_notes(delta)]
    else:
        out.append("none")
    out += ["", "## Totals", "", "| status | before | after |", "| --- | ---: | ---: |"]
    out += [f"| {status} | {b} | {a} |" for status, b, a in _totals(delta)]
    for name in CATEGORIES:
        changes = delta.categories[name]
        out += ["", f"## {TITLES[name]} ({len(changes)})", ""]
        if not changes:
            out.append("none")
        for c in changes:
            out.append(f"- `{_status(c.before)} -> {_status(c.after)}` "
                       f"{_time(c.before_time)} -> {_time(c.after_time)} {e(c.id)}")
            if details and name == "newly_failing":
                message, lines, omitted = detail(c)
                out.append(f"    - message: {e(message) if message else '(none)'}")
                if omitted:
                    out.append(f"    - ... {omitted} earlier traceback lines omitted")
                out += [f"    - traceback: {e(line)}" for line in lines]
    return "\n".join(out) + "\n"


# --- HTML -------------------------------------------------------------------

_CSS = """\
body{font-family:system-ui,sans-serif;margin:1.5rem;color:#1b1b1b;background:#fff}
table{border-collapse:collapse;margin:.5rem 0}
td,th{border:1px solid #ccc;padding:.2rem .5rem;text-align:left;vertical-align:top}
td.n{text-align:right}
code,pre{font-family:ui-monospace,monospace;white-space:pre-wrap;margin:0}
.loud{font-weight:bold;color:#b00020}
@media (prefers-color-scheme: dark){body{color:#e6e6e6;background:#161616}
td,th{border-color:#444}.loud{color:#ff6b6b}}
"""


def _html_value(value):
    """Escaped value plus a newline, so markup never follows on the same line."""
    return escape_html(value) + "\n"


def _render_html(delta, names, details, fail_on_slow):
    e = _html_value
    out = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta http-equiv="Content-Security-Policy" '
           "content=\"default-src 'none'; style-src 'unsafe-inline'\">",
           "<title>testdelta report</title>", f"<style>\n{_CSS}</style>", "</head>",
           "<body>", "<h1>testdelta report</h1>", "<table>"]
    for label, value in _header(delta, names, details):
        out.append(f"<tr><th>{label}</th><td>{e(value)}</td></tr>")
    out += ["</table>", f"<p><strong>Result</strong>: {summary(delta, fail_on_slow)}</p>",
            "<h2>Warnings</h2>"]
    if delta.warnings:
        out.append("<ul>")
        for warning in delta.warnings:
            loud = warning.startswith("total test count dropped")
            out.append(f'<li class="loud">WARNING: {warning}</li>' if loud
                       else f"<li>{warning}</li>")
        out += [f"<li>{label}: {e(value)}</li>" for label, value in _id_notes(delta)]
        out.append("</ul>")
    else:
        out.append("<p>none</p>")
    out += ["<h2>Totals</h2>", "<table>",
            "<tr><th>status</th><th>before</th><th>after</th></tr>"]
    out += [f'<tr><td>{status}</td><td class="n">{b}</td><td class="n">{a}</td></tr>'
            for status, b, a in _totals(delta)]
    out.append("</table>")
    for name in CATEGORIES:
        changes = delta.categories[name]
        out.append(f"<h2>{TITLES[name]} ({len(changes)})</h2>")
        if not changes:
            out.append("<p>none</p>")
            continue
        out += ["<table>", "<tr><th>before</th><th>after</th><th>before time</th>"
                "<th>after time</th><th>test</th></tr>"]
        for c in changes:
            out.append(f"<tr><td>{_status(c.before)}</td><td>{_status(c.after)}</td>"
                       f'<td class="n">{_time(c.before_time)}</td>'
                       f'<td class="n">{_time(c.after_time)}</td><td>{e(c.id)}</td></tr>')
            if details and name == "newly_failing":
                message, lines, omitted = detail(c)
                note = f"... {omitted} earlier traceback lines omitted\n" if omitted else ""
                out.append('<tr><td colspan="5">message: '
                           f"{e(message) if message else '(none)'}<pre>{note}"
                           + "".join(e(line) for line in lines) + "</pre></td></tr>")
        out.append("</table>")
    out += ["</body>", "</html>"]
    return "\n".join(out) + "\n"


# --- JSON -------------------------------------------------------------------

def _json_change(change, with_detail):
    item = {"id": redact(change.id), "before": change.before, "after": change.after,
            "before_time": change.before_time, "after_time": change.after_time}
    if with_detail:
        message, lines, omitted = detail(change)
        item["message"] = message
        item["traceback"] = lines
        item["traceback_omitted"] = omitted
    return item


def _render_json(delta, names, details, fail_on_slow):
    doc = {
        "schema": SCHEMA,
        "inputs": {"before": redact(names[0]), "after": redact(names[1])},
        "input_format": delta.format,
        "slow_rule": {"ratio": delta.slow_ratio, "min_seconds": delta.slow_min},
        "details": bool(details),
        "fail_on_slow": bool(fail_on_slow),
        "counts": [{"status": status, "before": b, "after": a}
                   for status, b, a in _totals(delta)],
        "summary": {"regressions": delta.regressions(fail_on_slow),
                    "improvements": delta.improvements()},
        "warnings": list(delta.warnings),
    }
    for name in CATEGORIES:
        with_detail = details and name == "newly_failing"
        doc[name] = [_json_change(c, with_detail) for c in delta.categories[name]]
    doc["duplicates"] = {"before": [redact(i) for i in delta.duplicates_before],
                         "after": [redact(i) for i in delta.duplicates_after]}
    doc["unresolved"] = {"before": [redact(i) for i in delta.unresolved_before],
                         "after": [redact(i) for i in delta.unresolved_after]}
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"


_RENDERERS = {"text": _render_text, "md": _render_md, "html": _render_html,
              "json": _render_json}


def render(delta, fmt="text", details=False, names=("before", "after"),
           fail_on_slow=False):
    """The rendered document, already passed through redact(); the CLI
    redacts it once more on the way out."""
    return redact(_RENDERERS[fmt](delta, names, details, fail_on_slow))
