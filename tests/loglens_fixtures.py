"""Synthetic fixture writers for loglens tests. Every helper writes into the
directory it is given (a temp dir) and returns the path of the file."""
import gzip
import json
import os
from pathlib import Path


def write_bytes(directory, name, data):
    path = Path(directory) / name
    path.write_bytes(data)
    return path


def write_text(directory, name, text):
    return write_bytes(directory, name, text.encode("utf-8"))


def write_lines(directory, name, lines):
    return write_text(directory, name, "".join(line + "\n" for line in lines))


def write_jsonl(directory, name, records):
    return write_lines(directory, name, [
        r if isinstance(r, str) else json.dumps(r) for r in records])


def write_gz(directory, name, text):
    path = Path(directory) / name
    with gzip.open(path, "wb") as f:
        f.write(text.encode("utf-8"))
    return path


def symlink_to(directory, name, target):
    path = Path(directory) / name
    os.symlink(target, path)
    return path


def traceback_lines(value, func="load_user", path="/srv/app/handlers/users.py",
                    line=42, exc="KeyError"):
    """A synthetic Python traceback; only the variable value changes."""
    return [
        "Traceback (most recent call last):",
        '  File "/srv/app/main.py", line 10, in run',
        "    handle(request)",
        f'  File "{path}", line {line}, in {func}',
        "    return users[user_id]",
        f"{exc}: '{value}'",
    ]
