"""Shape builder: infer per-path types, presence and nullability from JSON / JSONL.

Inputs are opened read-only ("rb") and parsed with the standard json module.
Nothing from the input is executed. Example values are kept only when
values=True; otherwise the model holds paths, types and counts only.
"""
import hashlib
import json
import os
import re

DEFAULT_MAX_BYTES = 200_000_000
DEFAULT_MAX_RECORDS = 1_000_000
DEFAULT_MAX_DEPTH = 64

ROOT = "$"
DEPTH_MARK = "\u2026(depth limit)"
JSONL_SUFFIXES = (".jsonl", ".ndjson")
MAX_VALUES = 3
MAX_VALUE_CHARS = 80
FIRST_N = 3

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*", re.ASCII)


class InvalidInput(Exception):
    """The input cannot be parsed or exceeds a hard guard."""


class UsageError(ValueError):
    """The request is inconsistent (e.g. --key with JSONL)."""


class _Obj(dict):
    """A parsed JSON object that remembers which keys appeared more than once."""
    __slots__ = ("dups",)


class _NonStd(float):
    """NaN / Infinity / -Infinity, which Python's json accepts but JSON does not."""
    __slots__ = ()


def _pairs_hook(pairs):
    obj = _Obj()
    obj.dups = []
    for key, value in pairs:
        if key in obj and key not in obj.dups:
            obj.dups.append(key)
        obj[key] = value
    return obj


def _parse(data):
    return json.loads(data, object_pairs_hook=_pairs_hook,
                      parse_constant=lambda c: _NonStd(float(c)))


def key_segment(key):
    """Path segment for an object key: bare identifier, else ["<escaped>"]."""
    if _IDENT.fullmatch(key):
        return key
    return '["' + json.dumps(key)[1:-1] + '"]'


def child_path(parent, key):
    seg = key_segment(key)
    if parent == ROOT:
        return seg
    if seg.startswith("["):
        return parent + seg
    return parent + "." + seg


def items_path(parent):
    return parent + "[]"


def json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _example(value):
    text = json.dumps(value)
    if len(text) > MAX_VALUE_CHARS:
        text = text[:MAX_VALUE_CHARS - 1] + "\u2026"
    return text


def _short_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def parse_keys(keys):
    """Turn ["FIELD", "path=FIELD", ...] (or a dict) into {array_path: field}."""
    if not keys:
        return {}
    if isinstance(keys, dict):
        return dict(keys)
    parsed = {}
    for spec in keys:
        if "=" in spec:
            path, _, field = spec.rpartition("=")
            path = path.strip() or ROOT
        else:
            path, field = ROOT, spec
        if not field:
            raise UsageError(f"--key {spec!r}: empty field name")
        if path in parsed:
            raise UsageError(f"--key given twice for {path}")
        parsed[path] = field
    return parsed


class _Warnings:
    MESSAGES = {
        "duplicate_key": "duplicate key {} (last value kept, as Python's json does)",
        "nonstandard_number": "non-standard number constant at {}",
        "depth_limit": "depth limit reached at {}; not descended further",
        "key_missing": "array element without the key field at {}",
        "key_duplicate": "duplicate key value in keyed array {} (first element kept)",
    }

    def __init__(self):
        self.counts = {}

    def add(self, kind, path):
        entry = self.counts.setdefault(kind, {"count": 0, "first": []})
        entry["count"] += 1
        if len(entry["first"]) < FIRST_N:
            entry["first"].append(path)

    def render(self):
        lines = []
        for kind in self.MESSAGES:
            entry = self.counts.get(kind)
            if not entry:
                continue
            for path in entry["first"]:
                lines.append(self.MESSAGES[kind].format(path))
            extra = entry["count"] - len(entry["first"])
            if extra > 0:
                lines.append(f"... and {extra} more ({kind.replace('_', ' ')})")
        return lines


class _Builder:
    def __init__(self, max_depth, values, keys):
        self.max_depth = max_depth
        self.values = values
        self.keys = keys
        self.entries = {}
        self.objects_seen = {}
        self.warnings = _Warnings()
        self.keyed = {p: {"field": f, "elements": {}, "missing_key": 0,
                          "duplicate_values": 0}
                      for p, f in keys.items()}

    def _entry(self, path, parent, key):
        entry = self.entries.get(path)
        if entry is None:
            entry = {"path": path, "parent": parent, "key": key, "types": set(),
                     "present": 0, "nulls": 0}
            if self.values:
                entry["values"] = []
            self.entries[path] = entry
        return entry

    def add_record(self, record):
        root = self._entry(ROOT, None, None)
        root["present"] += 1
        self._walk(record, ROOT)

    def _walk(self, value, path):
        # Iterative walk in document order: (value, path, depth, keyed context).
        stack = [(value, path, 0, ())]
        while stack:
            value, path, depth, ctx = stack.pop()
            entry = self.entries[path]
            entry["types"].add(json_type(value))
            if value is None:
                entry["nulls"] += 1
            elif isinstance(value, _NonStd):
                self.warnings.add("nonstandard_number", path)
            if self.values and not isinstance(value, (dict, list)) and value is not None:
                text = _example(value)
                if text not in entry["values"] and len(entry["values"]) < MAX_VALUES:
                    entry["values"].append(text)

            if isinstance(value, dict):
                self.objects_seen[path] = self.objects_seen.get(path, 0) + 1
                for key in getattr(value, "dups", ()):
                    self.warnings.add("duplicate_key", child_path(path, key))
                if value and self._at_limit(path, depth):
                    continue
                pushed = []
                for key, child in value.items():
                    cpath = child_path(path, key)
                    self._entry(cpath, path, key)["present"] += 1
                    pushed.append((child, cpath, depth + 1, ctx))
                stack.extend(reversed(pushed))

            elif isinstance(value, list):
                ipath = items_path(path)
                items = self._entry(ipath, path, None)
                items["present"] += 1
                items.setdefault("arrays_empty", 0)
                items.setdefault("elements", 0)
                if not value:
                    items["arrays_empty"] += 1
                    continue
                if self._at_limit(path, depth):
                    continue
                items["elements"] += len(value)
                keyed = self.keyed.get(path)
                pushed = []
                for element in value:
                    child_ctx = ctx
                    if keyed is not None:
                        child_ctx = self._register_keyed(keyed, path, element, ctx, depth + 1)
                    pushed.append((element, ipath, depth + 1, child_ctx))
                stack.extend(reversed(pushed))

    def _at_limit(self, path, depth):
        if depth < self.max_depth:
            return False
        mark = path + DEPTH_MARK
        if mark not in self.entries:
            self._entry(mark, path, None)["types"].add("depth limit")
        self.entries[mark]["present"] += 1
        self.warnings.add("depth_limit", path)
        return True

    def _register_keyed(self, keyed, path, element, ctx, depth):
        field = keyed["field"]
        if not isinstance(element, dict) or field not in element:
            keyed["missing_key"] += 1
            self.warnings.add("key_missing", path)
            return ctx + (None,)
        ident = ctx + (json.dumps(element[field], sort_keys=True),)
        digest = _short_hash(json.dumps(ident))
        if digest in keyed["elements"]:
            keyed["duplicate_values"] += 1
            self.warnings.add("key_duplicate", path)
        else:
            keyed["elements"][digest] = self._signature(element, depth)
        return ident

    def _signature(self, element, depth):
        """Hash of the set of (relative path, type) pairs inside one element."""
        pairs = set()
        stack = [(element, ROOT, depth)]
        while stack:
            value, rel, d = stack.pop()
            pairs.add((rel, json_type(value)))
            if d >= self.max_depth:
                continue
            if isinstance(value, dict):
                for key, child in value.items():
                    stack.append((child, child_path(rel, key), d + 1))
            elif isinstance(value, list):
                for child in value:
                    stack.append((child, items_path(rel), d + 1))
        return _short_hash(json.dumps(sorted(pairs)))

    def finish(self):
        paths = {}
        for path in sorted(self.entries):
            entry = self.entries[path]
            parent = entry["parent"]
            if parent is None or path.endswith(DEPTH_MARK) or entry["key"] is None:
                # Root, [] element entries and depth-limit marks: present wherever seen.
                parents = entry["present"]
            else:
                parents = self.objects_seen.get(parent, 0)
            out = {
                "path": path,
                "parent": parent,
                "key": entry["key"],
                "types": sorted(entry["types"]),
                "parents": parents,
                "present": entry["present"],
                "presence": entry["present"] / parents if parents else 0.0,
                "nulls": entry["nulls"],
                "nullable": entry["nulls"] > 0,
            }
            if "elements" in entry:
                out["elements"] = entry["elements"]
                out["arrays_empty"] = entry["arrays_empty"]
                out["empty_array"] = entry["elements"] == 0
            if self.values:
                out["values"] = list(entry["values"])
            paths[path] = out
        return paths


def is_jsonl(path, jsonl):
    if jsonl is not None:
        return bool(jsonl)
    return os.fspath(path).lower().endswith(JSONL_SUFFIXES)


def build_shape(path, *, jsonl=None, max_bytes=DEFAULT_MAX_BYTES,
                max_records=DEFAULT_MAX_RECORDS, max_depth=DEFAULT_MAX_DEPTH,
                values=False, keys=None):
    """Build the shape model of one JSON or JSONL file.

    keys ({array_path: field} or ["FIELD", "path=FIELD"]) additionally records
    per-element identities for keyed array matching; JSON documents only.
    Raises InvalidInput for unreadable, unparsable or too-large JSON input.
    """
    jsonl_input = is_jsonl(path, jsonl)
    keys = parse_keys(keys)
    if keys and jsonl_input:
        raise UsageError("--key applies to single JSON documents, not JSONL")
    builder = _Builder(max_depth, values, keys)
    model = {
        "file": os.path.basename(os.fspath(path)),
        "kind": "jsonl" if jsonl_input else "json",
        "values": bool(values),
        "records": 0,
        "sampled": False,
        "invalid_lines": {"count": 0, "first": []},
    }
    try:
        with open(path, "rb") as f:
            if jsonl_input:
                _read_jsonl(f, builder, model, max_bytes, max_records)
            else:
                _read_json(f, builder, max_bytes)
                model["records"] = 1
    except OSError as exc:
        raise InvalidInput(f"cannot read input: {exc.strerror or exc}") from None

    model["warnings"] = builder.warnings.render()
    model["warning_counts"] = builder.warnings.counts
    model["paths"] = builder.finish()
    if keys:
        model["keyed"] = builder.keyed
    return model


def _read_json(f, builder, max_bytes):
    if os.fstat(f.fileno()).st_size > max_bytes:
        raise InvalidInput(f"too large (over {max_bytes} bytes)")
    data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise InvalidInput(f"too large (over {max_bytes} bytes)")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidInput("not UTF-8") from None
    try:
        record = _parse(text)
    except RecursionError:
        raise InvalidInput("not valid JSON (nested too deeply for the parser)") from None
    except ValueError as exc:
        raise InvalidInput(f"not valid JSON ({exc.__class__.__name__})") from None
    builder.add_record(record)


def _read_jsonl(f, builder, model, max_bytes, max_records):
    invalid = model["invalid_lines"]
    consumed = 0
    for lineno, line in enumerate(f, start=1):
        consumed += len(line)
        if consumed > max_bytes:
            # A line cut by the byte guard is not counted as invalid.
            model["sampled"] = True
            break
        if not line.strip():
            continue
        if model["records"] >= max_records:
            model["sampled"] = True
            break
        try:
            record = _parse(line.decode("utf-8-sig" if lineno == 1 else "utf-8"))
        except (ValueError, RecursionError):
            invalid["count"] += 1
            if len(invalid["first"]) < FIRST_N:
                invalid["first"].append(lineno)
            continue
        model["records"] += 1
        builder.add_record(record)
