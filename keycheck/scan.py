"""Walk a root and stat the files whose names match.

Only os.scandir and lstat: a matched file is never opened and a symlink is
never followed (a matched symlink is listed as SYMLINK with its lstat
facts). Skipped always: .git, node_modules, __pycache__, site-packages,
any directory holding pyvenv.cfg, ~/.cache, ~/.local/share/docker, and any
directory on another device than the root (no crossing into mounts).
Skipped unless --all (DEC-465): ~/.local/share/uv and ~/.claude/plugins.
Home paths match wherever the root is. An IN-REPO-NOT-IGNORED file with a
tests/ or test/ folder in its path inside the repo gets the note
"in-repo fixture?"; it stays flagged.
"""
import dataclasses
import os
import stat

from keycheck import acl, gitcheck, match

FLAG_ORDER = ("IN-REPO-NOT-IGNORED", "OTHERS-CAN-READ", "STALE-COPY", "OLD", "SYMLINK")
EXIT_FLAGS = ("IN-REPO-NOT-IGNORED", "OTHERS-CAN-READ", "STALE-COPY")
SKIP_NAMES = {".git", "node_modules", "__pycache__", "site-packages"}
HOME_SKIPS = (".cache", ".local/share/docker")
NOISE_SKIPS = (".local/share/uv", ".claude/plugins")      # off with --all
FIXTURE_DIRS = {"tests", "test"}
FIXTURE_NOTE = "in-repo fixture?"
DAY = 86400


@dataclasses.dataclass
class Entry:
    rel: str            # relative to the root, "/" separated
    size: int
    age_days: int
    mode: str           # "0600"
    flags: list
    note: str = ""


@dataclasses.dataclass
class Result:
    files: list
    unreadable_dirs: int = 0


def _device(path):
    return os.lstat(path).st_dev


def skips(all_=False):
    """Every skip in effect, as shown in reports."""
    home = HOME_SKIPS + (() if all_ else NOISE_SKIPS)
    return (sorted(SKIP_NAMES) + ["virtualenvs (pyvenv.cfg)"]
            + ["~/" + s for s in home] + ["other devices", "symlinks"])


def _home_skips(home, all_):
    rels = HOME_SKIPS + (() if all_ else NOISE_SKIPS)
    return {os.path.join(home, *rel.split("/")) for rel in rels}


def _fixture_note(path, tree):
    parents = os.path.relpath(path, tree).split(os.sep)[:-1]
    return FIXTURE_NOTE if FIXTURE_DIRS.intersection(parents) else ""


def _walk(root, home, all_):
    """Yield (path, kind, lstat result) for matched names; count unreadable dirs."""
    skip = _home_skips(home, all_)
    device = _device(root)
    counter = {"unreadable": 0}
    found = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            counter["unreadable"] += 1
            continue
        if any(e.name == "pyvenv.cfg" for e in entries):
            continue
        parts = directory.split(os.sep)
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if (e.name not in SKIP_NAMES and e.path not in skip
                            and _device(e.path) == device):
                        stack.append(e.path)
                    continue
                kind = match.classify(parts + [e.name])
                if kind is None:
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                found.append((e.path, kind, st))
    return found, counter["unreadable"]


def scan(root, home, now, max_age_days, all_=False):
    root = os.path.abspath(root)
    found, unreadable = _walk(root, home, all_)
    readable_by_others = acl.reader() if os.name == "nt" else None

    trees = {}
    by_tree = {}
    for path, _, _ in found:
        directory = os.path.dirname(path)
        if directory not in trees:
            trees[directory] = gitcheck.work_tree(directory)
        if trees[directory]:
            by_tree.setdefault(trees[directory], []).append(path)
    exposed = set()
    for tree, paths in by_tree.items():
        exposed |= gitcheck.not_ignored(tree, paths)

    files = []
    for path, kind, st in found:
        flags = set()
        note = ""
        if path in exposed:
            flags.add("IN-REPO-NOT-IGNORED")
            note = _fixture_note(path, trees[os.path.dirname(path)])
        if stat.S_ISLNK(st.st_mode):
            flags.add("SYMLINK")
        else:
            if readable_by_others is not None:
                if readable_by_others(path):
                    flags.add("OTHERS-CAN-READ")
            elif st.st_mode & (stat.S_IRGRP | stat.S_IROTH):
                flags.add("OTHERS-CAN-READ")
            if kind == "stale":
                flags.add("STALE-COPY")
        age = max(0, int((now - st.st_mtime) // DAY))
        if age > max_age_days and not stat.S_ISLNK(st.st_mode):
            flags.add("OLD")
        files.append(Entry(rel=os.path.relpath(path, root).replace(os.sep, "/"),
                           size=st.st_size, age_days=age,
                           mode=f"{stat.S_IMODE(st.st_mode):04o}",
                           flags=[f for f in FLAG_ORDER if f in flags] or ["OK"],
                           note=note))
    return Result(files=files, unreadable_dirs=unreadable)
