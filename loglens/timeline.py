"""Timeline buckets, spike detection and the incident view.

Buckets are aligned to multiples of their size since the UTC epoch; entries
without a timestamp never reach the timeline.
"""
from dataclasses import dataclass, field
from statistics import median

from loglens.group import entry_signature
from loglens.readers import ERROR, LEVEL_RANK, WARNING
from loglens.redact import redact

BUCKETS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
AUTO = "auto"
MAX_AUTO_BUCKETS = 200
MIN_SPIKE_BUCKETS = 10
SPIKE_MADS = 3
TOP_GROUPS = 5


@dataclass
class Bucket:
    start: int                    # epoch seconds, multiple of the bucket size
    errors: int = 0               # ERROR and above
    warnings: int = 0             # WARNING only

    @property
    def total(self):
        return self.errors + self.warnings


@dataclass
class Timeline:
    name: str                     # "5m", ... or "-" when nothing is timed
    size: int
    buckets: list
    spikes: list | None           # None: fewer than MIN_SPIKE_BUCKETS buckets
    threshold: float | None = None


@dataclass
class ContextEntry:
    level: str
    signature: str
    message: str | None = None    # redacted; only with --raw


@dataclass
class Incident:
    group: object
    before: list = field(default_factory=list)
    after: list = field(default_factory=list)


def _epoch(ts):
    return int(ts.timestamp())


def _span(first, last, size):
    return last // size - first // size + 1


def choose_bucket(entries, spec=AUTO):
    """(name, seconds) for --bucket; auto is the smallest size giving at most
    MAX_AUTO_BUCKETS buckets between the first and last timestamp."""
    if spec != AUTO:
        return spec, BUCKETS[spec]
    if not entries:
        return "-", 0
    first, last = _epoch(entries[0].ts), _epoch(entries[-1].ts)
    for name, size in BUCKETS.items():
        if _span(first, last, size) <= MAX_AUTO_BUCKETS:
            return name, size
    return "1d", BUCKETS["1d"]


def detect_spikes(buckets):
    """(spikes, threshold): buckets whose ERROR+ and WARNING count is above
    median + 3 * max(MAD, 1); (None, None) when there are too few buckets."""
    if len(buckets) < MIN_SPIKE_BUCKETS:
        return None, None
    totals = [b.total for b in buckets]
    mid = median(totals)
    mad = median(abs(t - mid) for t in totals)
    threshold = mid + SPIKE_MADS * max(mad, 1)
    return [b for b in buckets if b.total > threshold], threshold


def build_timeline(entries, spec=AUTO):
    """entries: timestamped and already in timeline order."""
    name, size = choose_bucket(entries, spec)
    if not entries:
        return Timeline(name, size, [], None)
    first = _epoch(entries[0].ts) // size
    last = _epoch(entries[-1].ts) // size
    buckets = [Bucket((first + i) * size) for i in range(last - first + 1)]
    for entry in entries:
        rank = LEVEL_RANK.get(entry.level, 0)
        if rank < LEVEL_RANK[WARNING]:
            continue
        bucket = buckets[_epoch(entry.ts) // size - first]
        if rank >= LEVEL_RANK[ERROR]:
            bucket.errors += 1
        else:
            bucket.warnings += 1
    return Timeline(name, size, buckets, *detect_spikes(buckets))


def incidents(groups, sequence, *, context=0, raw=False):
    """The top WARNING+ groups by count (ties: first occurrence, then
    signature), each with the signatures of the `context` entries around its
    first occurrence in `sequence` (timeline order, untimed entries last).
    INFO and DEBUG groups never appear here."""
    serious = [g for g in groups if LEVEL_RANK.get(g.level, 0) >= LEVEL_RANK[WARNING]]
    top = sorted(serious, key=lambda g: (-g.count, g.first_index, g.signature))
    result = [Incident(g) for g in top[:TOP_GROUPS]]
    if context <= 0 or not result:
        return result
    signatures = [entry_signature(e) for e in sequence]

    def item(index):
        entry = sequence[index]
        return ContextEntry(entry.level, signatures[index],
                            redact(entry.message) if raw else None)

    for incident in result:
        index = incident.group.first_index
        incident.before = [item(i) for i in range(max(0, index - context), index)]
        incident.after = [item(i) for i in
                          range(index + 1, min(len(sequence), index + 1 + context))]
    return result
