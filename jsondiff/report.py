"""Report rendering: terminal text, Markdown and single-file HTML.

Every source-derived string (paths, keys, file names, warnings, values) goes
through exactly one escape helper per format. Output is deterministic: no
timestamps, no absolute paths, fixed ordering.
"""
import html
import unicodedata

from jsondiff.diff import BREAKING_LABEL, CATEGORIES, UNKEYED_NOTE

TITLE = "jsondiff report"
REVIEW_SENTENCE = ('Changes flagged "potentially breaking \u2014 review" are '
                   "candidates for review, not proof of breakage.")
VALUES_ON = "values: on"
VALUES_OFF = "values: off (use --values to show example values)"

_BIDI = frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A))
_MD_SPECIAL = "\\`*_[]|"
_MD_ENTITIES = {"<": "&lt;", ">": "&gt;", "&": "&amp;"}


def _visible(text):
    """Make control and bidi characters visible: Cc -> \\xNN, bidi -> \\uXXXX."""
    out = []
    for ch in str(text):
        code = ord(ch)
        if unicodedata.category(ch) == "Cc":
            out.append(f"\\x{code:02x}")
        elif code in _BIDI:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def esc_terminal(text):
    return _visible(text)


def esc_md(text):
    out = []
    for ch in _visible(text):
        if ch in _MD_SPECIAL:
            out.append("\\" + ch)
        else:
            out.append(_MD_ENTITIES.get(ch, ch))
    return "".join(out)


def esc_html(text):
    return html.escape(_visible(text), quote=True)


# ---------------------------------------------------------------- shared model

def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _input_line(meta):
    """Description of one input, without the file name (escaped separately)."""
    if meta["kind"] == "json":
        return "JSON"
    parts = ["JSONL", _plural(meta["records"], "record")]
    if meta["sampled"]:
        parts.append("sampled")
    invalid = meta["invalid_lines"]
    text = _plural(invalid["count"], "invalid line")
    if invalid["first"]:
        text += " (first: " + ", ".join(str(n) for n in invalid["first"]) + ")"
    parts.append(text)
    return ", ".join(parts)


def _sorted_changes(result):
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return sorted(result["changes"],
                  key=lambda c: (not c["breaking"], c["path"], order[c["category"]]))


def _summary_rows(result):
    counts = {c: 0 for c in CATEGORIES}
    for change in result["changes"]:
        counts[change["category"]] += 1
    rows = [(c, counts[c]) for c in CATEGORIES]
    rows.append(("unchanged", result["unchanged"]))
    rows.append(("potentially breaking",
                 sum(1 for c in result["changes"] if c["breaking"])))
    return rows


def _flag(change):
    return BREAKING_LABEL if change["breaking"] else ""


def _key_line(match):
    return (f"{match['only_in_new']} added, {match['only_in_old']} removed, "
            f"{match['changed']} changed, {match['unchanged']} unchanged; "
            f"missing key: old {match['missing_key_old']}, "
            f"new {match['missing_key_new']}")


# -------------------------------------------------------------------- terminal

def render_terminal(result, show_all=False):
    e = esc_terminal
    old, new = result["inputs"]["old"], result["inputs"]["new"]
    lines = [TITLE,
             f"old: {e(old['file'])} ({_input_line(old)})",
             f"new: {e(new['file'])} ({_input_line(new)})",
             VALUES_ON if result["values"] else VALUES_OFF,
             REVIEW_SENTENCE]
    if result["unkeyed_arrays"]:
        lines.append(f"{UNKEYED_NOTE}: "
                     + ", ".join(e(p) for p in result["unkeyed_arrays"]))

    lines += ["", "Summary"]
    lines += [f"  {label}: {count}" for label, count in _summary_rows(result)]

    lines += ["", "Changes"]
    changes = _sorted_changes(result)
    if not changes:
        lines.append("  none")
    else:
        rows = [("PATH", "CATEGORY", "DETAIL", "FLAG")]
        rows += [(e(c["path"]), c["category"], e(c["detail"]), _flag(c))
                 for c in changes]
        widths = [min(max(len(r[i]) for r in rows), 60) for i in range(3)]
        for index, row in enumerate(rows):
            text = "  " + "  ".join(row[i].ljust(widths[i]) for i in range(3))
            lines.append((text + "  " + row[3]).rstrip())
            if index and result["values"]:
                change = changes[index - 1]
                lines.append("      old values: "
                             + (", ".join(e(v) for v in change["old_values"]) or "-"))
                lines.append("      new values: "
                             + (", ".join(e(v) for v in change["new_values"]) or "-"))

    if result["key_matches"]:
        lines += ["", "Key matches"]
        for match in result["key_matches"]:
            lines.append(f"  {e(match['path'])} (key {e(match['field'])}): "
                         f"{_key_line(match)}")

    if show_all:
        lines += ["", "Unchanged paths"]
        lines += [f"  {e(p)}" for p in result["unchanged_paths"]] or ["  none"]

    lines += ["", "Warnings"]
    lines += [f"  {e(w)}" for w in result["warnings"]] or ["  none"]
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- markdown

def _md_row(cells):
    return "| " + " | ".join(cells) + " |"


def render_md(result, show_all=False):
    e = esc_md
    old, new = result["inputs"]["old"], result["inputs"]["new"]
    lines = [f"# {TITLE}", "",
             f"- old: {e(old['file'])} ({_input_line(old)})",
             f"- new: {e(new['file'])} ({_input_line(new)})",
             f"- {VALUES_ON if result['values'] else e(VALUES_OFF)}",
             "", e(REVIEW_SENTENCE)]
    if result["unkeyed_arrays"]:
        lines += ["", f"{e(UNKEYED_NOTE)}: "
                  + ", ".join(e(p) for p in result["unkeyed_arrays"])]

    lines += ["", "## Summary", "", _md_row(["category", "count"]),
              _md_row(["---", "---:"])]
    lines += [_md_row([label, str(count)]) for label, count in _summary_rows(result)]

    lines += ["", "## Changes", ""]
    changes = _sorted_changes(result)
    if not changes:
        lines.append("none")
    else:
        header = ["path", "category", "detail", "flag"]
        if result["values"]:
            header += ["old values", "new values"]
        lines += [_md_row(header), _md_row(["---"] * len(header))]
        for c in changes:
            cells = [e(c["path"]), c["category"], e(c["detail"]), e(_flag(c))]
            if result["values"]:
                cells += [", ".join(e(v) for v in c["old_values"]),
                          ", ".join(e(v) for v in c["new_values"])]
            lines.append(_md_row(cells))

    if result["key_matches"]:
        lines += ["", "## Key matches", "",
                  _md_row(["array", "key", "added", "removed", "changed",
                           "unchanged", "missing key (old)", "missing key (new)"]),
                  _md_row(["---"] * 2 + ["---:"] * 6)]
        for m in result["key_matches"]:
            lines.append(_md_row([e(m["path"]), e(m["field"]), str(m["only_in_new"]),
                                  str(m["only_in_old"]), str(m["changed"]),
                                  str(m["unchanged"]), str(m["missing_key_old"]),
                                  str(m["missing_key_new"])]))

    if show_all:
        lines += ["", "## Unchanged paths", ""]
        lines += [f"- {e(p)}" for p in result["unchanged_paths"]] or ["none"]

    lines += ["", "## Warnings", ""]
    lines += [f"- {e(w)}" for w in result["warnings"]] or ["none"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------ html

_STYLE = """
:root { --bg: #ffffff; --fg: #1b1f24; --muted: #57606a; --line: #d0d7de;
        --head: #f6f8fa; --warn-bg: #fff4e5; --warn-fg: #8a4b00; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --line: #30363d;
          --head: #161b22; --warn-bg: #3b2300; --warn-fg: #ffb757; }
}
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.5rem; margin: 0 0 8px; }
h2 { font-size: 1.15rem; margin: 32px 0 8px; border-bottom: 1px solid var(--line); }
p, li { margin: 4px 0; }
.muted { color: var(--muted); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { border: 1px solid var(--line); padding: 4px 8px; text-align: left;
         vertical-align: top; }
th { background: var(--head); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
       font-size: 13px; overflow-wrap: anywhere; }
tr.breaking td { background: var(--warn-bg); }
span.flag { color: var(--warn-fg); font-weight: 600; white-space: nowrap; }
"""


def render_html(result, show_all=False):
    e = esc_html
    old, new = result["inputs"]["old"], result["inputs"]["new"]
    out = ['<!DOCTYPE html>', '<html lang="en">', '<head>',
           '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f'<title>{TITLE}</title>', f'<style>{_STYLE}</style>', '</head>',
           '<body>', '<main>', '<header id="jd-header">', f'<h1>{TITLE}</h1>',
           f'<p>old: <code>{e(old["file"])}</code> ({e(_input_line(old))})</p>',
           f'<p>new: <code>{e(new["file"])}</code> ({e(_input_line(new))})</p>',
           f'<p>{e(VALUES_ON if result["values"] else VALUES_OFF)}</p>',
           f'<p class="muted">{e(REVIEW_SENTENCE)}</p>']
    if result["unkeyed_arrays"]:
        out.append(f'<p>{e(UNKEYED_NOTE)}: '
                   + ", ".join(f"<code>{e(p)}</code>" for p in result["unkeyed_arrays"])
                   + "</p>")
    out.append('</header>')

    out += ['<section id="jd-summary">', '<h2>Summary</h2>', '<div class="scroll">',
            '<table>', '<thead><tr><th>category</th><th>count</th></tr></thead>',
            '<tbody>']
    out += [f'<tr><td>{e(label)}</td><td class="num">{count}</td></tr>'
            for label, count in _summary_rows(result)]
    out += ['</tbody>', '</table>', '</div>', '</section>']

    out += ['<section id="jd-changes">', '<h2>Changes</h2>']
    changes = _sorted_changes(result)
    if not changes:
        out.append('<p>none</p>')
    else:
        header = ["path", "category", "detail", "flag"]
        if result["values"]:
            header += ["old values", "new values"]
        out += ['<div class="scroll">', '<table>', '<thead><tr>'
                + "".join(f"<th>{h}</th>" for h in header) + '</tr></thead>', '<tbody>']
        for index, c in enumerate(changes, start=1):
            row_class = ' class="breaking"' if c["breaking"] else ""
            flag = f'<span class="flag">{e(_flag(c))}</span>' if c["breaking"] else ""
            cells = [f'<td><code>{e(c["path"])}</code></td>',
                     f'<td>{e(c["category"])}</td>', f'<td>{e(c["detail"])}</td>',
                     f'<td>{flag}</td>']
            if result["values"]:
                for key in ("old_values", "new_values"):
                    cells.append("<td>" + ", ".join(f"<code>{e(v)}</code>"
                                                    for v in c[key]) + "</td>")
            out.append(f'<tr id="jd-change-{index}"{row_class}>' + "".join(cells)
                       + "</tr>")
        out += ['</tbody>', '</table>', '</div>']
    out.append('</section>')

    if result["key_matches"]:
        out += ['<section id="jd-keys">', '<h2>Key matches</h2>', '<div class="scroll">',
                '<table>', '<thead><tr><th>array</th><th>key</th><th>added</th>'
                '<th>removed</th><th>changed</th><th>unchanged</th>'
                '<th>missing key (old)</th><th>missing key (new)</th></tr></thead>',
                '<tbody>']
        for m in result["key_matches"]:
            nums = [m["only_in_new"], m["only_in_old"], m["changed"], m["unchanged"],
                    m["missing_key_old"], m["missing_key_new"]]
            out.append(f'<tr><td><code>{e(m["path"])}</code></td>'
                       f'<td><code>{e(m["field"])}</code></td>'
                       + "".join(f'<td class="num">{n}</td>' for n in nums) + '</tr>')
        out += ['</tbody>', '</table>', '</div>', '</section>']

    if show_all:
        out += ['<section id="jd-unchanged">', '<h2>Unchanged paths</h2>']
        if result["unchanged_paths"]:
            out += ['<ul>'] + [f'<li><code>{e(p)}</code></li>'
                               for p in result["unchanged_paths"]] + ['</ul>']
        else:
            out.append('<p>none</p>')
        out.append('</section>')

    out += ['<section id="jd-warnings">', '<h2>Warnings</h2>']
    if result["warnings"]:
        out += ['<ul>'] + [f'<li>{e(w)}</li>' for w in result["warnings"]] + ['</ul>']
    else:
        out.append('<p>none</p>')
    out += ['</section>', '</main>', '</body>', '</html>']
    return "\n".join(out) + "\n"
