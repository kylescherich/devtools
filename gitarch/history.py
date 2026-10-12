"""History model: one read of `git log` turned into overview, per-file
timelines, hotspots and activity.

The log is read once (--topo-order, reversed here to oldest first; git
cannot --follow with --reverse), with -z --raw --numstat -M. Commit messages
are not part of it: subjects() reads only subject lines, and only when the
report asks for them (--messages). Author e-mails are read
only to count distinct authors and are never kept; author names are kept
only with authors=True. Merge commits carry no diff (git log's default), so
they count as merges and never as file changes.

"Recent" hotspots cover the DAYS before the newest analysed commit, not the
current date, so the same repository always gives the same model.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from gitarch._redact import redact
from gitarch.gitcmd import GitError, rev_args

TOP = 15
RECENT_DAYS = 90
SUBJECT_CHARS = 120
LATEST = 20            # commits listed with --messages when there is no --path
PATH_COMMITS = 200     # commits listed for --path
DIFF_COMMITS = 10
DIFF_LINES = 200
MONTHLY_AFTER_DAYS = 365
_FORMAT = "--format=tformat:%x1e%H%x1f%P%x1f%ct%x1f%aE%x1f%aN"
_NUMSTAT = re.compile(r"(\d+|-)\t(\d+|-)\t(.*)", re.DOTALL)


@dataclass
class Change:
    status: str            # A, M, D, R, C, T
    path: str
    old_path: str | None
    added: int | None      # None for binary
    deleted: int | None


@dataclass
class Commit:
    sha: str
    parents: list
    ts: int
    author_email: str = field(repr=False)
    author_name: str = field(repr=False)
    changes: list = field(default_factory=list)

    @property
    def is_merge(self):
        return len(self.parents) > 1


@dataclass
class Rename:
    old: str
    new: str
    sha: str
    date: datetime


@dataclass
class FileHistory:
    path: str                      # current path (last path for a deleted file)
    introduced: tuple | None       # (sha, date); None if added before the analysed range
    last_changed: tuple            # (sha, date)
    commits: int = 0
    added: int | None = 0          # None when any change was binary
    deleted: int | None = 0
    binary: bool = False
    renames: list = field(default_factory=list)
    deleted_in: tuple | None = None

    @property
    def churn(self):
        return (self.added or 0) + (self.deleted or 0)


@dataclass
class CommitSummary:
    sha: str
    date: datetime
    added: int | None          # None when a binary file changed
    deleted: int | None
    merge: bool


@dataclass
class Diff:
    sha: str
    date: datetime
    lines: list                # redacted diff lines, at most DIFF_LINES
    more: int                  # lines left out
    binary: int                # binary file diffs skipped


@dataclass
class Overview:
    rev: str
    sha: str
    commits: int
    merges: int
    ordinary: int
    first: datetime | None
    last: datetime | None
    authors: int


@dataclass
class Hotspot:
    path: str
    changes: int
    churn: int


@dataclass
class Hotspots:
    days: int | None               # None = all time
    since: datetime | None
    files_by_changes: list
    files_by_churn: list
    dirs_by_changes: list
    dirs_by_churn: list


@dataclass
class History:
    overview: Overview
    path: str | None
    followed: bool
    files: list                    # FileHistory still present at the revision
    deleted: list                  # FileHistory deleted by the revision
    hotspots: Hotspots
    recent: Hotspots
    activity_unit: str             # "week" or "month"
    activity: list                 # (label, commits)
    authors: list | None           # (name, commits) with authors=True, else None
    commits: list                  # CommitSummary, newest first
    aliases: list                  # --path and its earlier names (followed renames)


def _utc(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _decode(data):
    return data.decode("utf-8", "replace")


def parse_log(data):
    """Commits from `git log -z --raw --numstat` output with _FORMAT.

    The output is a run of NUL-terminated tokens. A header starts with 0x1e;
    a raw entry starts with ':' and is followed by one path (two for a
    rename or copy); a numstat entry is 'added<TAB>deleted<TAB>path', or
    'added<TAB>deleted<TAB>' followed by two paths. Paths are consumed by
    count and never classified, so no file name can pass for a header."""
    tokens = _decode(data).split("\0")
    commits, current, counts = [], None, {}
    i = 0
    while i < len(tokens):
        token = tokens[i].lstrip("\n")
        i += 1
        if not token:
            continue
        if token.startswith("\x1e"):
            if current is not None:
                _apply_counts(current, counts)
            sha, parents, ts, email, name = token[1:].split("\x1f", 4)
            current = Commit(sha, parents.split(), int(ts), email, name)
            commits.append(current)
            counts = {}
        elif token.startswith(":") and current is not None:
            status = token.split()[-1][:1]
            if status in "RC":
                old, new = tokens[i], tokens[i + 1]
                i += 2
            else:
                old, new = None, tokens[i]
                i += 1
            current.changes.append(Change(status, new, old, 0, 0))
        elif current is not None and (match := _NUMSTAT.fullmatch(token)):
            added, deleted, path = match.groups()
            if not path:                      # rename: old and new follow
                path = tokens[i + 1]
                i += 2
            counts[path] = (None if added == "-" else int(added),
                            None if deleted == "-" else int(deleted))
        else:
            raise GitError(f"unexpected git log output near token {i}")
    if current is not None:
        _apply_counts(current, counts)
    return commits


def _apply_counts(commit, counts):
    for change in commit.changes:
        change.added, change.deleted = counts.get(change.path, (0, 0))


def _track(commits):
    """FileHistory per file identity, following renames: (live, deleted,
    touches), touches being (commit time, record, churn) per file change."""
    live, gone, touches = {}, [], []
    for c in commits:
        date = _utc(c.ts)
        for ch in c.changes:
            if ch.status == "R" and ch.old_path in live:
                record = live.pop(ch.old_path)
                record.renames.append(Rename(ch.old_path, ch.path, c.sha, date))
                record.path = ch.path
            elif ch.path in live:
                record = live[ch.path]
            else:
                # Added here, or (with --follow / a path filter / a merge
                # resolution) first seen here.
                introduced = (c.sha, date) if ch.status in "ACR" else None
                record = FileHistory(ch.path, introduced, (c.sha, date))
                if ch.status == "R":
                    record.renames.append(Rename(ch.old_path, ch.path, c.sha, date))
            record.commits += 1
            record.last_changed = (c.sha, date)
            touches.append((c.ts, record, (ch.added or 0) + (ch.deleted or 0)))
            if ch.added is None or ch.deleted is None:
                record.binary = True
            if record.binary:
                record.added = record.deleted = None
            else:
                record.added += ch.added
                record.deleted += ch.deleted
            if ch.status == "D":
                record.deleted_in = (c.sha, date)
                live.pop(ch.path, None)
                gone.append(record)
            else:
                live[ch.path] = record
    return (sorted(live.values(), key=lambda r: r.path),
            sorted(gone, key=lambda r: (r.path, r.deleted_in[1])), touches)


def _directory(path):
    return path.rsplit("/", 1)[0] if "/" in path else "."


def _rank(totals, index):
    """Top entries by the metric at index, ties by path."""
    items = sorted(((path, value) for path, value in totals.items() if value[index] > 0),
                   key=lambda kv: (-kv[1][index], kv[0]))
    return [Hotspot(path, changes, churn) for path, (changes, churn) in items[:TOP]]


def _hotspots(touches, days=None):
    """Changes and churn per file (by its current path) and per directory,
    over all touches or the DAYS before the newest one."""
    since = None
    if days is not None and touches:
        since = _utc(max(ts for ts, _, _ in touches)) - timedelta(days=days)
    files, dirs = {}, {}
    for ts, record, churn in touches:
        if since is not None and _utc(ts) < since:
            continue
        for table, key in ((files, record.path), (dirs, _directory(record.path))):
            changes, total = table.get(key, (0, 0))
            table[key] = (changes + 1, total + churn)
    return Hotspots(days, since, _rank(files, 0), _rank(files, 1),
                    _rank(dirs, 0), _rank(dirs, 1))


def _activity(commits):
    """(unit, [(label, commits)]) with every week (or month) in the span."""
    if not commits:
        return "week", []
    dates = [_utc(c.ts).date() for c in commits]
    first, last = min(dates), max(dates)
    counts = {}
    if (last - first).days > MONTHLY_AFTER_DAYS:
        for d in dates:
            counts[(d.year, d.month)] = counts.get((d.year, d.month), 0) + 1
        out, (y, m) = [], (first.year, first.month)
        while (y, m) <= (last.year, last.month):
            out.append((f"{y:04d}-{m:02d}", counts.get((y, m), 0)))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return "month", out
    for d in dates:
        key = d.isocalendar()[:2]
        counts[key] = counts.get(key, 0) + 1
    out, day = [], first - timedelta(days=first.weekday())
    while day <= last:
        year, week = day.isocalendar()[:2]
        out.append((f"{year:04d}-W{week:02d}", counts.get((year, week), 0)))
        day += timedelta(days=7)
    return "week", out


def _path_args(git, sha, path):
    """(options, pathspec, followed): a file is followed across renames.
    Options go before --end-of-options, the path always after "--"."""
    if path is None:
        return [], [], False
    path = path.removeprefix("./").rstrip("/")
    if not path:
        return [], [], False
    kind = git.run("cat-file", "-t", f"{sha}:{path}", ok=(0, 128)).strip()
    if kind == b"blob":
        return ["--follow"], ["--", path], True
    return [], ["--", path], False


def _summary(commit):
    added = deleted = 0
    for ch in commit.changes:
        if ch.added is None or ch.deleted is None:
            added = deleted = None
        elif added is not None:
            added += ch.added
            deleted += ch.deleted
    return CommitSummary(commit.sha, _utc(commit.ts), added, deleted, commit.is_merge)


def _aliases(path, followed, files, deleted):
    if path is None:
        return []
    names = {path.removeprefix("./").rstrip("/")}
    if followed:
        for record in files + deleted:
            names |= {r.old for r in record.renames} | {r.new for r in record.renames}
    return sorted(names)


def build(git, rev="HEAD", *, path=None, recent_days=RECENT_DAYS, authors=False):
    """The History of rev (optionally only path) in the repository of git."""
    sha = git.resolve(rev)
    options, pathspec, followed = _path_args(git, sha, path)
    # git cannot follow renames with --reverse, so the log is read newest
    # first and reversed here.
    data = git.run("log", "-z", "--raw", "--numstat", "-M", "--topo-order",
                   _FORMAT, *options, *rev_args(sha), *pathspec)
    commits = parse_log(data)[::-1]
    files, deleted, touches = _track(commits)
    merges = sum(1 for c in commits if c.is_merge)
    overview = Overview(rev, sha, len(commits), merges, len(commits) - merges,
                        _utc(min(c.ts for c in commits)) if commits else None,
                        _utc(max(c.ts for c in commits)) if commits else None,
                        len({c.author_email.lower() for c in commits}))
    unit, activity = _activity(commits)
    names = None
    if authors:
        tally = {}
        for c in commits:
            tally[c.author_name] = tally.get(c.author_name, 0) + 1
        names = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    return History(overview, path, followed, files, deleted,
                   _hotspots(touches), _hotspots(touches, recent_days),
                   unit, activity, names, [_summary(c) for c in reversed(commits)],
                   _aliases(path, followed, files, deleted))


def subjects(git, model):
    """{sha: subject} for the commits a report lists: the first line only,
    at most SUBJECT_CHARS characters, redacted. Read with an explicit
    format; the commit body is never requested."""
    limit = PATH_COMMITS if model.path is not None else LATEST
    options, pathspec, _ = _path_args(git, model.overview.sha, model.path)
    data = git.run("log", "-z", "--topo-order", f"--max-count={limit}",
                   "--format=tformat:%H%x1f%s", *options,
                   *rev_args(model.overview.sha), *pathspec)
    out = {}
    for record in _decode(data).split("\0"):
        sha, sep, subject = record.lstrip("\n").partition("\x1f")
        if sep:
            out[sha] = redact(subject.splitlines()[0] if subject else "")[:SUBJECT_CHARS]
    return out


_BINARY = re.compile(r"Binary files .* differ")


def diffs(git, model):
    """Diffs of the newest DIFF_COMMITS non-merge commits of a --path model,
    limited to the path and its earlier names: at most DIFF_LINES lines per
    commit, binary file diffs skipped and counted, every line redacted."""
    if model.path is None:
        raise ValueError("diffs need a path")
    out = []
    for commit in [c for c in model.commits if not c.merge][:DIFF_COMMITS]:
        data = git.run("show", "-p", "-M", "--format=tformat:",
                       *rev_args(commit.sha), "--", *model.aliases)
        lines, binary = [], 0
        for line in redact(_decode(data)).split("\n"):
            if _BINARY.fullmatch(line):
                binary += 1
            elif line or lines:
                lines.append(line)
        while lines and not lines[-1]:
            lines.pop()
        out.append(Diff(commit.sha, commit.date, lines[:DIFF_LINES],
                        max(0, len(lines) - DIFF_LINES), binary))
    return out
