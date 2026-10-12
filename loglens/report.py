"""Plain-text report. The returned text has already passed through redact();
the CLI redacts it once more on the way out."""
from loglens.redact import redact


def _ts(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else "-"


def render_text(groups, result, *, raw=False):
    total = len(result.entries) + len(result.untimed)
    out = [f"{total} entries from {len(result.files)} file(s), "
           f"{result.lines_read} lines; {len(result.untimed)} without timestamp "
           "(not on the timeline)"]
    if result.sampled:
        out.append(result.sampled_note)
    if result.invalid_json:
        where = "; ".join(f"{name}: lines {', '.join(map(str, nums))}"
                          for name, nums in result.invalid_json_lines.items())
        out.append(f"invalid JSON lines: {result.invalid_json} (first: {where})")
    if result.decode_errors:
        out.append(f"lines with non-UTF-8 bytes (replaced): {result.decode_errors}")
    out.append(f"{len(groups)} group(s)")
    for group in groups:
        out.append("")
        out.append(f"{group.level:<8} x{group.count}  {group.signature}")
        out.append(f"         first {_ts(group.first_ts)}  last {_ts(group.last_ts)}  "
                   f"files: {', '.join(group.files)}")
        if raw:
            for example in group.examples:
                out.append("         example:")
                out.extend("           " + line for line in example.split("\n"))
    return redact("\n".join(out) + "\n")
