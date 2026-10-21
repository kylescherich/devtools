"""Synthetic inputs for the testdelta tests, built at test time.

unittest -v logs and JUnit XML reports are assembled as text in the layout
the real tools print (checked against Python 3.11 and 3.12 output). The one
exception is real_unittest_log(), which runs a small synthetic suite with
this interpreter's unittest to guard against format drift (testdelta itself
never runs tests). Hostile shapes (DOCTYPE/ENTITY bombs, unsafe test names)
are built here too, so no file under tests/ holds them whole.
"""
import pathlib
import re
import subprocess
import sys
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


def ut_footer(count, ok=True, final=None):
    """The dash line, "Ran N tests" and the final OK / FAILED line."""
    if final is None:
        final = "OK" if ok else "FAILED"
    return [SEP_DASH, f"Ran {count} tests in 0.123s", "", final]


def ut_final(lines, blocks):
    """The final line real unittest prints for these lines and blocks:
    FAILED/OK with failures, errors, skipped, expected failures and
    unexpected successes, in unittest's order, zero counts left out."""
    heads = [block[1] for block in blocks]
    counts = [("failures", sum(h.startswith("FAIL: ") for h in heads)),
              ("errors", sum(h.startswith("ERROR: ") for h in heads)),
              ("skipped", sum(" ... skipped" in line for line in lines)),
              ("expected failures", sum(line.endswith(" ... expected failure")
                                        for line in lines)),
              ("unexpected successes", sum(h.startswith("UNEXPECTED SUCCESS: ")
                                           for h in heads))]
    failed = counts[0][1] or counts[1][1] or counts[4][1]
    items = ", ".join(f"{key}={value}" for key, value in counts if value)
    word = "FAILED" if failed else "OK"
    return f"{word} ({items})" if items else word


def unittest_log(lines, blocks=(), durations=None, count=None, newline="\n",
                 footer=True):
    """footer: True for the real footer, False for none (a truncated log),
    or a string for the final line."""
    out = list(lines) + [""]
    for block in blocks:
        out += block
    if durations is not None:
        out += ut_durations(durations)
    if footer is not False:
        final = ut_final(lines, blocks) if footer is True else footer
        out += ut_footer(len(lines) if count is None else count, final=final)
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
    """The suite counts are those of the cases, as pytest writes them."""
    out = ['<?xml version="1.0" encoding="utf-8"?>'] if decl else []
    errors = sum("<error" in case for case in cases)
    failures = sum("<failure" in case for case in cases)
    skipped = sum("<skipped" in case for case in cases)
    suite = (f'<testsuite name="pytest" errors="{errors}" failures="{failures}" '
             f'skipped="{skipped}" tests="{len(cases)}" time="1.000">')
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


# --- real unittest output with awkward names ----------------------------------

# Test names real unittest prints as they are: " ... ", "(", ")" and " ... ok"
# inside the name, a docstring that looks like a result line, a subtest and a
# skip reason holding " ... ".
ODD_WHERE = "test_dyn.C"
ODD_NAMES = ("test ... odd", "test (x) ... ok", "test_doc ... z", "test_sub ... s",
             "test_skip ... k", "test_ok")

_ODD_SUITE = """\
import unittest
FAIL = {fail}
class C(unittest.TestCase):
    pass
def _plain(self):
    if FAIL:
        self.fail("boom")
def _doc(self):
    \"\"\"Doc ... ok (fake.C.y) ... ok\"\"\"
    if FAIL:
        self.fail("doc")
def _sub(self):
    for i in range(2):
        with self.subTest(i=i):
            if FAIL:
                self.assertEqual(i, 0)
@unittest.skip("why ... ok")
def _skip(self):
    pass
def _ok(self):
    pass
setattr(C, "test ... odd", _plain)
setattr(C, "test (x) ... ok", _plain)
setattr(C, "test_doc ... z", _doc)
setattr(C, "test_sub ... s", _sub)
setattr(C, "test_skip ... k", _skip)
setattr(C, "test_ok", _ok)
"""


def real_unittest_log(directory, fail, form="long"):
    """The -v log (stderr) of this interpreter's unittest on the odd-name
    suite; form="short" rewrites ids to the pre-3.11 'name (module.Class)'."""
    work = pathlib.Path(directory) / ("suite_fail" if fail else "suite_pass")
    work.mkdir()
    write(work, "test_dyn.py", _ODD_SUITE.format(fail=bool(fail)))
    argv = [sys.executable, "-I", "-m", "unittest", "discover", "-v",
            "-s", str(work), "-t", str(work)]
    if sys.version_info >= (3, 12):
        argv += ["--durations", "0"]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=work)
    log = proc.stderr
    if form == "short":
        for name in ODD_NAMES:
            log = log.replace(f"({ODD_WHERE}.{name})", f"({ODD_WHERE})")
        assert not re.search(re.escape(ODD_WHERE) + r"\.test", log)
    return log


# --- renamed parametrized ids (BRIEF-DEVWS-039) ---------------------------------

RENAME_CLASS = "tests.test_harness.TestLoad"
RENAME_BASE = "test_malformed_file_is_null_with_warning"
STAMP_BEFORE = "2026-10-21 05:54:54.841690"
STAMP_AFTER = "2026-10-21 05:57:53.088515"


def stamped(stamp, k):
    """The BRIEF-HARNESS-037 shape: a timestamp embedded in the param part."""
    return f"{RENAME_BASE}[{stamp} \u2026 case{k}]"


def _cases(pairs, classname=RENAME_CLASS):
    """(name, status) pairs as JUnit cases; status pass or fail."""
    return [junit_case(name, classname) if status == "pass" else
            junit_case(name, classname, kind="fail", message="synthetic message",
                       text="Traceback line 1\nAssertionError: x")
            for name, status in pairs]


def rename_real(fail_after=None):
    """6 stamped ids that change between runs plus 7 fixed ids under the same
    base id; all pass, except stamped case fail_after on the after side."""
    fixed = [(f"{RENAME_BASE}[fixed-{k}]", "pass") for k in range(1, 8)]
    before = fixed + [(stamped(STAMP_BEFORE, k), "pass") for k in range(1, 7)]
    after = fixed + [(stamped(STAMP_AFTER, k), "fail" if k == fail_after else "pass")
                     for k in range(1, 7)]
    return junit_xml(_cases(before)), junit_xml(_cases(after))


def rename_uneven():
    """3 before vs 2 after under one base id, plus one id that matches."""
    before = [("test_ok", "pass")] + [(f"test_three[b{k}]", "pass") for k in range(3)]
    after = [("test_ok", "pass")] + [(f"test_three[a{k}]", "pass") for k in range(2)]
    return junit_xml(_cases(before)), junit_xml(_cases(after))


def rename_two_bases():
    """test_alpha renames 2 -> 2 (all pass); test_beta goes 2 -> 1."""
    before = ([(f"test_alpha[old{k}]", "pass") for k in range(2)]
              + [(f"test_beta[old{k}]", "pass") for k in range(2)])
    after = ([(f"test_alpha[new{k}]", "pass") for k in range(2)]
             + [("test_beta[new0]", "pass")])
    return junit_xml(_cases(before)), junit_xml(_cases(after))


RENAME_SCENARIOS = {"real": rename_real, "one_fails": lambda: rename_real(fail_after=3),
                    "uneven": rename_uneven, "two_bases": rename_two_bases}
