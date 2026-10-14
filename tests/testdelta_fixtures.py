"""Synthetic inputs for the testdelta tests, built at test time.

Nothing here runs a test suite: unittest -v logs and JUnit XML reports are
assembled as text in the layout the real tools print (checked against
Python 3.11 and 3.12 output). Hostile shapes (DOCTYPE/ENTITY bombs, unsafe
test names) are built here too, so no file under tests/ holds them whole.
"""
import pathlib
from xml.sax.saxutils import escape, quoteattr

SEP_EQ = "=" * 70
SEP_DASH = "-" * 70

# Untrusted test names. Non-ASCII only as escapes (pure-ASCII sources).
HTML_NAME = "test_<img src=x onerror=alert(1)>"
ANSI_NAME = "test_\x1b[31mred\x1b[0m"
BIDI_NAME = "test_\u202eexe.txt"
NEWLINE_NAME = "test_line1\nline2"

_WORD = {"pass": "ok", "fail": "FAIL", "error": "ERROR",
         "skip": "skipped 'synthetic reason'", "xfail": "expected failure",
         "xpass": "unexpected success"}
_BLOCK = {"fail": "FAIL", "error": "ERROR", "xpass": "UNEXPECTED SUCCESS"}


def write(directory, name, content):
    path = pathlib.Path(directory) / name
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    path.write_bytes(data)
    return path


# --- unittest -v text --------------------------------------------------------

def ut_head(where, name, form="long", params=None):
    head = f"{name} ({where}.{name})" if form == "long" else f"{name} ({where})"
    return f"{head} ({params})" if params else head


def ut_line(where, name, status, form="long", doc=None):
    """One verbose result line (two with a docstring description)."""
    head = ut_head(where, name, form)
    if doc is not None:
        return [head, f"{doc} ... {_WORD[status]}"]
    return [f"{head} ... {_WORD[status]}"]


def ut_block(kind, where, name, message="AssertionError: synthetic failure",
             frames=2, params=None, stdout=None, stderr=None, form="long"):
    """A FAIL / ERROR / UNEXPECTED SUCCESS block as printed after the run."""
    out = [SEP_EQ, f"{_BLOCK[kind]}: {ut_head(where, name, form, params)}"]
    if kind == "xpass":
        return out
    out += [SEP_DASH, "Traceback (most recent call last):"]
    for k in range(frames):
        out += [f'  File "/synthetic/{where}.py", line {k + 1}, in {name}',
                f"    step_{k}()"]
    out += message.split("\n") + [""]
    if stdout is not None:
        out += ["Stdout:", stdout, ""]
    if stderr is not None:
        out += ["Stderr:", stderr, ""]
    return out


def ut_durations(pairs):
    """3.12+ --durations block: pairs of (where, name, seconds)."""
    out = ["Slowest test durations", SEP_DASH]
    out += [f"{seconds:.3f}s     {name} ({where}.{name})" for where, name, seconds in pairs]
    return out + [""]


def ut_footer(count, ok=True):
    return [SEP_DASH, f"Ran {count} tests in 0.123s", "", "OK" if ok else "FAILED"]


def unittest_log(lines, blocks=(), durations=None, count=None, newline="\n"):
    out = list(lines) + [""]
    for block in blocks:
        out += block
    if durations is not None:
        out += ut_durations(durations)
    out += ut_footer(len(lines) if count is None else count)
    return newline.join(out) + newline


def ut_simple(statuses, where="pkg.mod.Class", form="long"):
    """A log with one line per (name, status) and a block for each failure."""
    lines, blocks = [], []
    for name, status in statuses:
        lines += ut_line(where, name, status, form)
        if status in _BLOCK:
            blocks.append(ut_block(status, where, name, form=form))
    return unittest_log(lines, blocks, count=len(statuses))


# --- JUnit XML ---------------------------------------------------------------

def junit_case(name, classname="pkg.mod.Class", time="0.010", kind=None,
               message=None, text=None, skip_type=None, file=None,
               system_out=None, system_err=None, extra=()):
    """A <testcase>; kind is None (pass), fail, error, skip or xfail."""
    attrs = []
    if classname is not None:
        attrs.append(f"classname={quoteattr(classname)}")
    attrs.append(f"name={quoteattr(name)}")
    if file is not None:
        attrs.append(f"file={quoteattr(file)}")
    if time is not None:
        attrs.append(f"time={quoteattr(time)}")
    body = []
    if kind in ("fail", "error"):
        tag = "failure" if kind == "fail" else "error"
        msg = "" if message is None else f" message={quoteattr(message)}"
        body.append(f"<{tag}{msg}>{escape(text or '')}</{tag}>")
    elif kind in ("skip", "xfail"):
        stype = "pytest.xfail" if kind == "xfail" else (skip_type or "pytest.skip")
        body.append(f'<skipped type={quoteattr(stype)} '
                    f'message={quoteattr(message or "synthetic reason")}/>')
    body += list(extra)
    if system_out is not None:
        body.append(f"<system-out>{escape(system_out)}</system-out>")
    if system_err is not None:
        body.append(f"<system-err>{escape(system_err)}</system-err>")
    head = "<testcase " + " ".join(attrs)
    if not body:
        return head + " />"
    return head + ">" + "".join(body) + "</testcase>"


def junit_xml(cases, root="testsuites", decl=True):
    out = ['<?xml version="1.0" encoding="utf-8"?>'] if decl else []
    suite = (f'<testsuite name="pytest" errors="0" failures="0" skipped="0" '
             f'tests="{len(cases)}" time="1.000">')
    if root == "testsuites":
        out += ["<testsuites>", suite]
    else:
        out.append(suite)
    out += ["  " + case for case in cases]
    out += ["</testsuite>", "</testsuites>"] if root == "testsuites" else ["</testsuite>"]
    return "\n".join(out) + "\n"


def junit_simple(statuses, classname="pkg.mod.Class", times=None):
    """(name, status) pairs; status pass/fail/error/skip/xfail/xpass."""
    cases = []
    for name, status in statuses:
        time = None if times is None else times.get(name)
        if status == "pass":
            cases.append(junit_case(name, classname, time=time))
        elif status == "xpass":
            cases.append(junit_case(name, classname, time=time, kind="fail",
                                    message="[XPASS(strict)] synthetic", text=""))
        else:
            cases.append(junit_case(name, classname, time=time, kind=status,
                                    message="synthetic message",
                                    text="Traceback line 1\nAssertionError: x"))
    return junit_xml(cases)


# --- hostile XML ---------------------------------------------------------------

def billion_laughs(levels=9, doctype="DOCTYPE"):
    ents = ['  <!ENTITY lol "lol">']
    for level in range(1, levels + 1):
        prev = "lol" if level == 1 else f"lol{level - 1}"
        ents.append(f'  <!ENTITY lol{level} "' + f"&{prev};" * 10 + '">')
    return ("<?xml version=\"1.0\"?>\n<!" + doctype + " lolz [\n" + "\n".join(ents)
            + "\n]>\n" + f'<testsuite><testcase name="&lol{levels};"/></testsuite>\n')


def external_entity(target="file:///etc/passwd"):
    return ("<?xml version=\"1.0\"?>\n<!DOCTYPE t [\n  <!ENTITY xxe SYSTEM \""
            + target + "\">\n]>\n"
            + '<testsuite><testcase classname="x" name="&xxe;"/></testsuite>\n')
