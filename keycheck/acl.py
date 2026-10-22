"""Windows ACL check from `icacls <path>` text.

An ACE line is "PRINCIPAL:(flag)...(rights)". It counts when its rights
include R, RX, M or F, it is not a DENY and not inherit-only (IO), and the
principal is neither the owner nor NT AUTHORITY\\SYSTEM nor
BUILTIN\\Administrators (case-insensitive). icacls does not print the
owner; the live owner is the current user from `whoami`, used only for this
comparison and never printed. If icacls fails, the file counts as readable
by others: the safe side.
"""
import os
import re
import shutil
import subprocess

TIMEOUT = 30
READ_RIGHTS = {"R", "RX", "M", "F"}
TRUSTED = {"nt authority\\system", "builtin\\administrators"}

_ACE = re.compile(r"^(?P<who>.+?):(?P<perms>(?:\([^()]*\))+)\s*$")
_GROUP = re.compile(r"\(([^()]*)\)")


def others_can_read(text, owner, path=None):
    """True when an ACE in icacls text grants read to anyone but the owner,
    NT AUTHORITY\\SYSTEM or BUILTIN\\Administrators."""
    owner = (owner or "").lower()
    for raw in text.splitlines():
        line = raw.strip()
        if path and line.startswith(path):
            line = line[len(path):].strip()
        match = _ACE.match(line)
        if not match:
            continue
        tokens = {t.strip().upper() for group in _GROUP.findall(match.group("perms"))
                  for t in group.split(",")}
        if "DENY" in tokens or "IO" in tokens or not tokens & READ_RIGHTS:
            continue
        who = match.group("who").strip().lower()
        if who == owner or who in TRUSTED:
            continue
        return True
    return False


def _run(argv):
    exe = shutil.which(argv[0])
    if not exe:
        return None
    try:
        proc = subprocess.run([os.path.abspath(exe), *argv[1:]], capture_output=True,
                              text=True, errors="replace", timeout=TIMEOUT, shell=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def reader():
    """A function path -> bool for this Windows user (calls icacls)."""
    owner = (_run(["whoami"]) or "").strip()

    def check(path):
        text = _run(["icacls", path])
        return True if text is None else others_can_read(text, owner, path)
    return check
