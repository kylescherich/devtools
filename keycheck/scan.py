"""Walk a root and stat the files whose names match.

STUB (test-first commit): finds nothing.
"""
import dataclasses
import os

FLAG_ORDER = ("IN-REPO-NOT-IGNORED", "OTHERS-CAN-READ", "STALE-COPY", "OLD", "SYMLINK")
EXIT_FLAGS = ("IN-REPO-NOT-IGNORED", "OTHERS-CAN-READ", "STALE-COPY")


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


def scan(root, home, now, max_age_days):
    return Result(files=[])
