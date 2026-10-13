"""The only place gitarch runs a program: git, read-only, hardened.

Every call is subprocess.Popen([...], shell=False, start_new_session=True)
with a fixed argument list:

    /abs/path/git --no-pager --literal-pathspecs
        -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.pager=cat
        -c color.ui=false -c log.showSignature=false
        -c core.attributesFile=/dev/null
        -C <work tree> <subcommand> [--no-ext-diff --no-textconv] ...

Only the subcommands in ALLOWED run; anything else raises CommandRefused
before a process starts. log, show and diff-tree always get --no-ext-diff
--no-textconv, inserted here so no caller can forget them. The one call
outside the allow-list is Git.version(), which runs exactly
[/abs/path/git, "--version"] with the same environment and bounds, for the
report header.

git is resolved once by resolve_git(): shutil.which over the absolute
entries of PATH only (".", empty and relative entries are dropped), refused
when its real path is inside the repository (or the common dir and its main
work tree) or its directory is the real current directory; it is then always
called by that absolute path. The environment is a copy of os.environ
without any GIT_* key, plus the settings in GIT_ENV (no lazy fetch, literal
pathspecs, no system attributes, no system or global config, no prompts, no
optional locks, LC_ALL=C), the sanitized PATH, and an explicit GIT_DIR and
GIT_WORK_TREE from the preflight Scope.

Bounds: stdout and stderr are read by two reader threads, each with its own
hard cap (100 MB stdout, 1 MB stderr); a reader that hits its cap keeps
draining and discarding, so neither pipe can block the other. A timeout or
a cap overrun kills the whole process group (SIGTERM, then SIGKILL after
2 s; a plain kill off POSIX) and ends in GitError (exit 3): "git timed out"
or "output limit".

Repository boundary:
gitarch reads one ordinary repository: its working tree and its own .git
directory. It refuses (exit 3, clear message, before any git call) anything
that would make git read or write elsewhere or reach the network. Linked
worktrees are allowed only with --allow-linked-worktree, which adds the
resolved common directory to scope and nothing else.

M1.1 refuses partial clones, alternates, includes, gitdir pointers and
symlinked git dirs.
"""
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

from gitarch.preflight import Scope, find_repo

ALLOWED = frozenset({"rev-parse", "log", "show", "diff-tree", "ls-files", "cat-file"})
DIFF_COMMANDS = frozenset({"log", "show", "diff-tree"})
CONFIG = ("core.hooksPath=/dev/null", "core.fsmonitor=false", "core.pager=cat",
          "color.ui=false", "log.showSignature=false", "core.attributesFile=/dev/null")
GLOBAL_FLAGS = ("--no-pager", "--literal-pathspecs")
NO_DIFF_DRIVERS = ("--no-ext-diff", "--no-textconv")
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C",
           "GIT_NO_LAZY_FETCH": "1", "GIT_LITERAL_PATHSPECS": "1", "GIT_ATTR_NOSYSTEM": "1"}
DEFAULT_TIMEOUT = 60
MAX_OUTPUT = 100 * 1024 * 1024
MAX_STDERR = 1024 * 1024
KILL_GRACE = 2.0        # seconds between SIGTERM and SIGKILL
_CHUNK = 65536
_POLL = 0.02
_POSIX = os.name == "posix"
_STDERR_SHOWN = 300


class GitError(Exception):
    """git failed, is missing, timed out or produced too much output (exit 3)."""


class UsageError(Exception):
    """Bad user input, refused before git runs (exit 2)."""


class CommandRefused(Exception):
    """A subcommand outside ALLOWED; raised before any process starts."""


def safe_path(path=None):
    """PATH with only absolute entries: ".", empty and relative ones dropped."""
    path = os.environ.get("PATH", "") if path is None else path
    return os.pathsep.join(p for p in path.split(os.pathsep) if p and os.path.isabs(p))


def resolve_git(scope):
    """Absolute path of git from the sanitized PATH; GitError when it is
    missing, inside the repository, or in the current directory."""
    found = shutil.which("git", path=safe_path())
    if not found:
        raise GitError("git not found (is it installed and on PATH?)")
    real = os.path.realpath(found)
    if scope.inside(real, scope.output_roots):
        raise GitError(f"refusing to run git from inside the repository: {found}")
    if os.path.dirname(real) == os.path.realpath(os.getcwd()):
        raise GitError(f"refusing to run git from the current directory: {found}")
    return os.path.abspath(found)


def git_env(scope=None):
    """os.environ without any GIT_* key, plus GIT_ENV, the sanitized PATH
    and, for a Scope, the explicit GIT_DIR and GIT_WORK_TREE (set after the
    clear, so ours win)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_ENV)
    env["PATH"] = safe_path()
    if scope is not None:
        env["GIT_DIR"] = scope.git_dir
        env["GIT_WORK_TREE"] = scope.work_tree
    return env


def check_rev(rev):
    """A revision from the user: non-empty and never option-like."""
    if not rev or rev.startswith("-"):
        raise UsageError(f"--rev must not start with '-': {rev}")
    return rev


def rev_args(rev):
    """The revision as git gets it: always after --end-of-options."""
    return ["--end-of-options", check_rev(rev)]


def build_argv(repo, subcommand, args, executable="git"):
    if subcommand not in ALLOWED:
        raise CommandRefused(f"git subcommand not allowed: {subcommand!r}")
    argv = [executable, *GLOBAL_FLAGS]
    for setting in CONFIG:
        argv += ["-c", setting]
    argv += ["-C", str(repo), subcommand]
    if subcommand in DIFF_COMMANDS:
        argv += NO_DIFF_DRIVERS
    argv += [str(a) for a in args]
    if any("%G" in a for a in argv):
        raise CommandRefused("%G format placeholders are not allowed")
    return argv


class _Reader(threading.Thread):
    """Drains one pipe to EOF. Keeps at most cap bytes; past the cap it sets
    overflow and goes on reading and discarding, so the child never blocks
    on this pipe and the other pipe keeps flowing."""

    def __init__(self, pipe, cap, name):
        super().__init__(name=f"gitarch-{name}", daemon=True)
        self.pipe, self.cap = pipe, cap
        self.chunks, self.kept = [], 0
        self.overflow = threading.Event()

    def run(self):
        try:
            while True:
                chunk = self.pipe.read1(_CHUNK) if hasattr(self.pipe, "read1") \
                    else self.pipe.read(_CHUNK)
                if not chunk:
                    break
                room = self.cap - self.kept
                if len(chunk) > room:
                    if room > 0:
                        self.chunks.append(chunk[:room])
                        self.kept += room
                    self.overflow.set()
                    continue
                self.chunks.append(chunk)
                self.kept += len(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.pipe.close()
            except OSError:
                pass

    def data(self):
        return b"".join(self.chunks)


@dataclass
class RunRecord:
    """The last process a Git ran, for tests: its Popen and both readers."""
    proc: object
    readers: list = field(default_factory=list)


def _kill_group(proc):
    """SIGTERM to the whole process group, SIGKILL to it after KILL_GRACE
    seconds (also reaching children that outlive the leader); a plain kill
    off POSIX. Always reaps the leader."""
    if not _POSIX:
        proc.kill()
        proc.wait()
        return
    for sig, grace in ((signal.SIGTERM, KILL_GRACE), (signal.SIGKILL, None)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        if grace is not None:
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
    proc.wait()


def _first_line(data):
    text = data.decode("utf-8", "replace").strip()
    return text.splitlines()[0][:_STDERR_SHOWN] if text else ""


class Git:
    """One repository, one timeout, one output cap for every call. A path is
    checked by preflight.find_repo() first (Refused, before any process);
    every call then runs in the Scope's work tree with its GIT_DIR."""

    def __init__(self, repo, *, timeout=DEFAULT_TIMEOUT, max_output=MAX_OUTPUT,
                 max_stderr=MAX_STDERR, allow_linked=False, executable=None):
        """executable is for tests only (fake git helpers); the CLI never
        passes it, so real runs always go through resolve_git()."""
        self.scope = repo if isinstance(repo, Scope) else find_repo(
            repo, allow_linked=allow_linked)
        self.repo = self.scope.work_tree
        self.timeout = timeout
        self.max_output = max_output
        self.max_stderr = max_stderr
        self.last_run = None
        self.executable = executable or resolve_git(self.scope)

    def run(self, subcommand, *args, ok=(0,)):
        """stdout bytes of one git call; GitError on any failure."""
        argv = build_argv(self.repo, subcommand, args, self.executable)
        return self._execute(argv, subcommand, ok)

    def version(self):
        """'2.43.0' from [git, "--version"]: the single call outside ALLOWED,
        with the same environment, timeout and output cap."""
        out = self._execute([self.executable, "--version"], "--version", (0,))
        text = out.decode("utf-8", "replace").strip()
        return text.removeprefix("git version ").strip() or "unknown"

    def _execute(self, argv, subcommand, ok):
        try:
            proc = subprocess.Popen(argv, shell=False, env=git_env(self.scope),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=_POSIX)
        except FileNotFoundError:
            raise GitError("git not found (is it installed and on PATH?)") from None
        out = _Reader(proc.stdout, self.max_output, "stdout")
        err = _Reader(proc.stderr, self.max_stderr, "stderr")
        self.last_run = RunRecord(proc, [out, err])
        out.start()
        err.start()
        deadline = time.monotonic() + self.timeout
        failure = None
        # Done when git has exited AND both pipes are at EOF (a child that
        # keeps a pipe open counts against the timeout too). The waits
        # return as soon as their process or reader finishes.
        while True:
            failure = self._overflow(out, err, subcommand)
            if failure is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = f"git timed out after {self.timeout:g} s (git {subcommand})"
                break
            step = min(_POLL, remaining)
            if proc.poll() is None:
                try:
                    proc.wait(timeout=step)
                except subprocess.TimeoutExpired:
                    pass
            elif out.is_alive() or err.is_alive():
                (out if out.is_alive() else err).join(timeout=step)
            else:
                failure = self._overflow(out, err, subcommand)   # a last late overrun
                break
        if failure is not None:
            _kill_group(proc)
        for reader in (out, err):
            reader.join(timeout=KILL_GRACE + 1)
        if failure is not None:
            raise GitError(failure)
        if proc.returncode not in ok:
            raise GitError(f"git {subcommand} failed: {_first_line(err.data())}")
        return out.data()

    def _overflow(self, out, err, subcommand):
        for reader, cap in ((out, self.max_output), (err, self.max_stderr)):
            if reader.overflow.is_set():
                name = reader.name.removeprefix("gitarch-")
                return (f"git output too large: output limit of {cap} bytes "
                        f"exceeded on {name} (git {subcommand})")
        return None

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
