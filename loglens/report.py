"""Reports in text, Markdown and HTML.

All three formats render the same sections from one Report. Every dynamic
string passes the format's escape step (control characters as \\xNN, bidi
controls as \\uXXXX; Markdown and HTML escape their own syntax on top). In
text and Markdown a literal backslash is doubled first, so a typed "\\x1b"
shows as "\\\\x1b" and only a real control shows as "\\x1b". The CLI then
sends the whole document through redact() on the way out.

Values that can contain redacted text are placed last on their line, quoted,
or (in HTML) followed by a newline: the final redact() pass leaves a label
followed by just the marker alone, but re-matches anything after the label up
to the end of the line or a quote.
"""
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from loglens.group import group_entries
from loglens.readers import LEVEL_RANK, WARNING
from loglens.redact import redact
from loglens.timeline import (AUTO, MIN_SPIKE_BUCKETS, OUTLIER_SECONDS, TOP_GROUPS,
                              build_timeline, incidents)

FORMATS = ("text", "md", "html")
BAR_WIDTH = 50
SPIKES_IN_SUMMARY = 3
NO_INCIDENTS = "no warning or error groups"
MIN_EMPTY_RUN = 5

_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028\u2029"
                     r"\u202a-\u202e\u2066-\u2069]")
_MD_SPECIAL = re.compile(r"([\\|`\[\]])")


def _escape_char(match):
    code = ord(match.group(0))
    return f"\\x{code:02x}" if code <= 0xff else f"\\u{code:04x}"


def _escape_controls(value):
    """Control characters -> \\xNN, bidi and line-separator characters -> \\uXXXX."""
    return _UNSAFE.sub(_escape_char, str(value))


def _double_backslashes(value):
    return str(value).replace("\\", "\\\\")


def escape_text(value):
    """Backslashes doubled, then controls escaped: the two never look alike."""
    return _escape_controls(_double_backslashes(value))


def escape_md(value):
    """Backslashes doubled before Markdown escaping, so they render doubled."""
    escaped = _MD_SPECIAL.sub(r"\\\1", _double_backslashes(value))
    return _escape_controls(html.escape(escaped, quote=False))


def escape_html(value):
    # Values only ever land in text nodes, never in attributes, so quotes stay
    # literal; that also lets the final redact() pass stop at them. Backslashes
    # stay single: HTML has no backslash escapes to confuse them with.
    return html.escape(_escape_controls(value), quote=False)


def _ts(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else "-"


def _bucket_ts(epoch):
    return _ts(datetime.fromtimestamp(epoch, tz=timezone.utc))


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _is_serious(group):
    return LEVEL_RANK.get(group.level, 0) >= LEVEL_RANK[WARNING]


@dataclass
class Report:
    files: list           # (name, size in bytes or None)
    header: list          # (label, value)
    summary: str
    groups: list
    timeline: object
    incidents: list
    raw: bool
    context: int


def _header(result, timeline, *, raw, bucket_spec):
    total = len(result.entries) + len(result.untimed)
    invalid = [f"{name}:{num}" for name, nums in result.invalid_json_lines.items()
               for num in nums][:3]
    invalid_text = str(result.invalid_json)
    if invalid:
        invalid_text += f" (first: {', '.join(invalid)})"
    if timeline.size:
        bucket = timeline.name + (" (auto)" if bucket_spec == "auto" else "")
    else:
        bucket = "- (no timestamped entries)"
    return [
        ("lines read", str(result.lines_read)),
        ("entries", str(total)),
        ("untimed entries (not on the timeline)", str(len(result.untimed))),
        ("invalid JSONL lines", invalid_text),
        ("replaced non-UTF-8 bytes", str(result.replaced_bytes)),
        ("sampled", f"first {result.max_lines} lines only" if result.sampled else "no"),
        ("syslog year", str(result.year) if result.syslog_used else "not used"),
        ("bucket", bucket),
        ("raw lines", "on" if raw else "off"),
        ("redaction", "on"),
    ]


def _spike_phrase(timeline):
    spikes = timeline.spikes
    if spikes is None:
        return f"spike detection needs >= {MIN_SPIKE_BUCKETS} buckets"
    if not spikes:
        return "no spikes"
    listed = ", ".join(f"{_bucket_ts(b.start)} ({b.total} events)"
                       for b in spikes[:SPIKES_IN_SUMMARY])
    more = len(spikes) - SPIKES_IN_SUMMARY
    return (f"{_plural(len(spikes), 'spike')} at {listed}"
            + (f" and {more} more" if more > 0 else ""))


def summary_sentence(groups, timeline):
    """One deterministic sentence; no clock time."""
    serious = [g for g in groups if _is_serious(g)]
    if not serious:
        return f"0 groups at WARNING or above; {_spike_phrase(timeline)}."
    files = {name for g in serious for name in g.files}
    worst = serious[0]
    # The signature is quoted so a re-matching redaction label stops at the quote.
    return (f"{_plural(len(serious), 'group')} at WARNING or above in "
            f"{_plural(len(files), 'file')}; worst: {worst.level} "
            f"\"{worst.signature}\" x{worst.count} (first {_ts(worst.first_ts)}, "
            f"last {_ts(worst.last_ts)}); {_spike_phrase(timeline)}.")


def build_report(result, groups, timeline, incidents, *, raw=False, context=0,
                 bucket_spec="auto"):
    files = [(name, result.file_sizes.get(name)) for name in result.files]
    return Report(files, _header(result, timeline, raw=raw, bucket_spec=bucket_spec),
                  summary_sentence(groups, timeline), groups, timeline, incidents,
                  raw, context)


def analyse(result, *, raw=False, bucket=AUTO, context=0):
    """ReadResult -> Report: groups, timeline, spikes and incident view."""
    sequence = result.entries + result.untimed
    groups = group_entries(sequence, keep_examples=raw)
    timeline = build_timeline(result.entries, bucket)
    view = incidents(groups, sequence, context=context, raw=raw)
    return build_report(result, groups, timeline, view, raw=raw, context=context,
                        bucket_spec=bucket)


def _bar(total, peak):
    if not total or not peak:
        return ""
    return "#" * max(1, round(total * BAR_WIDTH / peak))


def _timeline_rows(tl):
    """Buckets in order; each run of MIN_EMPTY_RUN or more buckets without
    ERROR+/WARNING events is replaced by its length (an int). Display only:
    spike detection always sees every bucket."""
    rows, run = [], []
    for b in [*tl.buckets, None]:
        if b is not None and not b.total:
            run.append(b)
            continue
        rows += [len(run)] if len(run) >= MIN_EMPTY_RUN else run
        run = []
        if b is not None:
            rows.append(b)
    return rows


def _empty_run(count):
    return f"... {count} empty buckets ..."


def _bucket_line(b, peak):
    return (f"{_bucket_ts(b.start)}  E {b.errors:>6}  W {b.warnings:>6}  "
            f"{_bar(b.total, peak)}").rstrip()


def _warning_lines(tl):
    if not tl.outliers:
        return ["none"]
    count = len(tl.outliers)
    first = min(e.ts for e in tl.outliers)
    return [f"{count} {'entry' if count == 1 else 'entries'} with outlying "
            f"timestamps (first {_ts(first)}); more than "
            f"{OUTLIER_SECONDS // 86400} days from the median, counted in their "
            "groups but left off the timeline"]


def _origin(group):
    """'hosts: 2 (web1, web2)  apps: 1 (nginx)' for syslog groups, else None."""
    if not group.hosts and not group.apps:
        return None
    return "  ".join(f"{label}: {len(names)} ({', '.join(group.origin_names(names))})"
                     for label, names in (("hosts", group.hosts), ("apps", group.apps)))


def _size(size):
    return "?" if size is None else f"{size} bytes"


# --- text ------------------------------------------------------------------

def _section(out, title):
    out += ["", title, "-" * len(title)]


def _render_text(rep):
    e = escape_text
    out = ["loglens report", "=============="]
    out.append("files:")
    out += [f"  {e(_size(size))}  {e(name)}" for name, size in rep.files]
    out += [f"{e(label)}: {e(value)}" for label, value in rep.header]

    _section(out, "summary")
    out.append(e(rep.summary))

    _section(out, "warnings")
    out += [e(line) for line in _warning_lines(rep.timeline)]

    _section(out, f"groups ({len(rep.groups)})")
    out.append(f"{'LEVEL':<8} {'COUNT':>7}  {'FIRST':<20}  {'LAST':<20}  SIGNATURE")
    for g in rep.groups:
        out.append(f"{e(g.level):<8} {g.count:>7}  {_ts(g.first_ts):<20}  "
                   f"{_ts(g.last_ts):<20}  {e(g.signature)}")

    tl = rep.timeline
    _section(out, f"timeline (bucket {e(tl.name)}; E = ERROR and above, W = WARNING)")
    if not tl.buckets:
        out.append("no timestamped entries")
    peak = max((b.total for b in tl.buckets), default=0)
    out += [_empty_run(row) if isinstance(row, int) else _bucket_line(row, peak)
            for row in _timeline_rows(tl)]

    _section(out, "spikes")
    out += [e(line) for line in _spike_lines(tl)]

    _section(out, f"incident view (top {TOP_GROUPS})")
    if not rep.incidents:
        out.append(NO_INCIDENTS)
    for n, inc in enumerate(rep.incidents, 1):
        g = inc.group
        out.append(f"{n}. {e(g.level)} x{g.count}  {e(g.signature)}")
        out.append(f"   first {_ts(g.first_ts)}  last {_ts(g.last_ts)}")
        out.append(f"   files: {e(', '.join(g.files))}")
        if _origin(g):
            out.append(f"   {e(_origin(g))}")
        for label, items in (("before", inc.before), ("after", inc.after)):
            if rep.context <= 0:
                continue
            out.append(f"   {label} ({len(items)}):")
            for item in items:
                out.append(f"     {e(item.level):<8} {e(item.signature)}")
                if item.message is not None:
                    out.append(f"       message: {e(item.message)}")

    if rep.raw:
        _section(out, "examples (--raw, redacted)")
        for g in rep.groups:
            out.append(f"{e(g.level):<8} {e(g.signature)}")
            for example in g.examples:
                out.append("  example:")
                out += ["    " + e(line) for line in example.split("\n")]
    return "\n".join(out) + "\n"


def _spike_lines(tl):
    if tl.spikes is None:
        return [f"spike detection needs >= {MIN_SPIKE_BUCKETS} buckets "
                f"(have {len(tl.buckets)})"]
    lines = [f"threshold: more than {tl.threshold:g} ERROR+/WARNING events per bucket "
             "(median + 3 * max(MAD, 1))"]
    if not tl.spikes:
        lines.append("no spikes")
    lines += [f"{_bucket_ts(b.start)}  {b.total} events" for b in tl.spikes]
    return lines


# --- Markdown --------------------------------------------------------------

def _render_md(rep):
    e = escape_md
    out = ["# loglens report", "", "## Header", "", "Files:", ""]
    out += [f"- {e(_size(size))}: {e(name)}" for name, size in rep.files]
    out.append("")
    out += [f"- {e(label)}: {e(value)}" for label, value in rep.header]

    out += ["", "## Summary", "", e(rep.summary)]

    out += ["", "## Warnings", ""]
    out += [f"- {e(line)}" for line in _warning_lines(rep.timeline)]

    out += ["", f"## Groups ({len(rep.groups)})", ""]
    out += ["| level | count | first | last | signature |",
            "| --- | ---: | --- | --- | --- |"]
    for g in rep.groups:
        out.append(f"| {e(g.level)} | {g.count} | {_ts(g.first_ts)} | {_ts(g.last_ts)} "
                   f"| {e(g.signature)}")       # last cell: no trailing pipe

    tl = rep.timeline
    out += ["", f"## Timeline (bucket {e(tl.name)}; E = ERROR and above, W = WARNING)", ""]
    if not tl.buckets:
        out.append("No timestamped entries.")
    else:
        peak = max(b.total for b in tl.buckets)
        out.append("```")
        out += [_empty_run(row) if isinstance(row, int) else _bucket_line(row, peak)
                for row in _timeline_rows(tl)]
        out.append("```")

    out += ["", "## Spikes", ""]
    out += [f"- {e(line)}" for line in _spike_lines(tl)]

    out += ["", f"## Incident view (top {TOP_GROUPS})", ""]
    if not rep.incidents:
        out.append(NO_INCIDENTS)
    for n, inc in enumerate(rep.incidents, 1):
        g = inc.group
        out.append(f"{n}. {e(g.level)} x{g.count}: {e(g.signature)}")
        out.append(f"    - first {_ts(g.first_ts)}, last {_ts(g.last_ts)}")
        out.append(f"    - files: {e(', '.join(g.files))}")
        if _origin(g):
            out.append(f"    - {e(_origin(g))}")
        for label, items in (("before", inc.before), ("after", inc.after)):
            if rep.context <= 0:
                continue
            out.append(f"    - {label} ({len(items)}):")
            for item in items:
                out.append(f"        - {e(item.level)}: {e(item.signature)}")
                if item.message is not None:
                    out.append(f"            - message: {e(item.message)}")

    if rep.raw:
        out += ["", "## Examples (--raw, redacted)", ""]
        for g in rep.groups:
            out.append(f"- {e(g.level)}: {e(g.signature)}")
            for example in g.examples:
                out.append("    - example:")
                out += [f"        - {e(line)}" for line in example.split("\n")]
    return "\n".join(out) + "\n"


# --- HTML ------------------------------------------------------------------

_CSS = """\
body{font-family:system-ui,sans-serif;margin:1.5rem;color:#1b1b1b;background:#fff}
table{border-collapse:collapse;margin:.5rem 0}
td,th{border:1px solid #ccc;padding:.2rem .5rem;text-align:left;vertical-align:top}
td.n{text-align:right}
code,pre{font-family:ui-monospace,monospace;white-space:pre-wrap}
.e{fill:#c0392b}.w{fill:#d4a017}
@media (prefers-color-scheme: dark){body{color:#e6e6e6;background:#161616}
td,th{border-color:#444}}
"""
_SVG_BAR, _SVG_HEIGHT = 6, 120


def _svg(tl):
    peak = max((b.total for b in tl.buckets), default=0)
    width = max(len(tl.buckets) * _SVG_BAR, _SVG_BAR)
    out = [f'<svg width="{width}" height="{_SVG_HEIGHT}" role="img" '
           f'viewBox="0 0 {width} {_SVG_HEIGHT}">',
           "<title>ERROR and above (red) and WARNING (amber) per bucket</title>"]
    for i, b in enumerate(tl.buckets):
        if not b.total:
            continue
        x = i * _SVG_BAR
        err_h = _SVG_HEIGHT * b.errors / peak
        warn_h = _SVG_HEIGHT * b.warnings / peak
        tip = f"<title>{_bucket_ts(b.start)} E {b.errors} W {b.warnings}</title>"
        if b.errors:
            out.append(f'<rect class="e" x="{x}" y="{_SVG_HEIGHT - err_h:.2f}" '
                       f'width="{_SVG_BAR - 1}" height="{err_h:.2f}">{tip}</rect>')
        if b.warnings:
            out.append(f'<rect class="w" x="{x}" y="{_SVG_HEIGHT - err_h - warn_h:.2f}" '
                       f'width="{_SVG_BAR - 1}" height="{warn_h:.2f}">{tip}</rect>')
    out.append("</svg>")
    return out


def _html_value(value):
    """Escaped value plus a newline, so markup never follows on the same line."""
    return escape_html(value) + "\n"


def _render_html(rep):
    e = _html_value
    out = ["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta http-equiv="Content-Security-Policy" '
           "content=\"default-src 'none'; style-src 'unsafe-inline'\">",
           "<title>loglens report</title>", f"<style>\n{_CSS}</style>", "</head>",
           "<body>", "<h1>loglens report</h1>", "<h2>Header</h2>",
           "<table>", "<tr><th>file</th><th>size</th></tr>"]
    out += [f"<tr><td class=\"n\">{e(_size(size))}</td><td>{e(name)}</td></tr>"
            for name, size in rep.files]
    out += ["</table>", "<table>"]
    out += [f"<tr><th>{e(label)}</th><td>{e(value)}</td></tr>"
            for label, value in rep.header]
    out += ["</table>", "<h2>Summary</h2>", f"<p>{e(rep.summary)}</p>"]
    out += ["<h2>Warnings</h2>", "<ul>"]
    out += [f"<li>{e(line)}</li>" for line in _warning_lines(rep.timeline)]
    out.append("</ul>")

    out += [f"<h2>Groups ({len(rep.groups)})</h2>", "<table>",
            "<tr><th>level</th><th>count</th><th>first</th><th>last</th>"
            "<th>signature</th></tr>"]
    for g in rep.groups:
        out.append(f"<tr><td>{e(g.level)}</td><td class=\"n\">{g.count}</td>"
                   f"<td>{_ts(g.first_ts)}</td><td>{_ts(g.last_ts)}</td>"
                   f"<td><code>{e(g.signature)}</code></td></tr>")
    out.append("</table>")

    tl = rep.timeline
    out.append(f"<h2>Timeline (bucket {e(tl.name)}; red = ERROR and above, "
               "amber = WARNING)</h2>")
    if not tl.buckets:
        out.append("<p>No timestamped entries.</p>")
    else:
        out += _svg(tl)
        out.append(f"<p>{len(tl.buckets)} buckets from {_bucket_ts(tl.buckets[0].start)} "
                   f"to {_bucket_ts(tl.buckets[-1].start)}</p>")
        out += ["<table>", "<tr><th>bucket</th><th>E</th><th>W</th></tr>"]
        for row in _timeline_rows(tl):
            if isinstance(row, int):
                out.append(f'<tr><td colspan="3">{_empty_run(row)}</td></tr>')
            else:
                out.append(f"<tr><td>{_bucket_ts(row.start)}</td>"
                           f'<td class="n">{row.errors}</td>'
                           f'<td class="n">{row.warnings}</td></tr>')
        out.append("</table>")

    out += ["<h2>Spikes</h2>", "<ul>"]
    out += [f"<li>{e(line)}</li>" for line in _spike_lines(tl)]
    out += ["</ul>", f"<h2>Incident view (top {TOP_GROUPS})</h2>"]
    if not rep.incidents:
        out.append(f"<p>{NO_INCIDENTS}</p>")
    out.append("<ol>")
    for inc in rep.incidents:
        g = inc.group
        out.append(f"<li><p>{e(g.level)} x{g.count}: <code>{e(g.signature)}</code></p>")
        out.append(f"<p>first {_ts(g.first_ts)}, last {_ts(g.last_ts)}; "
                   f"files: {e(', '.join(g.files))}</p>")
        if _origin(g):
            out.append(f"<p>{e(_origin(g))}</p>")
        for label, items in (("before", inc.before), ("after", inc.after)):
            if rep.context <= 0:
                continue
            out.append(f"<p>{label} ({len(items)}):</p>")
            out.append("<ul>")
            for item in items:
                line = f"<li>{e(item.level)}: <code>{e(item.signature)}</code>"
                if item.message is not None:
                    line += f"<br>message: <code>{e(item.message)}</code>"
                out.append(line + "</li>")
            out.append("</ul>")
        out.append("</li>")
    out.append("</ol>")

    if rep.raw:
        out.append("<h2>Examples (--raw, redacted)</h2>")
        for g in rep.groups:
            out.append(f"<h3>{e(g.level)}: <code>{e(g.signature)}</code></h3>")
            for example in g.examples:
                out.append("<pre>" + "".join(e(line) for line in example.split("\n"))
                           + "</pre>")
    out += ["</body>", "</html>"]
    return "\n".join(out) + "\n"


_RENDERERS = {"text": _render_text, "md": _render_md, "html": _render_html}


def render(report, fmt="text"):
    """The rendered document, already passed through redact(); the CLI
    redacts it once more on the way out."""
    return redact(_RENDERERS[fmt](report))
