"""Shape builder: infer per-path types, presence and nullability from JSON / JSONL."""


def build_shape(path, *, jsonl=None, max_bytes=200_000_000, max_records=1_000_000,
                max_depth=64, values=False):
    raise NotImplementedError
