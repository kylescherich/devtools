"""Synthetic fixture writers for jsondiff tests. Every helper writes into the
directory it is given (a temp dir) and returns the path of the file."""
import json
from pathlib import Path

HOSTILE_KEYS = ["a.b", 'say "hi"', "café", "<script>",
                '"><img onerror=x>', "rtl‮override", "esc\x1bkey"]


def write_text(directory, name, text):
    path = Path(directory) / name
    path.write_bytes(text.encode("utf-8"))
    return path


def write_json(directory, name, obj):
    return write_text(directory, name, json.dumps(obj))


def write_jsonl(directory, name, records):
    return write_text(directory, name,
                      "".join(json.dumps(r) + "\n" for r in records))


def nested(directory):
    return write_json(directory, "nested.json", {
        "user": {"id": 1, "address": {"city": "Springfield", "zip": "00000"}},
        "active": True,
    })


def orders(directory):
    return write_json(directory, "orders.json", {"items": [
        {"sku": "A1", "qty": 1, "x": 1},
        {"sku": "B2", "qty": 2, "x": 2},
        {"sku": "C3", "qty": 3, "x": 3},
        {"sku": "D4", "qty": 4},
    ]})


def mixed_array(directory):
    return write_json(directory, "mixed.json",
                      {"vals": [1, "two", 3.5, None, True, {"k": 1}, [2]],
                       "empty": []})


def null_vs_missing(directory):
    return write_jsonl(directory, "nulls.jsonl", [
        {"a": 1, "b": None},
        {"a": 2},
        {"a": 3, "b": 4},
        {"a": None, "b": 5},
    ])


def int_vs_float(directory):
    # Written as text so 1.0 stays a float on disk.
    return write_text(directory, "numbers.json",
                      '{"i": 1, "f": 1.0, "both": [1, 1.0], "flag": true}')


def hostile_keys(directory):
    return write_json(directory, "hostile.json",
                      {key: {key: 1} for key in HOSTILE_KEYS})


def duplicate_keys(directory):
    return write_text(directory, "dups.json",
                      '{"id": 1, "id": "x", "user": {"n": 1, "n": 2}, '
                      '"rows": [{"k": 1, "k": 2}, {"k": 3, "k": 4}, {"k": 5, "k": 6}, '
                      '{"k": 7, "k": 8}]}')


def nonstandard_numbers(directory):
    return write_text(directory, "nan.json",
                      '{"a": NaN, "b": [Infinity, -Infinity], "c": 1.5}')


def deep(directory, depth):
    obj = {"leaf": 1}
    for _ in range(depth):
        obj = {"d": obj}
    return write_json(directory, "deep.json", obj)


def invalid_jsonl(directory):
    lines = ['{"a": 1}', "not json", '{"a": 2}', "", "{broken", '{"a": 3}',
             "[1,", '{"a": "unterminated', '{"a": 4}']
    return write_text(directory, "bad.jsonl", "\n".join(lines) + "\n")


def many_records(directory, count):
    return write_jsonl(directory, "many.jsonl", [{"n": i} for i in range(count)])


def secret_values(directory):
    return write_json(directory, "secret.json",
                      {"token": "SECRET-TOKEN-VALUE-123", "pin": 987654,
                       "items": [{"password": "hunter2-SECRET"}]})


def keyed_orders(directory, name, orders_list):
    """A document whose `orders` array is keyed by `id`."""
    return write_json(directory, name, {"orders": orders_list})
