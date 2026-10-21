"""SQL functions for askdata query. STUB for the test-first run."""
from collections import Counter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: F401


def central_fallback(utc_dt):
    return None


class SqlFunctions:
    def __init__(self):
        self.counts = Counter()

    def tz(self, ts, zone=None):
        return None

    def warnings(self):
        return []
