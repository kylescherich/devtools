"""Markdown summary of a pip-audit JSON report (BRIEF-DEVWS-046, DEC-490).

Usage:
    audit_summary.py AUDIT_JSON IGNORE_FILE [--exit-code N] [--today YYYY-MM-DD]
                     [--pip-audit-version V] [--service S] [--empty-set]

Phase 1 is report-only: findings, a pip-audit error and a bad ignore list are
all shown, never enforced. Standard library only; always exits 0.
"""

import argparse
import datetime
import json
import re
import sys

PHASE = "Phase 1: report-only (DEC-490)"
DEC_RE = re.compile(r"^DEC-\d{3,}$")


def cell(value):
    """One Markdown table cell: no newlines, pipes escaped."""
    text = " ".join(str(value).split())
    return text.replace("|", "\\|")


def load_report(path):
    """Return the dependencies list, or None when the report is unusable."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
        return None
    return [d for d in data["dependencies"] if isinstance(d, dict)]


def parse_ignore(path, today):
    """Return (entries, warnings); entries are display strings."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return [], ["**warning: ignore file not found** (%s)" % cell(path)]
    entries, warnings = [], []
    for num, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = [f.strip() for f in line.split("|")]
        problem = None
        expires = None
        if len(fields) != 4:
            problem = "expected 4 fields separated by |, got %d" % len(fields)
        else:
            adv, reason, date, dec = fields
            if not adv or not reason:
                problem = "empty advisory id or reason"
            else:
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                        raise ValueError
                    expires = datetime.date.fromisoformat(date)
                except ValueError:
                    problem = "expiry is not a YYYY-MM-DD date"
                if problem is None and not DEC_RE.match(dec):
                    problem = "DEC is not DEC-NNN"
        if problem:
            warnings.append("**warning: malformed** line %d: %s: `%s`"
                            % (num, problem, cell(line).replace("`", "'")))
            continue
        adv, reason, date, dec = fields
        text = "%s | %s | expires %s | %s" % (cell(adv), cell(reason), date, dec)
        if expires < today:
            warnings.append("**warning: expired** line %d: %s" % (num, text))
        else:
            entries.append(text)
    return entries, warnings


def render(args):
    out = []
    version = args.pip_audit_version or "unknown"
    out.append("## Dependency audit (pip-audit)")
    out.append("")
    out.append("- %s" % PHASE)
    out.append("- auditor: %s" % cell(version))
    out.append("- service: %s" % cell(args.service))

    deps = None
    could_not_run = None
    if args.empty_set:
        deps = []
    else:
        deps = load_report(args.audit_json)
        code = args.exit_code
        if deps is None or code not in ("0", "1"):
            could_not_run = "audit could not run (exit %s)" % (cell(code) if code else "unknown")

    if could_not_run:
        out.append("")
        out.append("**%s**" % could_not_run)
        out.append("")
        out.append("No results; the job stays green in Phase 1.")
    else:
        skipped = [d for d in deps if "skip_reason" in d]
        # pip-audit's JSON can repeat a dependency or an advisory; show each
        # (package, version, advisory id) once, merging aliases and fix versions.
        audited = {}
        advisories = {}
        for dep in deps:
            if "skip_reason" in dep:
                continue
            pkg = (str(dep.get("name", "?")), str(dep.get("version", "?")))
            audited[pkg] = True
            for v in dep.get("vulns") or []:
                if not isinstance(v, dict):
                    continue
                aliases, fixes = advisories.setdefault(pkg + (str(v.get("id", "?")),), ({}, {}))
                aliases.update(dict.fromkeys(str(a) for a in v.get("aliases") or []))
                fixes.update(dict.fromkeys(str(f) for f in v.get("fix_versions") or []))
        rows = [
            (cell(name), cell(version), cell(adv),
             cell(", ".join(aliases) or "none"), cell(", ".join(fixes) or "none"))
            for (name, version, adv), (aliases, fixes) in advisories.items()
        ]
        vulnerable = len({key[:2] for key in advisories})
        out.append("- packages audited: %d" % len(audited))
        out.append("- vulnerable: %d" % vulnerable)
        out.append("- advisories: %d" % len(rows))
        out.append("- skipped: %d" % len(skipped))
        out.append("")
        if args.empty_set:
            out.append("audited set is empty (project has no dependencies); "
                       "nothing to audit, not an error")
        elif rows:
            out.append("| package | installed | advisory id | aliases | fixed in |")
            out.append("|---|---|---|---|---|")
            for r in rows:
                out.append("| %s |" % " | ".join(r))
        else:
            out.append("No known vulnerabilities found.")
        out.append("")
        out.append("## Skipped packages")
        out.append("")
        if skipped:
            for d in skipped:
                out.append("- %s: %s" % (cell(d.get("name", "?")), cell(d["skip_reason"])))
        else:
            out.append("- none")

    entries, warnings = parse_ignore(args.ignore_file, args.today)
    out.append("")
    out.append("## audit-ignore.txt")
    out.append("")
    if not entries and not warnings:
        out.append("- no entries")
    for e in entries:
        out.append("- %s" % e)
    for w in warnings:
        out.append("- %s" % w)
    out.append("")
    out.append("Phase 1: ignore entries are shown, never enforced.")
    return "\n".join(out) + "\n"


def parse_args(argv):
    p = argparse.ArgumentParser(description="Markdown summary of a pip-audit JSON report.")
    p.add_argument("audit_json")
    p.add_argument("ignore_file")
    p.add_argument("--exit-code", default=None, help="pip-audit's exit code")
    p.add_argument("--today", default=None, help="YYYY-MM-DD for expiry checks (default: UTC today)")
    p.add_argument("--pip-audit-version", default=None)
    p.add_argument("--service", default="pypi")
    p.add_argument("--empty-set", action="store_true",
                   help="the audited set is empty; pip-audit was not run")
    return p.parse_args(argv)


def main(argv=None):
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        try:
            args.today = datetime.date.fromisoformat(args.today) if args.today else None
        except ValueError:
            args.today = None
        if args.today is None:
            args.today = datetime.datetime.now(datetime.timezone.utc).date()
        sys.stdout.write(render(args))
    except SystemExit:
        # argparse usage error: still report, still exit 0 (report-only).
        sys.stdout.write("%s\n\n**audit summary could not be built (usage error)**\n" % PHASE)
    except Exception as exc:  # report-only: never fail the job from here
        sys.stdout.write("%s\n\n**audit summary could not be built (%s)**\n"
                         % (PHASE, type(exc).__name__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
