"""Differ: compare two shapes path by path.

Changes flagged breaking are candidates for review ("potentially breaking —
review"), not a verdict that something will break.
"""
from jsondiff.shape import DEPTH_MARK, UsageError, parse_keys

ADDED = "added"
REMOVED = "removed"
TYPE_CHANGED = "type changed"
BECAME_NULLABLE = "became nullable"
NO_LONGER_NULLABLE = "no longer nullable"
BECAME_OPTIONAL = "became optional"
NO_LONGER_OPTIONAL = "no longer optional"
ARRAY_ELEMENTS_CHANGED = "array element shape changed"

CATEGORIES = (ADDED, REMOVED, TYPE_CHANGED, BECAME_NULLABLE, NO_LONGER_NULLABLE,
              BECAME_OPTIONAL, NO_LONGER_OPTIONAL, ARRAY_ELEMENTS_CHANGED)
BREAKING = frozenset({REMOVED, TYPE_CHANGED, BECAME_OPTIONAL, BECAME_NULLABLE})
BREAKING_LABEL = "potentially breaking — review"
UNKEYED_NOTE = "array elements compared by merged shape, not position"
ALWAYS_EMPTY_NEW = "array always empty in new file"


def _change(path, category, detail):
    return {"path": path, "category": category, "detail": detail,
            "breaking": category in BREAKING}


def _type_list(entry):
    types = [t for t in entry["types"] if t != "null"]
    if types:
        return ", ".join(types)
    if entry.get("empty_array"):
        return "empty array"
    return "null only"


def _compare(path, old, new):
    changes = []
    old_types = [t for t in old["types"] if t != "null"]
    new_types = [t for t in new["types"] if t != "null"]
    if old_types != new_types:
        changes.append(_change(path, TYPE_CHANGED,
                               f"{_type_list(old)} → {_type_list(new)}"))
    if not old["nullable"] and new["nullable"]:
        changes.append(_change(path, BECAME_NULLABLE, "null values now seen"))
    elif old["nullable"] and not new["nullable"]:
        changes.append(_change(path, NO_LONGER_NULLABLE, "no null values seen"))
    if old["presence"] == 1.0 and new["presence"] < 1.0:
        changes.append(_change(path, BECAME_OPTIONAL,
                               f"no longer present in all records "
                               f"({new['present']} of {new['parents']})"))
    elif old["presence"] < 1.0 and new["presence"] == 1.0:
        changes.append(_change(path, NO_LONGER_OPTIONAL, "now present in all records"))
    return changes


def _enclosing_arrays(path):
    """Array paths whose elements contain `path` (`a[].b[].c` -> a, a[].b).

    `[]` inside an escaped key segment such as `["[]"]` is not an array.
    """
    arrays = []
    i = 0
    while i < len(path):
        if path.startswith('["', i):
            i += 2
            while i < len(path) and not path.startswith('"]', i):
                i += 2 if path[i] == "\\" else 1
            i += 2
        elif path.startswith("[]", i):
            arrays.append(path[:i])
            i += 2
        else:
            i += 1
    return arrays


def _always_empty_in(paths, path):
    """True when an array enclosing `path` is always empty in `paths`."""
    return any(paths.get(array + "[]", {}).get("empty_array")
               for array in _enclosing_arrays(path))


def _array_summaries(changes):
    under = {}
    for change in changes:
        for array in _enclosing_arrays(change["path"]):
            under.setdefault(array, []).append(change)
    summaries = []
    for array, items in under.items():
        risky = sum(1 for c in items if c["breaking"])
        detail = f"{len(items)} change(s) under {array}[]"
        if risky:
            detail += f" ({risky} {BREAKING_LABEL})"
        summaries.append(_change(array, ARRAY_ELEMENTS_CHANGED, detail))
    return summaries


def _array_paths(shape):
    return {p for p, e in shape["paths"].items() if "array" in e["types"]}


def _shape_warnings(label, shape):
    warnings = [f"{label}: {w}" for w in shape.get("warnings", [])]
    if shape.get("sampled"):
        warnings.append(f"{label}: sampled; only the first {shape['records']} "
                        f"record(s) were read")
    invalid = shape.get("invalid_lines") or {}
    if invalid.get("count"):
        lines = ", ".join(str(n) for n in invalid["first"])
        warnings.append(f"{label}: {invalid['count']} invalid line(s) skipped "
                        f"(first: {lines})")
    return warnings


def _match_keyed(array_path, old_keyed, new_keyed):
    old_el = old_keyed["elements"] if old_keyed else {}
    new_el = new_keyed["elements"] if new_keyed else {}
    both = old_el.keys() & new_el.keys()
    changed = sum(1 for k in both if old_el[k] != new_el[k])
    return {
        "path": array_path,
        "field": (old_keyed or new_keyed)["field"],
        "only_in_old": len(old_el.keys() - new_el.keys()),
        "only_in_new": len(new_el.keys() - old_el.keys()),
        "changed": changed,
        "unchanged": len(both) - changed,
        "missing_key_old": old_keyed["missing_key"] if old_keyed else 0,
        "missing_key_new": new_keyed["missing_key"] if new_keyed else 0,
        "duplicate_values_old": old_keyed["duplicate_values"] if old_keyed else 0,
        "duplicate_values_new": new_keyed["duplicate_values"] if new_keyed else 0,
    }


def diff_shapes(old, new, keys=None, keyed_inputs=None):
    """Compare two shape models; see the module docstring for wording."""
    keys = parse_keys(keys)
    if keys and "jsonl" in (old["kind"], new["kind"]):
        raise UsageError("--key applies to single JSON documents, not JSONL")
    if keys:
        if keyed_inputs is None:
            keyed_inputs = (old.get("keyed"), new.get("keyed"))
        if keyed_inputs[0] is None or keyed_inputs[1] is None:
            raise UsageError("keyed matching needs shapes built with keys=")

    old_paths, new_paths = old["paths"], new["paths"]
    changes = []
    for path in sorted(old_paths.keys() | new_paths.keys()):
        if path not in old_paths:
            changes.append(_change(path, ADDED, _type_list(new_paths[path])))
        elif path not in new_paths:
            detail = (ALWAYS_EMPTY_NEW if _always_empty_in(new_paths, path)
                      else _type_list(old_paths[path]))
            changes.append(_change(path, REMOVED, detail))
        elif path.endswith(DEPTH_MARK):
            continue
        elif old_paths[path].get("empty_array") != new_paths[path].get("empty_array"):
            # Empty is not a type: elements appearing are added, vanishing removed.
            if old_paths[path].get("empty_array"):
                changes.append(_change(path, ADDED, _type_list(new_paths[path])))
            else:
                changes.append(_change(path, REMOVED, ALWAYS_EMPTY_NEW))
        else:
            changes.extend(_compare(path, old_paths[path], new_paths[path]))
    changes.extend(_array_summaries(changes))
    order = {c: i for i, c in enumerate(CATEGORIES)}
    changes.sort(key=lambda c: (c["path"], order[c["category"]]))

    changed_paths = {c["path"] for c in changes}
    unchanged = sorted(p for p in old_paths.keys() & new_paths.keys()
                       if p not in changed_paths)

    warnings = _shape_warnings("old", old) + _shape_warnings("new", new)
    arrays = _array_paths(old) | _array_paths(new)
    key_matches = []
    for array_path in sorted(keys):
        if array_path not in arrays:
            warnings.append(f"--key {array_path}: no array at this path")
            continue
        key_matches.append(_match_keyed(array_path,
                                        keyed_inputs[0].get(array_path),
                                        keyed_inputs[1].get(array_path)))
    unkeyed = sorted(arrays - set(keys))
    notes = [UNKEYED_NOTE] if unkeyed else []

    return {
        "changes": changes,
        "unchanged": len(unchanged),
        "unchanged_paths": unchanged,
        "notes": notes,
        "unkeyed_arrays": unkeyed,
        "warnings": warnings,
        "key_matches": key_matches,
    }
