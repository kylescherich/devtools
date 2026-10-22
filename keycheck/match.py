"""Name and path patterns for key and secret files. Pure: never touches disk.

A name is compared in lower case. Key names: id_* (not *.pub), *.pem,
*.key, .keys, *secret*, *token*, .env and .env.* (not .env.example),
.git-credentials, .netrc, *.url with a "healthchecks" folder above it,
settings.json directly inside .openhands, .credentials.json directly inside
.claude (DEC-459/462). A name ending in a backup suffix (.bak*, .old,
.orig, ~), stripped repeatedly, is judged by what is left: a key name
there makes it a stale copy, anything else no match.
"""
import re

_BACKUP = (re.compile(r"~$"), re.compile(r"\.old$"), re.compile(r"\.orig$"),
           re.compile(r"(?<=.)\.bak.*$"))


def _strip_backup(name):
    while True:
        for pattern in _BACKUP:
            stripped = pattern.sub("", name, count=1)
            if stripped != name:
                name = stripped
                break
        else:
            return name


def _is_key(parents, name):
    n = name.lower()
    parent = parents[-1].lower() if parents else ""
    return ((n.startswith("id_") and not n.endswith(".pub"))
            or n.endswith((".pem", ".key")) or n == ".keys"
            or "secret" in n or "token" in n
            or n == ".env" or (n.startswith(".env.") and n != ".env.example")
            or n in (".git-credentials", ".netrc")
            or (n.endswith(".url") and any(p.lower() == "healthchecks" for p in parents))
            or (n == "settings.json" and parent == ".openhands")
            or (n == ".credentials.json" and parent == ".claude"))


def classify(parts):
    """parts: path components, the file name last.
    Returns None, "key" or "stale" (a backup variant of a key name)."""
    parents, name = tuple(parts[:-1]), parts[-1]
    base = _strip_backup(name)
    if base != name:
        return "stale" if base and _is_key(parents, base) else None
    return "key" if _is_key(parents, name) else None
