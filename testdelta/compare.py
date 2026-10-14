"""Comparison of two Runs: what got better, what got worse.

Every test id present on either side lands in at most one status category:

  newly_failing   pass/skip/xfail -> fail/error/xpass      regression
  removed         in before, missing after                 regression
  newly_skipped   pass -> skip                             regression
  fixed           fail/error -> pass                       improvement
  unskipped       skip -> pass                             improvement
  added           missing before, in after                 improvement
  still_failing   fail/error/xpass -> fail/error/xpass     unchanged problem
  other_changes   any other status change                  not counted

and, independently, in slower or faster when both times are known:
slower means after >= ratio * before AND after - before >= minimum (faster
the same in reverse). An n/a time is never treated as 0. Slower counts as a
regression only when asked (--fail-on-slow).
"""
from dataclasses import dataclass

from testdelta.readers import BAD, STATUSES

REGRESSIONS = ("newly_failing", "removed", "newly_skipped")
IMPROVEMENTS = ("fixed", "unskipped", "added")
CATEGORIES = (REGRESSIONS + ("slower",) + IMPROVEMENTS
              + ("faster", "still_failing", "other_changes"))

_WAS_OK = frozenset({"pass", "skip", "xfail"})


@dataclass(frozen=True)
class Change:
    id: str
    before: str = None          # status, or None when absent
    after: str = None
    before_time: float = None
    after_time: float = None
    message: str = None         # after side, newly_failing only
    traceback: tuple = ()


@dataclass(frozen=True)
class Delta:
    format: str
    counts_before: dict         # status -> count, in STATUSES order
    counts_after: dict
    total_before: int
    total_after: int
    categories: dict            # name -> tuple of Change, sorted by id
    warnings: tuple
    slow_ratio: float
    slow_min: float
    duplicates_before: tuple = ()
    duplicates_after: tuple = ()
    unresolved_before: tuple = ()
    unresolved_after: tuple = ()

    def regressions(self, fail_on_slow=False):
        names = REGRESSIONS + (("slower",) if fail_on_slow else ())
        return sum(len(self.categories[name]) for name in names)

    def improvements(self):
        return sum(len(self.categories[name]) for name in IMPROVEMENTS)


def classify(before, after):
    """The status category of one id, or None when nothing changed."""
    if before is None:
        return "added"
    if after is None:
        return "removed"
    if before in BAD and after in BAD:
        return "still_failing"
    if before == after:
        return None
    if before in _WAS_OK and after in BAD:
        return "newly_failing"
    if before == "pass" and after == "skip":
        return "newly_skipped"
    if before in ("fail", "error") and after == "pass":
        return "fixed"
    if before == "skip" and after == "pass":
        return "unskipped"
    return "other_changes"


def speed(before_time, after_time, ratio, minimum):
    """'slower', 'faster' or None; None whenever a time is unknown."""
    if before_time is None or after_time is None:
        return None
    if after_time >= ratio * before_time and after_time - before_time >= minimum:
        return "slower"
    if before_time >= ratio * after_time and before_time - after_time >= minimum:
        return "faster"
    return None


def _counts(results):
    counts = {status: 0 for status in STATUSES}
    for result in results.values():
        counts[result.status] += 1
    return counts


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _warnings(before, after, total_before, total_after):
    out = []
    if total_after < total_before:
        out.append(f"total test count dropped from {total_before} to {total_after} "
                   f"({total_before - total_after} fewer)")
    for side, run in (("before", before), ("after", after)):
        if run.duplicates:
            out.append(f"{side}: {_plural(len(run.duplicates), 'duplicate test id')}; "
                       "the worst status was kept")
        if run.unresolved:
            out.append(f"{side}: {_plural(len(run.unresolved), 'test')} started without "
                       "a status (truncated log?); left out")
    for side, run in (("before", before), ("after", after)):
        if run.results and all(r.time is None for r in run.results.values()):
            out.append(f"{side}: no test times; slower/faster need both times")
    return tuple(out)


def compare(before, after, slow_ratio=2.0, slow_min=0.5):
    if before.format != after.format:
        raise ValueError("runs of different formats")
    lists = {name: [] for name in CATEGORIES}
    for test_id in sorted(set(before.results) | set(after.results)):
        old, new = before.results.get(test_id), after.results.get(test_id)
        category = classify(old and old.status, new and new.status)
        change = Change(test_id, old and old.status, new and new.status,
                        old.time if old else None, new.time if new else None)
        if category == "newly_failing":
            lists[category].append(Change(change.id, change.before, change.after,
                                          change.before_time, change.after_time,
                                          new.message, new.traceback))
        elif category:
            lists[category].append(change)
        if old and new:
            pace = speed(old.time, new.time, slow_ratio, slow_min)
            if pace:
                lists[pace].append(change)
    total_before, total_after = len(before.results), len(after.results)
    return Delta(before.format, _counts(before.results), _counts(after.results),
                 total_before, total_after,
                 {name: tuple(items) for name, items in lists.items()},
                 _warnings(before, after, total_before, total_after),
                 slow_ratio, slow_min, before.duplicates, after.duplicates,
                 before.unresolved, after.unresolved)
