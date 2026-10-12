"""Synthetic git repositories for gitarch tests, built at test time in temp
dirs. The fixture's own git runs with GIT_CONFIG_GLOBAL=/dev/null,
GIT_CONFIG_NOSYSTEM=1 and fixed author/committer names, e-mails and dates,
so every repository (and every commit hash) is the same on every run."""
import os
import stat
import subprocess
from pathlib import Path

AUTHOR = ("Ada Fixture", "ada@example.test")
BASE_DATE = "2026-01-05T10:00:00Z"


def _env(date, author=AUTHOR):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1],
               GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1],
               GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date, LC_ALL="C")
    return env


def git(repo, *args, date=BASE_DATE, author=AUTHOR, data=None, check=True):
    """Run fixture git in repo; stdout bytes."""
    proc = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                           "-C", str(repo), *args], env=_env(date, author),
                          input=data if data is not None else b"",
                          capture_output=True, timeout=60)
    if check and proc.returncode:
        raise RuntimeError(f"fixture git {args} failed: {proc.stderr!r}")
    return proc.stdout


def init_repo(directory, name="repo"):
    repo = Path(directory) / name
    git(directory, "-c", "init.defaultBranch=main", "init", "-q", str(repo))
    return repo


def write(repo, path, content):
    target = Path(repo) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    target.write_bytes(content)
    return target


def commit(repo, message, date, author=AUTHOR):
    """Stage everything and commit; returns the new commit hash."""
    git(repo, "add", "-A", date=date)
    git(repo, "commit", "-q", "--allow-empty", "-m", message, date=date, author=author)
    return head(repo)


def head(repo, rev="HEAD"):
    return git(repo, "rev-parse", rev).decode("ascii").strip()


def basic_repo(directory, name="repo"):
    """Three ordinary commits: a.py, src/b.py, a rename and an edit."""
    repo = init_repo(directory, name)
    write(repo, "a.py", "print('a')\n")
    write(repo, "src/b.py", "x = 1\n")
    commit(repo, "add a and b", "2026-01-05T10:00:00Z")
    write(repo, "a.py", "print('a')\nprint('more')\n")
    commit(repo, "edit a", "2026-01-06T10:00:00Z")
    git(repo, "mv", "src/b.py", "src/c.py")
    commit(repo, "rename b to c", "2026-01-07T10:00:00Z")
    return repo


TRAP = """#!/bin/sh
# Trap: any run of this script means gitarch let git start an external program.
echo "trap $0 $*" >> '{marker}'
exit 0
"""
HOOKS = ("pre-commit", "post-checkout", "post-commit", "reference-transaction",
         "fsmonitor-watchman", "post-index-change")


def hostile_repo(directory):
    """A repository whose local config, attributes and history try to make git
    run an external program. Returns (repo, marker, script); the marker file
    lives outside the repository and must never appear.

    Commits are made FIRST (including a hand-built commit carrying a gpgsig
    header, on branch "signed"); only then is the hostile config written."""
    directory = Path(directory)
    repo = init_repo(directory, "hostile")
    marker = directory / "MARKER"
    script = directory / "trap.sh"
    script.write_text(TRAP.format(marker=marker), encoding="ascii")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    hooks = directory / "hooks"
    hooks.mkdir()
    for name in HOOKS:
        hook = hooks / name
        hook.write_text(TRAP.format(marker=marker), encoding="ascii")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    write(repo, ".gitattributes", "*.py diff=x filter=x\n")
    write(repo, "app.py", "def main():\n    return 1\n")
    write(repo, "lib/util.py", "VALUE = 1\n")
    commit(repo, "add app and util", "2026-01-05T10:00:00Z")
    write(repo, "app.py", "def main():\n    return 2\n")
    commit(repo, "change app", "2026-01-06T10:00:00Z")
    git(repo, "mv", "lib/util.py", "lib/helpers.py")
    commit(repo, "rename util", "2026-01-07T10:00:00Z")

    # A hand-built commit object with a gpgsig header, child of HEAD.
    ident = f"{AUTHOR[0]} <{AUTHOR[1]}> 1767866400 +0000"     # 2026-01-08T10:00:00Z
    signed = "\n".join([
        "tree " + head(repo, "HEAD^{tree}"),
        "parent " + head(repo),
        "author " + ident,
        "committer " + ident,
        "gpgsig -----BEGIN PGP SIGNATURE-----",
        " ",
        " iQEzBAABCAAdFiEEsyntheticsyntheticsyntheticAAAA",
        " =ABCD",
        " -----END PGP SIGNATURE-----",
        "",
        "signed commit",
        ""])
    sha = git(repo, "hash-object", "-t", "commit", "-w", "--stdin",
              data=signed.encode("utf-8")).decode("ascii").strip()
    git(repo, "update-ref", "refs/heads/signed", sha)

    # Only now the hostile local config; every entry points at the trap.
    for key, value in (("core.fsmonitor", str(script)),
                       ("core.hooksPath", str(hooks)),
                       ("core.pager", str(script)),
                       ("diff.x.textconv", str(script)),
                       ("diff.x.command", str(script)),
                       ("diff.external", str(script)),
                       ("filter.x.clean", str(script)),
                       ("filter.x.smudge", str(script)),
                       ("gpg.program", str(script)),
                       ("log.showSignature", "true")):
        git(repo, "config", "--local", key, value)
    return repo, marker, script
