"""Walk a root and stat the files whose names match.

Only os.scandir and lstat: a matched file is never opened and a symlink is
never followed (a matched symlink is listed as SYMLINK with its lstat
facts). Skipped: .git, node_modules, __pycache__, site-packages, any
directory holding pyvenv.cfg, ~/.cache, ~/.local/share/docker, and any
directory on another device than the root (no crossing into mounts).
"""
import dataclasses
import os
import stat

from keycheck import acl, gitcheck, match

FLAG_ORDER = ("IN-REPO-NOT-IGNORED", "OTHERS-CAN-READ", "STALE-COPY", "OLD", "SYMLINK")
EXIT_FLAGS = ("IN-REPO-NOT-IGNORED", "OTHERS-CAN-READ", "STALE-COPY")
SKIP_NAMES = {".git", "node_modules", "__pycache__", "site-packages"}
DAY = 86400


@dataclasses.dataclass
class Entry:
    rel: str            # relative to the root, "/" separated
    size: int
    age_days: int
    mode: str           # "0600"
    flags: list


@dataclasses.dataclass
class Result:
    files: list
    unreadable_dirs: int = 0


def _device(path):
    return os.lstat(path).st_dev


def _walk(root, home):
    """Yield (path, kind, lstat result) for matched names; count unreadable dirs."""
    skip = {os.path.join(home, ".cache"), os.path.join(home, ".local", "share", "docker")}
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


def scan(root, home, now, max_age_days):
    root = os.path.abspath(root)
    found, unreadable = _walk(root, home)
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
        if path in exposed:
            flags.add("IN-REPO-NOT-IGNORED")
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
                           flags=[f for f in FLAG_ORDER if f in flags] or ["OK"]))
    return Result(files=files, unreadable_dirs=unreadable)
