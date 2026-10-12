import tempfile
import unittest

from jsondiff.cli import _exit_code
from jsondiff.diff import UNKEYED_NOTE, diff_shapes
from jsondiff.shape import UsageError, build_shape
from tests import jsondiff_fixtures as fx


class DiffTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self._n = 0

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, obj, jsonl):
        self._n += 1
        if jsonl:
            return fx.write_jsonl(self.dir, f"f{self._n}.jsonl", obj)
        return fx.write_json(self.dir, f"f{self._n}.json", obj)

    def diff(self, old, new, *, jsonl=False, keys=None):
        old_shape = build_shape(self._write(old, jsonl), keys=keys)
        new_shape = build_shape(self._write(new, jsonl), keys=keys)
        result = diff_shapes(old_shape, new_shape, keys=keys)
        self.assertTrue(result)
        return result

    def changes(self, result, category=None):
        found = [c for c in result["changes"]
                 if category is None or c["category"] == category]
        return found

    def only(self, result, category):
        found = self.changes(result, category)
        self.assertTrue(found, f"no {category!r} change in {result['changes']}")
        return {c["path"]: c for c in found}


class TestCategories(DiffTestCase):
    def test_added(self):
        result = self.diff({"a": 1}, {"a": 1, "b": "x"})
        added = self.only(result, "added")
        self.assertEqual(list(added), ["b"])
        self.assertFalse(added["b"]["breaking"])
        self.assertEqual(added["b"]["detail"], "string")

    def test_added_in_some_array_elements_reports_presence(self):
        result = self.diff({"items": [{"a": 1}, {"a": 2}, {"a": 3}]},
                           {"items": [{"a": 1, "b": True}, {"a": 2}, {"a": 3}]})
        added = self.only(result, "added")
        self.assertEqual(added["items[].b"]["detail"],
                         "boolean, present in 1 of 3 (33%)")

    def test_added_in_some_jsonl_records_reports_presence(self):
        old = [{"a": i} for i in range(10)]
        new = [{"a": i, "b": "x"} if i < 5 else {"a": i} for i in range(10)]
        result = self.diff(old, new, jsonl=True)
        added = self.only(result, "added")
        self.assertEqual(added["b"]["detail"], "string, present in 5 of 10 (50%)")

    def test_removed(self):
        result = self.diff({"a": 1, "b": "x"}, {"a": 1})
        removed = self.only(result, "removed")
        self.assertEqual(list(removed), ["b"])
        self.assertTrue(removed["b"]["breaking"])

    def test_type_changed(self):
        result = self.diff({"a": 1}, {"a": "1"})
        changed = self.only(result, "type changed")
        self.assertEqual(changed["a"]["detail"], "integer \u2192 string")
        self.assertTrue(changed["a"]["breaking"])

    def test_integer_to_number_is_a_type_change(self):
        old = fx.write_text(self.dir, "o.json", '{"a": 1}')
        new = fx.write_text(self.dir, "n.json", '{"a": 1.0}')
        result = diff_shapes(build_shape(old), build_shape(new))
        self.assertTrue(result["changes"])
        self.assertEqual(self.only(result, "type changed")["a"]["detail"],
                         "integer \u2192 number")

    def test_null_is_not_a_type_change(self):
        result = self.diff([{"a": 1}], [{"a": 1}, {"a": None}], jsonl=True)
        self.assertTrue(result["changes"])
        self.assertEqual(self.changes(result, "type changed"), [])

    def test_became_nullable(self):
        result = self.diff([{"a": 1}, {"a": 2}], [{"a": 1}, {"a": None}], jsonl=True)
        nullable = self.only(result, "became nullable")
        self.assertEqual(list(nullable), ["a"])
        self.assertTrue(nullable["a"]["breaking"])

    def test_no_longer_nullable(self):
        result = self.diff([{"a": 1}, {"a": None}], [{"a": 1}, {"a": 2}], jsonl=True)
        change = self.only(result, "no longer nullable")
        self.assertFalse(change["a"]["breaking"])

    def test_became_optional(self):
        result = self.diff([{"a": 1, "b": 1}, {"a": 2, "b": 2}],
                           [{"a": 1, "b": 1}, {"a": 2}], jsonl=True)
        change = self.only(result, "became optional")
        self.assertEqual(list(change), ["b"])
        self.assertTrue(change["b"]["breaking"])
        self.assertIn("no longer present in all records", change["b"]["detail"])

    def test_no_longer_optional(self):
        result = self.diff([{"a": 1, "b": 1}, {"a": 2}],
                           [{"a": 1, "b": 1}, {"a": 2, "b": 2}], jsonl=True)
        change = self.only(result, "no longer optional")
        self.assertFalse(change["b"]["breaking"])

    def test_array_element_shape_changed(self):
        result = self.diff({"items": [{"sku": "A", "qty": 1}]},
                           {"items": [{"sku": 1, "qty": 1, "note": "x"}]})
        summary = self.only(result, "array element shape changed")
        self.assertEqual(list(summary), ["items"])
        self.assertIn("2 change(s) under items[]", summary["items"]["detail"])
        self.assertIn("potentially breaking", summary["items"]["detail"])
        self.assertIn("items[].sku", self.only(result, "type changed"))
        self.assertIn("items[].note", self.only(result, "added"))

    def test_nested_array_change_attributed_to_each_enclosing_array(self):
        result = self.diff({"o": [{"lines": [{"sku": "A"}]}]},
                           {"o": [{"lines": [{"sku": 1}]}]})
        summary = self.only(result, "array element shape changed")
        self.assertEqual(sorted(summary), ["o", "o[].lines"])

    def test_root_array_summary(self):
        result = self.diff([{"id": 1}], [{"id": "1"}])
        summary = self.only(result, "array element shape changed")
        self.assertEqual(list(summary), ["$"])

    def assert_no_empty_type_change(self, result):
        for change in self.changes(result, "type changed"):
            self.assertNotIn("empty", change["detail"])

    def test_always_empty_array_gaining_elements_is_added(self):
        result = self.diff({"items": []}, {"items": [{"sku": "A", "qty": 1}]})
        added = self.only(result, "added")
        self.assertEqual(sorted(added), ["items[]", "items[].qty", "items[].sku"])
        self.assertFalse(any(c["breaking"] for c in added.values()))
        self.assertEqual(self.changes(result, "type changed"), [])
        self.assertEqual(self.changes(result, "removed"), [])
        self.assert_no_empty_type_change(result)

    def test_array_becoming_always_empty_is_removed(self):
        result = self.diff({"items": [{"sku": "A"}, {"sku": "B"}]}, {"items": []})
        removed = self.only(result, "removed")
        self.assertEqual(sorted(removed), ["items[]", "items[].sku"])
        for change in removed.values():
            self.assertTrue(change["breaking"])
            self.assertEqual(change["detail"], "array always empty in new file")
        self.assertEqual(self.changes(result, "type changed"), [])
        self.assertEqual(self.changes(result, "no longer nullable"), [])
        self.assert_no_empty_type_change(result)

    def test_both_always_empty_is_unchanged(self):
        result = self.diff({"items": []}, {"items": []})
        self.assertEqual(result["changes"], [])
        self.assertGreater(result["unchanged"], 0)

    def test_brackets_inside_escaped_key_are_not_an_array(self):
        result = self.diff({"[]": 1}, {"[]": "x"})
        self.assertTrue(result["changes"])
        self.assertIn('["[]"]', self.only(result, "type changed"))
        self.assertEqual(self.changes(result, "array element shape changed"), [])

    def test_value_to_null_only_is_became_nullable_only(self):
        result = self.diff([{"a": "x"}, {"a": "y"}], [{"a": None}, {"a": None}],
                           jsonl=True)
        self.assertTrue(result["changes"])
        self.assertEqual([(c["path"], c["category"], c["breaking"])
                          for c in result["changes"]],
                         [("a", "became nullable", True)])

    def test_null_only_to_value_is_no_longer_nullable_only(self):
        result = self.diff([{"a": None}, {"a": None}], [{"a": "x"}, {"a": "y"}],
                           jsonl=True)
        self.assertTrue(result["changes"])
        self.assertEqual([(c["path"], c["category"], c["breaking"])
                          for c in result["changes"]],
                         [("a", "no longer nullable", False)])

    def test_null_only_to_value_or_null_is_type_change_only(self):
        result = self.diff([{"a": None}, {"a": None}], [{"a": "x"}, {"a": None}],
                           jsonl=True)
        self.assertTrue(result["changes"])
        self.assertEqual([(c["path"], c["category"], c["detail"], c["breaking"])
                          for c in result["changes"]],
                         [("a", "type changed", "null only → string", False)])
        self.assertEqual(_exit_code(result), 1)

    def test_value_or_null_to_null_only_is_type_change_only(self):
        result = self.diff([{"a": "x"}, {"a": None}], [{"a": None}, {"a": None}],
                           jsonl=True)
        self.assertTrue(result["changes"])
        self.assertEqual([(c["path"], c["category"], c["detail"], c["breaking"])
                          for c in result["changes"]],
                         [("a", "type changed", "string → null only", False)])
        self.assertEqual(_exit_code(result), 1)

    def test_breaking_flags_exactly_as_specified(self):
        result = self.diff(
            [{"keep": 1, "gone": 1, "retype": 1, "nul": 1, "unnul": None,
              "opt": 1, "unopt": 1},
             {"keep": 1, "gone": 1, "retype": 1, "nul": 1, "unnul": 1, "opt": 1}],
            [{"keep": 1, "new": 1, "retype": "1", "nul": None, "unnul": 1,
              "opt": 1, "unopt": 1},
             {"keep": 1, "new": 1, "retype": "1", "nul": 1, "unnul": 1, "unopt": 1}],
            jsonl=True)
        flags = {c["category"]: c["breaking"] for c in result["changes"]}
        self.assertEqual(flags, {
            "added": False, "removed": True, "type changed": True,
            "became nullable": True, "no longer nullable": False,
            "became optional": True, "no longer optional": False,
        })

    def test_unchanged_count(self):
        result = self.diff({"a": 1, "b": "x", "c": {"d": True}},
                           {"a": 1, "b": "x", "c": {"d": True}, "e": 2})
        self.assertEqual(len(result["changes"]), 1)
        # $, a, b, c, c.d
        self.assertEqual(result["unchanged"], 5)
        self.assertEqual(result["unchanged_paths"], ["$", "a", "b", "c", "c.d"])

    def test_identical_has_no_changes(self):
        result = self.diff({"a": [1, 2]}, {"a": [3]})
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["unchanged"], 3)


class TestKeyedArrays(DiffTestCase):
    # Old and new have the SAME merged element shape (a: integer 50%, b: string
    # 50%); only per-key matching can see that elements 1 and 2 changed shape.
    OLD = [{"id": 1, "a": 1}, {"id": 2, "b": "x"}, {"id": 3, "a": 5},
           {"id": 4, "b": "y"}]
    NEW = [{"id": 1, "b": "x"}, {"id": 2, "a": 1}, {"id": 4, "b": "z"},
           {"id": 5, "a": 6}]

    def assert_matches(self, match):
        self.assertEqual(match["only_in_old"], 1)   # id 3 removed
        self.assertEqual(match["only_in_new"], 1)   # id 5 added
        self.assertEqual(match["changed"], 2)       # ids 1 and 2
        self.assertEqual(match["unchanged"], 1)     # id 4 (value change only)

    def test_root_field_form(self):
        result = self.diff(self.OLD, self.NEW, keys=["id"])
        self.assertTrue(result["key_matches"])
        match = result["key_matches"][0]
        self.assertEqual((match["path"], match["field"]), ("$", "id"))
        self.assert_matches(match)
        # Merged shape alone sees nothing: keyed matching is what found these.
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["notes"], [])

    def test_path_field_form(self):
        result = self.diff({"orders": self.OLD}, {"orders": self.NEW},
                           keys=["orders=id"])
        self.assertTrue(result["key_matches"])
        match = result["key_matches"][0]
        self.assertEqual(match["path"], "orders")
        self.assert_matches(match)

    def test_nested_path_field_form(self):
        old = {"orders": [{"id": 1, "lines": [{"sku": "A", "q": 1},
                                               {"sku": "B", "q": 2}]}]}
        new = {"orders": [{"id": 1, "lines": [{"sku": "A", "q": "1"},
                                               {"sku": "C", "q": 3}]}]}
        result = self.diff(old, new, keys=["orders=id", "orders[].lines=sku"])
        matches = {m["path"]: m for m in result["key_matches"]}
        self.assertTrue(matches)
        lines = matches["orders[].lines"]
        self.assertEqual((lines["only_in_old"], lines["only_in_new"],
                          lines["changed"]), (1, 1, 1))
        self.assertEqual(matches["orders"]["changed"], 1)

    def test_missing_key_field_warns(self):
        result = self.diff([{"id": 1}, {"other": 2}], [{"id": 1}], keys=["id"])
        self.assertTrue(result["warnings"])
        self.assertEqual(result["key_matches"][0]["missing_key_old"], 1)
        self.assertTrue(any("without the key field" in w for w in result["warnings"]))

    def test_key_values_stringified_with_json_dumps(self):
        result = self.diff([{"id": 1}], [{"id": "1"}], keys=["id"])
        match = result["key_matches"][0]
        self.assertTrue(match)
        self.assertEqual((match["only_in_old"], match["only_in_new"]), (1, 1))

    def test_unkeyed_array_note(self):
        result = self.diff({"items": [{"a": 1}]}, {"items": [{"a": 1}]})
        self.assertTrue(result["notes"])
        self.assertIn(UNKEYED_NOTE, result["notes"])
        self.assertEqual(result["unkeyed_arrays"], ["items"])

    def test_key_with_jsonl_is_usage_error(self):
        old = fx.write_jsonl(self.dir, "o.jsonl", [{"id": 1}])
        new = fx.write_jsonl(self.dir, "n.jsonl", [{"id": 1}])
        with self.assertRaises(UsageError):
            build_shape(old, keys=["id"])
        old_shape, new_shape = build_shape(old), build_shape(new)
        self.assertTrue(old_shape["paths"])
        with self.assertRaises(UsageError):
            diff_shapes(old_shape, new_shape, keys=["id"])
