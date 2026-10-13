"""Repository boundary checks, before any git process starts (pure Python).

gitarch reads one ordinary repository: its working tree and its own .git
directory. find_repo() walks up from the given path to the first directory
holding .git and refuses (Refused, exit 3) anything that would make git read
or write elsewhere or reach the network:

- a bare repository, a symlinked repository path or .git, a .git file
  (gitdir pointer) unless --allow-linked-worktree and it is a genuine linked
  worktree, a commondir file in a repository that is not a linked worktree;
- objects/info/alternates or objects/info/http-alternates with content,
  objects/pack/*.promisor;
- in the repository config (the common dir's for a linked worktree, and
  config.worktree when extensions.worktreeConfig is set): any [include] or
  [includeIf "..."] section, core.attributesFile, core.worktree,
  extensions.partialClone, remote.*.promisor, remote.*.partialCloneFilter,
  mailmap.file, mailmap.blob;
- a symlink at HEAD, config, refs, packed-refs, objects, info, commondir,
  objects/info or objects/pack in a git directory in scope.

Config files are read by a small strict reader: anything it cannot read
without guessing is refused. The returned Scope names the only directories
git may touch; the runner passes them as GIT_DIR and GIT_WORK_TREE.
"""
import glob
import os
import re
import stat
from dataclasses import dataclass

MAX_CONFIG_BYTES = 1024 * 1024
TOP_LEVEL = ("HEAD", "config", "refs", "packed-refs", "objects", "info", "commondir")
OBJECT_DIRS = (os.path.join("objects", "info"), os.path.join("objects", "pack"))
REFUSED_SECTIONS = ("include", "includeif")
REFUSED_KEYS = {("core", "attributesfile"), ("core", "worktree"),
                ("extensions", "partialclone"), ("mailmap", "file"), ("mailmap", "blob")}
REFUSED_REMOTE_KEYS = ("promisor", "partialclonefilter")


class Refused(Exception):
    """The repository is outside gitarch's boundary (exit 3)."""


@dataclass(frozen=True)
class Scope:
    work_tree: str                 # real path of the work-tree root
    git_dir: str                   # real path of the git dir (.git, or worktrees/<id>)
    common_dir: str | None = None  # real path of the common dir, linked worktrees only

    @property
    def roots(self):
        """The reading scope: work tree, git dir and (linked) common dir."""
        return tuple(p for p in (self.work_tree, self.git_dir, self.common_dir) if p)

    @property
    def output_roots(self):
        """Where --output is refused: the reading scope plus, for a linked
        worktree whose common dir is a non-bare "<dir>/.git", the main work
        tree <dir> that holds it. Not part of the reading scope."""
        roots = self.roots
        if self.common_dir and os.path.basename(self.common_dir) == ".git":
            roots += (os.path.dirname(self.common_dir),)
        return roots

    def inside(self, path, roots=None):
        """True when the real location of path is in any of roots (default:
        the reading scope)."""
        target = os.path.join(os.path.realpath(os.path.dirname(os.path.abspath(path))),
                              os.path.basename(os.path.abspath(path)))
        return any(_within(target, root) for root in (roots or self.roots))


def _within(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


# --- strict config reader ----------------------------------------------------

_SECTION = re.compile(r'\[\s*([A-Za-z0-9-]+)(?:\s+"((?:[^"\\\n]|\\["\\])*)")?\s*\]'
                      r'\s*(?:[#;].*)?')
_KEY = re.compile(r"([A-Za-z][A-Za-z0-9-]*)\s*(=\s*(.*))?")
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}


def _value(raw, where):
    """Parse a value: quotes, escapes, inline comments; refuse anything odd."""
    out, quoted, i, pending = [], False, 0, ""
    while i < len(raw):
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= len(raw) or raw[i + 1] not in _ESCAPES:
                raise Refused(f"config {where}: unsupported escape")
            out.append(pending + _ESCAPES[raw[i + 1]])
            pending, i = "", i + 2
            continue
        if ch == '"':
            out.append(pending)
            pending, quoted, i = "", not quoted, i + 1
            continue
        if not quoted and ch in "#;":
            break
        if not quoted and ch in " \t":
            pending += ch                     # kept only if more value follows
        else:
            out.append(pending + ch)
            pending = ""
        i += 1
    if quoted:
        raise Refused(f"config {where}: unterminated quote")
    return "".join(out)


def _logical_lines(text, name):
    """Physical lines joined at a trailing backslash (continuation)."""
    lines, current, start = [], "", 1
    for number, line in enumerate(text.split("\n"), 1):
        if not current:
            start = number
        if line.endswith("\\") and not line.endswith("\\\\"):
            current += line[:-1]
            continue
        lines.append((start, current + line))
        current = ""
    if current:
        raise Refused(f"config {name}: continuation at end of file")
    return lines


def read_config(path):
    """[(section, subsection, key, value)] of a git config file, section and
    key lower-cased, subsection kept as written; each section header also
    yields (section, subsection, None, None), so an empty section is seen.
    Refused for anything that cannot be read without guessing."""
    name = os.path.basename(path)
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise Refused(f"config {path}: not a regular file")
        if info.st_size > MAX_CONFIG_BYTES:
            raise Refused(f"config {path}: larger than {MAX_CONFIG_BYTES} bytes")
        with open(path, "rb") as f:
            data = f.read(MAX_CONFIG_BYTES + 1)
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(f"config {path}: not UTF-8") from None
    except OSError as exc:
        raise Refused(f"config {path}: unreadable ({exc.strerror})") from None
    if "\0" in text or "\r" in text.replace("\r\n", "\n"):
        raise Refused(f"config {path}: unexpected control characters")
    text = text.replace("\r\n", "\n")
    entries, section = [], None
    for number, line in _logical_lines(text, name):
        where = f"{name} line {number}"
        stripped = line.strip()
        if not stripped or stripped[0] in "#;":
            continue
        if stripped.startswith("["):
            match = _SECTION.fullmatch(stripped)
            if not match:
                raise Refused(f"config {where}: unreadable or deprecated section header")
            sub = match.group(2)
            if sub is not None:
                sub = re.sub(r"\\(.)", r"\1", sub)
            section = (match.group(1).lower(), sub)
            entries.append((*section, None, None))
            continue
        if section is None:
            raise Refused(f"config {where}: key outside any section")
        match = _KEY.fullmatch(stripped)
        if not match:
            # "key # comment" / "key ; comment": a bare boolean with a comment
            head = re.match(r"([A-Za-z][A-Za-z0-9-]*)\s+[#;]", stripped)
            if not head:
                raise Refused(f"config {where}: unreadable line")
            entries.append((*section, head.group(1).lower(), "true"))
            continue
        key, has_value, raw = match.groups()
        value = _value(raw, where) if has_value else "true"
        entries.append((*section, key.lower(), value))
    return entries


# --- the boundary ------------------------------------------------------------

def _is_link(path):
    return os.path.islink(path)


def _check_symlinks(git_dir, label):
    for entry in TOP_LEVEL + OBJECT_DIRS:
        if _is_link(os.path.join(git_dir, entry)):
            raise Refused(f"{label}/{entry} is a symlink")


def _check_objects(git_dir, label):
    for name in ("alternates", "http-alternates"):
        path = os.path.join(git_dir, "objects", "info", name)
        if not os.path.lexists(path):
            continue
        try:
            with open(path, "rb") as f:
                content = f.read(65536)
        except OSError:
            raise Refused(f"{label}/objects/info/{name} is unreadable") from None
        if content.strip():
            raise Refused(f"{label}/objects/info/{name} is not empty "
                          "(objects from another repository)")
    promisors = glob.glob(os.path.join(glob.escape(git_dir), "objects", "pack", "*.promisor"))
    if promisors:
        raise Refused(f"{label} has promisor packs (partial clone)")


def _check_config(path, label):
    entries = read_config(path)
    worktree_config = False
    for section, sub, key, value in entries:
        if section in REFUSED_SECTIONS:
            raise Refused(f"{label}: [{section}] sections are not allowed")
        if key is None:
            continue
        if (section, key) in REFUSED_KEYS:
            raise Refused(f"{label}: {section}.{key} is not allowed")
        if section == "remote" and key in REFUSED_REMOTE_KEYS:
            raise Refused(f"{label}: remote.<name>.{key} is not allowed (partial clone)")
        if section == "extensions" and key == "worktreeconfig":
            worktree_config = value.strip().lower() in ("true", "yes", "on", "1")
    return worktree_config


def _check_git_dir(git_dir, label, *, has_config=True):
    """Symlinks, objects and config of one git directory in scope."""
    _check_symlinks(git_dir, label)
    if os.path.isdir(os.path.join(git_dir, "objects")):
        _check_objects(git_dir, label)
    if not has_config:
        return False
    config = os.path.join(git_dir, "config")
    if not os.path.lexists(config):
        raise Refused(f"{label}/config is missing")
    return _check_config(config, f"{label}/config")


def _read_pointer(path, prefix, label):
    try:
        with open(path, "rb") as f:
            text = f.read(4096).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise Refused(f"{label} is unreadable") from None
    if text.endswith("\n"):
        text = text[:-1]
    if not text.startswith(prefix) or "\n" in text or not text[len(prefix):]:
        raise Refused(f"{label} is not a single '{prefix}<path>' line")
    return text[len(prefix):]


def _linked_worktree(root, dot_git):
    """(admin dir, common dir) of a genuine linked worktree, else Refused."""
    target = _read_pointer(dot_git, "gitdir: ", ".git file")
    admin = os.path.realpath(os.path.join(root, target))
    parent = os.path.dirname(admin)
    if os.path.basename(parent) != "worktrees":
        raise Refused(".git file points outside the repository (not a linked worktree)")
    common = os.path.dirname(parent)
    back = _read_pointer(os.path.join(admin, "gitdir"), "", "worktree gitdir file")
    if os.path.realpath(os.path.join(admin, back)) != os.path.realpath(dot_git):
        raise Refused("linked worktree: its gitdir file does not point back to this .git")
    commondir = _read_pointer(os.path.join(admin, "commondir"), "", "worktree commondir file")
    if os.path.realpath(os.path.join(admin, commondir)) != os.path.realpath(common):
        raise Refused("linked worktree: commondir does not resolve to its common directory")
    if _is_link(common) or not os.path.isdir(os.path.join(common, "objects")):
        raise Refused("linked worktree: common directory is not a git directory")
    return admin, os.path.realpath(common)


def _looks_bare(path):
    return all(os.path.exists(os.path.join(path, n)) for n in ("HEAD", "objects", "refs"))


def find_repo(path, *, allow_linked=False):
    """The Scope of the repository at or above path; Refused otherwise."""
    start = os.path.abspath(path)
    if _is_link(start):
        raise Refused(f"repository path is a symlink: {path}")
    if not os.path.isdir(start):
        raise Refused(f"not a git repository: {path}")
    if not os.path.lexists(os.path.join(start, ".git")) and _looks_bare(start):
        raise Refused(f"bare repositories are not supported: {path}")
    root = start
    while not os.path.lexists(os.path.join(root, ".git")):
        parent = os.path.dirname(root)
        if parent == root:
            raise Refused(f"not a git repository: {path}")
        root = parent
    if _is_link(root):
        raise Refused(f"repository path is a symlink: {root}")
    dot_git = os.path.join(root, ".git")
    work_tree = os.path.realpath(root)
    if _is_link(dot_git):
        raise Refused(".git is a symlink")
    if os.path.isfile(dot_git):
        if not allow_linked:
            raise Refused(".git is a file (gitdir pointer); linked worktrees need "
                          "--allow-linked-worktree")
        admin, common = _linked_worktree(root, dot_git)
        worktree_config = _check_git_dir(common, "common dir")
        _check_git_dir(admin, "worktree git dir", has_config=False)
        if worktree_config and os.path.lexists(os.path.join(admin, "config.worktree")):
            _check_config(os.path.join(admin, "config.worktree"),
                          "worktree git dir/config.worktree")
        return Scope(work_tree, admin, common)
    if not os.path.isdir(dot_git):
        raise Refused(".git is neither a directory nor a file")
    git_dir = os.path.realpath(dot_git)
    if not _within(git_dir, work_tree):
        raise Refused(".git resolves outside the repository")
    if os.path.lexists(os.path.join(git_dir, "commondir")):
        raise Refused(".git/commondir in a repository that is not a linked worktree")
    worktree_config = _check_git_dir(git_dir, ".git")
    if worktree_config and os.path.lexists(os.path.join(git_dir, "config.worktree")):
        _check_config(os.path.join(git_dir, "config.worktree"), ".git/config.worktree")
    return Scope(work_tree, git_dir)
