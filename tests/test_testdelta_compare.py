"""testdelta comparison: every category, the slow/fast rule (ratio AND
minimum, n/a never counted), totals and warnings. Runs are built directly."""
import itertools
import unittest

from testdelta.compare import (CATEGORIES, IMPROVEMENTS, REGRESSIONS, classify,
                               compare, speed)
from testdelta.readers import STATUSES, Result, Run


def run(tests, fmt="junit", duplicates=(), unresolved=()):
    """tests: id -> status or (status, time) or (status, time, message)."""
    results = {}
    for test_id, value in tests.items():
        if isinstance(value, str):
            value = (value,)
        status, time, message = (tuple(value) + (None, None))[:3]
        results[test_id] = Result(status, time, message,
                                  ("tb 1", "tb 2") if message else ())
    return Run(fmt, results, tuple(duplicates), tuple(unresolved))


def ids(delta, name):
    return [c.id for c in delta.categories[name]]


# The full transition table (before, after) -> category; None = unchanged.
EXPECTED = {
    ("pass", "pass"): None, ("pass", "fail"): "newly_failing",
    ("pass", "error"): "newly_failing", ("pass", "skip"): "newly_skipped",
    ("pass", "xfail"): "other_changes", ("pass", "xpass"): "newly_failing",
    ("fail", "pass"): "fixed", ("fail", "fail"): "still_failing",
    ("fail", "error"): "still_failing", ("fail", "skip"): "other_changes",
    ("fail", "xfail"): "other_changes", ("fail", "xpass"): "still_failing",
    ("error", "pass"): "fixed", ("error", "fail"): "still_failing",
    ("error", "error"): "still_failing", ("error", "skip"): "other_changes",
    ("error", "xfail"): "other_changes", ("error", "xpass"): "still_failing",
    ("skip", "pass"): "unskipped", ("skip", "fail"): "newly_failing",
    ("skip", "error"): "newly_failing", ("skip", "skip"): None,
    ("skip", "xfail"): "other_changes", ("skip", "xpass"): "newly_failing",
    ("xfail", "pass"): "other_changes", ("xfail", "fail"): "newly_failing",
    ("xfail", "error"): "newly_failing", ("xfail", "skip"): "other_changes",
    ("xfail", "xfail"): None, ("xfail", "xpass"): "newly_failing",
    ("xpass", "pass"): "other_changes", ("xpass", "fail"): "still_failing",
    ("xpass", "error"): "still_failing", ("xpass", "skip"): "other_changes",
    ("xpass", "xfail"): "other_changes", ("xpass", "xpass"): "still_failing",
}


class TestCategories(unittest.TestCase):
    def test_table_covers_every_transition(self):
        self.assertEqual(set(EXPECTED), set(itertools.product(STATUSES, STATUSES)))

    def test_classify_every_transition(self):
        for (before, after), expected in EXPECTED.items():
            with self.subTest(before=before, after=after):
                self.assertEqual(classify(before, after), expected)

    def test_compare_every_transition(self):
        b, a = {}, {}
        for before, after in EXPECTED:
            b[f"t.{before}_{after}"] = before
            a[f"t.{before}_{after}"] = after
        delta = compare(run(b), run(a))
        for (before, after), expected in EXPECTED.items():
            test_id = f"t.{before}_{after}"
            found = [name for name in CATEGORIES if test_id in ids(delta, name)]
            with self.subTest(test_id=test_id):
                self.assertEqual(found, [expected] if expected else [])

    def test_added_and_removed(self):
        delta = compare(run({"a": "pass", "gone": "fail"}), run({"a": "pass", "new": "skip"}))
        self.assertEqual(ids(delta, "removed"), ["gone"])
        self.assertEqual(ids(delta, "added"), ["new"])
        gone = delta.categories["removed"][0]
        self.assertEqual((gone.before, gone.after), ("fail", None))
        new = delta.categories["added"][0]
        self.assertEqual((new.before, new.after), (None, "skip"))

    def test_lists_sorted_by_id(self):
        names = ["z.t", "a.t", "m.t", "B.t"]
        delta = compare(run({n: "pass" for n in names}), run({n: "fail" for n in names}))
        self.assertEqual(ids(delta, "newly_failing"), sorted(names))

    def test_still_failing_not_counted_as_regression(self):
        delta = compare(run({"a": "fail", "b": "error"}), run({"a": "fail", "b": "fail"}))
        self.assertEqual(ids(delta, "still_failing"), ["a", "b"])
        self.assertEqual(delta.regressions(), 0)
        self.assertEqual(delta.improvements(), 0)

    def test_regression_and_improvement_counts(self):
        before = run({"nf": "pass", "rm": "pass", "ns": "pass", "fx": "fail", "us": "skip"})
        after = run({"nf": "error", "ns": "skip", "fx": "pass", "us": "pass", "ad": "pass"})
        delta = compare(before, after)
        self.assertEqual(delta.regressions(), 3)
        self.assertEqual(delta.improvements(), 3)
        for name in REGRESSIONS + IMPROVEMENTS:
            self.assertEqual(len(delta.categories[name]), 1, name)

    def test_removed_alone_is_a_regression(self):
        delta = compare(run({"a": "pass", "b": "pass"}), run({"a": "pass"}))
        self.assertEqual(delta.regressions(), 1)

    def test_newly_skipped_alone_is_a_regression(self):
        delta = compare(run({"a": "pass"}), run({"a": "skip"}))
        self.assertEqual(delta.regressions(), 1)

    def test_other_changes_not_counted(self):
        delta = compare(run({"a": "xpass", "b": "fail"}), run({"a": "pass", "b": "skip"}))
        self.assertEqual(ids(delta, "other_changes"), ["a", "b"])
        self.assertEqual(delta.regressions(), 0)
        self.assertEqual(delta.improvements(), 0)

    def test_unchanged_listed_nowhere(self):
        delta = compare(run({"a": "pass", "b": "skip"}), run({"a": "pass", "b": "skip"}))
        self.assertTrue(all(not items for items in delta.categories.values()))

    def test_only_newly_failing_carries_failure_text(self):
        before = run({"nf": "pass", "sf": ("fail", 1.0, "old msg"), "ad": "pass"})
        after = run({"nf": ("fail", None, "new msg"), "sf": ("fail", 9.0, "still msg"),
                     "x": ("error", None, "added msg"), "ad": "pass"})
        delta = compare(before, after)
        newly = delta.categories["newly_failing"][0]
        self.assertEqual(newly.message, "new msg")
        self.assertEqual(newly.traceback, ("tb 1", "tb 2"))
        for name in CATEGORIES:
            if name == "newly_failing":
                continue
            for change in delta.categories[name]:
                self.assertIsNone(change.message, name)
                self.assertEqual(change.traceback, (), name)
        self.assertEqual(ids(delta, "slower"), ["sf"])

    def test_formats_must_match(self):
        with self.assertRaises(ValueError):
            compare(run({}, fmt="junit"), run({}, fmt="unittest"))


class TestSlowRule(unittest.TestCase):
    def test_both_conditions_required(self):
        cases = [
            ((1.0, 2.0), "slower"),        # ratio 2.0 and +1.0 s
            ((1.0, 1.5), None),            # +0.5 s but ratio 1.5
            ((0.1, 0.3), None),            # ratio 3 but only +0.2 s
            ((10.0, 11.0), None),          # +1.0 s but ratio 1.1
            ((0.25, 0.75), "slower"),      # ratio 3, +0.5 s: both exactly met
            ((0.0, 0.5), "slower"),        # 0.0 is a known time
            ((0.0, 0.4), None),            # 0.0 known, but under the minimum
            ((2.0, 1.0), "faster"),
            ((0.3, 0.1), None),
            ((11.0, 10.0), None),
            ((0.5, 0.0), "faster"),
        ]
        for (before, after), expected in cases:
            with self.subTest(before=before, after=after):
                self.assertEqual(speed(before, after, 2.0, 0.5), expected)

    def test_unknown_time_never_slow_or_fast(self):
        for before, after in ((None, 100.0), (100.0, None), (None, None), (None, 0.0),
                              (0.0, None)):
            with self.subTest(before=before, after=after):
                self.assertIsNone(speed(before, after, 2.0, 0.5))

    def test_na_never_counted_as_zero_in_compare(self):
        # If n/a were read as 0, "a" would be slower and "b" faster.
        before = run({"a": ("pass", None), "b": ("pass", 5.0), "c": ("pass", 1.0)})
        after = run({"a": ("pass", 5.0), "b": ("pass", None), "c": ("pass", 3.0)})
        delta = compare(before, after)
        self.assertEqual(ids(delta, "slower"), ["c"])
        self.assertEqual(ids(delta, "faster"), [])

    def test_custom_ratio_and_minimum(self):
        before = run({"a": ("pass", 1.0), "b": ("pass", 1.0)})
        after = run({"a": ("pass", 1.3), "b": ("pass", 1.6)})
        self.assertEqual(ids(compare(before, after), "slower"), [])
        delta = compare(before, after, slow_ratio=1.5, slow_min=0.2)
        self.assertEqual(ids(delta, "slower"), ["b"])
        self.assertEqual((delta.slow_ratio, delta.slow_min), (1.5, 0.2))

    def test_slower_any_status_and_alongside_status_category(self):
        before = run({"a": ("pass", 1.0), "f": ("fail", 1.0)})
        after = run({"a": ("fail", 4.0), "f": ("fail", 4.0)})
        delta = compare(before, after)
        self.assertEqual(ids(delta, "slower"), ["a", "f"])
        self.assertEqual(ids(delta, "newly_failing"), ["a"])
        self.assertEqual(ids(delta, "still_failing"), ["f"])

    def test_added_and_removed_never_slow(self):
        delta = compare(run({"gone": ("pass", 0.1)}), run({"new": ("pass", 99.0)}))
        self.assertEqual(ids(delta, "slower"), [])
        self.assertEqual(ids(delta, "faster"), [])

    def test_slower_counts_only_with_fail_on_slow(self):
        delta = compare(run({"a": ("pass", 1.0)}), run({"a": ("pass", 3.0)}))
        self.assertEqual(ids(delta, "slower"), ["a"])
        self.assertEqual(delta.regressions(), 0)
        self.assertEqual(delta.regressions(fail_on_slow=True), 1)

    def test_faster_never_a_regression(self):
        delta = compare(run({"a": ("pass", 3.0)}), run({"a": ("pass", 1.0)}))
        self.assertEqual(ids(delta, "faster"), ["a"])
        self.assertEqual(delta.regressions(fail_on_slow=True), 0)


class TestTotalsAndWarnings(unittest.TestCase):
    def test_counts_per_status(self):
        before = run({"a": "pass", "b": "pass", "c": "fail", "d": "skip"})
        after = run({"a": "pass", "b": "error", "c": "xfail", "d": "xpass", "e": "pass"})
        delta = compare(before, after)
        self.assertEqual(list(delta.counts_before), list(STATUSES))
        self.assertEqual(delta.counts_before,
                         {"pass": 2, "fail": 1, "error": 0, "skip": 1, "xfail": 0, "xpass": 0})
        self.assertEqual(delta.counts_after,
                         {"pass": 2, "fail": 0, "error": 1, "skip": 0, "xfail": 1, "xpass": 1})
        self.assertEqual((delta.total_before, delta.total_after), (4, 5))

    def test_total_drop_warned_first(self):
        before = run({"a": ("pass", 1.0), "b": "pass", "c": "pass"}, duplicates=["a"])
        after = run({"a": ("pass", 1.0)})
        warnings = compare(before, after).warnings
        self.assertTrue(warnings)
        self.assertEqual(warnings[0], "total test count dropped from 3 to 1 (2 fewer)")

    def test_no_drop_warning_when_equal_or_more(self):
        for after in ({"a": "pass", "b": "pass"}, {"a": "pass", "c": "pass"},
                      {"a": "pass", "b": "pass", "c": "pass"}):
            with self.subTest(after=sorted(after)):
                warnings = compare(run({"a": "pass", "b": "pass"}), run(after)).warnings
                self.assertFalse(any("dropped" in w for w in warnings))

    def test_drop_to_zero(self):
        warnings = compare(run({"a": "pass"}), run({})).warnings
        self.assertEqual(warnings[0], "total test count dropped from 1 to 0 (1 fewer)")

    def test_duplicates_and_unresolved_warned(self):
        before = run({"a": ("pass", 1.0)}, duplicates=["a"])
        after = run({"a": ("pass", 1.0)}, unresolved=["x", "y"])
        delta = compare(before, after)
        self.assertEqual(delta.warnings, (
            "before: 1 duplicate test id; the worst status was kept",
            "after: 2 tests started without a status (truncated log?); left out"))
        self.assertEqual(delta.duplicates_before, ("a",))
        self.assertEqual(delta.unresolved_after, ("x", "y"))

    def test_no_times_warned_per_side(self):
        delta = compare(run({"a": "pass"}, fmt="unittest"),
                        run({"a": ("pass", 0.5)}, fmt="unittest"))
        self.assertEqual(delta.warnings,
                         ("before: no test times; slower/faster need both times",))

    def test_warnings_hold_no_test_ids(self):
        before = run({"secret.id": "pass"}, duplicates=["secret.id"])
        after = run({}, unresolved=["other.secret"])
        delta = compare(before, after)
        self.assertTrue(delta.warnings)
        self.assertFalse(any("secret" in w for w in delta.warnings))


if __name__ == "__main__":
    unittest.main()
