import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jsondiff.shape import InvalidInput, build_shape
from tests import jsondiff_fixtures as fx


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ShapeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def shape(self, path, **kwargs):
        model = build_shape(path, **kwargs)
        self.assertTrue(model)
        self.assertTrue(model["paths"])
        return model


class TestShapeStructure(ShapeTestCase):
    def test_nested_objects(self):
        paths = self.shape(fx.nested(self.dir))["paths"]
        self.assertEqual(paths["$"]["types"], ["object"])
        self.assertEqual(paths["user.address.city"]["types"], ["string"])
        self.assertEqual(paths["user.id"]["types"], ["integer"])
        self.assertEqual(paths["active"]["types"], ["boolean"])
        self.assertEqual(paths["user.address"]["presence"], 1.0)
        self.assertEqual(list(paths), sorted(paths))

    def test_arrays_of_objects_presence_counts_elements(self):
        model = self.shape(fx.orders(self.dir))
        paths = model["paths"]
        self.assertEqual(paths["items"]["types"], ["array"])
        self.assertEqual(paths["items[]"]["types"], ["object"])
        self.assertEqual(paths["items[]"]["elements"], 4)
        x = paths["items[].x"]
        self.assertEqual((x["present"], x["parents"]), (3, 4))
        self.assertEqual(x["presence"], 0.75)
        self.assertEqual(paths["items[].sku"]["presence"], 1.0)

    def test_root_array_path(self):
        path = fx.write_json(self.dir, "root.json", [{"id": 1}, {"id": 2}])
        paths = self.shape(path)["paths"]
        self.assertIn("$[]", paths)
        self.assertEqual(paths["$[].id"]["types"], ["integer"])

    def test_mixed_type_array_and_empty_array(self):
        paths = self.shape(fx.mixed_array(self.dir))["paths"]
        self.assertEqual(paths["vals[]"]["types"],
                         ["array", "boolean", "integer", "null", "number",
                          "object", "string"])
        self.assertTrue(paths["vals[]"]["nullable"])
        self.assertIn("vals[].k", paths)
        self.assertIn("vals[][]", paths)
        self.assertEqual(paths["empty[]"]["types"], [])
        self.assertTrue(paths["empty[]"]["empty_array"])
        self.assertFalse(paths["vals[]"]["empty_array"])

    def test_null_vs_missing(self):
        paths = self.shape(fx.null_vs_missing(self.dir))["paths"]
        a, b = paths["a"], paths["b"]
        # a: present everywhere, null once.  b: missing once, null once.
        self.assertEqual((a["present"], a["parents"], a["nulls"]), (4, 4, 1))
        self.assertTrue(a["nullable"])
        self.assertEqual(a["presence"], 1.0)
        self.assertEqual((b["present"], b["parents"], b["nulls"]), (3, 4, 1))
        self.assertEqual(b["presence"], 0.75)
        self.assertTrue(b["nullable"])
        self.assertEqual(a["types"], ["integer", "null"])

    def test_integer_and_number_are_different_types(self):
        paths = self.shape(fx.int_vs_float(self.dir))["paths"]
        self.assertEqual(paths["i"]["types"], ["integer"])
        self.assertEqual(paths["f"]["types"], ["number"])
        self.assertNotEqual(paths["i"]["types"], paths["f"]["types"])
        self.assertEqual(paths["both[]"]["types"], ["integer", "number"])

    def test_bool_is_boolean_not_integer(self):
        paths = self.shape(fx.int_vs_float(self.dir))["paths"]
        self.assertEqual(paths["flag"]["types"], ["boolean"])
        path = fx.write_json(self.dir, "b.json", {"v": [True, False, 0]})
        paths = self.shape(path)["paths"]
        self.assertEqual(paths["v[]"]["types"], ["boolean", "integer"])


class TestKeyEscaping(ShapeTestCase):
    def test_hostile_keys_escaped_in_paths_raw_in_model(self):
        paths = self.shape(fx.hostile_keys(self.dir))["paths"]
        expected = {
            "a.b": '["a.b"]',
            'say "hi"': '["say \\"hi\\""]',
            "caf\u00e9": '["caf\\u00e9"]',
            "<script>": '["<script>"]',
            '"><img onerror=x>': '["\\"><img onerror=x>"]',
            "rtl\u202eoverride": '["rtl\\u202eoverride"]',
            "esc\x1bkey": '["esc\\u001bkey"]',
        }
        for raw, top in expected.items():
            self.assertIn(top, paths)
            self.assertEqual(paths[top]["key"], raw)
            nested = top + top
            self.assertIn(nested, paths)
            self.assertEqual(paths[nested]["key"], raw)
            self.assertEqual(paths[nested]["parent"], top)
        for path in paths:
            self.assertNotIn("\u202e", path)
            self.assertNotIn("\x1b", path)

    def test_dotted_key_under_identifier(self):
        path = fx.write_json(self.dir, "u.json", {"user": {"a.b": 1, "ok": 2}})
        paths = self.shape(path)["paths"]
        self.assertIn('user["a.b"]', paths)
        self.assertIn("user.ok", paths)
        self.assertNotIn("user.a.b", paths)


class TestWarningsAndGuards(ShapeTestCase):
    def test_duplicate_keys_warning(self):
        model = self.shape(fx.duplicate_keys(self.dir))
        self.assertTrue(model["warnings"])
        dups = model["warning_counts"]["duplicate_key"]
        self.assertEqual(dups["count"], 6)
        self.assertEqual(dups["first"], ["id", "user.n", "rows[].k"])
        self.assertIn("duplicate key id (last value kept, as Python's json does)",
                      model["warnings"])
        self.assertEqual(model["paths"]["id"]["types"], ["string"])

    def test_nonstandard_number_constants(self):
        model = self.shape(fx.nonstandard_numbers(self.dir))
        self.assertTrue(model["warnings"])
        self.assertEqual(model["paths"]["a"]["types"], ["number"])
        self.assertEqual(model["paths"]["b[]"]["types"], ["number"])
        self.assertEqual(model["warning_counts"]["nonstandard_number"]["count"], 3)
        self.assertIn("non-standard number constant at a", model["warnings"])

    def test_depth_limit_entry(self):
        model = self.shape(fx.deep(self.dir, 10), max_depth=3)
        paths = model["paths"]
        self.assertIn("d.d.d\u2026(depth limit)", paths)
        self.assertIn("d.d.d", paths)
        self.assertNotIn("d.d.d.d", paths)
        self.assertEqual([p for p in paths if "depth limit" in p],
                         ["d.d.d\u2026(depth limit)"])
        self.assertTrue(model["warnings"])
        self.assertIn("depth limit", " ".join(model["warnings"]))

    def test_parser_recursion_error_is_invalid_input(self):
        path = fx.write_text(self.dir, "abyss.json", "[" * 200_000 + "]" * 200_000)
        with self.assertRaises(InvalidInput):
            build_shape(path)

    def test_invalid_json_and_too_large(self):
        bad = fx.write_text(self.dir, "bad.json", "{oops")
        with self.assertRaises(InvalidInput):
            build_shape(bad)
        big = fx.nested(self.dir)
        with self.assertRaises(InvalidInput) as ctx:
            build_shape(big, max_bytes=10)
        self.assertIn("too large", str(ctx.exception))


class TestJsonl(ShapeTestCase):
    def test_jsonl_merge_presence_and_nullable(self):
        model = self.shape(fx.null_vs_missing(self.dir))
        self.assertEqual(model["kind"], "jsonl")
        self.assertEqual(model["records"], 4)
        self.assertEqual(model["paths"]["$"]["parents"], 4)
        self.assertEqual(model["paths"]["b"]["presence"], 0.75)
        self.assertEqual(model["paths"]["a"]["nulls"], 1)
        self.assertFalse(model["sampled"])

    def test_jsonl_flag_overrides_extension(self):
        path = fx.write_text(self.dir, "lines.txt", '{"a": 1}\n{"a": 2}\n')
        model = self.shape(path, jsonl=True)
        self.assertEqual(model["records"], 2)

    def test_invalid_jsonl_lines(self):
        model = self.shape(fx.invalid_jsonl(self.dir))
        self.assertEqual(model["records"], 4)
        self.assertEqual(model["invalid_lines"]["count"], 4)
        self.assertEqual(model["invalid_lines"]["first"], [2, 5, 7])

    def test_max_records_sampling(self):
        model = self.shape(fx.many_records(self.dir, 50), max_records=10)
        self.assertEqual(model["records"], 10)
        self.assertTrue(model["sampled"])
        exact = self.shape(fx.many_records(self.dir, 10), max_records=10)
        self.assertFalse(exact["sampled"])

    def test_max_bytes_samples_jsonl(self):
        path = fx.many_records(self.dir, 100)
        model = self.shape(path, max_bytes=100)
        self.assertTrue(model["sampled"])
        self.assertLess(model["records"], 100)
        self.assertEqual(model["invalid_lines"]["count"], 0)


class TestPrivacyAndReadOnly(ShapeTestCase):
    def test_values_not_stored_by_default(self):
        model = self.shape(fx.secret_values(self.dir))
        dumped = json.dumps(model)
        self.assertTrue(dumped)
        for secret in ("SECRET-TOKEN-VALUE-123", "987654", "hunter2"):
            self.assertNotIn(secret, dumped)
        for entry in model["paths"].values():
            self.assertNotIn("values", entry)

    def test_values_when_requested(self):
        model = self.shape(fx.many_records(self.dir, 10), values=True)
        self.assertEqual(model["paths"]["n"]["values"], ["0", "1", "2"])
        long = fx.write_json(self.dir, "long.json", {"s": "x" * 500})
        values = self.shape(long, values=True)["paths"]["s"]["values"]
        self.assertTrue(values)
        self.assertEqual(len(values[0]), 80)

    def test_inputs_unchanged(self):
        writers = [fx.nested, fx.orders, fx.mixed_array, fx.null_vs_missing,
                   fx.int_vs_float, fx.hostile_keys, fx.duplicate_keys,
                   fx.nonstandard_numbers, fx.invalid_jsonl, fx.secret_values]
        paths = [w(self.dir) for w in writers]
        before = {p: sha256(p) for p in paths}
        self.assertTrue(before)
        for p in paths:
            self.shape(p)
            self.shape(p, values=True)
        self.assertEqual(before, {p: sha256(p) for p in paths})
