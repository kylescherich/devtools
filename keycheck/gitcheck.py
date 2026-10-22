"""Is a file inside a git work tree and not ignored?

The work tree is found with lstat only: the nearest directory above the
file holding a .git file or a .git directory with a HEAD. One
`git check-ignore -z --stdin` call per work tree receives the names on
stdin; git never gets file content and keycheck never opens a file.

git runs by absolute path, shell=False, with a timeout, every GIT_*
variable removed, no prompt, no optional locks, fsmonitor and hooks off.
Global config stays on, so core.excludesFile counts as "ignored". Any git
failure (missing git, timeout, a refused repo) counts as not ignored: the
safe side.
"""
import os
import shutil
import stat
import subprocess

TIMEOUT = 30


def work_tree(directory):
    d = directory
    while True:
        dot = os.path.join(d, ".git")
        try:
            mode = os.lstat(dot).st_mode
            if stat.S_ISREG(mode):
                return d
            if stat.S_ISDIR(mode):
                os.lstat(os.path.join(dot, "HEAD"))
                return d
        except OSError:
            pass
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    return env


def not_ignored(tree, paths):
    """The subset of paths (absolute, inside tree) git does not ignore."""
    paths = list(paths)
    git = shutil.which("git")
    if not git:
        return set(paths)
    rels = [os.path.relpath(p, tree).replace(os.sep, "/") for p in paths]
    cmd = [os.path.abspath(git), "--no-pager", "-c", "core.fsmonitor=false",
           "-c", f"core.hooksPath={os.devnull}", "-C", tree,
           "check-ignore", "-z", "--stdin"]
    stdin = b"".join(os.fsencode(r) + b"\0" for r in rels)
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, env=_env(),
                              timeout=TIMEOUT, shell=False)
    except (OSError, subprocess.SubprocessError):
        return set(paths)
    if proc.returncode not in (0, 1):     # 1: nothing ignored
        return set(paths)
    ignored = {os.fsdecode(x) for x in proc.stdout.split(b"\0") if x}
    return {p for p, r in zip(paths, rels) if r not in ignored}
