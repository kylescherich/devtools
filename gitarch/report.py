"""gitarch reports in text, Markdown and HTML.

All three formats render the same sections from one Report. Every value
taken from the repository (paths, subjects, author names, diff lines)
passes the format's escape step: control characters as \\xNN, bidi and
line-separator characters as \\uXXXX, a literal backslash doubled in text
and Markdown; Markdown also escapes | ` [ ] and HTML special characters;
HTML uses html.escape. render() then sends the whole document through
redact(), and the CLI does so once more on the way out.

Values that can contain redacted text are placed last on their line or
(in HTML) followed by a newline, so the final redact() pass, which runs
some values to the end of the line, never eats report structure. The HTML
report has a strict CSP, no scripts, no links, no images, no event
handlers, and values only ever land in text nodes. The current date or
time never appears.
"""
import html
import re
from dataclasses import dataclass

from gitarch._redact import redact
from gitarch.history import DIFF_LINES, LATEST, PATH_COMMITS

FORMATS = ("text", "md", "html")
BAR_WIDTH = 50
SHORT = 10

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
    return _escape_controls(_double_backslashes(value))


def escape_md(value):
    escaped = _MD_SPECIAL.sub(r"\\\1", _double_backslashes(value))
    return _escape_controls(html.escape(escaped, quote=False))


def escape_html(value):
    # Values only land in text nodes, never in attributes.
    return html.escape(_escape_controls(value), quote=False)


def _date(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else "-"


def _day(value):
    return value.strftime("%Y-%m-%d") if value is not None else "-"


def _short(sha):
    return sha[:SHORT]


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _lines(added, deleted):
    return "binary" if added is None or deleted is None else f"+{added} -{deleted}"


def _bar(count, peak):
    if not count or not peak:
        return ""
    return "#" * max(1, round(count * BAR_WIDTH / peak))


@dataclass
class Report:
    repo: str                  # the repository directory's name
    history: object            # history.History
    messages: bool
    diffs: list | None         # history.Diff list with --diffs
    subjects: dict             # sha -> redacted subject (empty without --messages)
    git_version: str = "-"     # from git --version


def _header(rep):
    h, ov = rep.history, rep.history.overview
    scope = "all files"
    if h.path is not None:
        scope = h.path + (" (renames followed)" if h.followed else "")
    return [
        ("repo", rep.repo),
        ("rev", f"{ov.rev} ({_short(ov.sha)})"),
        ("git", rep.git_version),
        ("commits", f"{ov.commits} ({_plural(ov.merges, 'merge')}, {ov.ordinary} ordinary)"),
        ("first commit", _date(ov.first)),
        ("last commit", _date(ov.last)),
        ("author count", str(ov.authors)),
        ("messages", "on" if rep.messages else "off"),
        ("diffs", "on" if rep.diffs is not None else "off"),
        ("authors", "on" if h.authors is not None else "off"),
        ("redaction", "on"),
        ("path", scope),           # last: a path may hold anything
    ]


def overview_sentence(rep):
    h, ov = rep.history, rep.history.overview
    text = (f"{_plural(ov.commits, 'commit')} ({_plural(ov.merges, 'merge')}) from "
            f"{_day(ov.first)} to {_day(ov.last)} by {_plural(ov.authors, 'author')}; "
            f"{_plural(len(h.files), 'file')} present, {len(h.deleted)} deleted")
    top = h.hotspots.files_by_changes
    if top:
        # Quoted and last, so a re-matching redaction label stops at the quote.
        text += f"; most changed ({_plural(top[0].changes, 'commit')}): \"{top[0].path}\""
    return text + "."


def _hotspot_tables(h):
    """(title, rows) for all-time and recent hotspots."""
    out = []
    for spots, when in ((h.hotspots, "all time"),
                        (h.recent, f"last {h.recent.days} days, since {_day(h.recent.since)}")):
        out += [(f"files by changes ({when})", spots.files_by_changes),
                (f"files by churn ({when})", spots.files_by_churn),
                (f"directories by changes ({when})", spots.dirs_by_changes),
                (f"directories by churn ({when})", spots.dirs_by_churn)]
    return out


def _commit_rows(rep):
    """(commit, subject or None) for the per-path list or, with --messages
    and no --path, the latest commits."""
    h = rep.history
    if h.path is not None:
        listed = h.commits[:PATH_COMMITS]
    elif rep.messages:
        listed = h.commits[:LATEST]
    else:
        return None
    return [(c, rep.subjects.get(c.sha) if rep.messages else None) for c in listed]


def _commit_title(rep):
    if rep.history.path is not None:
        return "commits touching the path (newest first)"
    return "latest commits (newest first)"


# --- text ------------------------------------------------------------------

def _section(out, title):
    out += ["", title, "-" * len(title)]


def _render_text(rep):
    e, h = escape_text, rep.history
    out = ["gitarch report", "=============="]
    out += [f"{label}: {e(value)}" for label, value in _header(rep)]
    _section(out, "overview")
    out.append(e(overview_sentence(rep)))

    for title, rows in _hotspot_tables(h):
        _section(out, title)
        out.append(f"{'CHANGES':>7}  {'CHURN':>9}  PATH")
        out += [f"{s.changes:>7}  {s.churn:>9}  {e(s.path)}" for s in rows] or ["none"]

    _section(out, f"activity (per {h.activity_unit})")
    peak = max((n for _, n in h.activity), default=0)
    out += [f"{label:<8}  {n:>5}  {_bar(n, peak)}".rstrip() for label, n in h.activity]

    _section(out, f"file timeline ({len(h.files)})")
    out.append(f"{'INTRODUCED':<10}  {'LAST CHANGED':<12}  {'COMMITS':>7}  "
               f"{'LINES':<15}  PATH")
    for f in h.files:
        intro = _day(f.introduced[1]) if f.introduced else "-"
        out.append(f"{intro:<10}  {_day(f.last_changed[1]):<12}  {f.commits:>7}  "
                   f"{_lines(f.added, f.deleted):<15}  {e(f.path)}")
        for r in f.renames:
            out.append(f"    renamed in {_short(r.sha)} {_day(r.date)} from: {e(r.old)}")
    if not h.files:
        out.append("none")

    _section(out, f"deleted files ({len(h.deleted)})")
    for f in h.deleted:
        out.append(f"deleted {_day(f.deleted_in[1])} in {_short(f.deleted_in[0])}  "
                   f"{f.commits:>5} commits  {e(f.path)}")
    if not h.deleted:
        out.append("none")

    if h.authors is not None:
        _section(out, f"authors ({len(h.authors)})")
        out += [f"{n:>7}  {e(name)}" for name, n in h.authors]

    rows = _commit_rows(rep)
    if rows is not None:
        _section(out, _commit_title(rep))
        for c, subject in rows:
            line = f"{_short(c.sha)}  {_date(c.date)}  {_lines(c.added, c.deleted):<15}"
            if c.merge:
                line += "  merge"
            if subject is not None:
                line += f"  {e(subject)}"
            out.append(line.rstrip())

    if rep.diffs is not None:
        _section(out, f"diffs (newest {len(rep.diffs)} commits, at most {DIFF_LINES} lines each)")
        for d in rep.diffs:
            out.append(f"commit {_short(d.sha)} {_date(d.date)}")
            out += ["    " + e(line) for line in d.lines]
            out += _diff_notes(d)
    return "\n".join(out) + "\n"


def _diff_notes(d):
    notes = []
    if d.binary:
        notes.append(f"    ({_plural(d.binary, 'binary file diff')} skipped)")
    if d.more:
        notes.append(f"    ... {d.more} more lines")
    return notes


# --- Markdown --------------------------------------------------------------

def _render_md(rep):
    e, h = escape_md, rep.history
    out = ["# gitarch report", "", "## Header", ""]
    out += [f"- {e(label)}: {e(value)}" for label, value in _header(rep)]
    out += ["", "## Overview", "", e(overview_sentence(rep))]

    out += ["", "## Hotspots"]
    for title, rows in _hotspot_tables(h):
        out += ["", f"### {e(title)}", ""]
        if not rows:
            out.append("none")
            continue
        out += ["| changes | churn | path |", "| ---: | ---: | --- |"]
        out += [f"| {s.changes} | {s.churn} | {e(s.path)}" for s in rows]   # last cell open

    out += ["", f"## Activity (per {h.activity_unit})", "", "```"]
    peak = max((n for _, n in h.activity), default=0)
    out += [f"{label:<8}  {n:>5}  {_bar(n, peak)}".rstrip() for label, n in h.activity]
    out.append("```")

    out += ["", f"## File timeline ({len(h.files)})", ""]
    if h.files:
        out += ["| introduced | last changed | commits | lines | path |",
                "| --- | --- | ---: | --- | --- |"]
    for f in h.files:
        intro = _day(f.introduced[1]) if f.introduced else "-"
        out.append(f"| {intro} | {_day(f.last_changed[1])} | {f.commits} | "
                   f"{_lines(f.added, f.deleted)} | {e(f.path)}")
    renames = [(f, r) for f in h.files for r in f.renames]
    if renames:
        out += ["", "Renames:", ""]
        for _, r in renames:                  # one path per line, last
            out += [f"- {_short(r.sha)} {_day(r.date)} to: {e(r.new)}",
                    f"    - from: {e(r.old)}"]
    if not h.files:
        out.append("none")

    out += ["", f"## Deleted files ({len(h.deleted)})", ""]
    out += [f"- deleted {_day(f.deleted_in[1])} in {_short(f.deleted_in[0])}, "
            f"{_plural(f.commits, 'commit')}: {e(f.path)}" for f in h.deleted] or ["none"]

    if h.authors is not None:
        out += ["", f"## Authors ({len(h.authors)})", ""]
        out += [f"- {n}: {e(name)}" for name, n in h.authors]

    rows = _commit_rows(rep)
    if rows is not None:
        out += ["", f"## {e(_commit_title(rep))}", ""]
        for c, subject in rows:
            line = f"- {_short(c.sha)} {_date(c.date)} {_lines(c.added, c.deleted)}"
            if c.merge:
                line += " merge"
            if subject is not None:
                line += f": {e(subject)}"
            out.append(line)

    if rep.diffs is not None:
        out += ["", f"## Diffs (newest {len(rep.diffs)} commits, at most {DIFF_LINES} lines each)"]
        for d in rep.diffs:
            out += ["", f"- commit {_short(d.sha)} {_date(d.date)}"]
            out += [f"    - {e(line)}" for line in d.lines]
            out += ["    - " + note.strip() for note in _diff_notes(d)]
    return "\n".join(out) + "\n"


# --- HTML ------------------------------------------------------------------

_CSS = """\
body{font-family:system-ui,sans-serif;margin:1.5rem;color:#1b1b1b;background:#fff}
table{border-collapse:collapse;margin:.5rem 0}
td,th{border:1px solid #ccc;padding:.2rem .5rem;text-align:left;vertical-align:top}
td.n{text-align:right}
code,pre{font-family:ui-monospace,monospace;white-space:pre-wrap}
.b{fill:#2f6db5}
@media (prefers-color-scheme: dark){body{color:#e6e6e6;background:#161616}
td,th{border-color:#444}}
"""
_SVG_BAR, _SVG_HEIGHT = 8, 100


def _html_value(value):
    """Escaped value plus a newline, so markup never follows on the same line."""
    return escape_html(value) + "\n"


def _svg(activity):
    peak = max((n for _, n in activity), default=0)
    width = max(len(activity) * _SVG_BAR, _SVG_BAR)
    out = [f'<svg width="{width}" height="{_SVG_HEIGHT}" role="img" '
           f'viewBox="0 0 {width} {_SVG_HEIGHT}">',
           "<title>commits per period</title>"]
    for i, (label, n) in enumerate(activity):
        if not n:
            continue
        height = _SVG_HEIGHT * n / peak
        out.append(f'<rect class="b" x="{i * _SVG_BAR}" y="{_SVG_HEIGHT - height:.2f}" '
                   f'width="{_SVG_BAR - 1}" height="{height:.2f}">'
                   f"<title>{escape_html(label)}: {n}</title></rect>")
    out.append("</svg>")
    return out


def _render_html(rep):
    e, h = _html_value, rep.history
    out = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta http-equiv="Content-Security-Policy" '
           "content=\"default-src 'none'; style-src 'unsafe-inline'\">",
           "<title>gitarch report</title>", f"<style>\n{_CSS}</style>", "</head>",
           "<body>", "<h1>gitarch report</h1>", "<h2>Header</h2>", "<table>"]
    out += [f"<tr><th>{e(label)}</th><td>{e(value)}</td></tr>" for label, value in _header(rep)]
    out += ["</table>", "<h2>Overview</h2>", f"<p>{e(overview_sentence(rep))}</p>"]

    out.append("<h2>Hotspots</h2>")
    for title, rows in _hotspot_tables(h):
        out.append(f"<h3>{e(title)}</h3>")
        if not rows:
            out.append("<p>none</p>")
            continue
        out += ["<table>", "<tr><th>changes</th><th>churn</th><th>path</th></tr>"]
        out += [f'<tr><td class="n">{s.changes}</td><td class="n">{s.churn}</td>'
                f"<td><code>{e(s.path)}</code></td></tr>" for s in rows]
        out.append("</table>")

    out.append(f"<h2>Activity (per {h.activity_unit})</h2>")
    out += _svg(h.activity)
    out += ["<table>", "<tr><th>period</th><th>commits</th></tr>"]
    out += [f'<tr><td>{escape_html(label)}</td><td class="n">{n}</td></tr>'
            for label, n in h.activity]
    out.append("</table>")

    out.append(f"<h2>File timeline ({len(h.files)})</h2>")
    if h.files:
        out += ["<table>", "<tr><th>introduced</th><th>last changed</th><th>commits</th>"
                "<th>lines</th><th>path</th></tr>"]
        for f in h.files:
            intro = _day(f.introduced[1]) if f.introduced else "-"
            renames = "".join(f"<br>renamed in {_short(r.sha)} {_day(r.date)} from: "
                              f"<code>{e(r.old)}</code>" for r in f.renames)
            out.append(f"<tr><td>{intro}</td><td>{_day(f.last_changed[1])}</td>"
                       f'<td class="n">{f.commits}</td><td>{_lines(f.added, f.deleted)}</td>'
                       f"<td><code>{e(f.path)}</code>{renames}</td></tr>")
        out.append("</table>")
    else:
        out.append("<p>none</p>")

    out += [f"<h2>Deleted files ({len(h.deleted)})</h2>", "<ul>"]
    out += [f"<li>deleted {_day(f.deleted_in[1])} in {_short(f.deleted_in[0])}, "
            f"{_plural(f.commits, 'commit')}: <code>{e(f.path)}</code></li>"
            for f in h.deleted] or ["<li>none</li>"]
    out.append("</ul>")

    if h.authors is not None:
        out += [f"<h2>Authors ({len(h.authors)})</h2>", "<ul>"]
        out += [f"<li>{n}: {e(name)}</li>" for name, n in h.authors]
        out.append("</ul>")

    rows = _commit_rows(rep)
    if rows is not None:
        out += [f"<h2>{escape_html(_commit_title(rep))}</h2>", "<ul>"]
        for c, subject in rows:
            line = f"<li>{_short(c.sha)} {_date(c.date)} {_lines(c.added, c.deleted)}"
            if c.merge:
                line += " merge"
            if subject is not None:
                line += f": {e(subject)}"
            out.append(line + "</li>")
        out.append("</ul>")

    if rep.diffs is not None:
        out.append(f"<h2>Diffs (newest {len(rep.diffs)} commits, at most {DIFF_LINES} lines "
                   "each)</h2>")
        for d in rep.diffs:
            out.append(f"<h3>commit {_short(d.sha)} {_date(d.date)}</h3>")
            out.append("<pre>" + "".join(e(line) for line in d.lines) + "</pre>")
            out += [f"<p>{note.strip()}</p>" for note in _diff_notes(d)]
    out += ["</body>", "</html>"]
    return "\n".join(out) + "\n"


_RENDERERS = {"text": _render_text, "md": _render_md, "html": _render_html}


def render(report, fmt="text"):
    """The rendered document, already passed through redact(); the CLI
    redacts it once more on the way out."""
    return redact(_RENDERERS[fmt](report))
