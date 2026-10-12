"""The only place gitarch runs a program: git, read-only, hardened.

Every call is subprocess.run([...], shell=False) with a fixed argument list:

    git --no-pager -c core.hooksPath=/dev/null -c core.fsmonitor=false
        -c core.pager=cat -c color.ui=false -c log.showSignature=false
        -C <repo> <subcommand> [--no-ext-diff --no-textconv] ...

Only the subcommands in ALLOWED run; anything else raises CommandRefused
before a process starts. log, show and diff-tree always get --no-ext-diff
--no-textconv, inserted here so no caller can forget them. The environment is
a copy of os.environ without any GIT_* key, plus the settings in GIT_ENV. Each
call has a timeout and an output cap; both end in GitError (exit 3).
"""
import os
import signal
import subprocess
import tempfile

try:
    import resource
except ImportError:              # not POSIX: the size check after the run remains
    resource = None

ALLOWED = frozenset({"rev-parse", "log", "show", "diff-tree", "ls-files", "cat-file"})
DIFF_COMMANDS = frozenset({"log", "show", "diff-tree"})
CONFIG = ("core.hooksPath=/dev/null", "core.fsmonitor=false", "core.pager=cat",
          "color.ui=false", "log.showSignature=false")
NO_DIFF_DRIVERS = ("--no-ext-diff", "--no-textconv")
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
DEFAULT_TIMEOUT = 60
MAX_OUTPUT = 100 * 1024 * 1024
_STDERR_SHOWN = 300
_SIGXFSZ = getattr(signal, "SIGXFSZ", None)    # file size limit hit


class GitError(Exception):
    """git failed, is missing, timed out or produced too much output (exit 3)."""


class UsageError(Exception):
    """Bad user input, refused before git runs (exit 2)."""


class CommandRefused(Exception):
    """A subcommand outside ALLOWED; raised before any process starts."""


def git_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_ENV)
    return env


def check_rev(rev):
    """A revision from the user: non-empty and never option-like."""
    if not rev or rev.startswith("-"):
        raise UsageError(f"--rev must not start with '-': {rev}")
    return rev


def rev_args(rev):
    """The revision as git gets it: always after --end-of-options."""
    return ["--end-of-options", check_rev(rev)]


def build_argv(repo, subcommand, args):
    if subcommand not in ALLOWED:
        raise CommandRefused(f"git subcommand not allowed: {subcommand!r}")
    argv = ["git", "--no-pager"]
    for setting in CONFIG:
        argv += ["-c", setting]
    argv += ["-C", str(repo), subcommand]
    if subcommand in DIFF_COMMANDS:
        argv += NO_DIFF_DRIVERS
    argv += [str(a) for a in args]
    if any("%G" in a for a in argv):
        raise CommandRefused("%G format placeholders are not allowed")
    return argv


def _limit_output(max_bytes):
    def apply():
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes + 1, max_bytes + 1))
    return apply if resource is not None else None


def _first_line(data):
    text = data.decode("utf-8", "replace").strip()
    return text.splitlines()[0][:_STDERR_SHOWN] if text else ""


class Git:
    """One repository, one timeout, one output cap for every call."""

    def __init__(self, repo, *, timeout=DEFAULT_TIMEOUT, max_output=MAX_OUTPUT):
        self.repo = str(repo)
        self.timeout = timeout
        self.max_output = max_output

    def run(self, subcommand, *args, ok=(0,)):
        """stdout bytes of one git call; GitError on any failure."""
        argv = build_argv(self.repo, subcommand, args)
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.run(argv, shell=False, env=git_env(),
                                      stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                      timeout=self.timeout,
                                      preexec_fn=_limit_output(self.max_output))
            except FileNotFoundError:
                raise GitError("git not found (is it installed and on PATH?)") from None
            except subprocess.TimeoutExpired:
                raise GitError(f"git {subcommand} timed out after "
                               f"{self.timeout:g} s") from None
            size = os.fstat(out.fileno()).st_size
            if size > self.max_output or (_SIGXFSZ is not None
                                          and proc.returncode == -_SIGXFSZ):
                raise GitError("git output too large")
            err.seek(0)
            stderr = err.read(4096)
            if proc.returncode not in ok:
                raise GitError(f"git {subcommand} failed: {_first_line(stderr)}")
            out.seek(0)
            return out.read()

    def resolve(self, rev="HEAD"):
        """Full commit hash for rev, after checking the repository: GitError
        for not a repository, no commits, or an unknown revision."""
        args = rev_args(rev)
        try:
            self.run("rev-parse", "--git-dir")
        except GitError as exc:
            if "not found" in str(exc) or "timed out" in str(exc):
                raise
            raise GitError(f"not a git repository: {self.repo}") from None
        out = self.run("rev-parse", "--verify", "--quiet", "--end-of-options",
                       args[1] + "^{commit}", ok=(0, 1))
        if out.strip():
            return out.decode("ascii", "replace").strip()
        if not self.run("log", "--all", "-1", "--format=tformat:%H", ok=(0, 128)).strip():
            raise GitError(f"repository has no commits: {self.repo}")
        raise GitError(f"unknown revision: {rev}")
