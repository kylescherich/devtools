"""The only place gitarch runs a program: git, read-only, hardened.

Every call is subprocess.run([...], shell=False) with a fixed argument list:

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
GIT_WORK_TREE from the preflight Scope. Each call has a timeout and an
output cap; both end in GitError (exit 3).

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
import tempfile

from gitarch.preflight import Scope, find_repo

try:
    import resource
except ImportError:              # not POSIX: the size check after the run remains
    resource = None

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
_STDERR_SHOWN = 300
_SIGXFSZ = getattr(signal, "SIGXFSZ", None)    # file size limit hit


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


def _limit_output(max_bytes):
    def apply():
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes + 1, max_bytes + 1))
    return apply if resource is not None else None


def _first_line(data):
    text = data.decode("utf-8", "replace").strip()
    return text.splitlines()[0][:_STDERR_SHOWN] if text else ""


class Git:
    """One repository, one timeout, one output cap for every call. A path is
    checked by preflight.find_repo() first (Refused, before any process);
    every call then runs in the Scope's work tree with its GIT_DIR."""

    def __init__(self, repo, *, timeout=DEFAULT_TIMEOUT, max_output=MAX_OUTPUT,
                 allow_linked=False, executable=None):
        """executable is for tests only (fake git helpers); the CLI never
        passes it, so real runs always go through resolve_git()."""
        self.scope = repo if isinstance(repo, Scope) else find_repo(
            repo, allow_linked=allow_linked)
        self.repo = self.scope.work_tree
        self.timeout = timeout
        self.max_output = max_output
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
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.run(argv, shell=False, env=git_env(self.scope),
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
